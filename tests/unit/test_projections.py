"""Projection integrity tests (adopt-governance-kit design D9).

The cannot-disagree property: planning projections are byte-generated from
roadmap_data.py, and every story's trace cites a task id that exists in
specs/001-orbital-drift-ct/tasks.md with a status matching the checkbox state.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from orbital_drift import projections
from orbital_drift.planning import roadmap_data

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
TASKS: Final = REPO_ROOT / "specs" / "001-orbital-drift-ct" / "tasks.md"

_TASK_LINE = re.compile(r"^- \[(?P<done>[ x])\] (?P<task_id>T\d{3})\b", re.MULTILINE)
_TRACE_TASK = re.compile(r"\bT\d{3}\b")


def _task_states() -> dict[str, bool]:
    """{task id: checked} parsed from the plan-of-record tasks file."""
    text = TASKS.read_text(encoding="utf-8")
    states = {
        match.group("task_id"): match.group("done") == "x" for match in _TASK_LINE.finditer(text)
    }
    assert states, "tasks.md parsed to zero tasks — every assertion below is vacuous"
    return states


def test_committed_projections_match_the_data_source() -> None:
    assert projections._drift() == []


def test_every_story_trace_cites_a_real_task() -> None:
    states = _task_states()
    missing = [
        f"{story.key} -> {task_id}"
        for story in roadmap_data.STORIES
        for task_id in _TRACE_TASK.findall(story.acceptance)
        if task_id not in states
    ]
    assert missing == [], f"story traces cite task ids absent from tasks.md: {missing}"


def test_every_agent_task_has_a_story() -> None:
    """Scope parity in the other direction: no unchecked task silently missing
    from the backlog. T001 (done before the backlog existed) is the one
    exemption."""
    states = _task_states()
    traced = {
        task_id
        for story in roadmap_data.STORIES
        for task_id in _TRACE_TASK.findall(story.acceptance)
    }
    untracked = [task_id for task_id in states if task_id not in traced and task_id != "T001"]
    assert untracked == [], f"tasks.md tasks with no backlog story: {untracked}"


@pytest.mark.parametrize("guard_fn", roadmap_data.GUARDS, ids=lambda fn: fn.__name__)
def test_guard_functions_are_clean(guard_fn: Callable[[], list[str]]) -> None:
    """Every guard in the registry, not a hand-kept subset — the first version
    listed four by name and the three added later were never run here."""
    assert guard_fn() == []


def test_guard_functions_catch_violations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: each convention guard CAN fail (the
    green-with-the-fix-removed failure mode)."""
    bad = roadmap_data.Story(
        key="S9.9",
        title="S9.9 Markdown **bold** and unicode — both banned",
        epic="E-ghost",
        acceptance="No trace anchor here.",
        labels=("x",),
        points=0,
        priority="Low",
    )
    monkeypatch.setattr(roadmap_data, "STORIES", (bad,))
    assert roadmap_data.csv_projected_fields_are_plain_ascii() != []
    assert roadmap_data.every_story_traces_to_a_task() == ["S9.9"]
    assert roadmap_data.owner_stories_carry_no_points() == ["S9.9"]
    assert roadmap_data.every_story_epic_exists() == ["S9.9"]


def test_write_then_check_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--write emits both files; --check is then clean; a one-byte hand-edit
    drifts (spec scenario 'Hand-edit fails the gate')."""
    monkeypatch.setattr(projections, "PLANNING_DIR", tmp_path)
    monkeypatch.setattr(projections, "ROADMAP", tmp_path / "roadmap.md")
    monkeypatch.setattr(projections, "CSV_PATH", tmp_path / "jira-import.csv")

    assert projections.main(["--write"]) == 0
    assert projections.main(["--check", "--json"]) == 0
    assert '"ok": true' in capsys.readouterr().out

    with (tmp_path / "roadmap.md").open("a", encoding="utf-8") as handle:
        handle.write("x")
    assert projections.main(["--check"]) == 1
    assert "differs from" in capsys.readouterr().err


def test_check_reports_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(projections, "ROADMAP", tmp_path / "roadmap.md")
    monkeypatch.setattr(projections, "CSV_PATH", tmp_path / "jira-import.csv")
    assert projections.main(["--check"]) == 1
    assert "missing" in capsys.readouterr().err


def test_label_overflow_raises_instead_of_truncating() -> None:
    """The CSV has three Labels columns; a fourth label used to vanish
    silently — data loss inside the module whose premise is that the backlog
    cannot disagree with the plan."""
    with pytest.raises(projections.ProjectionError, match="Labels columns"):
        projections._label_cells(("a", "b", "c", "d"))


def test_label_cells_pads_to_the_column_count() -> None:
    assert projections._label_cells(("a",)) == ["a", "", ""]
    assert projections._label_cells(()) == ["", "", ""]


def test_story_with_undeclared_epic_raises_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch projections.STORIES, not roadmap_data.STORIES: the emitter binds
    its own module-level name at import, so patching the source module leaves
    the renderer untouched (the previous negative control never reached it)."""
    ghost = roadmap_data.Story(
        key="S9.9",
        title="S9.9 orphan",
        epic="E-ghost",
        acceptance="AC: none. Trace: T002.",
        labels=("orbital-drift",),
        points=1,
        priority="Low",
    )
    monkeypatch.setattr(projections, "STORIES", (ghost,))
    with pytest.raises(projections.ProjectionError, match="undeclared epics"):
        projections.render_csv()


def test_drift_compares_bytes_not_decoded_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CRLF checkout (the Windows default, and this repo is authored on
    Windows) compared EQUAL under read_text's universal-newline translation,
    so the documented 'byte-match' claim was not true."""
    monkeypatch.setattr(projections, "PLANNING_DIR", tmp_path)
    monkeypatch.setattr(projections, "ROADMAP", tmp_path / "roadmap.md")
    monkeypatch.setattr(projections, "CSV_PATH", tmp_path / "jira-import.csv")
    assert projections.main(["--write"]) == 0

    crlf = (tmp_path / "roadmap.md").read_bytes().replace(b"\n", b"\r\n")
    (tmp_path / "roadmap.md").write_bytes(crlf)
    assert projections.main(["--check"]) == 1


def test_empty_data_still_renders_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projections, "EPICS", ())
    monkeypatch.setattr(projections, "STORIES", ())
    csv_text = projections.render_csv()
    assert csv_text.startswith('"Issue Type"')
    assert projections.render_roadmap().startswith("# Orbital-Drift Roadmap")


def test_csv_quotes_embedded_commas_and_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    tricky = roadmap_data.Story(
        key="S9.8",
        title='S9.8 has "quotes", and a comma',
        epic="E0",
        acceptance="AC: survives csv quoting. Trace: T002.",
        labels=("orbital-drift",),
        points=1,
        priority="Low",
    )
    monkeypatch.setattr(projections, "STORIES", (tricky,))
    rendered = projections.render_csv()
    assert '"S9.8 has ""quotes"", and a comma"' in rendered


def test_plain_check_mode_reports_success(capsys: pytest.CaptureFixture[str]) -> None:
    assert projections.main(["--check"]) == 0
    assert "match roadmap_data.py" in capsys.readouterr().out


def test_labels_are_declared_catches_an_unknown_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rogue = roadmap_data.Story(
        key="S9.7",
        title="S9.7 rogue label",
        epic="E0",
        acceptance="AC: x. Trace: T002.",
        labels=("orbital-drift", "not-a-declared-label"),
        points=1,
        priority="Low",
    )
    monkeypatch.setattr(roadmap_data, "STORIES", (rogue,))
    assert roadmap_data.labels_are_declared() == ["S9.7: not-a-declared-label"]


def test_labels_fit_the_csv_projection_catches_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fat = roadmap_data.Story(
        key="S9.6",
        title="S9.6 too many labels",
        epic="E0",
        acceptance="AC: x. Trace: T002.",
        labels=("orbital-drift", "owner", "infra", "data"),
        points=1,
        priority="Low",
    )
    monkeypatch.setattr(roadmap_data, "STORIES", (fat,))
    assert roadmap_data.labels_fit_the_csv_projection() == ["S9.6"]


def test_the_labels_column_count_has_exactly_one_home() -> None:
    """The guard and the emitter must agree by construction, not by comment.

    ``roadmap_data.labels_fit_the_csv_projection`` used to carry its own
    ``limit = 3`` annotated "duplicated to keep this module I/O-free". That
    reason was wrong — importing an ``int`` is not I/O. The real constraint is
    direction: ``projections`` imports ``roadmap_data``, so ``roadmap_data``
    cannot import ``projections`` back without a cycle. The fix is to put the
    constant in the leaf and re-export it, not to keep two copies.

    Two copies is not a style question here. Widening the CSV to four Labels
    columns while updating only the emitter leaves the guard rejecting a
    perfectly valid fourth label; updating only the guard restores exactly the
    silent-data-loss bug ``_label_cells``' docstring says it exists to prevent.

    IDENTITY ALONE CANNOT PROVE THIS, which is why the source check below
    exists. ``LABEL_COLUMNS`` is ``3``, and CPython interns every int in
    -5..256, so a re-introduced ``LABEL_COLUMNS: Final = 3`` inside
    ``projections`` would be the very same object and satisfy ``is``. The
    identity assertion is kept — it is the cheap check that the two names agree
    at runtime — but the assertion that actually detects a second home has to
    read the source and confirm no second declaration exists.

    Plain regex, not the AST toolkit: an ``import``ed name is not an assignment,
    so a one-line ``^LABEL_COLUMNS\\s*[:=]`` match distinguishes "declared here"
    from "imported here" without parsing, and the AST helper is out of this
    change's scope (RB-008 defers it explicitly).
    """
    assert roadmap_data.LABEL_COLUMNS is projections.LABEL_COLUMNS, (
        "projections.LABEL_COLUMNS must be the re-exported roadmap_data value, not a second literal"
    )

    source = Path(projections.__file__).read_text(encoding="utf-8")
    assert re.search(r"^LABEL_COLUMNS\s*[:=]", source, re.M) is None, (
        "projections.py declares LABEL_COLUMNS itself. Because small ints are "
        "interned, that second home would still satisfy the `is` check above while "
        "silently re-creating the two-copies defect. Import it from roadmap_data."
    )

    home = Path(roadmap_data.__file__).read_text(encoding="utf-8")
    assert re.search(r"^LABEL_COLUMNS\s*[:=]", home, re.M), (
        "roadmap_data.py no longer declares LABEL_COLUMNS, so the check above passes "
        "vacuously — neither module would own it"
    )


def test_the_overflow_guard_uses_the_shared_constant_not_a_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Move the single home and BOTH sides move. This is what pins the re-export.

    Without this, ``labels_fit_the_csv_projection`` could go back to a local
    literal that merely happens to equal the constant, and the test above would
    still pass.
    """
    monkeypatch.setattr(roadmap_data, "LABEL_COLUMNS", 1)
    two_labels = roadmap_data.Story(
        key="S9.5",
        title="S9.5 two labels",
        epic="E0",
        acceptance="AC: x. Trace: T002.",
        labels=("orbital-drift", "owner"),
        points=1,
        priority="Low",
    )
    monkeypatch.setattr(roadmap_data, "STORIES", (two_labels,))
    assert roadmap_data.labels_fit_the_csv_projection() == ["S9.5"]


def test_the_emitter_uses_the_shared_constant_not_a_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of "the guard AND the emitter agree by construction".

    The guard's half is pinned above by monkeypatching ``roadmap_data``. That
    cannot move the emitter, which binds ``LABEL_COLUMNS`` into its own module
    namespace at import — so until this test, replacing the emitter's two uses
    with a literal ``3`` left the whole unit suite green while the docstring
    above claimed both sides were bound. Padding width and overflow ceiling are
    separate expressions, so both are exercised.
    """
    monkeypatch.setattr(projections, "LABEL_COLUMNS", 4)

    assert projections._label_cells(("a", "b")) == ["a", "b", "", ""], (
        "_label_cells pads to a literal instead of LABEL_COLUMNS"
    )
    # Four labels must now FIT, where at the real width of 3 they raise.
    assert projections._label_cells(("a", "b", "c", "d")) == ["a", "b", "c", "d"]

    with pytest.raises(projections.ProjectionError):
        projections._label_cells(("a", "b", "c", "d", "e"))


def test_the_csv_header_declares_one_labels_column_per_shared_constant() -> None:
    """The header's width is the same fact as the padding width.

    It was three literal ``"Labels"`` strings — a fourth un-derived expression
    — so widening the constant would have emitted N label cells underneath 3
    headers: a CSV Jira accepts and silently mis-imports.
    """
    header = projections.render_csv().splitlines()[0]
    declared = header.count('"Labels"')
    assert declared == projections.LABEL_COLUMNS, (
        f"CSV header declares {declared} Labels columns but LABEL_COLUMNS is "
        f"{projections.LABEL_COLUMNS}"
    )


def test_keys_are_unique_catches_a_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    twin = roadmap_data.Story(
        key="S0.2",
        title="S0.2 duplicate key",
        epic="E0",
        acceptance="AC: x. Trace: T002.",
        labels=("orbital-drift",),
        points=1,
        priority="Low",
    )
    monkeypatch.setattr(roadmap_data, "STORIES", (twin, twin))
    assert roadmap_data.keys_are_unique() == ["S0.2"]


def test_owner_story_with_points_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard's name promised this and the body only checked `points == 0`."""
    owner = roadmap_data.Story(
        key="S9.5",
        title="S9.5 owner story with points",
        epic="E7",
        acceptance="AC: x. Trace: T003.",
        labels=("orbital-drift", "owner"),
        points=5,
        priority="Highest",
    )
    monkeypatch.setattr(roadmap_data, "STORIES", (owner,))
    assert roadmap_data.owner_stories_carry_no_points() == ["S9.5"]


def test_epic_fields_are_ascii_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Epic.name/summary are CSV-projected too; the first guard checked only
    stories, so a non-ASCII em-dash in an epic summary shipped."""
    bad = roadmap_data.Epic(
        key="E9",
        name="E9 has an em dash \u2014 here",
        summary="Summary.",
        priority="Low",
    )
    monkeypatch.setattr(roadmap_data, "EPICS", (bad,))
    monkeypatch.setattr(roadmap_data, "STORIES", ())
    assert roadmap_data.csv_projected_fields_are_plain_ascii() != []
