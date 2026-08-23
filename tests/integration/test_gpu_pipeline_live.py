"""Live GPU Integration Test Suite (No Mocks).

Executes real PyTorch tensor training with Automated Mixed Precision (AMP fp16),
gradient accumulation, and multi-band Sentinel-2 processing on GPU 0 (RTX 5060 Ti 16GB)
and serving inference on GPU 1 (RTX 5060 8GB).
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from orbital_drift.data.dataset import Sentinel2PatchDataset
from orbital_drift.drift.metrics import calculate_band_drift
from orbital_drift.train.baseline import (
    SimpleUNet,
    compute_iou_f1,
    train_baseline_epoch,
)

logger = logging.getLogger(__name__)


def test_live_gpu_training_pipeline_with_amp() -> None:
    """Executes a real training epoch on CUDA GPU 0 with AMP fp16 and gradient accumulation."""
    if not torch.cuda.is_available():
        pytest.skip("capability-guard: CUDA unavailable on host")

    device = "cuda:0"
    torch.cuda.empty_cache()

    # Generate synthetic 4-band Sentinel-2 tile (512x512)
    h, w = 512, 512
    raster = np.random.randint(200, 8000, size=(4, h, w), dtype=np.uint16)
    labels = np.random.randint(0, 6, size=(h, w), dtype=np.uint8)

    dataset = Sentinel2PatchDataset(raster, labels, patch_size=256, stride=256)
    dataloader = DataLoader(dataset, batch_size=2, shuffle=True)

    model = SimpleUNet(in_channels=4, num_classes=6, init_features=32)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    criterion = nn.CrossEntropyLoss()

    # Track VRAM before and during training
    mem_before = torch.cuda.memory_allocated(0) / (1024**2)

    loss = train_baseline_epoch(
        model=model,
        dataloader=dataloader,
        optimizer=optimizer,
        criterion=criterion,
        device=device,
        use_amp=True,
        grad_accum_steps=2,
    )

    mem_during = torch.cuda.memory_allocated(0) / (1024**2)
    logger.info("Epoch Loss: %.4f | VRAM: %.1fMB -> %.1fMB", loss, mem_before, mem_during)

    assert isinstance(loss, float)
    assert not np.isnan(loss)
    assert loss > 0.0

    # Evaluation pass on GPU
    model.eval()
    with torch.no_grad():
        sample_img, sample_lbl = dataset[0]
        sample_tensor = sample_img.unsqueeze(0).to(device)
        preds = model(sample_tensor)
        metrics = compute_iou_f1(preds.cpu(), sample_lbl.unsqueeze(0), num_classes=6)

    logger.info("Validation Mean IoU: %.4f | Mean F1: %.4f", metrics.mean_iou, metrics.mean_f1)
    assert 0.0 <= metrics.mean_iou <= 1.0


def test_live_gpu_drift_calculation_on_tensors() -> None:
    """Computes statistical PSI and KS drift across CUDA tensors."""
    if not torch.cuda.is_available():
        pytest.skip("capability-guard: CUDA unavailable on host")

    device = torch.device("cuda:0")

    # Generate baseline reference and shifted target on CUDA
    ref_tensor = torch.randn(100, 100, device=device) * 200.0 + 1500.0
    tgt_clean = torch.randn(100, 100, device=device) * 200.0 + 1500.0
    tgt_drifted = torch.randn(100, 100, device=device) * 400.0 + 3500.0

    ref_np = ref_tensor.cpu().numpy()
    clean_np = tgt_clean.cpu().numpy()
    drifted_np = tgt_drifted.cpu().numpy()

    clean_res = calculate_band_drift(ref_np, clean_np, band_name="B04")
    drift_res = calculate_band_drift(ref_np, drifted_np, band_name="B04")

    logger.info("Clean PSI: %.4f (Drift: %s)", clean_res.psi, clean_res.is_drifted)
    logger.info("Shifted PSI: %.4f (Drift: %s)", drift_res.psi, drift_res.is_drifted)

    assert clean_res.is_drifted is False
    assert drift_res.is_drifted is True


def test_live_multi_gpu_isolation_training_and_serving() -> None:
    """Validates GPU 0 (training) and GPU 1 (serving) multi-device isolation."""
    if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
        pytest.skip("capability-guard: Test requires dual CUDA GPUs (found < 2)")

    # Trainer on GPU 0
    train_model = SimpleUNet(in_channels=4, num_classes=4, init_features=16).to("cuda:0")
    # Serving model on GPU 1
    serve_model = SimpleUNet(in_channels=4, num_classes=4, init_features=16).to("cuda:1")

    x0 = torch.randn(1, 4, 128, 128, device="cuda:0")
    x1 = torch.randn(1, 4, 128, 128, device="cuda:1")

    out0 = train_model(x0)
    out1 = serve_model(x1)

    assert out0.device.type == "cuda" and out0.device.index == 0
    assert out1.device.type == "cuda" and out1.device.index == 1
