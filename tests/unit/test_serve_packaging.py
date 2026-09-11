"""T053 remainder (RB-015 Part H): HEALTHCHECK, compose, and port injection.

File-level contracts. They do not `docker run` the image (T066 is Part F
and needs an FR). Mutation: restore `/healthz` in HEALTHCHECK → these fail.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "docker-compose.yaml"
ENTRYPOINT = REPO_ROOT / "scripts" / "serve_entrypoint.sh"


def test_dockerfile_healthcheck_probes_livez_not_healthz() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "HEALTHCHECK" in text
    assert "CMD curl" in text
    # Slice the HEALTHCHECK *instruction*, not the first mention of ENTRYPOINT
    # (a comment above HEALTHCHECK names ENTRYPOINT and would empty the slice).
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("HEALTHCHECK"))
    block_lines = [lines[start]]
    if lines[start].rstrip().endswith("\\") and start + 1 < len(lines):
        block_lines.append(lines[start + 1])
    block = "\n".join(block_lines)
    assert "/livez" in block
    assert "/healthz" not in block


def test_dockerfile_env_and_wrapper_use_serving_port() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ORBITAL_DRIFT_SERVING_PORT" in text
    assert "ORBITAL_DRIFT_SERVE_PORT" not in text
    assert "serve_entrypoint.sh" in text
    # Exec-form JSON does not expand ${VAR}; the wrapper must.
    assert '"--port", "8000"' not in text
    assert '"--port", "${ORBITAL_DRIFT_SERVING_PORT' not in text


def test_compose_healthcheck_probes_livez() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "/livez" in text
    assert "http://localhost:8000/healthz" not in text


def test_serve_entrypoint_expands_serving_port() -> None:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "ORBITAL_DRIFT_SERVING_PORT" in text
    assert "exec uvicorn" in text
    assert "${" in text  # shell expansion, not Docker exec-form JSON
