"""Unit tests for FastAPI Serving edge cases and error contracts."""

from __future__ import annotations

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.serve.app import ModelContainer, _resolve_serve_device, app, container
from orbital_drift.train.baseline import SimpleUNet

# Not real credentials -- fixed test doubles for the required lakeFS fields,
# matching tests/unit/test_config.py's `_construct_with_valid_credentials`
# pattern. Used only by the RB-010 Part 5 config-wiring classes below.
_TEST_ACCESS_KEY = "unit-test-access-value"
_TEST_SECRET_KEY = "unit-test-secret-value"  # noqa: S105 -- test double, not a real secret


def _build_config(**overrides: object) -> OrbitalDriftConfig:
    return OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        **overrides,  # type: ignore[arg-type]
    )


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


# ═══════════════════════════════════════════════════════════════════════════════
# RB-010 Part 5: per-module config wiring. This file, not test_serving.py,
# owns these because they exercise `ModelContainer` and `_resolve_serve_device`
# directly rather than through the FastAPI TestClient -- each test builds its
# own local `ModelContainer` instead of mutating the shared module-level
# `container` singleton the tests above use, so these cannot be polluted by,
# or pollute, test execution order. See docs/decision-log.md RB-010.
# ═══════════════════════════════════════════════════════════════════════════════


class TestServeDeviceConfigWiring:
    """`ModelContainer.device` and the module-level `dev` heuristic each
    resolve with precedence: explicit argument > `config.serve_device` > that
    call site's own pre-existing hardcoded default. serve/app.py carries TWO
    independent hardcoded device rules (see `_resolve_serve_device`'s
    docstring); both are preserved unchanged for callers that pass neither
    `device` nor `config`.
    """

    def test_model_container_default_device_unchanged_without_config(self) -> None:
        """Positive control: ModelContainer()'s own pre-existing default."""
        local_container = ModelContainer()
        assert local_container.device == "cpu"

    def test_model_container_explicit_device_wins_over_config(self) -> None:
        cfg = _build_config(serve_device="cuda:9")
        local_container = ModelContainer(device="cpu", config=cfg)
        assert local_container.device == "cpu"

    def test_model_container_config_supplies_device_when_omitted(self) -> None:
        cfg = _build_config(serve_device="cuda:9")
        local_container = ModelContainer(config=cfg)
        assert local_container.device == "cuda:9"

    def test_module_level_container_singleton_is_unaffected_by_this_wiring(self) -> None:
        """The real, already-imported singleton was built at import time with
        no config available (see `_resolve_serve_device`'s docstring for why
        that must stay true) -- its device must still be whatever the
        pre-existing multi-GPU heuristic produced on this host, unchanged by
        this test module merely importing `ModelContainer`/`_resolve_serve_device`.
        """
        expected = (
            "cuda:1" if torch.cuda.is_available() and torch.cuda.device_count() > 1 else "cpu"
        )
        assert container.device == expected

    def test_resolve_serve_device_config_used_when_supplied(self) -> None:
        cfg = _build_config(serve_device="cuda:9")
        assert _resolve_serve_device(cfg) == "cuda:9"

    def test_resolve_serve_device_hardcoded_fallback_unchanged_without_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pins the exact pre-existing heuristic: cuda:1 only with >1 device
        visible, else cpu -- unchanged by this fix."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
        assert _resolve_serve_device(None) == "cuda:1"

        monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
        assert _resolve_serve_device(None) == "cpu"

        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert _resolve_serve_device(None) == "cpu"


class TestCanaryRatioConfigWiring:
    """`ModelContainer.set_models`'s `canary_ratio` resolves with precedence:
    explicit argument > `config.canary_ratio` > the pre-existing hardcoded
    `0.10` default."""

    def test_default_canary_ratio_unchanged_without_config(self) -> None:
        """Positive control: the exact pre-existing zero-arg default."""
        model = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
        local_container = ModelContainer()
        local_container.set_models(production=model, prod_version=1)
        assert local_container.canary_ratio == 0.10

    def test_config_supplies_canary_ratio_when_omitted(self) -> None:
        model = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
        cfg = _build_config(canary_ratio=0.35)
        local_container = ModelContainer()
        local_container.set_models(production=model, prod_version=1, config=cfg)
        assert local_container.canary_ratio == 0.35

    def test_explicit_canary_ratio_overrides_config(self) -> None:
        model = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
        cfg = _build_config(canary_ratio=0.35)
        local_container = ModelContainer()
        local_container.set_models(production=model, prod_version=1, canary_ratio=0.75, config=cfg)
        assert local_container.canary_ratio == 0.75
