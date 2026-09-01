"""Baseline Land-Cover Segmentation Model and Training Pipeline.

Implements U-Net architecture with AMP fp16 support, gradient accumulation,
IoU/F1 evaluation metrics, and MLflow run logging ({lakeFS commit, git SHA, config hash}).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, NamedTuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from orbital_drift.config import OrbitalDriftConfig

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Config resolution helpers (RB-010 Part 5: per-module config wiring)
#
# Precedence for every wired field below: explicit function/constructor
# argument > `config`'s matching field > the pre-existing hardcoded literal.
# Passing no `config` at all reproduces this module's pre-Part-5 behaviour
# exactly -- every call site in this module resolves the same way instead of
# re-deriving the precedence rule ad hoc.
#
# `gradient_accumulation_steps` is the one field where "the pre-existing
# hardcoded literal" and "config's default" had already diverged before this
# fix: this module's own default was `1`, `OrbitalDriftConfig`'s is `2`
# (docs/decision-log.md RB-010; config.py's field comment). Passing a
# `config` and omitting `grad_accum_steps` now yields `2`, not `1` -- a
# deliberate, small, opt-in behaviour change. Callers who pass neither
# `config` nor `grad_accum_steps` keep today's `1` exactly. See
# `_resolve_grad_accum_steps` below.
# -----------------------------------------------------------------------------


def _resolve_device(device: str | None, config: OrbitalDriftConfig | None) -> str:
    """Resolves the training device.

    Precedence: explicit `device` > `config.train_device` > the pre-existing
    hardcoded `"cuda:0" if torch.cuda.is_available() else "cpu"` heuristic --
    now evaluated at call time instead of at function-definition time (import
    time), so it reflects CUDA availability at the moment training actually
    runs rather than whatever it was when this module was first imported.
    """
    if device is not None:
        return device
    if config is not None:
        return config.train_device
    return (
        "cuda:0"  # pin: pre-existing hardcoded fallback default, see docstring above
        if torch.cuda.is_available()
        else "cpu"
    )


def _resolve_use_amp(use_amp: bool | None, config: OrbitalDriftConfig | None) -> bool:
    """Resolves whether AMP (fp16) is enabled.

    Precedence: explicit `use_amp` > `config.use_amp` > the pre-existing
    hardcoded default of `True`.
    """
    if use_amp is not None:
        return use_amp
    if config is not None:
        return config.use_amp
    return True


def _resolve_grad_accum_steps(
    grad_accum_steps: int | None,
    config: OrbitalDriftConfig | None,
) -> int:
    """Resolves the gradient accumulation step count.

    Precedence: explicit `grad_accum_steps` > `config.gradient_accumulation_steps`
    > the pre-existing hardcoded default of `1`.

    CONFIRMED DIVERGENCE (RB-010): this module's own hardcoded default was
    `1`; `OrbitalDriftConfig.gradient_accumulation_steps` defaults to `2`
    (deliberately, per RB-010 Part 4, for the 16GB VRAM budget with AMP +
    gradient accumulation). This function resolves precedence only -- it does
    not reconcile the two literals -- so passing a `config` and omitting
    `grad_accum_steps` now yields `2`. Omitting both keeps `1`, unchanged.
    """
    if grad_accum_steps is not None:
        return grad_accum_steps
    if config is not None:
        return config.gradient_accumulation_steps
    return 1


def _resolve_num_classes(num_classes: int | None, config: OrbitalDriftConfig | None) -> int:
    """Resolves the segmentation class count.

    Precedence: explicit `num_classes` > `config.num_classes` > the
    pre-existing hardcoded default of `10`.
    """
    if num_classes is not None:
        return num_classes
    if config is not None:
        return config.num_classes
    return 10  # pin: pre-existing hardcoded fallback default, see docstring above


class DoubleConv(nn.Module):
    """(Conv2d -> BatchNorm -> ReLU) * 2."""

    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False),  # pin: 3x3 kernel
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False),  # pin: 3x3 kernel
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(x)  # type: ignore[no-any-return]


class SimpleUNet(nn.Module):
    """Lightweight U-Net baseline for multi-spectral segmentation."""

    def __init__(
        self,
        in_channels: int = 4,  # pin: follow-up D-012 F1 (no config field yet)
        num_classes: int | None = None,
        init_features: int = 32,  # pin: follow-up D-012 F1 (no config field yet)
        config: OrbitalDriftConfig | None = None,
    ) -> None:
        """Builds the network.

        `num_classes` resolves via `_resolve_num_classes`: explicit argument >
        `config.num_classes` > the pre-existing hardcoded default of `10`
        (RB-010 Part 5: per-module config wiring).
        """
        super().__init__()
        resolved_num_classes = _resolve_num_classes(num_classes, config)
        features = init_features
        self.encoder1 = DoubleConv(in_channels, features)
        self.pool1 = nn.MaxPool2d(2, 2)
        self.encoder2 = DoubleConv(features, features * 2)
        self.pool2 = nn.MaxPool2d(2, 2)
        self.encoder3 = DoubleConv(features * 2, features * 4)  # pin: channel multiplier
        self.pool3 = nn.MaxPool2d(2, 2)

        self.bottleneck = DoubleConv(features * 4, features * 8)  # pin: channel multiplier

        self.upconv3 = nn.ConvTranspose2d(
            features * 8,  # pin: channel multiplier
            features * 4,  # pin: channel multiplier
            2,
            stride=2,
        )
        self.decoder3 = DoubleConv(features * 8, features * 4)  # pin: channel multiplier
        self.upconv2 = nn.ConvTranspose2d(
            features * 4,  # pin: channel multiplier
            features * 2,
            2,
            stride=2,
        )
        self.decoder2 = DoubleConv(features * 4, features * 2)  # pin: channel multiplier
        self.upconv1 = nn.ConvTranspose2d(features * 2, features, 2, stride=2)
        self.decoder1 = DoubleConv(features * 2, features)

        self.conv_out = nn.Conv2d(features, resolved_num_classes, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        enc1 = self.encoder1(x)
        enc2 = self.encoder2(self.pool1(enc1))
        enc3 = self.encoder3(self.pool2(enc2))

        bottleneck = self.bottleneck(self.pool3(enc3))

        dec3 = self.upconv3(bottleneck)
        dec3 = torch.cat((dec3, enc3), dim=1)
        dec3 = self.decoder3(dec3)

        dec2 = self.upconv2(dec3)
        dec2 = torch.cat((dec2, enc2), dim=1)
        dec2 = self.decoder2(dec2)

        dec1 = self.upconv1(dec2)
        dec1 = torch.cat((dec1, enc1), dim=1)
        dec1 = self.decoder1(dec1)

        return self.conv_out(dec1)  # type: ignore[no-any-return]


class EvalMetrics(NamedTuple):
    """Evaluation metrics summary."""

    mean_iou: float
    mean_f1: float
    per_class_iou: dict[int, float]


def compute_iou_f1(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    num_classes: int | None = None,
    config: OrbitalDriftConfig | None = None,
) -> EvalMetrics:
    """Computes Mean IoU and Mean F1/Dice score across classes.

    `num_classes` resolves via `_resolve_num_classes`: explicit argument >
    `config.num_classes` > the pre-existing hardcoded default of `10`
    (RB-010 Part 5: per-module config wiring).
    """
    resolved_num_classes = _resolve_num_classes(num_classes, config)
    preds = torch.argmax(predictions, dim=1).view(-1)
    targs = targets.view(-1)

    ious: dict[int, float] = {}
    f1s: list[float] = []

    for cls in range(resolved_num_classes):
        pred_cls = preds == cls
        targ_cls = targs == cls
        intersection = float(torch.sum(pred_cls & targ_cls).item())
        union = float(torch.sum(pred_cls | targ_cls).item())
        total = float(torch.sum(pred_cls).item() + torch.sum(targ_cls).item())

        if union > 0:
            iou = intersection / union
            ious[cls] = iou
        else:
            ious[cls] = 1.0

        if total > 0:
            f1 = (2.0 * intersection) / total
            f1s.append(f1)
        else:
            f1s.append(1.0)

    mean_iou = float(np.mean(list(ious.values())))
    mean_f1 = float(np.mean(f1s))
    return EvalMetrics(mean_iou=mean_iou, mean_f1=mean_f1, per_class_iou=ious)


def train_baseline_epoch(
    model: nn.Module,
    dataloader: DataLoader[tuple[torch.Tensor, torch.Tensor]],
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    device: str | None = None,
    use_amp: bool | None = None,
    grad_accum_steps: int | None = None,
    config: OrbitalDriftConfig | None = None,
) -> float:
    """Executes one training epoch with optional AMP and gradient accumulation.

    `device`, `use_amp`, and `grad_accum_steps` each resolve through this
    module's `_resolve_*` helpers (RB-010 Part 5: per-module config wiring):
    an explicit argument always wins, then `config`'s matching field, then
    the original hardcoded default. See `_resolve_grad_accum_steps` for the
    one field (gradient accumulation) whose config default has already
    diverged from this module's own historical default.
    """
    resolved_device = _resolve_device(device, config)
    resolved_use_amp = _resolve_use_amp(use_amp, config)
    resolved_grad_accum_steps = _resolve_grad_accum_steps(grad_accum_steps, config)

    model.train()
    model.to(resolved_device)
    total_loss = 0.0
    optimizer.zero_grad()

    scaler = torch.amp.GradScaler("cuda", enabled=(resolved_use_amp and "cuda" in resolved_device))

    for step, (images, targets) in enumerate(dataloader):
        images = images.to(resolved_device)
        targets = targets.to(resolved_device)

        with torch.amp.autocast("cuda", enabled=(resolved_use_amp and "cuda" in resolved_device)):
            outputs = model(images)
            loss = criterion(outputs, targets)
            loss = loss / resolved_grad_accum_steps

        scaler.scale(loss).backward()

        if (step + 1) % resolved_grad_accum_steps == 0 or (step + 1) == len(dataloader):
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad()

        total_loss += loss.item() * resolved_grad_accum_steps

    return total_loss / max(len(dataloader), 1)


def build_run_metadata(
    lakefs_commit: str,
    git_sha: str,
    config_dict: dict[str, Any],
) -> dict[str, str]:
    """Generates immutable run metadata triple {lakeFS commit, git SHA, config hash}."""
    cfg_json = json.dumps(config_dict, sort_keys=True)
    cfg_hash = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()[:16]  # pin: truncated hash
    return {
        "lakefs_commit_id": lakefs_commit,
        "git_sha": git_sha,
        "config_hash": cfg_hash,
    }
