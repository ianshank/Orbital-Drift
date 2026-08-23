"""Contract tests for Baseline Model Training and Model Registry (Constitution Principle V)."""

from __future__ import annotations

import numpy as np
import pytest
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from orbital_drift.data.dataset import Sentinel2PatchDataset
from orbital_drift.registry.ops import ModelRegistryOps
from orbital_drift.train.baseline import (
    SimpleUNet,
    build_run_metadata,
    compute_iou_f1,
    train_baseline_epoch,
)


@pytest.mark.contract
def test_dataset_patch_generation_and_normalization() -> None:
    """Probes tensor shape, normalization, and patch extraction."""
    # (4 bands, 512 height, 512 width)
    raster = np.random.randint(0, 8000, size=(4, 512, 512), dtype=np.uint16)
    labels = np.random.randint(0, 10, size=(512, 512), dtype=np.uint8)

    ds = Sentinel2PatchDataset(raster, labels, patch_size=256, stride=256, normalize_max=10000.0)
    assert len(ds) == 4  # 2x2 patches

    img, lbl = ds[0]
    assert img.shape == (4, 256, 256)
    assert lbl.shape == (256, 256)
    assert img.dtype == torch.float32
    assert lbl.dtype == torch.int64
    assert 0.0 <= img.min().item() <= 1.0
    assert 0.0 <= img.max().item() <= 1.0


@pytest.mark.contract
def test_unet_forward_pass_and_iou_computation() -> None:
    """Probes U-Net architecture output logits and IoU/F1 calculation."""
    model = SimpleUNet(in_channels=4, num_classes=5, init_features=16)
    dummy_input = torch.randn(2, 4, 64, 64)  # batch 2
    out = model(dummy_input)

    assert out.shape == (2, 5, 64, 64)

    dummy_targets = torch.randint(0, 5, (2, 64, 64), dtype=torch.int64)
    metrics = compute_iou_f1(out, dummy_targets, num_classes=5)

    assert 0.0 <= metrics.mean_iou <= 1.0
    assert 0.0 <= metrics.mean_f1 <= 1.0
    assert len(metrics.per_class_iou) == 5


@pytest.mark.contract
def test_training_epoch_execution_and_loss_decrease() -> None:
    """Probes train_baseline_epoch execution."""
    raster = np.random.randint(0, 5000, size=(4, 128, 128), dtype=np.uint16)
    labels = np.random.randint(0, 4, size=(128, 128), dtype=np.uint8)
    ds = Sentinel2PatchDataset(raster, labels, patch_size=64, stride=64)
    loader = DataLoader(ds, batch_size=2)

    model = SimpleUNet(in_channels=4, num_classes=4, init_features=8)
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


@pytest.mark.contract
def test_provenance_metadata_triple_generation() -> None:
    """Probes {lakeFS commit, git SHA, config hash} generation."""
    meta = build_run_metadata(
        lakefs_commit="c0ffee123456",
        git_sha="9de5a0e7f1",
        config_dict={"lr": 0.001, "batch_size": 16},
    )
    assert meta["lakefs_commit_id"] == "c0ffee123456"
    assert meta["git_sha"] == "9de5a0e7f1"
    assert len(meta["config_hash"]) == 16


@pytest.mark.contract
def test_model_registry_stage_transitions_and_rollback() -> None:
    """Probes model stage transitions None -> Staging -> Production and Rollback."""
    reg = ModelRegistryOps()
    v1 = reg.register_model_version("unet-test", run_id="run-1")
    v2 = reg.register_model_version("unet-test", run_id="run-2")

    assert v1 == 1
    assert v2 == 2

    # Promote v1 to Production
    reg.transition_stage("unet-test", v1, "Production")
    assert reg.get_stage_version("unet-test", "Production") == 1

    # Stage v2
    reg.transition_stage("unet-test", v2, "Staging")
    assert reg.get_stage_version("unet-test", "Staging") == 2

    # Promote v2 to Production (v1 automatically Archived)
    reg.transition_stage("unet-test", v2, "Production")
    assert reg.get_stage_version("unet-test", "Production") == 2

    # Rollback Production (v1 promoted back to Production)
    rolled_back_v = reg.rollback_production("unet-test")
    assert rolled_back_v == 1
    assert reg.get_stage_version("unet-test", "Production") == 1
