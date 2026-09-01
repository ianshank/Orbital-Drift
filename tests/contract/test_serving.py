"""Contract tests for FastAPI Canary Serving and Observability Endpoint.

Adheres to Constitution Principle V.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from orbital_drift.serve.app import app, container
from orbital_drift.train.baseline import SimpleUNet


@pytest.mark.contract
def test_serving_healthz_and_metrics_endpoints() -> None:
    """Probes /healthz and /metrics HTTP endpoints.

    RB-010 Part 13: /healthz is no longer unconditionally "ok" -- it is
    honest about whether a production model is actually loaded. Both states
    are exercised here explicitly (rather than relying on suite ordering
    against the shared module-level `container` singleton), mirroring the
    reset-then-assert pattern already used in
    tests/unit/test_serving_edge_cases.py's `test_predict_503_when_no_production_model_loaded`.
    """
    client = TestClient(app)

    # No production model loaded -> degraded, not-ready.
    container.production_model = None
    res_health_degraded = client.get("/healthz")
    assert res_health_degraded.status_code == 503
    assert res_health_degraded.json()["status"] == "degraded"

    # Production model loaded -> ok.
    prod_model = SimpleUNet(in_channels=4, num_classes=3, init_features=8)
    container.set_models(production=prod_model, prod_version=1)
    res_health_ok = client.get("/healthz")
    assert res_health_ok.status_code == 200
    assert res_health_ok.json()["status"] == "ok"

    res_metrics = client.get("/metrics")
    assert res_metrics.status_code == 200
    metrics = res_metrics.json()
    assert "orbital_drift_requests_total" in metrics
    assert "orbital_drift_canary_ratio" in metrics


@pytest.mark.contract
def test_serving_inference_with_canary_routing() -> None:
    """Probes /predict endpoint with production model and staging canary."""
    client = TestClient(app)

    prod_model = SimpleUNet(in_channels=4, num_classes=3, init_features=8)
    staging_model = SimpleUNet(in_channels=4, num_classes=3, init_features=8)

    # Set 100% Production first
    container.set_models(
        production=prod_model,
        prod_version=1,
        staging=staging_model,
        staging_version=2,
        canary_ratio=0.0,
    )

    dummy_image = np.random.rand(4, 32, 32).tolist()
    payload = {
        "image_array": dummy_image,
        "request_id": "test-req-001",
    }

    res = client.post("/predict", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["request_id"] == "test-req-001"
    assert data["served_by_model"] == "Production"
    assert data["model_version"] == 1
    assert len(data["predicted_classes"]) == 32
    assert len(data["predicted_classes"][0]) == 32
    assert data["inference_latency_ms"] > 0.0

    # Set 100% Canary (Staging)
    container.canary_ratio = 1.0
    res_canary = client.post("/predict", json=payload)
    assert res_canary.status_code == 200
    data_canary = res_canary.json()
    assert data_canary["served_by_model"] == "Staging"
    assert data_canary["model_version"] == 2
