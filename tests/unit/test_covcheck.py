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
