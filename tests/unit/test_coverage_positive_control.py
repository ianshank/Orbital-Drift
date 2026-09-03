"""Positive controls for the coverage gate's ENGINE, not for its command line.

``tests/unit/test_checks_sh_behaviour.py`` drives ``ci/checks.sh`` with a stubbed
``python``, so everything it can prove about the ``coverage`` stage is of the form
"the script passed ``--cov-fail-under=85``". That is worth asserting and it is not
the interesting claim. The interesting claim is the one FR-011a actually makes:

    a shortfall in measured statement AND BRANCH coverage FAILS the build.

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

The branch half of that claim (``--cov-branch``, RB-008 part 3, decided in
``docs/decisions/001-coverage-gate.md`` D-14) has its own controls in the last
section of this file, for the same reason: the argv assertion in
``test_the_coverage_stage_measures_branches_not_only_statements`` stays green if
the flag becomes a no-op.

Isolation: each control builds a throwaway package and its own ``pytest.ini`` under
``tmp_path`` and runs pytest there with ``-c``, so the repository's ``addopts``,
``testpaths``, ``pythonpath`` and plugin set have no influence on the measurement.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

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


# A module whose every STATEMENT is reachable without taking every ARC: calling
# `branchy(True)` executes all five lines and leaves the `if`'s false exit
# (6->8) untaken. This shape cannot be built from `_MODULE` above — skipping
# either arc of `not_reached` also skips one of its `return`s, so the miss shows
# up as a statement miss and proves nothing about branch measurement.
_BRANCHY_MODULE: Final = '''\
"""Throwaway product module with one branch and no unreachable statement."""


def branchy(flag: bool) -> int:
    total = 0
    if flag:
        total += 1
    return total
'''

# A module with statements and not one branch, half of it never executed. The
# shape `covcheck`'s rejected-alternative 3 is about.
_BRANCHLESS_MODULE: Final = '''\
"""Throwaway product module with statements and no branch at all."""


def straight_line() -> int:
    total = 1
    total += 1
    return total


def never_called() -> int:
    unused = 2
    unused += 2
    return unused
'''

_TEST_TAKING_ONE_ARC: Final = '''\
"""Executes every statement in the package and one of its two arcs."""

from probe_pkg import mod


def test_one_arc() -> None:
    assert mod.branchy(True) == 1
'''

_TEST_TAKING_THE_STRAIGHT_LINE: Final = '''\
"""Executes one of the package's two branchless functions."""

from probe_pkg import mod


def test_straight_line() -> None:
    assert mod.straight_line() == 2
'''


def _workspace(tmp_path: Path, test_body: str, module: str = _MODULE) -> Path:
    """A self-contained project: one package, one test, one pytest config."""
    package = tmp_path / "probe_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""Probe package."""\n', encoding="utf-8")
    (package / "mod.py").write_text(module, encoding="utf-8")

    tests = tmp_path / "t"
    tests.mkdir()
    (tests / "test_probe.py").write_text(test_body, encoding="utf-8")

    # `-c` points at this, so the repo's own [tool.pytest.ini_options] — addopts,
    # testpaths, minversion, strict-config — cannot reach the subprocess.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return tmp_path


def _sanitized_env() -> dict[str, str]:
    """Return a copy of the environment with PYTEST_ADDOPTS removed.

    Prevents developer-local settings (e.g., --no-cov) from affecting
    subprocess runs, keeping the positive control hermetic.
    """
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    return env


def _run(
    root: Path,
    threshold: str,
    *,
    branch: bool = False,
    json_report: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the real pytest-cov exactly as ``stage_coverage`` invokes it.

    ``branch`` and ``json_report`` mirror the two flags ``stage_coverage``
    passes beyond the always-on set (``--cov-branch`` since RB-008 part 3, and
    ``--cov-report=json`` for ``orbital_drift.covcheck``). They default off so
    the controls that predate branch coverage measure exactly what they did.
    """
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
            *(["--cov-branch"] if branch else []),
            "--cov-report=term-missing",
            *(["--cov-report=json"] if json_report else []),
            f"--cov-fail-under={threshold}",
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=_sanitized_env(),
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


def test_a_cov_path_that_does_not_exist_fails_closed(tmp_path: Path) -> None:
    """If someone renames or deletes ``src/orbital_drift``, the gate must redden.

    ``stage_coverage`` hardcodes ``--cov=src/orbital_drift``. Nothing in the
    repository forces that path to keep existing — no test ties the stage's flag
    to the package layout — so the question "what does the gate do when it is
    pointed at nothing?" decides whether a rename produces a loud failure or a
    silent, permanently green gate measuring an empty set.

    The answer is a behaviour of a third-party library the gate's soundness rests
    on, which is exactly the kind of thing this repo pins and re-measures rather
    than assumes. Asserted so a future coverage.py that starts treating "no data"
    as 100% is caught here rather than by nobody.
    """
    root = _workspace(tmp_path, _TEST_TOUCHING_NOTHING)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "t",
            "-c",
            "pytest.ini",
            "-p",
            "no:cacheprovider",
            "--cov=no_such_package",
            "--cov-report=term-missing",
            "--cov-fail-under=85",
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=_sanitized_env(),
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        "measuring a path that does not exist exited 0. A rename of "
        f"src/orbital_drift would silently disarm the FR-011a gate rather than "
        f"failing it:\n{combined}"
    )
    # Not just ANY non-zero exit: confirmed to fail for the RIGHT reason — no
    # data was collected because the path does not exist — rather than some
    # unrelated crash (a typo in the argv above, a broken pytest invocation)
    # that would also produce a non-zero exit and let this test pass for the
    # wrong reason. "No data to report" is coverage.py's own diagnosis,
    # verified against the real tool's output before asserting on it.
    assert "No data to report" in combined or "was never imported" in combined, (
        f"failed, but not for the expected 'no data collected' reason — this test "
        f"cannot tell a genuine fail-closed from an unrelated crash:\n{combined}"
    )
    assert "Total coverage: 0.00%" in combined or "Coverage failure" in combined, (
        f"expected coverage.py to report zero measured coverage for the missing "
        f"path, not some other failure shape:\n{combined}"
    )


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


# =============================================================================
# RB-008 part 3 — the ENGINE half of `--cov-branch`.
#
# tests/unit/test_checks_sh_behaviour.py asserts the flag reaches argv. Under a
# stubbed python that is all it can assert, and argv construction is not the
# claim: the claim is that the flag CHANGES THE MEASUREMENT and can redden a
# build no statement floor would have reddened. A stub-only test for a new gate
# is a BLOCK in this repo (adversarial-reviewer.md, ported from the finding
# RB-006 judgment call 3 records: "new CI gates need a stub test AND a positive
# control"). These are the positive controls, run against the real engine.
# =============================================================================


def _summary(root: Path, filename: str) -> dict[str, Any]:
    """The per-file ``summary`` block coverage.py wrote into ``coverage.json``.

    Read from the report the run under test just produced — never planted by
    this file — so an assertion about coverage.py's arithmetic is an assertion
    about coverage.py.
    """
    report: dict[str, Any] = json.loads((root / "coverage.json").read_text(encoding="utf-8"))
    files = report["files"]
    # String replace, not Path(...).as_posix(): on POSIX a backslash is a legal
    # filename character, so as_posix() would leave Windows-style keys untouched.
    normalized = {key.replace("\\", "/"): value for key, value in files.items()}
    assert filename in normalized, f"{filename} absent from the report: {sorted(normalized)}"
    summary: dict[str, Any] = normalized[filename]["summary"]
    # coverage.py OMITS the branch counts entirely from a summary measured
    # without branch tracking. Named here rather than left to surface as a
    # KeyError three assertions later, so "the run was not measuring branches"
    # reads as itself instead of as a typo in a key name.
    branch_keys = {"covered_branches", "num_branches", "percent_branches_covered"}
    missing = sorted(branch_keys - summary.keys())
    assert not missing, (
        f"{filename}'s summary carries no branch counts ({missing} absent), so the "
        "report was produced WITHOUT branch measurement and nothing below can be "
        f"asserted about branch arithmetic: {summary!r}"
    )
    return summary


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_summary_lookup_is_path_separator_agnostic(tmp_path: Path, sep: str) -> None:
    """``_summary()`` must find a file's entry whichever separator the report used.

    coverage.py keys ``report["files"]`` by the OS-native separator (backslash on
    Windows), while callers pass POSIX-style literals. The parametrized ``sep``
    makes both key shapes fail identically on every host if the lookup stops
    normalizing — the first version of this test hardcoded the backslash shape
    against a ``Path.as_posix()`` normalization, which is a no-op on POSIX and
    so reddened Linux CI while staying green on Windows (PR #19, run
    33784050330); both shapes are pinned now so the trap cannot recur.
    """
    payload = {
        "files": {
            f"probe_pkg{sep}__init__.py": {"summary": {"num_statements": 0}},
            f"probe_pkg{sep}mod.py": {
                "summary": {
                    "covered_lines": 1,
                    "num_statements": 1,
                    "percent_covered": 100.0,
                    "covered_branches": 0,
                    "num_branches": 0,
                    "percent_branches_covered": 100.0,
                }
            },
        }
    }
    (tmp_path / "coverage.json").write_text(json.dumps(payload), encoding="utf-8")

    summary = _summary(tmp_path, "probe_pkg/mod.py")

    assert summary["num_statements"] == 1


def test_cov_branch_is_what_turns_an_untaken_arc_into_a_failing_build(tmp_path: Path) -> None:
    """The flag's positive control: same code, same tests, same floor, two verdicts.

    ``probe_pkg.mod.branchy`` is executed by a test that passes ``True`` only.
    Every statement in the package runs, so statement coverage is 100% and any
    floor at or below 100 clears — which is precisely the blind spot RB-008
    part 3 exists to close: an ``if x: return BLOCK`` whose false exit is never
    taken looks fully covered. Turning on ``--cov-branch`` makes the same run
    measure 6 of 7 (5 statements + 1 of 2 arcs) = 85.71%, which a floor of 90
    rejects.

    A floor BETWEEN the two rates is what makes this falsifiable: the run
    without the flag must pass it and the run with the flag must fail it. If
    ``--cov-branch`` ever becomes a no-op — silently dropped, renamed, defaulted
    away by a future coverage.py — both runs return the same verdict and this
    test reddens, whereas the argv assertion in
    ``test_the_coverage_stage_measures_branches_not_only_statements`` would stay
    green while the gate measured nothing new.

    Measured 2026-08-22 against coverage 7.15.4 / pytest-cov, the pins in
    ci/versions.env.
    """
    statements_only = _run(
        _workspace(tmp_path / "plain", _TEST_TAKING_ONE_ARC, _BRANCHY_MODULE), "90"
    )
    with_branches = _run(
        _workspace(tmp_path / "branch", _TEST_TAKING_ONE_ARC, _BRANCHY_MODULE),
        "90",
        branch=True,
    )

    assert statements_only.returncode == 0, (
        "the control run is supposed to PASS the 90 floor on statements alone — "
        "if it fails, the two verdicts below no longer isolate the flag:\n"
        f"{statements_only.stdout}\n{statements_only.stderr}"
    )
    assert "Required test coverage of 90% reached" in statements_only.stdout, (
        f"statement-only coverage of the probe is not 100%:\n{statements_only.stdout}"
    )

    assert "1 passed" in with_branches.stdout, (
        "the probe's own test was supposed to PASS, so this control proves the "
        f"failure came from coverage and not from a broken test:\n{with_branches.stdout}"
    )
    assert with_branches.returncode != 0, (
        "--cov-branch did not change the verdict: a package with an arc no test "
        "ever took still cleared the same floor it cleared without the flag. The "
        "flag is being accepted and ignored, and both coverage floors are back to "
        f"measuring statements only:\n{with_branches.stdout}\n{with_branches.stderr}"
    )
    assert "Required test coverage of 90% not reached" in with_branches.stdout, (
        "the run failed, but not by naming the coverage floor — this control "
        f"cannot tell a threshold breach from an unrelated crash:\n{with_branches.stdout}"
    )


def test_percent_covered_is_the_combined_rate_computed_from_the_reports_own_counts(
    tmp_path: Path,
) -> None:
    """Pins the arithmetic BOTH floors now depend on, against the real engine.

    ``--cov-fail-under`` tests the terminal TOTAL and ``orbital_drift.covcheck``
    reads the per-file ``summary.percent_covered`` out of ``coverage.json``.
    With ``--cov-branch`` on, that one field stops being the statement rate and
    becomes ``(covered_lines + covered_branches) / (num_statements +
    num_branches)`` — an empirical claim about coverage.py 7.15.4, and the whole
    reason ``covcheck``'s docstring can say the per-file bar got harder without
    its VALUE moving.

    ``tests/unit/test_covcheck.py``'s fixture cannot pin this: it COMPUTES the
    combined rate with this same formula and writes the answer into its own
    ``percent_covered`` key, so it proves ``check()`` reads the key and nothing
    about what the engine puts there. Here the expected value is recomputed from
    the four counts THIS report carries, so if a coverage.py release put the
    statement rate back in that field, the equality below fails and the silent
    loosening of both floors is caught.

    The probe is chosen so the two candidate readings are far apart —
    ``percent_statements_covered`` is 100.0 while the combined rate is 85.71 —
    because an equality that held for both would pin neither.
    """
    root = _workspace(tmp_path, _TEST_TAKING_ONE_ARC, _BRANCHY_MODULE)
    result = _run(root, "0", branch=True, json_report=True)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    summary = _summary(root, "probe_pkg/mod.py")
    assert summary["num_branches"] > 0, (
        "the probe measured zero arcs, so this test would pass for a "
        f"report that says nothing about branch arithmetic: {summary!r}"
    )
    assert summary["percent_statements_covered"] == 100.0, (
        "the probe is supposed to be at 100% statements, so that the combined "
        f"rate and the statement rate cannot be confused: {summary!r}"
    )

    expected = (
        100.0
        * (summary["covered_lines"] + summary["covered_branches"])
        / (summary["num_statements"] + summary["num_branches"])
    )
    assert summary["percent_covered"] == pytest.approx(expected), (
        "summary.percent_covered is not the combined statement+branch rate this "
        "report's own counts imply. Both the global floor and the per-file floor "
        "read that field, so they are now measuring something other than what "
        f"ci/checks.sh and orbital_drift.covcheck document: {summary!r}"
    )
    assert summary["percent_covered"] != pytest.approx(summary["percent_statements_covered"]), (
        "the combined rate equals the statement rate on a probe built to make "
        f"them differ — the equality above pins nothing: {summary!r}"
    )


def test_a_file_with_no_arcs_reports_one_hundred_percent_branches_covered(
    tmp_path: Path,
) -> None:
    """The measurement behind ``covcheck``'s rejected alternative 3.

    ``orbital_drift.covcheck``'s docstring rejects a SEPARATE branch bar partly
    because such a bar is vacuous on a branchless file: coverage.py reports
    ``percent_branches_covered = 100.0`` when ``num_branches == 0``, so a file
    with no arcs passes a branch floor by having nothing to fail. That claim was
    stated as measured and the module it was measured with was never committed —
    which is the same undated, hand-asserted shape the governance skill's
    definition-of-done item 6 calls a defect. This is the module, committed.

    The probe is HALF UNCOVERED on purpose (``never_called`` is never called):
    a branch-only floor of 90 would pass this file at 100.0 while a third of its
    statements went untested, and the combined rate degrades gracefully to the
    statement rate — 62.5 in both — which is the second half of the same
    argument.

    Measured 2026-08-22 against coverage 7.15.4.
    """
    root = _workspace(tmp_path, _TEST_TAKING_THE_STRAIGHT_LINE, _BRANCHLESS_MODULE)
    result = _run(root, "0", branch=True, json_report=True)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    summary = _summary(root, "probe_pkg/mod.py")
    assert summary["num_branches"] == 0, (
        f"the branchless probe grew an arc — it no longer tests the claim: {summary!r}"
    )
    assert summary["percent_branches_covered"] == 100.0, (
        "coverage.py no longer reports 100% branches for a file with no branches. "
        "covcheck's rejected alternative 3 rests on this: a separate branch floor "
        f"would now behave differently than that decision assumed: {summary!r}"
    )
    assert summary["percent_statements_covered"] < 100.0, (
        "the probe is supposed to be partly uncovered, so that a branch-only "
        f"floor passing it at 100.0 is demonstrably vacuous: {summary!r}"
    )
    assert summary["percent_covered"] == pytest.approx(summary["percent_statements_covered"]), (
        "with no arcs to blend in, the combined rate must degrade to the statement "
        "rate — the graceful-degradation half of rejected alternative 3: "
        f"{summary!r}"
    )
