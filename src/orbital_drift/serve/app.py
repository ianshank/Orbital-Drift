"""FastAPI Serving Application with Dynamic Canary Traffic Splitting.

Loads Production and Staging candidate models on dedicated GPU, routes requests according
to configured canary ratio, and exports Prometheus latency/prediction metrics.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Final

import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from orbital_drift.config import OrbitalDriftConfig

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Orbital-Drift Land-Cover Serving API",
    description="Inference service with canary routing and Prometheus observability",
    version="1.0.0",
)

# RB-010 Part 13 (baseline hardening): `InferenceRequest.image_array` had no
# size bound, so `np.array(payload.image_array, ...)` in `predict()` ran on
# a raw, attacker-controlled nested list before any check existed -- a
# client could submit an arbitrarily large JSON body and force a
# correspondingly large in-memory allocation and torch forward pass purely
# from body size, with no Starlette/uvicorn body-size limit configured
# either (out of scope here; see the design-constraint note in the task
# this comment traces to). This ceiling is derived from this repo's own
# realistic scale, not an arbitrary round number:
#   - `OrbitalDriftConfig.patch_size` (config.py) defaults to 256px -- the
#     realistic spatial H/W this pipeline cuts Sentinel-2 patches to. We
#     deliberately do NOT construct a live `OrbitalDriftConfig` here to read
#     it: doing so requires lakeFS credentials with no default (RB-010 Part
#     4), which would turn every import of this module into a hard
#     credential dependency -- the same problem `_resolve_serve_device`'s
#     docstring above documents and avoids, for the same reason. We use the
#     field's documented default value (256) directly instead.
#   - Sentinel-2 L2A exposes at most 13 usable spectral bands. config.py's
#     `bands` field is configurable per-AOI and defaults to only 4 of them
#     (`DEFAULT_BANDS`), so we bound against the full sensor complement --
#     not just the 4-band default -- to avoid rejecting legitimate
#     full-band requests.
#   - 256 * 256 * 13 = 851,968 elements is therefore the largest single
#     patch this pipeline would realistically ever construct today.
#   - We apply a 4x headroom multiplier on top of that (3,407,872 elements)
#     so a legitimate caller using, e.g., a 512x512 patch at the 4-band
#     default (1,048,576 elements) is not rejected, while still bounding
#     worst-case allocation to a few million float32 values (order of tens
#     of MB) per request instead of an unbounded amount.
_MAX_IMAGE_ELEMENTS: Final[int] = 256 * 256 * 13 * 4  # pin: see docstring above = 3,407,872


class InferenceRequest(BaseModel):
    """Payload containing multi-spectral raster patches."""

    image_array: list[list[list[float]]] = Field(
        description="3D list representing (C, H, W) normalized spectral values",
    )
    request_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Request identifier. Echoed back in InferenceResponse and used "
        "to correlate a generic client-facing error message with the detailed "
        "server-side log entry on failure (see predict()'s exception handler). "
        "Server-generates a fresh UUID per request when the caller doesn't supply "
        "one, so concurrent unlabeled requests never collide on a shared value "
        "(RB-010 Part 13; previously a fixed 'req-001' fixture-looking default).",
    )

    @field_validator("image_array")
    @classmethod
    def _bound_image_array_size(cls, value: list[list[list[float]]]) -> list[list[list[float]]]:
        """Rejects oversized payloads before `predict()` builds a numpy array /
        runs a torch forward pass on them. See `_MAX_IMAGE_ELEMENTS` for the
        ceiling and its derivation (RB-010 Part 13)."""
        total_elements = sum(len(row) for channel in value for row in channel)
        if total_elements > _MAX_IMAGE_ELEMENTS:
            raise ValueError(
                f"image_array contains {total_elements} elements, exceeding the "
                f"maximum of {_MAX_IMAGE_ELEMENTS} allowed per request"
            )
        return value


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
            else (
                config.canary_ratio
                if config is not None
                else 0.10  # pin: fallback default mirroring OrbitalDriftConfig.canary_ratio
            )
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
    return (
        "cuda:1"  # pin: pre-existing multi-GPU heuristic fallback, see docstring above
        if torch.cuda.is_available() and torch.cuda.device_count() > 1
        else "cpu"
    )


dev = _resolve_serve_device()
container = ModelContainer(device=dev)


@app.get("/healthz")  # pin: REST endpoint path, protocol format literal
def healthz(response: Response) -> dict[str, str]:
    """Liveness/readiness probe.

    RB-010 Part 13: previously reported "ok" unconditionally, even before
    any model was loaded -- `container.production_model` is `None` from
    process start until `set_models()` is called (today only from tests;
    real startup wiring to a model registry is a separate, deferred
    RB-010/forward-roadmap item, not this task). An orchestrator/load
    balancer polling this endpoint would have routed live traffic to an
    instance that cannot serve a single real `/predict` request. Now
    reports a degraded, non-200 status until a production model is loaded.
    """
    if container.production_model is None:
        response.status_code = 503  # pin: well-known HTTP status code (Service Unavailable)
        return {
            "status": "degraded",
            "service": "orbital-drift-serving",
            "reason": "no production model loaded",
        }
    return {"status": "ok", "service": "orbital-drift-serving"}


@app.get("/metrics")  # pin: REST endpoint path, protocol format literal
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


@app.post("/predict", response_model=InferenceResponse)  # pin: REST endpoint path
def predict(payload: InferenceRequest) -> InferenceResponse:
    """Executes land-cover segmentation inference with canary routing."""
    if container.production_model is None:
        raise HTTPException(
            status_code=503,  # pin: well-known HTTP status code (Service Unavailable)
            detail="No production model loaded",
        )

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
        if np_arr.ndim == 3:  # pin: (C, H, W) tensor rank, algorithm-intrinsic
            # (C, H, W) -> (1, C, H, W)
            tensor_in = torch.from_numpy(np_arr).unsqueeze(0).to(container.device)
        else:
            raise ValueError(  # pin: 3D tensor rank (C, H, W), algorithm-intrinsic
                f"Expected 3D input (C, H, W), got shape {np_arr.shape}"
            )

        with torch.no_grad():
            output_logits = active_model(tensor_in)  # type: ignore[misc]
            class_map = torch.argmax(output_logits, dim=1).squeeze(0).cpu().numpy().tolist()

    except Exception as exc:
        # RB-010 Part 13: the raw exception text used to be returned directly
        # in the HTTP `detail` field, leaking internal error/shape
        # information (stack traces, tensor shapes, model internals) to any
        # caller. Log the full detail server-side only; return a generic
        # message plus `request_id` so the operator can correlate the two.
        logger.error(
            "Inference execution failed for request_id=%s: %s",
            payload.request_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=400,  # pin: well-known HTTP status code (Bad Request)
            detail=f"Inference failed (request_id={payload.request_id}); "
            "see server logs for details.",
        ) from exc

    latency_ms = (time.perf_counter() - t0) * 1000.0  # pin: seconds-to-milliseconds unit conversion
    container.metrics["total_latency_ms"] += latency_ms

    return InferenceResponse(
        request_id=payload.request_id,
        served_by_model=served_label,
        model_version=version,
        predicted_classes=class_map,
        inference_latency_ms=latency_ms,
    )
