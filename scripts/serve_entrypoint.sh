#!/bin/sh
# T053 remainder / D-015/D-03: Docker exec-form JSON does not expand ${VAR}.
# This wrapper is the only place ORBITAL_DRIFT_SERVING_PORT is injected into
# uvicorn. Default 8000 matches Dockerfile EXPOSE and docker-compose.
set -eu
port="${ORBITAL_DRIFT_SERVING_PORT:-8000}"
host="${ORBITAL_DRIFT_SERVE_HOST:-0.0.0.0}"
exec uvicorn orbital_drift.serve.app:app --host "$host" --port "$port"
