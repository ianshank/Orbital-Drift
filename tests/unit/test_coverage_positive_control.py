"""Positive controls for the coverage gate's ENGINE, not for its command line.

``tests/unit/test_checks_sh_behaviour.py`` drives ``ci/checks.sh`` with a stubbed
``python``, so everything it can prove about the ``coverage`` stage is of the form
"the script passed ``--cov-fail-under=85``". That is worth asserting and it is not
the interesting claim. The interesting claim is the one FR-011a actually makes:

    a shortfall in measured statement coverage FAILS the build.

A stub cannot demonstrate that, in exactly the way ``ci/checks.sh``'s own gitleaks
stage could not be demonstrated by asserting that a ``docker run`` string was
constructed — which is why ``test_gitleaks_positive_control.py`` exists and runs
the real pinned container against planted secrets. This file is the same idea for
the same reason: plant a known-uncovered module, run the REAL pytest-cov, and
require the gate to redden.

The load-bearing case is ``test_the_threshold_actually_fails_a_run_whose_tests_all_pass``.
The coverage gate's whole value is that it fails a build for a reason unrelated to
whether the tests passed; if that case ever goes green, the gate is decorative and
every other assertion in the suite would still be satisfied.

Isolation: each control builds a throwaway package and its own ``pytest.ini`` under
``tmp_path`` and runs pytest there with ``-c``, so the repository's ``addopts``,
``testpaths``, ``pythonpath`` and plugin set have no influence on the measurement.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from shell_harness import PINS

# A module with four statements, of which a test can be made to reach some or none.
_MODULE: Final = '''\
"""Throwaway product module."""


def reached() -> int:
    return 1


def not_reached(flag: bool) -> str:
    if flag:
        return "a"
    return "b"
'''

_TEST_TOUCHING_NOTHING: Final = '''\
"""A passing test that imports nothing from the package under measurement."""


def test_passes() -> None:
    assert True
'''

_TEST_TOUCHING_EVERYTHING: Final = '''\
"""A passing test that executes every statement in the package."""

from probe_pkg import mod


def test_covers() -> None:
    assert mod.reached() == 1
    assert mod.not_reached(True) == "a"
    assert mod.not_reached(False) == "b"
'''


def _workspace(tmp_path: Path, test_body: str) -> Path:
    """A self-contained project: one package, one test, one pytest config."""
    package = tmp_path / "probe_pkg"
    package.mkdir()
    (package / "__init__.py").write_text('"""Probe package."""\n', encoding="utf-8")
    (package / "mod.py").write_text(_MODULE, encoding="utf-8")

    tests = tmp_path / "t"
    tests.mkdir()
    (tests / "test_probe.py").write_text(test_body, encoding="utf-8")

    # `-c` points at this, so the repo's own [tool.pytest.ini_options] — addopts,
    # testpaths, minversion, strict-config — cannot reach the subprocess.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return tmp_path


def _run(root: Path, threshold: str) -> subprocess.CompletedProcess[str]:
    """Run the real pytest-cov exactly as ``stage_coverage`` invokes it."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "t",
            "-c",
            "pytest.ini",
            "-p",
            "no:cacheprovider",
            "--cov=probe_pkg",
            "--cov-report=term-missing",
            f"--cov-fail-under={threshold}",
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_threshold_actually_fails_a_run_whose_tests_all_pass(tmp_path: Path) -> None:
    """The whole point of the gate: green tests, red build, on coverage alone.

    If this ever passes, ``--cov-fail-under`` has stopped being enforced and the
    ``coverage`` stage is reporting success for a codebase nobody tested. Every
    other coverage assertion in the suite would still be green in that state.
    """
    result = _run(_workspace(tmp_path, _TEST_TOUCHING_NOTHING), "85")

    assert result.returncode != 0, (
        "a run measuring 0% coverage exited 0 — the threshold is not enforced\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "1 passed" in result.stdout, (
        "the test itself was supposed to PASS, so this control proves the failure "
        f"came from coverage and not from a broken test:\n{result.stdout}"
    )
    assert "Required test coverage of 85% not reached" in result.stdout, (
        "the failure does not name coverage as the cause, so an operator cannot "
        f"tell a threshold breach from a test failure:\n{result.stdout}"
    )


def test_full_coverage_passes_the_same_threshold(tmp_path: Path) -> None:
    """The negative control: the gate is not simply always red."""
    result = _run(_workspace(tmp_path, _TEST_TOUCHING_EVERYTHING), "85")

    assert result.returncode == 0, (
        f"a fully-covered package failed the gate:\n{result.stdout}\n{result.stderr}"
    )
    assert "Required test coverage of 85% reached" in result.stdout, result.stdout


def test_zero_measurable_statements_reports_one_hundred_percent(tmp_path: Path) -> None:
    """The measured behaviour the whole self-arming design rests on.

    ``ci/checks.sh``'s ``stage_coverage`` deliberately has NO "there is no product
    code yet" branch, because coverage reports 100% for a package with no
    executable lines — so the threshold clears today and arms itself when the
    first statement lands. That is an empirical claim about coverage.py, made in
    docs/decisions/001-coverage-gate.md D-02, and it is the reason a filename-based
    special case was rejected. Asserted here so a future coverage.py release
    changing it fails loudly rather than turning the gate red for a repo that has
    simply not written any product code yet.
    """
    root = tmp_path
    package = root / "probe_pkg"
    package.mkdir()
    # Docstring only — exactly the shape of src/orbital_drift today.
    (package / "__init__.py").write_text('"""Nothing executable here."""\n', encoding="utf-8")

    tests = root / "t"
    tests.mkdir()
    (tests / "test_probe.py").write_text(_TEST_TOUCHING_NOTHING, encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    result = _run(root, "85")
    assert result.returncode == 0, (
        "a package with zero measurable statements failed the threshold. The "
        "coverage stage relies on this passing, and would now need the explicit "
        f"DECLARED-EMPTY branch that D-02 argued was unnecessary:\n{result.stdout}"
    )
    assert "Required test coverage of 85% reached" in result.stdout, result.stdout


@pytest.mark.parametrize("threshold", ["85", PINS["COVERAGE_MIN_PERCENT"]])
def test_the_repository_threshold_is_a_value_the_engine_accepts(
    tmp_path: Path, threshold: str
) -> None:
    """The pinned number must be one ``--cov-fail-under`` actually honours.

    Parametrized over the literal this file uses and over the real pin, so if
    ``COVERAGE_MIN_PERCENT`` is ever set to something the engine rejects — a
    percentage sign, a blank, a value over 100 — it is caught here rather than at
    the first CI run after the bump.
    """
    result = _run(_workspace(tmp_path, _TEST_TOUCHING_EVERYTHING), threshold)
    assert result.returncode == 0, (
        f"threshold {threshold!r} did not behave as a percentage the engine accepts:\n"
        f"{result.stdout}\n{result.stderr}"
    )
