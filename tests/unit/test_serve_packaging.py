"""T053 remainder (RB-015 Part H): HEALTHCHECK, compose, and port injection.

File-level contracts. They do not `docker run` the image (T066 is Part F
and needs an FR). Mutation: restore `/healthz` in HEALTHCHECK → these fail.
Mutation: wrapper `--port 8000` with the env named only in comments → these fail.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "docker-compose.yaml"
ENTRYPOINT = REPO_ROOT / "scripts" / "serve_entrypoint.sh"


def _continued_instruction(text: str, prefix: str) -> str:
    """Return a Dockerfile instruction including every `\\` continuation line."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(prefix))
    collected = [lines[start]]
    index = start
    while collected[-1].rstrip().endswith("\\") and index + 1 < len(lines):
        index += 1
        collected.append(lines[index])
    return "\n".join(collected)


def _code_without_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )


def test_dockerfile_healthcheck_probes_livez_not_healthz() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "HEALTHCHECK" in text
    assert "CMD curl" in text
    block = _continued_instruction(text, "HEALTHCHECK")
    assert "/livez" in block
    assert "/healthz" not in block
    assert "ORBITAL_DRIFT_SERVING_PORT" in block


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
    code = _code_without_comments(ENTRYPOINT.read_text(encoding="utf-8"))
    # Assignment, not a comment (AR-1): a wrapper that hardcodes --port 8000
    # while mentioning the env only in comments must fail this test.
    assert 'port="${ORBITAL_DRIFT_SERVING_PORT' in code
    assert "exec uvicorn" in code
    assert '--port "$port"' in code
