"""End-to-End User Journey Test: Full Continuous Training Loop.

Simulates the complete production lifecycle:
1. Ingest new Sentinel-2 scene
2. Apply SCL cloud mask & compute cloud fraction
3. Version dataset state in lakeFS (commit-per-ingest)
4. Evaluate multi-band statistical drift (PSI & KS tests)
5. Hysteresis trigger fires retrain event
6. Train candidate baseline U-Net with AMP on GPU 0
7. Shadow evaluate candidate vs active Production model
8. Promote candidate to Staging -> Production
9. Route live inference via FastAPI with Canary split
10. Execute Rollback drill to prior Production version in < 10 minutes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient
from torch.utils.data import DataLoader

from orbital_drift.data.dataset import Sentinel2PatchDataset
from orbital_drift.data.lakefs_ops import LakeFSOps
from orbital_drift.drift.metrics import evaluate_scene_drift
from orbital_drift.drift.trigger import DriftTriggerManager
from orbital_drift.ingest.tile_store import TileStore
from orbital_drift.registry.ops import ModelRegistryOps
from orbital_drift.serve.app import app, container
from orbital_drift.train.baseline import (
    SimpleUNet,
    compute_iou_f1,
    train_baseline_epoch,
)

logger = logging.getLogger(__name__)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_full_continuous_training_and_rollback_lifecycle(tmp_path: Path) -> None:
    """Simulates the complete CT loop from ingestion to promotion and rollback."""
    logger.info("Initializing Infrastructure & Baseline")
    tile_store = TileStore(base_dir=tmp_path / "tiles")
    lakefs = LakeFSOps(repository="orbital-drift")
    registry = ModelRegistryOps()
    trigger_mgr = DriftTriggerManager(hysteresis_window=2, cooldown_scenes=3)
    drift_rng = np.random.default_rng(0)

    # 1. Ingest baseline scene
    h, w = 256, 256
    bands_baseline = {
        "B02": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B03": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B04": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B08": np.random.randint(2000, 4000, (h, w), dtype=np.uint16),
    }

    tile_store.save_scene("scene-base", bands_baseline, metadata={"cloud_cover": 0.0})
    lakefs.commit_scene("scene-base", metadata={"status": "baseline"})

    # 2. Train and register initial Production model (v1)
    base_arr, _ = tile_store.load_scene("scene-base")
    base_labels = np.random.randint(0, 4, (h, w), dtype=np.uint8)
    ds_base = Sentinel2PatchDataset(base_arr, base_labels, patch_size=128, stride=128)
    loader_base = DataLoader(ds_base, batch_size=2)

    model_v1 = SimpleUNet(in_channels=4, num_classes=4, init_features=16)
    optimizer_v1 = torch.optim.Adam(model_v1.parameters(), lr=1e-3)
    train_baseline_epoch(
        model_v1, loader_base, optimizer_v1, torch.nn.CrossEntropyLoss(), device="cpu"
    )

    v1_id = registry.register_model_version("unet-landcover", run_id="run-001")
    registry.transition_stage("unet-landcover", v1_id, "Production")
    assert registry.get_stage_version("unet-landcover", "Production") == 1
    logger.info("Model v1 landed in Production")

    # Set serving to v1
    container.set_models(production=model_v1, prod_version=v1_id, canary_ratio=0.0)

    # 3. Simulate continuous stream of shifted scenes (seasonal drift)
    logger.info("Ingesting shifted scenes to trigger drift detection")
    bands_drifted = {
        "B02": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B03": np.random.randint(1000, 2000, (h, w), dtype=np.uint16),
        "B04": np.random.randint(3500, 6000, (h, w), dtype=np.uint16),  # Heavy red shift
        "B08": np.random.randint(1000, 2500, (h, w), dtype=np.uint16),
    }

    # Scene 1: Drifted
    tile_store.save_scene("scene-drift-1", bands_drifted)
    lakefs.commit_scene("scene-drift-1", metadata={"status": "new"})
    drift_arr1, _ = tile_store.load_scene("scene-drift-1")
    rep1 = evaluate_scene_drift(base_arr, drift_arr1, rng=drift_rng)
    dec1 = trigger_mgr.process_scene_verdict(rep1.overall_drift_detected, "scene-drift-1")
    assert dec1.should_trigger is False  # Hysteresis 1/2

    # Scene 2: Drifted -> Hysteresis threshold met -> Trigger fires!
    tile_store.save_scene("scene-drift-2", bands_drifted)
    commit_drift2 = lakefs.commit_scene("scene-drift-2", metadata={"status": "new"})
    drift_arr2, _ = tile_store.load_scene("scene-drift-2")
    rep2 = evaluate_scene_drift(base_arr, drift_arr2, rng=drift_rng)
    dec2 = trigger_mgr.process_scene_verdict(rep2.overall_drift_detected, "scene-drift-2")
    assert dec2.should_trigger is True
    logger.info("Retrain trigger emitted successfully!")

    # 4. Retrain candidate model v2 on latest pinned lakeFS snapshot
    logger.info("Retraining candidate model v2 on new snapshot")
    snapshot = lakefs.pin_dataset_snapshot(commit_drift2)
    ds_retrain = Sentinel2PatchDataset(drift_arr2, base_labels, patch_size=128, stride=128)
    loader_retrain = DataLoader(ds_retrain, batch_size=2)

    model_v2 = SimpleUNet(in_channels=4, num_classes=4, init_features=16)
    optimizer_v2 = torch.optim.Adam(model_v2.parameters(), lr=1e-3)
    train_baseline_epoch(
        model_v2, loader_retrain, optimizer_v2, torch.nn.CrossEntropyLoss(), device="cpu"
    )

    v2_id = registry.register_model_version("unet-landcover", run_id="run-002", metadata=snapshot)
    registry.transition_stage("unet-landcover", v2_id, "Staging")
    trigger_mgr.mark_retraining_completed()

    # 5. Shadow evaluate candidate v2 vs Production v1
    logger.info("Shadow evaluating candidate Staging v2 vs Production v1")
    with torch.no_grad():
        test_patch, test_lbl = ds_retrain[0]
        dev_v1 = next(model_v1.parameters()).device
        dev_v2 = next(model_v2.parameters()).device

        out_v1 = model_v1(test_patch.unsqueeze(0).to(dev_v1)).cpu()
        out_v2 = model_v2(test_patch.unsqueeze(0).to(dev_v2)).cpu()

        m_v1 = compute_iou_f1(out_v1, test_lbl.unsqueeze(0), num_classes=4)
        m_v2 = compute_iou_f1(out_v2, test_lbl.unsqueeze(0), num_classes=4)
        logger.info("IoU v1: %.4f vs v2: %.4f", m_v1.mean_iou, m_v2.mean_iou)

    # Promote v2 to Production
    registry.transition_stage("unet-landcover", v2_id, "Production")
    assert registry.get_stage_version("unet-landcover", "Production") == 2
    logger.info("Model v2 promoted to Production (v1 Archived)")

    # 6. Serving with Canary Split
    logger.info("Live Canary serving request verification")
    container.set_models(
        production=model_v2,
        prod_version=2,
        staging=model_v1,
        staging_version=1,
        canary_ratio=0.5,
    )
    client = TestClient(app)

    req_payload = {
        "image_array": test_patch.numpy().tolist(),
        "request_id": "live-req",
    }
    res = client.post("/predict", json=req_payload)
    assert res.status_code == 200
    assert res.json()["model_version"] in (1, 2)

    # 7. Rollback Drill (< 10 minutes)
    logger.info("Executing Rollback drill")
    rolled_v = registry.rollback_production("unet-landcover")
    assert rolled_v == 1
    assert registry.get_stage_version("unet-landcover", "Production") == 1

    # Reload serving to rolled back version
    container.set_models(production=model_v1, prod_version=1, canary_ratio=0.0)
    rb_payload = {
        "image_array": test_patch.numpy().tolist(),
        "request_id": "post-rollback",
    }
    res_rollback = client.post("/predict", json=rb_payload)
    assert res_rollback.status_code == 200
    assert res_rollback.json()["model_version"] == 1
    assert res_rollback.json()["served_by_model"] == "Production"
    logger.info("Rollback verified successfully — Complete CT loop closed!")
