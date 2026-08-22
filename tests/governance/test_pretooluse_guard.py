"""Pinned verdicts for the PreToolUse guard (charter C-1/C-5).

Every "blocks X" / "allows Y" claim in scripts/pretooluse_guard.sh's header is
probed for real here, and each block asserts the REASON string, not merely the
exit code — a guard test that matches exit codes alone can stay green via an
unrelated fail-closed error path with the actual fix removed (donor-kit
incident; adversarial-reviewer tooling-diff protocol).

REGRESSION CORPUS. The `LAUNDERING` cases below were all measured ALLOWED by
the first (sed + ERE) implementation and are the reason the tokenizer moved
into orbital_drift.guard. They must never pass again.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
GUARD: Final = REPO_ROOT / "scripts" / "pretooluse_guard.sh"
ALLOWLIST: Final = REPO_ROOT / ".claude" / "allowed-remotes.txt"
#: Subprocess ceiling: the guard shells out to git and python; anything slower
#: than this is a hang, not a slow box. Every subprocess in this repo sets one.
TIMEOUT: Final = 60.0


def _bash() -> str:
    found = shutil.which("bash")
    assert found, "bash is required to run the guard (Git Bash on Windows)"
    return found


def _run(payload: str) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
    env.pop("GUARD_DEBUG", None)
    return subprocess.run(
        [_bash(), str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        env=env,
    )


def _verdict(command: str) -> subprocess.CompletedProcess[str]:
    return _run(json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}))


# --- the regression corpus: every shape the sed+ERE guard let through -------

LAUNDERING: Final[tuple[tuple[str, str], ...]] = (
    # Co-located `git` token routed the whole segment into the push branch,
    # which resolved `origin` (allow-listed) and continued.
    ("kubectl delete ns $(git rev-parse --abbrev-ref HEAD)", "C-1"),
    ("helm upgrade rel ./chart --set sha=$(git rev-parse HEAD)", "C-1"),
    ("terraform apply -var git_sha=$(git rev-parse HEAD)", "C-1"),
    # An ALLOWED_READONLY prefix laundered a dangerous token later in the
    # segment, because `$(...)`, backticks and bare `&` were not separators.
    ("helm template . --set x=$(kubectl get nodes -o name)", "C-1"),
    ("helm template . & kubectl apply -f x", "C-1"),
    ("terraform validate `kubectl apply -f x`", "C-1"),
    ("helm lint . $(argocd app sync foo)", "C-1"),
    # A flag value containing `/` hid the push verb from the ERE entirely.
    ("git -C /somewhere push evil main", "C-5"),
    # An unparseable destination fell back to `origin` and was allowed.
    ("git -c user.name=x push evil", "C-5"),
)

BLOCKED: Final[tuple[tuple[str, str], ...]] = (
    ("kubectl apply -f x.yaml", "C-1"),
    ("kubectl get pods", "C-1"),  # settings deny-list is stricter; guard matches it
    ("git commit -m ok && kubectl delete pod x", "C-1"),
    ("git commit && argo submit wf.yaml", "C-1"),  # bare argo
    ("argocd app sync x", "C-1"),
    ("terraform plan", "C-1"),
    ("terraform apply -auto-approve", "C-1"),
    ("terraform init", "C-1"),  # without -backend=false it initializes a real backend
    ("helm install release ./chart", "C-1"),
    ("kustomize build overlays/prod", "C-1"),
    ("git push evil main", "C-5"),
    ("sudo kubectl apply -f x.yaml", "C-1"),  # wrapper stripped
    ("env FOO=bar kubectl apply -f x.yaml", "C-1"),
    ("FOO=bar kubectl apply -f x.yaml", "C-1"),  # leading assignment stripped
    ("/usr/bin/kubectl apply -f x.yaml", "C-1"),  # absolute path resolves to basename
    ("echo hi; kubectl apply -f x", "C-1"),
    ("echo hi | kubectl apply -f -", "C-1"),
    ('kubectl apply -f "unterminated', "C-1"),  # unparseable -> fail closed
    # `-c` payloads are CODE: quoting must not launder them. Without the
    # SHELLS branch every rule above is one `sh -c` away from irrelevant.
    ('bash -c "kubectl apply -f x.yaml"', "C-1"),
    ("sh -c 'terraform apply -auto-approve'", "C-1"),
    ("""bash -c "sh -c 'kubectl delete ns prod'" """.strip(), "C-1"),
    ('bash -c "git push evil main"', "C-5"),
    ("bash -c", "C-1"),  # -c with no argument is unanalyzable -> fail closed
)

ALLOWED: Final[tuple[str, ...]] = (
    "pytest -q",
    "git status",
    "git commit -m 'ordinary message'",
    # A quoted mention is DATA, not a command. The regex implementation could
    # not tell the two apart and blocked this as an accepted false positive;
    # the lexer can, so it no longer has to. `bash -c "<same string>"` is
    # code and is in BLOCKED above — that is the distinction that matters.
    'git commit -m "kubectl apply -f x.yaml"',
    'echo "run kubectl apply yourself"',
    "helm template ./chart",
    "helm template . --set image.tag=v1",
    "helm lint ./chart",
    "terraform validate",
    "terraform fmt -check",
    "terraform init -backend=false",
    "sh ci/checks.sh lint",
    "ls -la && echo done",
    "python -m orbital_drift.traceability --json",
)


@pytest.mark.parametrize(("command", "constraint"), LAUNDERING)
def test_laundering_shapes_are_blocked(command: str, constraint: str) -> None:
    """The measured bypass corpus. A regression here is a live fail-open."""
    result = _verdict(command)
    assert result.returncode == 2, (
        f"LAUNDERING REGRESSION — {command!r} was allowed (rc={result.returncode}). "
        f"stderr: {result.stderr}"
    )
    assert f"BLOCKED ({constraint}" in result.stderr, (
        f"block must name its constraint for {command!r}; stderr was: {result.stderr}"
    )


@pytest.mark.parametrize(("command", "constraint"), BLOCKED)
def test_blocks_with_reason(command: str, constraint: str) -> None:
    result = _verdict(command)
    assert result.returncode == 2, (
        f"expected BLOCK (exit 2) for {command!r}, got {result.returncode}: {result.stderr}"
    )
    assert f"BLOCKED ({constraint}" in result.stderr, (
        f"block must name its constraint for {command!r}; stderr was: {result.stderr}"
    )


@pytest.mark.parametrize("command", ALLOWED)
def test_allows(command: str) -> None:
    result = _verdict(command)
    assert result.returncode == 0, (
        f"expected ALLOW (exit 0) for {command!r}, got {result.returncode}: {result.stderr}"
    )


def test_allowlisted_push_is_allowed() -> None:
    """A push to a remote in the allowlist passes the guard.

    Reads the allowlist rather than hard-coding `origin` so the test survives a
    fork whose origin differs (audit TD-6).
    """
    entries = [
        line.strip()
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert entries, "allowlist is empty — the C-5 gate would have nothing to permit"
    result = _verdict(f"git push {entries[0]} 001-orbital-drift-ct")
    assert result.returncode == 0, result.stderr


def test_bare_push_resolves_the_effective_remote() -> None:
    """`git push` with no destination must resolve, not assume, and this repo's
    effective remote is allow-listed — so it is permitted."""
    result = _verdict("git push")
    assert result.returncode == 0, result.stderr


def test_unanalyzable_dangerous_payload_fails_closed() -> None:
    result = _run("not json at all -- kubectl delete ns prod")
    assert result.returncode == 2, "dangerous-shaped unparsable payload must BLOCK"
    assert "BLOCKED" in result.stderr


def test_unanalyzable_benign_payload_is_allowed() -> None:
    result = _run("not json at all -- echo hello")
    assert result.returncode == 0, result.stderr


def test_empty_command_is_allowed() -> None:
    result = _verdict("")
    assert result.returncode == 0, result.stderr


def test_debug_mode_traces_segments() -> None:
    """GUARD_DEBUG renders the parsed segments — the affordance whose absence
    let the laundering bugs survive a full review cycle."""
    import os

    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
    env["GUARD_DEBUG"] = "1"
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "echo a && echo $(echo b)"}}
    )
    result = subprocess.run(
        [_bash(), str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "guard: segment=" in result.stderr, result.stderr
