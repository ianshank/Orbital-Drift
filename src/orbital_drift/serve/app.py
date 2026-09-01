"""FastAPI Serving Application with Dynamic Canary Traffic Splitting.

Loads Production and Staging candidate models on dedicated GPU, routes requests according
to configured canary ratio, and exports Prometheus latency/prediction metrics.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from orbital_drift.config import OrbitalDriftConfig

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Orbital-Drift Land-Cover Serving API",
    description="Inference service with canary routing and Prometheus observability",
    version="1.0.0",
)


class InferenceRequest(BaseModel):
    """Payload containing multi-spectral raster patches."""

    image_array: list[list[list[float]]] = Field(
        description="3D list representing (C, H, W) normalized spectral values",
    )
    request_id: str = Field(default="req-001", description="Unique request identifier")


class InferenceResponse(BaseModel):
    """Inference output containing predicted land-cover class map."""

    request_id: str
    served_by_model: str  # 'Production' or 'Staging'
    model_version: int
    predicted_classes: list[list[int]]  # 2D class index map
    inference_latency_ms: float


class ModelContainer:
    """Holds active model instances for Production and Staging candidate."""

    def __init__(
        self,
        device: str | None = None,
        config: OrbitalDriftConfig | None = None,
    ) -> None:
        """Initializes the container.

        `device` resolves with precedence (RB-010 Part 5: per-module config
        wiring): explicit argument > `config.serve_device` > this
        constructor's own pre-existing hardcoded `"cpu"` default. This is
        independent of the module-level `dev`/`_resolve_serve_device`
        heuristic below -- the OTHER hardcoded device rule this file
        carried (see that function's docstring for why) -- so passing
        neither `device` nor `config` here reproduces this constructor's own
        original `"cpu"` default exactly, unchanged.
        """
        self.device = (
            device if device is not None else (config.serve_device if config is not None else "cpu")
        )
        self.production_model: nn.Module | None = None
        self.production_version: int = 1
        self.staging_model: nn.Module | None = None
        self.staging_version: int = 2
        self.canary_ratio: float = 0.0

        # Prometheus metrics mock/tracker
        self.metrics: dict[str, Any] = {
            "requests_total": 0,
            "requests_production": 0,
            "requests_staging": 0,
            "total_latency_ms": 0.0,
        }

    def set_models(
        self,
        production: nn.Module,
        prod_version: int = 1,
        staging: nn.Module | None = None,
        staging_version: int = 2,
        canary_ratio: float | None = None,
        config: OrbitalDriftConfig | None = None,
    ) -> None:
        """Loads Production/Staging models and sets the canary ratio.

        `canary_ratio` resolves with precedence (RB-010 Part 5: per-module
        config wiring): explicit argument > `config.canary_ratio` > the
        pre-existing hardcoded `0.10` default.
        """
        self.production_model = production.to(self.device).eval()
        self.production_version = prod_version
        if staging is not None:
            self.staging_model = staging.to(self.device).eval()
            self.staging_version = staging_version
        self.canary_ratio = (
            canary_ratio
            if canary_ratio is not None
            else (config.canary_ratio if config is not None else 0.10)
        )


def _resolve_serve_device(config: OrbitalDriftConfig | None = None) -> str:
    """Resolves the module-level default serving device.

    Precedence (RB-010 Part 5: per-module config wiring): `config.serve_device`
    when a config is supplied, else the pre-existing hardcoded multi-GPU
    heuristic, unchanged (`"cuda:1"` when a second GPU is visible, else
    `"cpu"`) -- the SECOND, independently-written device-selection rule this
    file carried alongside `train/baseline.py`'s own `_resolve_device`.

    Deliberately does NOT call `get_config()`/load a config itself: this
    function seeds the module-level `container` singleton at IMPORT time (see
    `dev = _resolve_serve_device()` below), and `OrbitalDriftConfig` requires
    lakeFS credentials with no default (RB-010 Part 4) -- an unconditional
    `get_config()` here would turn every `import orbital_drift.serve.app`
    into a hard, fail-fast dependency on those credentials being configured,
    breaking every existing test that imports `app`/`container` without
    setting them. RB-010 Part 13 (serve/app.py baseline hardening, which
    depends on this part) owns actually loading config at process startup
    and rebuilding the container from it; this function only makes that
    wiring *possible*.
    """
    if config is not None:
        return config.serve_device
    return "cuda:1" if torch.cuda.is_available() and torch.cuda.device_count() > 1 else "cpu"


dev = _resolve_serve_device()
container = ModelContainer(device=dev)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": "orbital-drift-serving"}


@app.get("/metrics")
def get_metrics() -> dict[str, Any]:
    """Prometheus-style JSON metrics endpoint."""
    avg_latency = container.metrics["total_latency_ms"] / max(
        container.metrics["requests_total"], 1
    )
    return {
        "orbital_drift_requests_total": container.metrics["requests_total"],
        "orbital_drift_requests_production": container.metrics["requests_production"],
        "orbital_drift_requests_staging": container.metrics["requests_staging"],
        "orbital_drift_average_latency_ms": avg_latency,
        "orbital_drift_canary_ratio": container.canary_ratio,
        "requests_total": container.metrics["requests_total"],
        "requests_production": container.metrics["requests_production"],
        "requests_staging": container.metrics["requests_staging"],
        "avg_latency_ms": avg_latency,
        "canary_ratio": container.canary_ratio,
    }


@app.post("/predict", response_model=InferenceResponse)
def predict(payload: InferenceRequest) -> InferenceResponse:
    """Executes land-cover segmentation inference with canary routing."""
    if container.production_model is None:
        raise HTTPException(status_code=503, detail="No production model loaded")

    t0 = time.perf_counter()

    # Canary routing logic
    route_to_staging = (
        container.staging_model is not None
        and container.canary_ratio > 0.0
        and random.random() < container.canary_ratio  # noqa: S311
    )

    if route_to_staging:
        active_model = container.staging_model
        served_label = "Staging"
        version = container.staging_version
        container.metrics["requests_staging"] += 1
    else:
        active_model = container.production_model
        served_label = "Production"
        version = container.production_version
        container.metrics["requests_production"] += 1

    container.metrics["requests_total"] += 1

    # Convert image array to tensor
    try:
        np_arr = np.array(payload.image_array, dtype=np.float32)
        if np_arr.ndim == 3:
            # (C, H, W) -> (1, C, H, W)
            tensor_in = torch.from_numpy(np_arr).unsqueeze(0).to(container.device)
        else:
            raise ValueError(f"Expected 3D input (C, H, W), got shape {np_arr.shape}")

        with torch.no_grad():
            output_logits = active_model(tensor_in)  # type: ignore[misc]
            class_map = torch.argmax(output_logits, dim=1).squeeze(0).cpu().numpy().tolist()

    except Exception as exc:
        logger.error("Inference execution failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Inference error: {exc}") from exc

    latency_ms = (time.perf_counter() - t0) * 1000.0
    container.metrics["total_latency_ms"] += latency_ms

    return InferenceResponse(
        request_id=payload.request_id,
        served_by_model=served_label,
        model_version=version,
        predicted_classes=class_map,
        inference_latency_ms=latency_ms,
    )
