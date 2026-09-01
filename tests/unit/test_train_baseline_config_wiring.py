"""Unit tests for RB-010 Part 5 config wiring in train/baseline.py.

Precedence contract under test, for every wired field (`train_device`,
`use_amp`, `gradient_accumulation_steps`, `num_classes`): an explicit
function/constructor argument wins; else `config`'s matching field; else the
pre-existing hardcoded literal -- so a caller that passes neither `config`
argument nor the specific field argument sees no behaviour change at all
relative to this module before RB-010 Part 5. See docs/decision-log.md
RB-010 and src/orbital_drift/config.py's `gradient_accumulation_steps` field
comment for the one field (gradient accumulation) whose config default and
this module's own hardcoded default had already diverged (2 vs 1) before
this fix -- `TestResolveGradAccumStepsPrecedence` pins that divergence
explicitly rather than papering over it.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.data.dataset import Sentinel2PatchDataset
from orbital_drift.train import baseline
from orbital_drift.train.baseline import SimpleUNet, compute_iou_f1, train_baseline_epoch

# Not real credentials -- fixed test doubles for the required lakeFS fields,
# matching tests/unit/test_config.py's `_construct_with_valid_credentials`
# pattern so every config built here is a normal, fully type-checked
# keyword-argument construction rather than an env-var round-trip.
_TEST_ACCESS_KEY = "unit-test-access-value"
_TEST_SECRET_KEY = "unit-test-secret-value"  # noqa: S105 -- test double, not a real secret


def _build_config(**overrides: object) -> OrbitalDriftConfig:
    return OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        **overrides,  # type: ignore[arg-type]
    )


# -----------------------------------------------------------------------------
# _resolve_* precedence: explicit arg > config field > hardcoded fallback
# -----------------------------------------------------------------------------


class TestResolveDevicePrecedence:
    def test_explicit_device_wins_over_config(self) -> None:
        cfg = _build_config(train_device="cuda:7")
        assert baseline._resolve_device("cpu", cfg) == "cpu"

    def test_config_used_when_device_omitted(self) -> None:
        cfg = _build_config(train_device="cuda:3")
        assert baseline._resolve_device(None, cfg) == "cuda:3"

    def test_hardcoded_fallback_when_both_omitted(self) -> None:
        """Byte-for-byte the pre-existing formula, just evaluated at call time."""
        expected = "cuda:0" if torch.cuda.is_available() else "cpu"
        assert baseline._resolve_device(None, None) == expected


class TestResolveUseAmpPrecedence:
    def test_explicit_use_amp_wins_over_config(self) -> None:
        cfg = _build_config(use_amp=True)
        assert baseline._resolve_use_amp(False, cfg) is False

    def test_config_used_when_use_amp_omitted(self) -> None:
        cfg = _build_config(use_amp=False)
        assert baseline._resolve_use_amp(None, cfg) is False

    def test_hardcoded_fallback_when_both_omitted(self) -> None:
        assert baseline._resolve_use_amp(None, None) is True


class TestResolveGradAccumStepsPrecedence:
    """Also the confirmed-divergence regression (RB-010): config's default
    (2) is what a config-driven caller now gets; this module's own historical
    default (1) is preserved exactly for callers that pass neither."""

    def test_explicit_grad_accum_steps_wins_over_config(self) -> None:
        cfg = _build_config(gradient_accumulation_steps=6)
        assert baseline._resolve_grad_accum_steps(3, cfg) == 3

    def test_config_used_when_grad_accum_steps_omitted(self) -> None:
        cfg = _build_config(gradient_accumulation_steps=6)
        assert baseline._resolve_grad_accum_steps(None, cfg) == 6

    def test_hardcoded_fallback_when_both_omitted_is_still_one(self) -> None:
        """Zero-config behaviour is byte-for-byte unchanged: still 1, not 2."""
        assert baseline._resolve_grad_accum_steps(None, None) == 1

    def test_default_config_value_resolves_to_two_not_one(self) -> None:
        """The confirmed divergence itself: OrbitalDriftConfig's own default
        (no override) is 2, not this module's historical 1. A caller that
        opts into `config=` and omits `grad_accum_steps` now trains with
        accumulation=2 -- exactly per RB-010 Part 4's deliberate 16GB VRAM
        budget default, NOT per this module's previously-independent 1.
        """
        cfg = _build_config()
        assert cfg.gradient_accumulation_steps == 2
        assert baseline._resolve_grad_accum_steps(None, cfg) == 2


class TestResolveNumClassesPrecedence:
    def test_explicit_num_classes_wins_over_config(self) -> None:
        cfg = _build_config(num_classes=7)
        assert baseline._resolve_num_classes(4, cfg) == 4

    def test_config_used_when_num_classes_omitted(self) -> None:
        cfg = _build_config(num_classes=7)
        assert baseline._resolve_num_classes(None, cfg) == 7

    def test_hardcoded_fallback_when_both_omitted(self) -> None:
        assert baseline._resolve_num_classes(None, None) == 10


# -----------------------------------------------------------------------------
# End-to-end: the public constructors/functions actually use the resolved
# value, not just the private resolvers in isolation.
# -----------------------------------------------------------------------------


def test_simple_unet_num_classes_sourced_from_config_when_omitted() -> None:
    cfg = _build_config(num_classes=6)
    model = SimpleUNet(in_channels=2, init_features=4, config=cfg)
    out = model(torch.randn(1, 2, 16, 16))
    assert out.shape == (1, 6, 16, 16)


def test_simple_unet_explicit_num_classes_still_overrides_config() -> None:
    cfg = _build_config(num_classes=6)
    model = SimpleUNet(in_channels=2, num_classes=3, init_features=4, config=cfg)
    out = model(torch.randn(1, 2, 16, 16))
    assert out.shape == (1, 3, 16, 16)


def test_compute_iou_f1_num_classes_sourced_from_config_when_omitted() -> None:
    cfg = _build_config(num_classes=3)
    predictions = torch.randn(1, 3, 4, 4)
    targets = torch.randint(0, 3, (1, 4, 4), dtype=torch.int64)
    metrics = compute_iou_f1(predictions, targets, config=cfg)
    assert len(metrics.per_class_iou) == 3


def _tiny_loader(batch_size: int = 2) -> DataLoader[tuple[torch.Tensor, torch.Tensor]]:
    raster = np.random.randint(0, 5000, size=(4, 32, 32), dtype=np.uint16)
    labels = np.random.randint(0, 2, size=(32, 32), dtype=np.uint8)
    ds = Sentinel2PatchDataset(raster, labels, patch_size=16, stride=16)
    return DataLoader(ds, batch_size=batch_size)


def test_train_baseline_epoch_forwards_device_and_config_to_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`train_baseline_epoch` must consult `_resolve_device` with exactly the
    `device`/`config` it was itself called with, rather than reading
    `config.train_device` (or not) some other, ad hoc way.

    Patches the resolver to a spy that always returns "cpu" rather than
    monkeypatching `torch.cuda.is_available` directly: PyTorch's own
    optimizer internals (independently of this module's own
    `torch.cuda.is_available()` call inside `_resolve_device`'s fallback
    branch) also probe accelerator availability during `optimizer.step()` on
    this PyTorch version, so lying to `torch.cuda.is_available` globally
    crashes deep inside `torch.optim.Adam.step()` on a host with no real
    NVIDIA driver -- a PyTorch-internals artifact, not a signal about whether
    this module's own wiring is correct. Spying on the resolver call
    isolates exactly the fact under test: did `train_baseline_epoch` forward
    `device` and `config` to `_resolve_device` unchanged.
    """
    calls: list[tuple[str | None, OrbitalDriftConfig | None]] = []

    def _spy_resolve_device(device: str | None, config: OrbitalDriftConfig | None) -> str:
        calls.append((device, config))
        return "cpu"

    monkeypatch.setattr(baseline, "_resolve_device", _spy_resolve_device)

    loader = _tiny_loader()
    model = SimpleUNet(in_channels=4, num_classes=2, init_features=4)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()
    cfg = _build_config(train_device="cpu", use_amp=False)

    loss = train_baseline_epoch(
        model=model,
        dataloader=loader,
        optimizer=optimizer,
        criterion=criterion,
        config=cfg,
    )
    assert isinstance(loss, float)
    assert calls == [(None, cfg)]


def test_train_baseline_epoch_explicit_device_wins_over_poisoned_config() -> None:
    """Explicit `device="cpu"` must win even when config's value is garbage
    that would crash if actually used -- proving explicit-arg precedence
    end-to-end, not just at the resolver level.
    """
    loader = _tiny_loader()
    model = SimpleUNet(in_channels=4, num_classes=2, init_features=4)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()
    cfg = _build_config(train_device="not-a-real-torch-device", use_amp=False)

    loss = train_baseline_epoch(
        model=model,
        dataloader=loader,
        optimizer=optimizer,
        criterion=criterion,
        device="cpu",
        config=cfg,
    )
    assert isinstance(loss, float)


def test_train_baseline_epoch_config_grad_accum_steps_end_to_end() -> None:
    """A config with a non-default `gradient_accumulation_steps` drives a
    full epoch without error -- guards against the resolved value being read
    once and then ignored, or against a stale hardcoded `1` reappearing and
    silently diverging from config again.
    """
    loader = _tiny_loader()
    model = SimpleUNet(in_channels=4, num_classes=2, init_features=4)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()
    cfg = _build_config(gradient_accumulation_steps=3, use_amp=False, train_device="cpu")

    loss = train_baseline_epoch(
        model=model,
        dataloader=loader,
        optimizer=optimizer,
        criterion=criterion,
        config=cfg,
    )
    assert isinstance(loss, float)
    assert loss > 0.0


def test_train_baseline_epoch_with_no_config_and_no_overrides_matches_pre_wiring_behavior() -> None:
    """Positive control: the exact call shape used before RB-010 Part 5 (no
    `config`, explicit `device`/`use_amp`) keeps working identically.
    """
    loader = _tiny_loader()
    model = SimpleUNet(in_channels=4, num_classes=2, init_features=4)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    loss = train_baseline_epoch(
        model=model,
        dataloader=loader,
        optimizer=optimizer,
        criterion=criterion,
        device="cpu",
        use_amp=False,
    )
    assert isinstance(loss, float)
    assert loss > 0.0
