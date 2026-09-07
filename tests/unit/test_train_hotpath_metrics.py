"""Characterization tests for RB-013 hot-path metric / epoch-loop contracts.

These tests pin behaviour that was already true of the class-loop
``compute_iou_f1`` and the per-step ``loss.item()`` epoch reporter. They are
born-green against that implementation on purpose: a vectorized rewrite must
not change empty-class convention, out-of-range-target false-positive
accounting, or the mean-loss formula that undoes gradient-accumulation
scaling.

CUDA parametrization includes ``"cuda"`` only when
``torch.cuda.is_available()`` so this file never introduces a new
skip site (closed allowlist in ``tests/governance/test_zero_skip_guard.py``).
"""

from __future__ import annotations

import numpy as np
import pytest
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from orbital_drift.train.baseline import compute_iou_f1, train_baseline_epoch

_DEVICES: tuple[str, ...] = ("cpu", "cuda") if torch.cuda.is_available() else ("cpu",)


def _one_hot_logits(labels: torch.Tensor, num_classes: int) -> torch.Tensor:
    """Build logits whose argmax recovers ``labels`` (in-range classes only)."""
    n, height, width = labels.shape
    logits = torch.full((n, num_classes, height, width), -10.0)
    in_range = (labels >= 0) & (labels < num_classes)
    batch_i, row_i, col_i = torch.where(in_range)
    logits[batch_i, labels[batch_i, row_i, col_i], row_i, col_i] = 10.0
    return logits


def _loop_reference(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    num_classes: int,
) -> tuple[float, float, dict[int, float]]:
    """Independent numpy transcription of the original per-class loop.

    Exists so a naive ``in_range``-filter vectorization (drop OOB-target
    pixels before counting predictions) cannot pass by matching itself.
    """
    preds = torch.argmax(predictions, dim=1).reshape(-1).detach().cpu().numpy()
    targs = targets.reshape(-1).detach().cpu().numpy()
    ious: dict[int, float] = {}
    f1s: list[float] = []
    for cls in range(num_classes):
        pred_cls = preds == cls
        targ_cls = targs == cls
        intersection = float(np.sum(pred_cls & targ_cls))
        union = float(np.sum(pred_cls | targ_cls))
        total = float(np.sum(pred_cls) + np.sum(targ_cls))
        ious[cls] = (intersection / union) if union > 0 else 1.0
        f1s.append((2.0 * intersection) / total if total > 0 else 1.0)
    return float(np.mean(list(ious.values()))), float(np.mean(f1s)), ious


@pytest.mark.parametrize("device", _DEVICES)
def test_empty_class_iou_and_f1_are_one(device: str) -> None:
    """Pin (already-true): a class absent from both pred and target scores 1.0."""
    targets = torch.zeros(1, 2, 2, dtype=torch.int64, device=device)
    logits = _one_hot_logits(targets, num_classes=3).to(device)
    metrics = compute_iou_f1(logits, targets, num_classes=3)
    assert metrics.per_class_iou[0] == pytest.approx(1.0)
    assert metrics.per_class_iou[1] == pytest.approx(1.0)
    assert metrics.per_class_iou[2] == pytest.approx(1.0)
    assert metrics.mean_iou == pytest.approx(1.0)
    assert metrics.mean_f1 == pytest.approx(1.0)


@pytest.mark.parametrize("device", _DEVICES)
def test_per_class_iou_includes_every_class_index(device: str) -> None:
    """Pin (already-true): dict keys are 0..n-1 even when some classes are empty."""
    targets = torch.zeros(1, 1, 1, dtype=torch.int64, device=device)
    logits = _one_hot_logits(targets, num_classes=4).to(device)
    metrics = compute_iou_f1(logits, targets, num_classes=4)
    assert set(metrics.per_class_iou) == {0, 1, 2, 3}


@pytest.mark.parametrize("device", _DEVICES)
def test_oob_targets_still_count_in_range_predictions_as_false_positives(
    device: str,
) -> None:
    """Pin (already-true): OOB targets do not drop in-range pred pixels.

    All pixels predicted class 0, all targets 99. Class 0 union is every
    pixel (false positives); class 1 is empty. A rewrite that filters
    ``targs`` to ``[0, n)`` before counting predictions would score both
    classes as empty (IoU 1.0) and fail this test.
    """
    targets = torch.full((1, 2, 2), 99, dtype=torch.int64, device=device)
    logits = torch.zeros(1, 2, 2, 2, device=device)
    logits[0, 0] = 10.0
    metrics = compute_iou_f1(logits, targets, num_classes=2)
    assert metrics.per_class_iou[0] == pytest.approx(0.0)
    assert metrics.per_class_iou[1] == pytest.approx(1.0)
    assert metrics.mean_iou == pytest.approx(0.5)
    assert metrics.mean_f1 == pytest.approx(0.5)


@pytest.mark.parametrize("device", _DEVICES)
def test_oob_argmax_is_not_clamped_into_class_range(device: str) -> None:
    """Pin (already-true): argmax class >= num_classes is ignored, not wrapped."""
    targets = torch.zeros(1, 1, 2, dtype=torch.int64, device=device)
    logits = torch.zeros(1, 3, 1, 2, device=device)
    logits[0, 2] = 10.0
    metrics = compute_iou_f1(logits, targets, num_classes=2)
    assert metrics.per_class_iou[0] == pytest.approx(0.0)
    assert metrics.per_class_iou[1] == pytest.approx(1.0)


@pytest.mark.parametrize("device", _DEVICES)
def test_compute_iou_f1_matches_independent_loop_reference(device: str) -> None:
    """Pin (already-true): vectorized output matches the original class loop."""
    torch.manual_seed(0)
    num_classes = 4
    logits = torch.randn(2, num_classes, 8, 8, device=device)
    targets = torch.randint(-1, num_classes + 2, (2, 8, 8), device=device)
    metrics = compute_iou_f1(logits, targets, num_classes=num_classes)
    mean_iou, mean_f1, per_class = _loop_reference(logits, targets, num_classes)
    assert metrics.mean_iou == pytest.approx(mean_iou)
    assert metrics.mean_f1 == pytest.approx(mean_f1)
    assert metrics.per_class_iou.keys() == per_class.keys()
    for cls, expected in per_class.items():
        assert metrics.per_class_iou[cls] == pytest.approx(expected)


class _ConstantCriterion(nn.Module):
    """Loss that reports a constant, still attached to the graph."""

    def __init__(self, value: float) -> None:
        super().__init__()
        self.value = value

    def forward(self, outputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        del targets
        return (outputs * 0).sum() + outputs.new_tensor(self.value)


class _TinyLinearSeg(nn.Module):
    """One-parameter stand-in so the optimizer has something to step."""

    def __init__(self, num_classes: int) -> None:
        super().__init__()
        self.bias = nn.Parameter(torch.zeros(num_classes, 1, 1))

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        return images[:, :1] * 0 + self.bias


class _PairDataset(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    """Tiny (image, label) dataset with a mypy-precise pair type."""

    def __init__(self, images: torch.Tensor, labels: torch.Tensor) -> None:
        self.images = images
        self.labels = labels

    def __len__(self) -> int:
        return int(self.images.shape[0])

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.images[index], self.labels[index]


def test_train_baseline_epoch_mean_loss_undoes_grad_accum_division() -> None:
    """Pin (already-true): reported mean is the unscaled criterion value.

    The loop divides ``loss`` by ``grad_accum_steps`` for backward, then
    multiplies that scaled value back up when accumulating the reported
    mean. Constant criterion 4.0 must therefore return 4.0 at accum=1 and
    accum=2, not 2.0.
    """
    images = torch.zeros(4, 1, 4, 4)
    labels = torch.zeros(4, 4, 4, dtype=torch.int64)
    loader = DataLoader(_PairDataset(images, labels), batch_size=2)
    criterion = _ConstantCriterion(4.0)

    for accum in (1, 2):
        model = _TinyLinearSeg(num_classes=2)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.0)
        reported = train_baseline_epoch(
            model=model,
            dataloader=loader,
            optimizer=optimizer,
            criterion=criterion,
            device="cpu",
            use_amp=False,
            grad_accum_steps=accum,
        )
        assert reported == pytest.approx(4.0)


def test_train_baseline_epoch_skips_model_to_when_already_on_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin: skip ``model.to`` when the lead parameter is already on device."""
    images = torch.zeros(2, 1, 4, 4)
    labels = torch.zeros(2, 4, 4, dtype=torch.int64)
    loader = DataLoader(_PairDataset(images, labels), batch_size=2)
    model = _TinyLinearSeg(num_classes=2)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.0)
    calls: list[str] = []
    original_to = model.to

    def _spy_to(device: str) -> _TinyLinearSeg:
        calls.append(device)
        return original_to(device)

    monkeypatch.setattr(model, "to", _spy_to)
    reported = train_baseline_epoch(
        model=model,
        dataloader=loader,
        optimizer=optimizer,
        criterion=_ConstantCriterion(4.0),
        device="cpu",
        use_amp=False,
        grad_accum_steps=1,
    )
    assert reported == pytest.approx(4.0)
    assert calls == []
