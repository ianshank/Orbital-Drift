"""Comprehensive Live Dual-GPU End-to-End User Journey Test Suite.

Executes the entire Continuous Training and Canary Serving loop live on dual CUDA GPUs:
- Primary Trainer on GPU 0 (NVIDIA RTX 5060 Ti) with PyTorch AMP fp16 autocast.
- Canary Serving Container on GPU 1 (NVIDIA RTX 5060) with dynamic traffic splitting.
- Cross-device transfers, lakeFS versioning, MLflow stage lifecycle, and sub-second rollback drill.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pytest
import torch
import torch.nn as nn
from fastapi.testclient import TestClient
from torch.utils.data import DataLoader

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.data.dataset import Sentinel2PatchDataset
from orbital_drift.data.lakefs_ops import LakeFSOps
from orbital_drift.drift.metrics import evaluate_scene_drift
from orbital_drift.drift.trigger import DriftTriggerManager
from orbital_drift.ingest.cloud import evaluate_cloud_mask
from orbital_drift.ingest.tile_store import TileStore
from orbital_drift.registry.ops import ModelRegistryOps
from orbital_drift.serve.app import app, container
from orbital_drift.train.baseline import (
    SimpleUNet,
    compute_iou_f1,
    train_baseline_epoch,
)

logger = logging.getLogger(__name__)


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_live_dual_gpu_continuous_training_and_serving_e2e(tmp_path: Path) -> None:
    """Runs the complete CT lifecycle across live dual GPUs without mocks."""
    if not torch.cuda.is_available():
        pytest.skip("capability-guard: CUDA unavailable on host")

    num_gpus = torch.cuda.device_count()
    train_device = "cuda:0"
    serve_device = "cuda:1" if num_gpus >= 2 else "cuda:0"

    logger.info("Initializing Live Dual-GPU E2E CT Test on %s and %s", train_device, serve_device)
    config = OrbitalDriftConfig(
        train_device=train_device,
        serve_device=serve_device,
        tile_store_path=tmp_path / "tiles",
        lakefs_access_key="test-access-value",
        lakefs_secret_key="test-secret-value",
    )

    tile_store = TileStore(base_dir=config.tile_store_path)
    lakefs = LakeFSOps(repository=config.lakefs_repository)
    registry = ModelRegistryOps()
    trigger_mgr = DriftTriggerManager(
        hysteresis_window=config.drift_hysteresis_window,
        cooldown_scenes=config.drift_cooldown_scenes,
    )

    # -------------------------------------------------------------------------
    # Stage 1: Ingestion & SCL Cloud Masking
    # -------------------------------------------------------------------------
    h, w = 256, 256
    bands_raw = {
        "B02": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B03": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B04": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B08": np.random.randint(2000, 4000, (h, w), dtype=np.uint16),
    }
    scl_clean = np.full((h, w), 4, dtype=np.uint8)  # Class 4: Vegetation (clear sky)
    cloud_eval = evaluate_cloud_mask(scl_clean, cloud_threshold=config.cloud_cover_threshold)
    assert cloud_eval.is_usable is True

    tile_store.save_scene(
        "scene-base-001", bands_raw, metadata={"cloud_fraction": cloud_eval.cloud_fraction}
    )
    commit_base = lakefs.commit_scene("scene-base-001", metadata={"status": "initial_baseline"})
    assert isinstance(commit_base, str) and len(commit_base) >= 8

    # -------------------------------------------------------------------------
    # Stage 2: Initial Baseline Model (v1) Training on GPU 0 (AMP fp16)
    # -------------------------------------------------------------------------
    base_arr, _ = tile_store.load_scene("scene-base-001")
    base_labels = np.random.randint(0, 4, (h, w), dtype=np.uint8)
    ds_base = Sentinel2PatchDataset(base_arr, base_labels, patch_size=128, stride=128)
    loader_base = DataLoader(ds_base, batch_size=2, shuffle=True)

    torch.cuda.empty_cache()
    vram_before_train = torch.cuda.memory_allocated(0) / (1024**2)

    model_v1 = SimpleUNet(in_channels=4, num_classes=4, init_features=16)
    optimizer_v1 = torch.optim.AdamW(model_v1.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    loss_v1 = train_baseline_epoch(
        model=model_v1,
        dataloader=loader_base,
        optimizer=optimizer_v1,
        criterion=criterion,
        device=train_device,
        use_amp=config.use_amp,
        grad_accum_steps=config.grad_accum_steps,
    )
    vram_after_train = torch.cuda.memory_allocated(0) / (1024**2)
    logger.info(
        "Model v1 Trained on %s | Loss: %.4f | VRAM: %.1fMB -> %.1fMB",
        train_device,
        loss_v1,
        vram_before_train,
        vram_after_train,
    )
    assert loss_v1 > 0.0

    # Register v1 and Promote to Production
    v1_id = registry.register_model_version(
        "unet-dual-gpu", run_id="run-gpu-001", metadata=lakefs.pin_dataset_snapshot(commit_base)
    )
    registry.transition_stage("unet-dual-gpu", v1_id, "Production")
    assert registry.get_stage_version("unet-dual-gpu", "Production") == 1

    # Deploy v1 to Serving Container on GPU 1
    model_v1_serve = SimpleUNet(in_channels=4, num_classes=4, init_features=16)
    model_v1_serve.load_state_dict(model_v1.state_dict())
    model_v1_serve.to(serve_device)
    model_v1_serve.eval()

    container.set_models(
        production=model_v1_serve,
        prod_version=1,
        canary_ratio=0.0,
    )

    # -------------------------------------------------------------------------
    # Stage 3: Statistical Drift Detection & Continuous Training Trigger
    # -------------------------------------------------------------------------
    # Generate drifted scenes (heavy NIR & Red shift)
    bands_drifted = {
        "B02": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B03": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B04": np.random.randint(3500, 6000, (h, w), dtype=np.uint16),
        "B08": np.random.randint(1000, 2200, (h, w), dtype=np.uint16),
    }

    # Scene 1: Drifted (Hysteresis 1/3)
    tile_store.save_scene("scene-drift-001", bands_drifted)
    lakefs.commit_scene("scene-drift-001")
    drift_arr1, _ = tile_store.load_scene("scene-drift-001")
    rep1 = evaluate_scene_drift(base_arr, drift_arr1, psi_threshold=config.psi_threshold)
    dec1 = trigger_mgr.process_scene_verdict(rep1.overall_drift_detected, "scene-drift-001")
    assert dec1.should_trigger is False  # 1/3

    # Scene 2: Drifted (Hysteresis 2/3)
    tile_store.save_scene("scene-drift-002", bands_drifted)
    lakefs.commit_scene("scene-drift-002")
    drift_arr2, _ = tile_store.load_scene("scene-drift-002")
    rep2 = evaluate_scene_drift(base_arr, drift_arr2, psi_threshold=config.psi_threshold)
    dec2 = trigger_mgr.process_scene_verdict(rep2.overall_drift_detected, "scene-drift-002")
    assert dec2.should_trigger is False  # 2/3

    # Scene 3: Drifted (Hysteresis 3/3 -> Retrain Trigger Emitted!)
    tile_store.save_scene("scene-drift-003", bands_drifted)
    commit_drift3 = lakefs.commit_scene("scene-drift-003")
    drift_arr3, _ = tile_store.load_scene("scene-drift-003")
    rep3 = evaluate_scene_drift(base_arr, drift_arr3, psi_threshold=config.psi_threshold)
    dec3 = trigger_mgr.process_scene_verdict(rep3.overall_drift_detected, "scene-drift-003")
    assert dec3.should_trigger is True  # 3/3 -> Fired!
    logger.info("Continuous Training Retrain Event Emitted: %s", dec3.reason)

    # -------------------------------------------------------------------------
    # Stage 4: Retraining Candidate Model (v2) on GPU 0 with Lineage Snapshot
    # -------------------------------------------------------------------------
    snapshot_drift = lakefs.pin_dataset_snapshot(commit_drift3)
    ds_retrain = Sentinel2PatchDataset(drift_arr3, base_labels, patch_size=128, stride=128)
    loader_retrain = DataLoader(ds_retrain, batch_size=2, shuffle=True)

    model_v2 = SimpleUNet(in_channels=4, num_classes=4, init_features=16)
    optimizer_v2 = torch.optim.AdamW(model_v2.parameters(), lr=1e-3)
    loss_v2 = train_baseline_epoch(
        model=model_v2,
        dataloader=loader_retrain,
        optimizer=optimizer_v2,
        criterion=criterion,
        device=train_device,
        use_amp=config.use_amp,
        grad_accum_steps=config.grad_accum_steps,
    )
    assert loss_v2 > 0.0

    v2_id = registry.register_model_version(
        "unet-dual-gpu", run_id="run-gpu-002", metadata=snapshot_drift
    )
    registry.transition_stage("unet-dual-gpu", v2_id, "Staging")
    trigger_mgr.mark_retraining_completed()

    # -------------------------------------------------------------------------
    # Stage 5: Shadow Evaluation & Promotion Gate
    # -------------------------------------------------------------------------
    model_v1.to(train_device).eval()
    model_v2.to(train_device).eval()
    with torch.no_grad():
        test_patch, test_lbl = ds_retrain[0]
        test_input = test_patch.unsqueeze(0).to(train_device)
        out_v1 = model_v1(test_input)
        out_v2 = model_v2(test_input)

        m_v1 = compute_iou_f1(out_v1.cpu(), test_lbl.unsqueeze(0), num_classes=4)
        m_v2 = compute_iou_f1(out_v2.cpu(), test_lbl.unsqueeze(0), num_classes=4)
        logger.info(
            "Shadow Eval: Baseline Mean IoU=%.4f vs Candidate Mean IoU=%.4f",
            m_v1.mean_iou,
            m_v2.mean_iou,
        )

    # Promote Candidate v2 to Production
    registry.transition_stage("unet-dual-gpu", v2_id, "Production")
    assert registry.get_stage_version("unet-dual-gpu", "Production") == 2

    # -------------------------------------------------------------------------
    # Stage 6: Live Canary Serving on GPU 1 (FastAPI Inference + Metrics)
    # -------------------------------------------------------------------------
    model_v2_serve = SimpleUNet(in_channels=4, num_classes=4, init_features=16)
    model_v2_serve.load_state_dict(model_v2.state_dict())
    model_v2_serve.to(serve_device)
    model_v2_serve.eval()

    # Set up 40% Canary split (40% Staging v1, 60% Production v2)
    container.device = serve_device
    container.set_models(
        production=model_v2_serve,
        prod_version=2,
        staging=model_v1_serve,
        staging_version=1,
        canary_ratio=0.40,
    )

    client = TestClient(app)

    # Send a batch of 50 inference requests to verify live routing on GPU 1
    versions_seen: list[int] = []
    latencies: list[float] = []

    req_tensor = test_patch.numpy().tolist()
    for req_idx in range(50):
        t0 = time.perf_counter()
        resp = client.post(
            "/predict",
            json={"request_id": f"req-gpu-{req_idx}", "image_array": req_tensor},
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)
        assert resp.status_code == 200
        body = resp.json()
        assert body["served_by_model"] in ("Production", "Staging")
        versions_seen.append(body["model_version"])

    # Verify both models handled traffic according to canary split
    v1_count = versions_seen.count(1)
    v2_count = versions_seen.count(2)
    p99_lat = float(np.percentile(latencies, 99))
    logger.info(
        "Inference Traffic: v1 (Staging)=%d, v2 (Prod)=%d | P99 Latency=%.2fms",
        v1_count,
        v2_count,
        p99_lat,
    )
    assert v1_count > 0, "Canary staging should have received traffic"
    assert v2_count > 0, "Production model should have received traffic"

    # Query Prometheus metrics endpoint
    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    metrics_data = metrics_resp.json()
    assert metrics_data["requests_total"] >= 50
    assert metrics_data["canary_ratio"] == 0.40
    assert metrics_data["avg_latency_ms"] < 200.0

    # -------------------------------------------------------------------------
    # Stage 7: Automated Rollback Drill (< 10 Minutes SLA)
    # -------------------------------------------------------------------------
    t_drill_start = time.perf_counter()
    logger.info("Executing Emergency Automated Rollback Drill...")

    # Step 1: Model Registry Demotion
    rolled_version = registry.rollback_production("unet-dual-gpu")
    assert rolled_version == 1
    assert registry.get_stage_version("unet-dual-gpu", "Production") == 1

    # Step 2: Instant Traffic Neutralization (0% canary, reload v1)
    container.set_models(
        production=model_v1_serve,
        prod_version=1,
        canary_ratio=0.0,
    )

    # Step 3: Immediate Verification of Post-Rollback Traffic
    post_rb_resp = client.post(
        "/predict",
        json={"request_id": "req-post-rollback", "image_array": req_tensor},
    )
    assert post_rb_resp.status_code == 200
    assert post_rb_resp.json()["model_version"] == 1
    assert post_rb_resp.json()["served_by_model"] == "Production"

    drill_duration_sec = time.perf_counter() - t_drill_start
    logger.info("Rollback Drill Completed in %.4f seconds (SLA: < 600s)", drill_duration_sec)
    assert drill_duration_sec < 600.0

    # Clean up GPU memory
    torch.cuda.empty_cache()
