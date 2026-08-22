"""Tests for the per-file coverage floor (charter C-6, DEC-004).

The floor exists to catch a module nobody tests hiding behind a healthy
aggregate, so the cases that matter are the ones where the GLOBAL number is
fine and a single file is not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orbital_drift import covcheck


def _report(tmp_path: Path, files: dict[str, tuple[int, float]]) -> Path:
    """Write a minimal coverage JSON report: {filename: (statements, percent)}."""
    payload: dict[str, Any] = {
        "files": {
            name: {"summary": {"num_statements": statements, "percent_covered": percent}}
            for name, (statements, percent) in files.items()
        }
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_clean_report_passes(tmp_path: Path) -> None:
    path = _report(tmp_path, {"src/orbital_drift/a.py": (100, 99.0)})
    assert covcheck.check(path) == []


def test_module_below_the_floor_is_named(tmp_path: Path) -> None:
    path = _report(
        tmp_path,
        {"src/orbital_drift/a.py": (200, 99.0), "src/orbital_drift/b.py": (15, 0.0)},
    )
    failures = covcheck.check(path)
    assert len(failures) == 1
    assert "src/orbital_drift/b.py" in failures[0]
    assert "0.0%" in failures[0]


def test_the_aggregate_hiding_case_is_exactly_what_fires(tmp_path: Path) -> None:
    """The motivating scenario: 214/229 statements covered is ~93% overall, so
    a global floor of 90 passes while one module is entirely untested."""
    path = _report(
        tmp_path,
        {"src/orbital_drift/covered.py": (214, 100.0), "src/orbital_drift/new.py": (15, 0.0)},
    )
    assert covcheck.check(path) != []


def test_zero_statement_modules_are_skipped_not_failed(tmp_path: Path) -> None:
    """A docstring-only __init__ is neither covered nor uncovered."""
    path = _report(
        tmp_path,
        {"src/orbital_drift/__init__.py": (0, 0.0), "src/orbital_drift/a.py": (10, 100.0)},
    )
    assert covcheck.check(path) == []


def test_a_report_of_only_zero_statement_modules_is_vacuous(tmp_path: Path) -> None:
    path = _report(tmp_path, {"src/orbital_drift/__init__.py": (0, 0.0)})
    assert any("vacuous" in problem for problem in covcheck.check(path))


def test_missing_report_is_reported(tmp_path: Path) -> None:
    assert any("not found" in problem for problem in covcheck.check(tmp_path / "nope.json"))


def test_unparseable_report_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text("{not json", encoding="utf-8")
    assert any("could not be read" in problem for problem in covcheck.check(path))


def test_empty_files_map_is_vacuous(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"files": {}}), encoding="utf-8")
    assert any("vacuous" in problem for problem in covcheck.check(path))


def test_exempt_file_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _report(tmp_path, {"src/orbital_drift/legacy.py": (50, 10.0)})
    monkeypatch.setattr(covcheck, "EXEMPT", {"src/orbital_drift/legacy.py": "documented reason"})
    assert covcheck.check(path) == []


def test_main_returns_nonzero_and_explains(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _report(tmp_path, {"src/orbital_drift/b.py": (15, 0.0)})
    assert covcheck.main(["--json-report", str(path)]) == 1
    captured = capsys.readouterr()
    assert "unwatched" in captured.err


def test_main_returns_zero_when_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _report(tmp_path, {"src/orbital_drift/a.py": (10, 100.0)})
    assert covcheck.main(["--json-report", str(path)]) == 0
    assert "at or above" in capsys.readouterr().out


def test_floor_is_configurable_for_the_check_not_the_gate(tmp_path: Path) -> None:
    """--floor exists so the check is testable at other bars; the GATE's bar
    stays the module constant (a gate bar that can be configured per-run is
    not a gate bar)."""
    path = _report(tmp_path, {"src/orbital_drift/a.py": (10, 50.0)})
    assert covcheck.check(path, floor=40.0) == []
    assert covcheck.check(path, floor=60.0) != []


# =============================================================================
# RB-008 part 3 — what the per-file floor MEASURES once ci/checks.sh passes
# --cov-branch. The bar's VALUE does not move; the quantity it compares does.
# =============================================================================


def _branch_report(
    tmp_path: Path,
    *,
    statements: int,
    covered_statements: int,
    branches: int,
    covered_branches: int,
) -> tuple[Path, float]:
    """A one-file report in the EXACT shape coverage.py 7.15.4 emits with
    ``--cov-branch``, plus the combined rate it puts in ``percent_covered``.

    Every key below was copied from a real report — measured 2026-08-22 at
    9de5a0e by running the gate's own invocation and reading
    ``coverage.json``. The point of reproducing the whole summary rather than
    the two keys ``check()`` happens to read is that the statement-only and
    branch-only rates ARE present in the real file: choosing ``percent_covered``
    over them is a decision, and a fixture that omitted them would hide it.

    WHAT THIS FIXTURE CANNOT PIN, SO NOBODY READS MORE INTO IT. ``combined`` is
    COMPUTED here with the same formula the tests below describe and then
    planted in this fixture's own ``percent_covered``, so every assertion
    against it proves that ``check()`` reads that key — not that coverage.py
    puts the combined rate there. The engine-side claim is pinned against the
    real engine by
    ``tests/unit/test_coverage_positive_control.py::test_percent_covered_is_the_combined_rate_computed_from_the_reports_own_counts``,
    which recomputes the expected value from the counts a real report carries.
    """
    combined = 100.0 * (covered_statements + covered_branches) / (statements + branches)
    payload: dict[str, Any] = {
        "meta": {"version": "7.15.4", "format": 3, "branch_coverage": True},
        "files": {
            "src/orbital_drift/a.py": {
                "summary": {
                    "covered_lines": covered_statements,
                    "num_statements": statements,
                    "percent_covered": combined,
                    "missing_lines": statements - covered_statements,
                    "excluded_lines": 0,
                    "percent_statements_covered": 100.0 * covered_statements / statements,
                    "num_branches": branches,
                    "num_partial_branches": branches - covered_branches,
                    "covered_branches": covered_branches,
                    "missing_branches": branches - covered_branches,
                    "percent_branches_covered": (
                        100.0 * covered_branches / branches if branches else 100.0
                    ),
                }
            }
        },
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, combined


def test_the_per_file_floor_reads_the_combined_rate_not_the_statement_rate(
    tmp_path: Path,
) -> None:
    """A file with every statement executed and half its arcs never taken FAILS.

    This is the whole reason ``--cov-branch`` was worth turning on. Statement
    coverage cannot see an untaken arc: ``if x: return BLOCK`` reports both
    lines executed as soon as any test runs the ``if`` with a falsey ``x``, and
    the BLOCK is never proved. Here the file is at 100% statements — it would
    clear the 90 floor outright, and did, before RB-008 — while 40 of its 100
    arcs were never taken, putting the combined rate at 80%.

    Falsifiable in one edit: point ``check()`` at ``percent_statements_covered``
    (which this fixture carries, exactly as a real report does) and this goes
    green while the untested arcs stay untested. Verified by making that edit:
    the assertion below reddens.
    """
    path, combined = _branch_report(
        tmp_path,
        statements=100,
        covered_statements=100,
        branches=100,
        covered_branches=60,
    )
    assert combined == 80.0, f"fixture arithmetic drifted: {combined}"

    failures = covcheck.check(path, floor=90.0)
    assert len(failures) == 1, (
        "a file whose statements are fully covered but whose arcs are not was not "
        f"caught by the per-file floor: {failures!r}"
    )
    assert "80.0%" in failures[0], (
        "the per-file floor did not compare the COMBINED statement+branch rate. "
        f"Reading percent_statements_covered instead would report 100%: {failures[0]!r}"
    )


def test_the_bar_the_combined_rate_replaces_is_strictly_easier(tmp_path: Path) -> None:
    """The control, and the reason no floor VALUE had to change (RB-008 limit).

    Same file, same 90 floor, branches NOT measured: coverage.py's
    ``percent_covered`` is then the statement rate, the file reports 100%, and
    the floor passes. One flag, no threshold edit, and the gate got strictly
    harder for exactly the files whose arcs are under-tested — measured across
    this tree at 9de5a0e: guard.py 97.81 -> 96.55, remotes.py 97.22 -> 95.45,
    projections.py 98.85 -> 98.29, covcheck.py 98.15 -> 97.30.
    """
    statement_only = _report(tmp_path, {"src/orbital_drift/a.py": (100, 100.0)})
    assert covcheck.check(statement_only, floor=90.0) == []
