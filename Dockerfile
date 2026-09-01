# =============================================================================
# Orbital-Drift — Multi-Stage Production Container (Constitution Principles I, IV, VII)
# =============================================================================

# --- Stage 1: Builder --------------------------------------------------------
# Digest-pinned (RB-010 Part 12, ci/versions.env PYTHON_BASE_IMAGE) — a bare
# `python:3.12-slim-bookworm` tag is mutable and can be re-pushed to point at
# different bytes; only the digest catches a content rewrite that keeps the
# tag string. Shared by both stages below (see ci/versions.env for why one pin
# is correct here rather than two).
FROM python:3.12-slim-bookworm@sha256:f2431a8cca8c5c6b04bc1309ab7ce99cc36bda0e1787e88a7fac21d9b450a923 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# `[tool.hatch.build.targets.wheel] packages = ["src/orbital_drift"]`
# (pyproject.toml) names a path that must exist in the build context BEFORE
# hatchling runs, or it has nothing to package (RB-010 Part 12 Blocker 1: this
# COPY was previously missing entirely, so `pip wheel .` below failed on every
# build — src/ was only ever copied into the runtime stage, too late for the
# builder to see it).
COPY src/ /build/src/
RUN pip install --upgrade pip setuptools wheel && \
    pip wheel --no-deps --wheel-dir /build/wheels .

# --- Stage 2: Runtime --------------------------------------------------------
# Same digest as the builder stage above — see that FROM line's comment.
FROM python:3.12-slim-bookworm@sha256:f2431a8cca8c5c6b04bc1309ab7ce99cc36bda0e1787e88a7fac21d9b450a923 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/appuser/.local/bin:${PATH}" \
    ORBITAL_DRIFT_SERVE_PORT=8000 \
    ORBITAL_DRIFT_SERVE_HOST="0.0.0.0"

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security compliance (Principle VII)
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

WORKDIR /app

COPY --from=builder /build/wheels /wheels
COPY pyproject.toml .
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY src/ /app/src/
# RB-010 Part 12 Blocker 2: this previously installed no extras at all.
# pyproject.toml's base [project].dependencies is deliberately minimal
# (pydantic/pydantic-settings only, per the hexagonal ports boundary) --
# fastapi/uvicorn/numpy/torch all live in optional extras. `serve/app.py` (the
# module ENTRYPOINT below actually serves) imports fastapi and numpy at module
# scope, both declared in the `serve` extra, AND imports torch/torch.nn at
# module scope too -- torch is declared ONLY in the `train` extra, not
# `serve`, so `serve` alone is insufficient: the app cannot even be imported
# without it. `uvicorn` (the ENTRYPOINT binary itself) is also in `serve`.
# Every top-level import in serve/app.py is covered by the union of these two
# extras and no other; installing anything broader would carry unused adapter
# surface into a serving image.
RUN pip install --no-cache-dir -e ".[serve,train]"

RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${ORBITAL_DRIFT_SERVE_PORT}/healthz || exit 1

ENTRYPOINT ["uvicorn", "orbital_drift.serve.app:app", "--host", "0.0.0.0", "--port", "8000"]
