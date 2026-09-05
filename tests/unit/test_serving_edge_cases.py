"""Unit tests for FastAPI Serving edge cases and error contracts."""

from __future__ import annotations

import logging
import uuid

import numpy as np
import pytest
import torch
import torch.nn as nn
from fastapi.testclient import TestClient

import orbital_drift.serve.app as serve_app_module
from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.serve.app import (
    InferenceRequest,
    ModelContainer,
    _resolve_serve_device,
    app,
    container,
)
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


@pytest.fixture(autouse=True)
def _reset_the_module_level_container() -> None:
    """Isolate every test in this file from the SHARED module-level `container`.

    MEASURED FLAKE THIS CURES (RB-012 review round 2; the CI failure on this
    file was `coverage`, not `unit`, because that stage runs every suite in one
    process): `test_predict_400_masks_internal_exception_detail` failed
    **2 of 30 runs on `main` at 4b6ac35** and 4 of 30 on the RB-012 branch —
    the same rate, so it is pre-existing and not that change's.

    THE MECHANISM, which is two production defects meeting:
    1. `ModelContainer.set_models` only assigns `staging_model` when a staging
       model is PASSED (`serve/app.py:157`), so a staging model left by an
       earlier test survives into the next one; but it unconditionally sets
       `canary_ratio` to its `0.10` fallback.
    2. Canary routing draws from the unseeded process-global
       `random.random()` (`serve/app.py:263`).

    So a test that calls `set_models(production=...)` with no staging model
    still gets a 10% chance of its request being routed to a STALE staging
    model from a previous test. In the exception-masking test that means the
    planted `_RaisingModel` never runs and a real `SimpleUNet` raises a torch
    shape error instead, so the secret marker never reaches the log and the
    assertion fails.

    This fixture removes the cross-test leak. It does NOT fix either production
    defect — those are real, are recorded in `docs/decisions/013-*.md`, and are
    owned by T053, which rewires `set_models` anyway. Resetting shared state
    between tests is not quarantining a test: the assertions are untouched and
    every one of them still runs.
    """
    container.production_model = None
    container.staging_model = None
    container.canary_ratio = 0.0
    container.metrics = {
        "requests_total": 0,
        "requests_production": 0,
        "requests_staging": 0,
        "total_latency_ms": 0.0,
    }


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
# RB-010 Part 13: serve/app.py baseline hardening. Four confirmed defects from
# this session's SDLC review: (1) no size/shape bound on InferenceRequest,
# (2) raw exception text leaked into the HTTP `detail` field, (3) /healthz
# reported "ok" even with no model loaded, (4) request_id's fixed
# 'req-001' default looked like leftover fixture data. See docs/decision-log.md
# RB-010's Part 13 line and serve/app.py's inline comments for full rationale.
# ═══════════════════════════════════════════════════════════════════════════════


class _RaisingModel(nn.Module):
    """Test double whose forward() raises with a distinctive message that must
    never reach an HTTP response body (item 2: exception detail leakage)."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: ARG002 -- test double, signature only
        raise RuntimeError("SECRET_INTERNAL_TRACEBACK_DETAIL_zzz789")


def test_max_image_elements_ceiling_matches_documented_derivation() -> None:
    """Pins `_MAX_IMAGE_ELEMENTS`'s documented derivation (config.py's
    `patch_size` default of 256px * Sentinel-2's 13-band max * 4x headroom)
    so the ceiling can't silently drift from the comment that justifies it.
    """
    assert serve_app_module._MAX_IMAGE_ELEMENTS == 256 * 256 * 13 * 4
    assert serve_app_module._MAX_IMAGE_ELEMENTS == 3_407_872


def test_predict_422_on_oversized_image_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 1: an oversized image_array must be rejected by pydantic
    validation (422) before predict() ever builds a numpy array / runs a
    torch forward pass on it -- not accepted and processed.

    The real ceiling (`_MAX_IMAGE_ELEMENTS`, several million elements) is
    monkeypatched down here so the test stays fast while exercising the
    exact same `InferenceRequest` validator that guards the real default;
    `test_max_image_elements_ceiling_matches_documented_derivation` above
    separately pins that real default value.
    """
    monkeypatch.setattr(serve_app_module, "_MAX_IMAGE_ELEMENTS", 4)
    model = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
    container.set_models(production=model, prod_version=1)
    client = TestClient(app)

    # 1 channel * 2 rows * 3 cols = 6 elements > the monkeypatched ceiling of 4.
    payload = {
        "request_id": "req-oversized",
        "image_array": [[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]],
    }
    response = client.post("/predict", json=payload)
    assert response.status_code == 422


def test_predict_400_masks_internal_exception_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Item 2: the raw exception text must not leak into the HTTP response
    body; it must be logged server-side (with the request_id for
    correlation) instead."""
    model = _RaisingModel()
    container.set_models(production=model, prod_version=1)
    client = TestClient(app)

    payload = {
        "request_id": "req-exc-leak-check",
        "image_array": np.random.rand(1, 4, 4).tolist(),
    }
    with caplog.at_level(logging.ERROR, logger="orbital_drift.serve.app"):
        response = client.post("/predict", json=payload)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "SECRET_INTERNAL_TRACEBACK_DETAIL_zzz789" not in response.text
    assert "req-exc-leak-check" in detail  # correlation id still surfaced to the client

    # The full detail IS logged server-side, just not returned to the client.
    assert "SECRET_INTERNAL_TRACEBACK_DETAIL_zzz789" in caplog.text


def test_healthz_reports_not_ready_without_production_model() -> None:
    """Item 3: /healthz must not report "ok" before a production model is
    loaded -- `container.production_model` is `None` from process start
    until `set_models()` is called."""
    container.production_model = None
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] != "ok"
    assert body["reason"] == "no production model loaded"


def test_healthz_reports_ok_once_production_model_loaded() -> None:
    """Item 3, positive case: once a production model is loaded, /healthz
    reports ok again."""
    model = SimpleUNet(in_channels=1, num_classes=2, init_features=8)
    container.set_models(production=model, prod_version=1)
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_default_request_id_is_server_generated_and_not_the_old_fixture_value() -> None:
    """Item 4: request_id no longer defaults to the fixed 'req-001'
    fixture-looking value; the server generates a fresh, unique identifier
    per request when the caller omits one, since it's echoed back in
    InferenceResponse and used for client/server error-log correlation
    (predict()'s exception handler) -- a shared constant default would
    collide across concurrent unlabeled requests and defeat that purpose."""
    req_a = InferenceRequest(image_array=[[[0.1]]])
    req_b = InferenceRequest(image_array=[[[0.1]]])

    assert req_a.request_id != "req-001"
    assert req_a.request_id != req_b.request_id
    # Server-generated ids are valid uuid4 hex strings.
    assert uuid.UUID(hex=req_a.request_id).version == 4


def test_explicit_request_id_is_still_honored() -> None:
    """Item 4 must not regress the existing contract: a caller-supplied
    request_id is still used verbatim, not overridden."""
    req = InferenceRequest(image_array=[[[0.1]]], request_id="caller-supplied-id")
    assert req.request_id == "caller-supplied-id"


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
