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
