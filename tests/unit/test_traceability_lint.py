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


def test_a_missing_matrix_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No matrix where one is required must be a named violation, not a traceback.

    ``MATRIX`` is pointed at a directory, so ``lint``'s first branch
    (``traceability.py:150`` ``if not MATRIX.is_file()``) returns the "is
    missing" violation. It therefore never reaches the ``OSError`` handler at
    :155-156 — this test's earlier name and docstring claimed it exercised that
    handler, and RB-008 part 3's branch measurement is what showed otherwise.
    Renamed and restated to say what it actually covers.

    The assertion stays as-is deliberately: strengthening it to name the
    violation is RB-008 part (2)'s "unfalsifiable tests removed or
    strengthened", a different PR, and this change alters no assertion.
    """
    monkeypatch.setattr(traceability, "MATRIX", tmp_path)
    assert traceability.lint() != []


# --- the collection paths, which `--cov-branch` showed were never taken ----
#
# RB-008 part 3. This module carries the THINNEST per-file margin in the tree
# (91.45% combined against the 90 floor, measured at 18330d4), and the arcs
# below are the reason: `_collected_node_ids` has three failure exits and the
# suite exercised none of them, while `lint`'s early return on a collection
# error was likewise unproved. These are not theoretical — a Green row citing
# an uncollectable node id took CI red on PR #6. Each test names the one-line
# production mutation that reddens it.


def _broken_collection_root(tmp_path: Path) -> Path:
    """A directory pytest CANNOT collect: one test module with a bad import.

    A real subprocess against a real broken tree, not a stubbed
    ``subprocess.run``. Cheap — measured at 0.11s — because the tree holds one
    file, and honest, because the exit code being classified is the one pytest
    actually produces (2, ``Interrupted: 1 error during collection``) rather
    than one a fixture asserted into existence.
    """
    root = tmp_path / "uncollectable"
    root.mkdir()
    (root / "test_broken.py").write_text(
        "import a_module_that_is_definitely_not_installed_xyz\n", encoding="utf-8"
    )
    return root


def test_a_failed_collection_is_reported_as_an_error_not_an_empty_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes traceability.py:141->142 (and the 142-144 statements with it).

    The distinction this arc protects is the reason the function returns a pair
    at all: an empty node-id set and a failed collection are indistinguishable
    downstream and mean OPPOSITE things. Empty-because-broken reported as
    empty-because-collected makes every Green row in the matrix report "cites a
    test that does not collect", pointing the operator at the matrix when the
    fault is a broken import under tests/.

    THE MUTATION THAT REDDENS THIS: widen the classifier at traceability.py:141
    from `if proc.returncode not in (0, 5):` to `... not in (0, 2, 5):`. A
    collection error is exit 2, so the error string becomes None and the
    assertions below fail — exactly the misclassification the arc prevents.
    """
    monkeypatch.setattr(traceability, "REPO_ROOT", _broken_collection_root(tmp_path))

    node_ids, error = traceability._collected_node_ids()

    assert node_ids == frozenset(), f"a broken tree still yielded node ids: {node_ids!r}"
    assert error is not None, "a collection error was reported as a successful empty collection"
    assert "collect-only failed (exit 2)" in error, error
    # The diagnostic must carry pytest's own last line, or the operator learns
    # only that something failed.
    assert "no output" not in error, error


def test_a_hung_collection_is_named_as_a_hang_not_as_a_matrix_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closes the `subprocess.TimeoutExpired` handler (traceability.py:135-136).

    Driven by shrinking the module's own ceiling rather than by stubbing
    `subprocess.run`, so a REAL `subprocess.run` raises a REAL `TimeoutExpired`
    against a real (and really killed) child. `COLLECT_TIMEOUT` exists because
    a hung collection would otherwise hang the `traceability` CI stage until
    the job's 20-minute timeout with no stage-level diagnosis; this is the only
    test that has ever watched it fire.

    THE MUTATION THAT REDDENS THIS: change the handler body at
    traceability.py:136-139 to `return frozenset(), None`. A hang then reads as
    a clean empty collection and the `error is not None` assertion fails.
    """
    monkeypatch.setattr(traceability, "COLLECT_TIMEOUT", 0.001)

    node_ids, error = traceability._collected_node_ids()

    assert node_ids == frozenset()
    assert error is not None, "a timed-out collection was reported as a successful one"
    assert "did not finish within" in error, error
    assert "not a matrix defect" in error, error


def test_a_collection_failure_is_reported_instead_of_blaming_every_green_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes traceability.py:197->198 and the 198-199 statements: `lint`'s
    early return when collection failed.

    This is the arc above, seen at the level the operator reads. With
    collection broken, the report must contain the collection diagnosis and
    must NOT contain a single "does not collect" complaint — otherwise a broken
    import under tests/ is rendered as a matrix full of lying Green rows, and
    the operator edits the matrix to fix a problem that is not in it.

    THE MUTATION THAT REDDENS THIS: change traceability.py:197 from
    `if collect_error is not None:` to `if collect_error is None:`. Both
    assertions below then fail together — the diagnosis vanishes from the
    report and the Green row is blamed instead.
    """
    monkeypatch.setattr(traceability, "REPO_ROOT", _broken_collection_root(tmp_path))

    problems = _lint_text(
        tmp_path,
        monkeypatch,
        "| FR-900 | thing | `src/x.py` | tests/unit/test_x.py::test_y | M1 | Green | - |\n",
    )

    assert any("collect-only failed" in problem for problem in problems), problems
    assert not any("does not collect" in problem for problem in problems), (
        "a broken collection was rendered as a matrix defect: the operator is "
        f"pointed at the Green row instead of at the broken import. {problems!r}"
    )


def test_a_matrix_that_is_not_valid_utf8_is_a_named_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes traceability.py:155-156, the `OSError`/`UnicodeDecodeError` guard
    around reading the matrix.

    `test_a_missing_matrix_is_reported` above does NOT reach this handler: it
    points `MATRIX` at a directory, and `MATRIX.is_file()` returns the "is
    missing" violation before `read_text` is ever called — which is why that
    test was renamed off the word "unreadable". A file that
    exists and cannot be DECODED is the shape that actually gets here, and it
    is uid-independent, unlike a chmod-000 file under a root test runner.

    THE MUTATION THAT REDDENS THIS: change the handler at traceability.py:156
    to `return []`. The undecodable matrix then lints clean and the first
    assertion below fails.
    """
    matrix = tmp_path / "REQUIREMENT-TRACEABILITY.md"
    matrix.write_bytes(b"| FR-900 | \xff\xfe not valid utf-8 | x |\n")
    monkeypatch.setattr(traceability, "MATRIX", matrix)
    monkeypatch.setattr(traceability, "REPO_ROOT", tmp_path)

    problems = traceability.lint()

    assert any("could not be read" in problem for problem in problems), problems
    assert any("REQUIREMENT-TRACEABILITY.md" in problem for problem in problems), problems
