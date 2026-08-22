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
from shell_harness import PINS


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
    """The ``measured == 0`` branch: files ARE present, none has statements.

    This and ``test_empty_files_map_is_vacuous`` drive two different guards
    that produce two different messages, and both used to assert only that
    the word "vacuous" appeared somewhere. That is satisfied by EITHER branch,
    so deleting one guard entirely left both tests green while a genuinely
    vacuous report sailed through — the exact failure mode a vacuity check
    exists to prevent, reproduced in the check's own tests. Assert the
    distinguishing phrase.
    """
    path = _report(tmp_path, {"src/orbital_drift/__init__.py": (0, 0.0)})
    assert covcheck.check(path) == [
        f"{path.name} reported no measurable files — the check would be vacuous"
    ]


def test_missing_report_is_reported(tmp_path: Path) -> None:
    assert any("not found" in problem for problem in covcheck.check(tmp_path / "nope.json"))


def test_unparseable_report_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text("{not json", encoding="utf-8")
    assert any("could not be read" in problem for problem in covcheck.check(path))


def test_empty_files_map_is_vacuous(tmp_path: Path) -> None:
    """The ``not files`` branch: the report has no files map at all.

    Distinct from ``test_a_report_of_only_zero_statement_modules_is_vacuous``
    above, which reaches the LATER ``measured == 0`` guard with a populated
    files map. See that test's docstring for why "vacuous" alone was not
    enough to tell them apart.
    """
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"files": {}}), encoding="utf-8")
    assert covcheck.check(path) == [f"{path.name} reported no files — the check would be vacuous"]


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
    """--floor exists so the check is testable at other bars.

    The GATE's bar is not one of them: ``ci/checks.sh``'s ``stage_coverage``
    passes ``--floor "${COVERAGE_PER_FILE_MIN_PERCENT}"`` from ci/versions.env
    (RB-008 F4), and ``tests/unit/test_checks_sh_behaviour.py``'s
    ``test_the_per_file_floor_is_the_pinned_one_not_the_module_default``
    asserts that argv. A per-run floor is a testing affordance, never the bar.
    """
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


def test_a_breach_message_names_the_quantity_it_measured_not_only_statements(
    tmp_path: Path,
) -> None:
    """The message must not pair a combined numerator with a statements-only
    denominator — the operator reads THIS string, not D-14.

    The shape below is this PR's own probe: 5 statements all covered, 2 arcs of
    which 1 is taken, so the rate is 6/7 = 85.7%. The message the module shipped
    before this test read ``85.7% over 5 statements``, which pairs a numerator
    drawn from statements AND arcs with a denominator labelled "statements" —
    85.7% of 5 statements is 4.3 statements, which is not a quantity that
    exists, and it sends an operator hunting for uncovered LINES in a file whose
    entire shortfall is arcs. Same defect class as D-14's own "FR-011a false
    about its own gate", one layer down.

    Mutation that reddens this: restore
    ``f"{filename}: {rate:.1f}% over {statements} statements < ..."`` in
    ``covcheck.check``. Verified by making exactly that edit — the
    ``combined statement+branch`` assertion fails. The second assertion pins the
    absence of the old pairing directly, so a message that merely APPENDED the
    branch count while keeping "85.7% over 5 statements" intact is also caught.
    """
    path, combined = _branch_report(
        tmp_path,
        statements=5,
        covered_statements=5,
        branches=2,
        covered_branches=1,
    )
    assert f"{combined:.1f}" == "85.7", f"fixture arithmetic drifted: {combined}"

    failures = covcheck.check(path, floor=90.0)
    assert len(failures) == 1, f"the probe did not breach the floor: {failures!r}"
    assert "85.7% combined statement+branch over 5 statements and 2 branches" in failures[0], (
        f"the breach message does not say what the percentage was taken over: {failures[0]!r}"
    )
    assert "85.7% over 5 statements" not in failures[0], (
        f"the message still attributes the combined rate to a statement count: {failures[0]!r}"
    )


def test_a_report_without_branch_measurement_reports_zero_branches(tmp_path: Path) -> None:
    """``num_branches`` is absent from a report produced WITHOUT ``--cov-branch``.

    The gate always passes ``--cov-branch`` (``stage_coverage``), so this is the
    hand-run case. Reporting ``0 branches`` is correct rather than merely safe:
    with no arcs in the denominator the combined rate degrades to the statement
    rate (D-14, rejected alternative 3), so ``50.0% ... over 10 statements and 0
    branches`` is arithmetically the statement rate it looks like. The "0
    branches" is also the signal that distinguishes a hand-run from the gate.

    Mutation that reddens this: drop the ``.get`` default and read
    ``summary["num_branches"]`` — a hand-run report raises ``KeyError`` instead
    of reporting.
    """
    path = _report(tmp_path, {"src/orbital_drift/a.py": (10, 50.0)})
    failures = covcheck.check(path, floor=90.0)
    assert len(failures) == 1
    assert "50.0% combined statement+branch over 10 statements and 0 branches" in failures[0]


def test_a_fractional_floor_is_reported_exactly_not_rounded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A message must not state a bar the run did not enforce.

    Both messages formatted the floor as ``{floor:.0f}``, so ``--floor 99.9``
    announced "at or above 100%" — a file at 99.95% passes while the output
    claims a bar it never met. Latent while the floor was an integer constant in
    this module; reachable since RB-008 F4 made it a CLI argument read from a
    text pin file, where ``90.5`` is one keystroke away.
    """
    path = _report(tmp_path, {"src/orbital_drift/a.py": (10, 100.0)})
    assert covcheck.main(["--json-report", str(path), "--floor", "99.9"]) == 0
    out = capsys.readouterr().out
    assert "99.9%" in out, f"the floor was rounded in the report: {out!r}"
    assert "100%" not in out, f"the report states a floor that was never enforced: {out!r}"


def test_the_module_default_agrees_with_the_pinned_gate_bar() -> None:
    """``PER_FILE_FLOOR`` and ci/versions.env must describe ONE number.

    The gate passes the pin explicitly, so this constant is now the fallback for
    a direct ``python -m orbital_drift.covcheck`` — which an operator runs while
    fixing a breach, and which must therefore report the same verdict the gate
    will. Nothing bound the two before: the audit lowered ``PER_FILE_FLOOR`` to
    11.0 and the whole suite stayed green, because every test here passes an
    explicit floor (99.0 / 0.0 / 40.0 / 60.0) and the gate passed none.

    Value ratified at 90 by RB-006; RB-008 moved its home to ci/versions.env and
    bound it here, and changed nothing about the number.
    """
    pinned = PINS["COVERAGE_PER_FILE_MIN_PERCENT"]
    assert float(pinned) == covcheck.PER_FILE_FLOOR, (
        f"covcheck.PER_FILE_FLOOR is {covcheck.PER_FILE_FLOOR}, ci/versions.env "
        f"COVERAGE_PER_FILE_MIN_PERCENT is {pinned}. An operator running covcheck "
        "by hand would get a different verdict from the gate's."
    )
