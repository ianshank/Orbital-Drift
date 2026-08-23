"""Unit tests for FastAPI Serving edge cases and error contracts."""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from orbital_drift.serve.app import app, container
from orbital_drift.train.baseline import SimpleUNet


def test_predict_503_when_no_production_model_loaded() -> None:
    """Verifies that predict returns 503 if no model is loaded in container."""
    container.production_model = None
    client = TestClient(app)

    payload = {
        "request_id": "req-unloaded",
        "image_array": [[[0.1, 0.2], [0.3, 0.4]]],
    }
    response = client.post("/predict", json=payload)
    assert response.status_code == 503
    assert "No production model loaded" in response.json()["detail"]


def test_predict_422_on_invalid_dimension() -> None:
    """Verifies that 2D input instead of 3D (C, H, W) returns 422 Unprocessable Entity."""
    model = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
    container.set_models(production=model, prod_version=1)
    client = TestClient(app)

    # 2D instead of 3D
    payload = {
        "request_id": "req-2d",
        "image_array": [[0.1, 0.2], [0.3, 0.4]],
    }
    response = client.post("/predict", json=payload)
    assert response.status_code == 422


def test_canary_routing_boundary_ratios() -> None:
    """Verifies 100% production when ratio is 0.0 and 100% staging when ratio is 1.0."""
    prod = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
    staging = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
    client = TestClient(app)

    # 0.0 canary ratio -> 100% production
    container.set_models(
        production=prod,
        prod_version=1,
        staging=staging,
        staging_version=2,
        canary_ratio=0.0,
    )
    img_data = np.random.rand(1, 32, 32).tolist()
    resp_prod = client.post("/predict", json={"request_id": "r0", "image_array": img_data})
    assert resp_prod.status_code == 200
    assert resp_prod.json()["served_by_model"] == "Production"

    # 1.0 canary ratio -> 100% staging
    container.set_models(
        production=prod,
        prod_version=1,
        staging=staging,
        staging_version=2,
        canary_ratio=1.0,
    )
    resp_staging = client.post("/predict", json={"request_id": "r1", "image_array": img_data})
    assert resp_staging.status_code == 200
    assert resp_staging.json()["served_by_model"] == "Staging"
