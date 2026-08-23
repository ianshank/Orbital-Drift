# =============================================================================
# Orbital-Drift — Multi-Stage Production Container (Constitution Principles I, IV, VII)
# =============================================================================

# --- Stage 1: Builder --------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

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
RUN pip install --upgrade pip setuptools wheel && \
    pip wheel --no-deps --wheel-dir /build/wheels .

# --- Stage 2: Runtime --------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

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
RUN pip install --no-cache-dir -e .

RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${ORBITAL_DRIFT_SERVE_PORT}/healthz || exit 1

ENTRYPOINT ["uvicorn", "orbital_drift.serve.app:app", "--host", "0.0.0.0", "--port", "8000"]
