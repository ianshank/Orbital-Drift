"""Meta-tests: the zero-skip guard actually fires (adopt-governance-kit D10).

A guard nobody has watched fire is a decoration. Each test here runs pytest in
a subprocess on a tmp mini-suite with a COPY of the real tests/conftest.py and
asserts the guard's observable behavior — including the block REASON (the
banner), not merely the exit code (spec scenario: "Skip escalates to failure").
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
#: Subprocess ceiling. A nested pytest that hangs would otherwise hang
#: the job to the runner's 20-minute limit with no local diagnosis.
TIMEOUT: Final = 120.0
CONFTEST: Final = REPO_ROOT / "tests" / "conftest.py"
GUARD_BANNER: Final = "ZERO-SKIP GUARD"
CAPABILITY_PREFIX: Final = "capability-guard:"

# The complete allowance: every call site permitted to use the
# capability-guard: skip prefix (design D10). Adding a new site requires
# editing this list in the same PR — the allowance cannot grow silently
# (spec scenario: "Capability-guard allowance is enumerated").
ALLOWED_CAPABILITY_GUARD_FILES: Final = frozenset(
    {
        "tests/unit/test_checks_sh_behaviour.py",
        "tests/unit/test_gitleaks_positive_control.py",
    }
)


def _run_pytest(suite_dir: Path) -> subprocess.CompletedProcess[str]:
    shutil.copy(CONFTEST, suite_dir / "conftest.py")
    # Fixed argv built from repo paths and sys.executable — the S603 concern is
    # per-file-ignored for tests/ in pyproject (same rationale as the harness
    # behaviour tests).
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(suite_dir), "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        cwd=suite_dir,
    )


def test_guard_escalates_a_green_run_with_a_skip(tmp_path: Path) -> None:
    (tmp_path / "test_mini.py").write_text(
        "import pytest\n"
        "def test_passes() -> None: ...\n"
        'def test_parked() -> None: pytest.skip("parked for later")\n',
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode != 0, "a green-with-skip run must exit nonzero"
    assert GUARD_BANNER in result.stdout, "the guard must name itself as the reason"
    assert "test_parked" in result.stdout, "the guard must name the offending test"


def test_guard_permits_capability_guard_skips(tmp_path: Path) -> None:
    (tmp_path / "test_mini.py").write_text(
        "import pytest\n"
        "def test_passes() -> None: ...\n"
        "def test_probe() -> None:\n"
        f'    pytest.skip("{CAPABILITY_PREFIX} docker daemon absent on this box")\n',
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, (
        f"a {CAPABILITY_PREFIX} skip is the one allowance and must stay green:\n{result.stdout}"
    )


def test_guard_stays_quiet_on_a_clean_suite(tmp_path: Path) -> None:
    (tmp_path / "test_mini.py").write_text(
        "def test_passes() -> None: ...\n",
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, f"clean suite must stay green:\n{result.stdout}"
    assert GUARD_BANNER not in result.stdout


def test_guard_passes_a_failing_run_through_unchanged(tmp_path: Path) -> None:
    (tmp_path / "test_mini.py").write_text(
        "import pytest\n"
        "def test_fails() -> None: assert False\n"
        'def test_parked() -> None: pytest.skip("parked")\n',
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 1, "pytest's own failure exit must pass through"
    assert GUARD_BANNER not in result.stdout, (
        "escalate-only: the guard must not add its banner to an already-failing run"
    )


def test_guard_escalates_xfail_and_xpass(tmp_path: Path) -> None:
    (tmp_path / "test_mini.py").write_text(
        "import pytest\n"
        "@pytest.mark.xfail(reason='parked as expected-fail')\n"
        "def test_xf() -> None: assert False\n",
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode != 0, "xfail has no allowance and must escalate"
    assert GUARD_BANNER in result.stdout


def test_capability_guard_call_sites_are_exactly_the_enumerated_set() -> None:
    """The allowance is a closed list, not a convention.

    Scans every test module for the literal prefix; a new call site outside
    ALLOWED_CAPABILITY_GUARD_FILES fails until it is deliberately added here.
    """
    pattern = re.compile(re.escape(CAPABILITY_PREFIX))
    found: set[str] = set()
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in {"tests/conftest.py", "tests/governance/test_zero_skip_guard.py"}:
            continue  # the guard and this meta-test define/verify the prefix
        if pattern.search(path.read_text(encoding="utf-8")):
            found.add(relative)
    assert found == set(ALLOWED_CAPABILITY_GUARD_FILES), (
        f"capability-guard: call sites {sorted(found)} != enumerated allowance "
        f"{sorted(ALLOWED_CAPABILITY_GUARD_FILES)} — grow the list deliberately or "
        "fix the stray skip"
    )


def test_prefix_is_anchored_not_a_substring(tmp_path: Path) -> None:
    """A mid-string mention must NOT launder a parked skip.

    The guard tested `prefix in reason`, so any skip could be excused by
    appending the token — and the call-site enumeration test cannot catch it,
    because it only checks WHICH files contain the literal, not where in the
    reason string it lands.
    """
    (tmp_path / "test_mini.py").write_text(
        "import pytest\n"
        "def test_passes() -> None: ...\n"
        "def test_parked() -> None:\n"
        f'    pytest.skip("parked for later, unrelated to {CAPABILITY_PREFIX} nothing")\n',
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode != 0, (
        f"a mid-string {CAPABILITY_PREFIX!r} must not excuse a parked skip:\n{result.stdout}"
    )
    assert GUARD_BANNER in result.stdout


def test_pytest_rendered_prefix_is_still_honoured(tmp_path: Path) -> None:
    """pytest renders the reason as 'Skipped: <reason>'; the anchor must allow
    that prefix or the one legitimate allowance would stop working."""
    (tmp_path / "test_mini.py").write_text(
        "import pytest\n"
        "def test_probe() -> None:\n"
        f'    pytest.skip("{CAPABILITY_PREFIX} docker absent")\n',
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout
