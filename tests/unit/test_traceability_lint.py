"""Tests for the traceability-matrix linter (spec scenarios "Green row must
collect" and "Unknown status refused"), written red-first against
orbital_drift.traceability."""

from __future__ import annotations

from pathlib import Path

import pytest

from orbital_drift import traceability

HEADER = (
    "| Req | Summary | Planned module(s) | Planned test(s) | Milestone | Status | Notes |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _lint_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> list[str]:
    matrix = tmp_path / "REQUIREMENT-TRACEABILITY.md"
    matrix.write_text(HEADER + body, encoding="utf-8")
    monkeypatch.setattr(traceability, "MATRIX", matrix)
    return traceability.lint()


def test_clean_matrix_lints_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| FR-900 | thing | `src/x.py` | `tests/unit/test_x.py` | M1 (T099) "
        "| Planned-gated | - |\n",
    )
    assert problems == []


def test_unknown_status_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| FR-900 | thing | `src/x.py` | `tests/unit/test_x.py` | M1 | Done | - |\n",
    )
    assert any("outside the enum" in problem for problem in problems)


def test_empty_cell_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| FR-900 | thing | `src/x.py` |  | M1 | Planned-gated | - |\n",
    )
    assert any("empty tests cell" in problem for problem in problems)


def test_duplicate_requirement_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = "| FR-900 | thing | `src/x.py` | `tests/unit/test_x.py` | M1 | Planned-gated | - |\n"
    problems = _lint_text(tmp_path, monkeypatch, row + row)
    assert any("duplicate requirement id" in problem for problem in problems)


def test_green_row_with_uncollectable_node_id_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| FR-900 | thing | `src/x.py` | tests/unit/test_nope.py::test_ghost | M1 | Green | - |\n",
    )
    assert any("does not collect" in problem for problem in problems)


def test_green_row_without_node_id_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| FR-900 | thing | `src/x.py` | (none) | M1 | Green | - |\n",
    )
    assert any("cites no pytest node id" in problem for problem in problems)


def test_missing_matrix_is_a_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(traceability, "MATRIX", tmp_path / "nope.md")
    monkeypatch.setattr(traceability, "REPO_ROOT", tmp_path)
    assert any("missing" in problem for problem in traceability.lint())


def test_main_json_reports_ok_for_the_committed_matrix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert traceability.main(["--json"]) == 0
    report = capsys.readouterr().out
    assert '"ok": true' in report


def test_main_plain_mode_prints_clean_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert traceability.main([]) == 0
    assert "matrix clean" in capsys.readouterr().out


def test_main_nonzero_and_named_problems_on_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix = tmp_path / "REQUIREMENT-TRACEABILITY.md"
    matrix.write_text(
        HEADER + "| FR-900 | thing | `src/x.py` | `tests/unit/test_x.py` | M1 | Bogus | - |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(traceability, "MATRIX", matrix)
    assert traceability.main([]) == 1
    assert "outside the enum" in capsys.readouterr().err


def test_the_committed_matrix_is_clean() -> None:
    """The real matrix passes its own linter (this is what the CI stage runs)."""
    assert traceability.lint() == []


@pytest.mark.parametrize(
    "node_id",
    [
        "tests/unit/test_x.py::test_y",
        "tests/unit/test_x.py::test_y[case-1]",
        "tests/unit/test_x.py::TestClass::test_y",
        "tests/gov-ernance/test_x.py::test_y",
    ],
)
def test_node_id_regex_covers_every_shape_pytest_emits(node_id: str) -> None:
    """189 of this suite's collected ids contain '['. The first regex
    truncated at the bracket and missed hyphenated paths entirely, so the first
    Green row citing a parametrized test would have failed a correct matrix."""
    assert traceability._NODE_ID.findall(node_id) == [node_id]


def test_malformed_row_is_reported_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pipe inside a code span used to drop the row AND its invalid status."""
    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| FR-900 | uses `a|b` pipe | `src/x.py` | `tests/unit/test_x.py` "
        "| M1 | TOTALLY-BOGUS | - |\n",
    )
    assert any("cells, expected 7" in problem for problem in problems), problems


def test_legend_and_separator_rows_are_still_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arity check must keep ignoring non-data tables — the malformed-row
    detection is scoped to rows whose first cell looks like a requirement id."""
    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| Status | Meaning |\n|---|---|\n| `Green` | verified |\n"
        "| FR-900 | thing | `src/x.py` | `tests/unit/test_x.py` | M1 | Planned-gated | - |\n",
    )
    assert problems == []


def test_header_only_matrix_is_vacuous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    problems = _lint_text(tmp_path, monkeypatch, "")
    assert any("vacuous" in problem for problem in problems)


def test_unreadable_matrix_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory where a file is expected: an OSError must become a named
    violation, not a traceback out of a CI stage."""
    monkeypatch.setattr(traceability, "MATRIX", tmp_path)
    assert traceability.lint() != []
