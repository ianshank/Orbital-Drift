"""Behavioral tests for scripts/session_start_check.sh (charter C-5/C-6 layer).

Before this file, the script had exactly one line of coverage — an
existence check in test_repo_structure.py — despite being the mechanism that
(a) warns the operator about pin drift and (b) reports whether the C-5
pre-push hook is installed on this clone. Untested logic in a script that
runs on every session start is exactly the "mechanism whose output goes
nowhere" class of defect the script's own header calls out having already
had once (F10: output went to stderr, which a SessionStart hook does not
surface).

HERMETIC BY CONSTRUCTION: the fake ".venv/Scripts/python.exe" is a bash
script (shebang-executed, the same trampoline mechanism this repo's real
`.git/hooks/pre-push` relies on) that re-execs the CURRENT interpreter
(`sys.executable` — the real, known-good dev venv this test suite is running
under). That makes every case below independent of what else is on the host
PATH, so the same tree produces the same result on any machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPT: Final = REPO_ROOT / "scripts" / "session_start_check.sh"
TIMEOUT: Final = 30.0


def _bash() -> str:
    found = shutil.which("bash")
    assert found, "bash is required to run the hook body (Git Bash on Windows)"
    return found


def _git() -> str:
    found = shutil.which("git")
    assert found, "git is required to build the fixture repository"
    return found


def _fake_python(venv_scripts_dir: Path) -> None:
    """A bash-shebang script standing in for the venv interpreter.

    `od_find_python` only checks `-x`; it does not care whether the target is
    a real PE executable. Bash's own exec path re-execs a non-PE file by its
    shebang line (the mechanism `.git/hooks/pre-push` already depends on), so
    this dispatches every call straight through to the REAL interpreter
    running this test — pin comparisons are then against ground truth, not a
    second, potentially-different Python.
    """
    real = Path(sys.executable).as_posix()
    venv_scripts_dir.mkdir(parents=True, exist_ok=True)
    wrapper = venv_scripts_dir / "python.exe"
    wrapper.write_text(f'#!/usr/bin/env bash\nexec "{real}" "$@"\n', encoding="utf-8", newline="\n")
    wrapper.chmod(0o755)


def _versions_env(repo: Path, pins: dict[str, str]) -> None:
    ci_dir = repo / "ci"
    ci_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{key}={value}" for key, value in pins.items()) + "\n"
    (ci_dir / "versions.env").write_text(body, encoding="utf-8")


def _run(repo: Path, *, no_interpreter_on_path: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(repo)
    if no_interpreter_on_path:
        env["PATH"] = _minimal_path_for_dirname_only()
    return subprocess.run(
        [_bash(), str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        env=env,
        cwd=str(repo),
    )


def _minimal_path_for_dirname_only() -> str:
    """A PATH containing exactly one external binary: `dirname`.

    This is what `session_start_check.sh` needs on the code path up to and
    including its "no venv yet" branch (`SCRIPT_DIR=$(dirname ...)`; `cd`,
    `pwd`, `[`, `command -v` are bash builtins) — and it is an ALLOWLIST, not
    a denylist, because a denylist cannot work here.

    A first version filtered PATH by removing any component whose directory
    NAME mentioned "python" or "windowsapps". That is hermetic on Windows
    (verified: it isolates the WindowsApps `python.exe` App Execution Alias
    stub, whose directory name never mentions Python at all) but MEASURED
    BROKEN in GitHub Actions' Ubuntu runner: the system ships `/usr/bin/
    python3` — and `dirname` ITSELF also lives in `/usr/bin`, by ordinary
    coreutils packaging. Excluding "any directory containing python" and
    "keeping dirname available" are mutually exclusive on that OS; there is
    no name-based filter that satisfies both. Resolving `dirname` once (via
    `shutil.which`, before any isolation) and copying just that one binary
    into a fresh directory sidesteps the entanglement entirely, and does so
    the same way on every OS — the same governed-by-default principle this
    repo already applies to path classification elsewhere (deny by default,
    allow only what is named).
    """
    dirname_bin = shutil.which("dirname")
    assert dirname_bin, "dirname is required to compute SCRIPT_DIR in the hook body"
    bin_dir = Path(tempfile.mkdtemp(prefix="minimal-path-"))
    target = bin_dir / Path(dirname_bin).name
    shutil.copy2(dirname_bin, target)
    target.chmod(0o755)
    return str(bin_dir)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        [_git(), "init", "-q"], cwd=str(root), check=True, timeout=TIMEOUT, capture_output=True
    )
    return root


def _real_version(distribution: str) -> str:
    return metadata.version(distribution)


# --- always-safe invariants, true in every case -----------------------------


def test_always_exits_zero_even_on_every_kind_of_drift(repo: Path) -> None:
    """A SessionStart hook must never be session-blocking. Exercises the
    worst case in one call: no venv AND no versions.env."""
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_never_writes_to_stderr_on_the_happy_path(repo: Path) -> None:
    """The bug this file exists to prevent: the first version wrote every
    message to stderr, where a SessionStart hook's output is not surfaced."""
    _fake_python(repo / ".venv" / "Scripts")
    _versions_env(repo, {"PYTEST_VERSION": _real_version("pytest")})
    result = _run(repo)
    assert result.stderr == "", f"stderr must stay empty on the happy path: {result.stderr!r}"


# --- venv discovery ----------------------------------------------------------


def test_no_venv_reports_bootstrap_command_on_stdout(repo: Path) -> None:
    """No repo .venv AND no interpreter anywhere on PATH — the true "cannot
    even start" case. Every real host used to develop or run CI for this
    project (this Windows box, GitHub's Ubuntu runners) has a system Python
    on PATH, so `od_find_python`'s fallback must be denied one via the
    minimal allowlisted PATH, or the script proceeds into the pin-check
    branch instead (that fallthrough is itself verified below)."""
    result = _run(repo, no_interpreter_on_path=True)
    assert result.returncode == 0
    assert 'pip install -e ".[dev]"' in result.stdout
    assert result.stdout, "the bootstrap message must reach stdout, not be silently dropped"


def test_no_repo_venv_falls_through_to_path_python(repo: Path) -> None:
    """The other half of the same branch: with no repo .venv but a real
    interpreter on PATH (this host's normal state), the script must NOT
    report "no venv yet" — it has an interpreter, so it proceeds to the pin
    check, which (no ci/versions.env here) fails soft."""
    result = _run(repo)
    assert result.returncode == 0
    assert "no venv yet" not in result.stdout
    assert "pin check errored" in result.stdout


# --- pin drift ---------------------------------------------------------------


def test_matching_pin_produces_no_drift_message(repo: Path) -> None:
    _fake_python(repo / ".venv" / "Scripts")
    _versions_env(repo, {"PYTEST_VERSION": _real_version("pytest")})
    result = _run(repo)
    assert "pin drift" not in result.stdout
    assert result.returncode == 0


def test_mismatched_pin_is_named_on_stdout(repo: Path) -> None:
    _fake_python(repo / ".venv" / "Scripts")
    real = _real_version("pytest")
    bogus = f"{real}.notreal"
    _versions_env(repo, {"PYTEST_VERSION": bogus})
    result = _run(repo)
    assert "pin drift" in result.stdout
    assert "pytest" in result.stdout
    assert bogus in result.stdout
    assert real not in result.stdout.split("pin drift")[0]  # the drift line, not a red herring
    assert result.stderr == ""


def test_uninstalled_pin_reports_not_installed(repo: Path) -> None:
    """A pin naming a distribution that plainly is not installed anywhere —
    proves the check does not just diff strings, it queries metadata."""
    _fake_python(repo / ".venv" / "Scripts")
    _versions_env(repo, {"DEFINITELY_NOT_A_REAL_PACKAGE_VERSION": "1.0.0"})
    result = _run(repo)
    assert "not installed" in result.stdout
    assert "definitely-not-a-real-package" in result.stdout


@pytest.mark.parametrize("distribution", ["python", "hatchling", "pip", "gitleaks", "shellcheck"])
def test_exempt_pins_are_never_checked(repo: Path, distribution: str) -> None:
    """These are pinned but not `python -m`-importable (build-time or
    container-run tools); checking them would always report false drift."""
    # Deliberately NOT named `key`: `{key: "<value>"}` reads, to gitleaks'
    # generic-api-key rule, as a keyword-adjacent assignment of a high-enough-
    # entropy quoted string — a false positive triggered by the identifier
    # choice alone, not by anything version-, tool-, or secret-shaped. Per
    # ci/gitleaks.toml's own documented preference order: prefer changing the
    # source so nothing looks secret-shaped, before reaching for an allowlist.
    pin_name = f"{distribution.upper().replace('-', '_')}_VERSION"
    unpinned_placeholder = "not.pinned.here"
    _fake_python(repo / ".venv" / "Scripts")
    _versions_env(repo, {pin_name: unpinned_placeholder})
    result = _run(repo)
    assert "pin drift" not in result.stdout
    assert distribution not in result.stdout


def test_missing_versions_env_fails_soft(repo: Path) -> None:
    """No ci/versions.env at all: the inline Python raises, and the shell
    catches it rather than propagating a traceback to the session."""
    _fake_python(repo / ".venv" / "Scripts")
    result = _run(repo)
    assert result.returncode == 0
    assert "pin check errored" in result.stdout
    assert result.stderr == ""


# --- C-5 pre-push hook presence ----------------------------------------------


def test_reports_when_the_pre_push_hook_is_not_installed(repo: Path) -> None:
    _fake_python(repo / ".venv" / "Scripts")
    _versions_env(repo, {"PYTEST_VERSION": _real_version("pytest")})
    result = _run(repo)
    assert "pre-push hook is NOT installed" in result.stdout
    assert "install_hooks.sh" in result.stdout


def test_says_nothing_once_the_hook_is_installed(repo: Path) -> None:
    _fake_python(repo / ".venv" / "Scripts")
    _versions_env(repo, {"PYTEST_VERSION": _real_version("pytest")})
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-push").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    result = _run(repo)
    assert "NOT installed" not in result.stdout


def test_not_a_git_repository_skips_the_hook_check_without_erroring(tmp_path: Path) -> None:
    """CLAUDE_PROJECT_DIR pointed somewhere that is not a git work tree (a
    stray call, a misconfigured hook) must not crash the session-start check."""
    root = tmp_path / "not-a-repo"
    root.mkdir()
    _fake_python(root / ".venv" / "Scripts")
    _versions_env(root, {"PYTEST_VERSION": _real_version("pytest")})
    result = _run(root)
    assert result.returncode == 0
    assert result.stderr == ""
