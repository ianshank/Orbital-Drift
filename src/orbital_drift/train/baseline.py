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

logger = logging.getLogger(__name__)


class DoubleConv(nn.Module):
    """(Conv2d -> BatchNorm -> ReLU) * 2."""

    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(x)  # type: ignore[no-any-return]


class SimpleUNet(nn.Module):
    """Lightweight U-Net baseline for multi-spectral segmentation."""

    def __init__(
        self,
        in_channels: int = 4,
        num_classes: int = 10,
        init_features: int = 32,
    ) -> None:
        super().__init__()
        features = init_features
        self.encoder1 = DoubleConv(in_channels, features)
        self.pool1 = nn.MaxPool2d(2, 2)
        self.encoder2 = DoubleConv(features, features * 2)
        self.pool2 = nn.MaxPool2d(2, 2)
        self.encoder3 = DoubleConv(features * 2, features * 4)
        self.pool3 = nn.MaxPool2d(2, 2)

        self.bottleneck = DoubleConv(features * 4, features * 8)

        self.upconv3 = nn.ConvTranspose2d(features * 8, features * 4, 2, stride=2)
        self.decoder3 = DoubleConv(features * 8, features * 4)
        self.upconv2 = nn.ConvTranspose2d(features * 4, features * 2, 2, stride=2)
        self.decoder2 = DoubleConv(features * 4, features * 2)
        self.upconv1 = nn.ConvTranspose2d(features * 2, features, 2, stride=2)
        self.decoder1 = DoubleConv(features * 2, features)

        self.conv_out = nn.Conv2d(features, num_classes, 1)

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
    num_classes: int = 10,
) -> EvalMetrics:
    """Computes Mean IoU and Mean F1/Dice score across classes."""
    preds = torch.argmax(predictions, dim=1).view(-1)
    targs = targets.view(-1)

    ious: dict[int, float] = {}
    f1s: list[float] = []

    for cls in range(num_classes):
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
    device: str = "cuda:0" if torch.cuda.is_available() else "cpu",
    use_amp: bool = True,
    grad_accum_steps: int = 1,
) -> float:
    """Executes one training epoch with optional AMP and gradient accumulation."""
    model.train()
    model.to(device)
    total_loss = 0.0
    optimizer.zero_grad()

    scaler = torch.amp.GradScaler("cuda", enabled=(use_amp and "cuda" in device))

    for step, (images, targets) in enumerate(dataloader):
        images = images.to(device)
        targets = targets.to(device)

        with torch.amp.autocast("cuda", enabled=(use_amp and "cuda" in device)):
            outputs = model(images)
            loss = criterion(outputs, targets)
            loss = loss / grad_accum_steps

        scaler.scale(loss).backward()

        if (step + 1) % grad_accum_steps == 0 or (step + 1) == len(dataloader):
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad()

        total_loss += loss.item() * grad_accum_steps

    return total_loss / max(len(dataloader), 1)


def build_run_metadata(
    lakefs_commit: str,
    git_sha: str,
    config_dict: dict[str, Any],
) -> dict[str, str]:
    """Generates immutable run metadata triple {lakeFS commit, git SHA, config hash}."""
    cfg_json = json.dumps(config_dict, sort_keys=True)
    cfg_hash = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()[:16]
    return {
        "lakefs_commit_id": lakefs_commit,
        "git_sha": git_sha,
        "config_hash": cfg_hash,
    }
