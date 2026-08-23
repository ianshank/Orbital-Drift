"""Projection emitter for the Orbital-Drift backlog (design D9).

Renders ``planning/roadmap.md`` and ``planning/jira-import.csv`` from
``orbital_drift.planning.roadmap_data`` — the single data source — and checks
the committed files byte-match what the data emits::

    python -m orbital_drift.projections --check --json   # CI drift gate
    python -m orbital_drift.projections --write          # regenerate (re-baseline only)

Spec scenario "Hand-edit fails the gate": --check exits nonzero naming the
drifted file; it never regenerates silently.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Final

# LABEL_COLUMNS is imported, not defined here, and is deliberately RE-EXPORTED:
# `projections.LABEL_COLUMNS` is part of this module's surface and stays
# resolvable. Its single home is roadmap_data (the leaf) because this module
# imports that one — see the constant's own comment for why the previous
# arrangement, a copy here plus a private `limit = 3` there, was a defect
# rather than a style choice.
#
# The `as LABEL_COLUMNS` redundant alias is load-bearing, not noise: mypy runs
# --strict here, which implies --no-implicit-reexport, so a plainly-imported
# name is NOT visible to importers of this module. Without the alias,
# `projections.LABEL_COLUMNS` type-checks as an error at every call site while
# working fine at runtime — the alias is mypy's documented way to say "this
# re-export is intentional".
from orbital_drift.planning.roadmap_data import EPICS, STORIES
from orbital_drift.planning.roadmap_data import LABEL_COLUMNS as LABEL_COLUMNS

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PLANNING_DIR: Final = REPO_ROOT / "planning"
ROADMAP: Final = PLANNING_DIR / "roadmap.md"
CSV_PATH: Final = PLANNING_DIR / "jira-import.csv"

_CSV_BANNER: Final = (
    "GENERATED FILE - DO NOT HAND-EDIT - source of truth: "
    "orbital_drift.planning.roadmap_data - regenerate: "
    "python -m orbital_drift.projections --write"
)
_MD_BANNER: Final = (
    "<!-- GENERATED FILE - DO NOT HAND-EDIT. Source of truth: "
    "src/orbital_drift/planning/roadmap_data.py. Regenerate: "
    "python -m orbital_drift.projections --write -->"
)


def render_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(
        [
            "Issue Type",
            "Summary",
            "Epic Name",
            "Epic Link",
            "Description",
            # DERIVED, not three literals. The header's width and _label_cells'
            # padding width are the same fact; spelling it out here made a
            # fourth un-derived expression, so widening LABEL_COLUMNS would
            # have emitted N cells under 3 headers.
            *(["Labels"] * LABEL_COLUMNS),
            "Story Points",
            "Priority",
            _CSV_BANNER,
        ]
    )
    epic_names = {epic.key: epic.name for epic in EPICS}
    orphans = sorted({story.epic for story in STORIES} - set(epic_names))
    if orphans:
        # A bare KeyError here would surface as a traceback from a CI stage.
        raise ProjectionError(f"stories reference undeclared epics: {orphans}")
    for epic in EPICS:
        writer.writerow(
            [
                "Epic",
                epic.name,
                epic.name,
                "",
                epic.summary,
                *_label_cells(epic.labels),
                "",
                epic.priority,
                "",
            ]
        )
    for story in STORIES:
        points = "" if story.points is None else str(story.points)
        writer.writerow(
            [
                "Story",
                story.title,
                "",
                epic_names[story.epic],
                story.acceptance,
                *_label_cells(story.labels),
                points,
                story.priority,
                "",
            ]
        )
    return buffer.getvalue()


class ProjectionError(ValueError):
    """The data cannot be projected without losing information."""


def _label_cells(labels: tuple[str, ...]) -> list[str]:
    """Pad labels out to the CSV's fixed Labels columns.

    RAISES rather than truncating: the first implementation sliced to three and
    a fourth label vanished silently — `--write` regenerated, the byte-check
    passed, and nothing failed, inside the one module whose entire premise is
    that the backlog cannot disagree with the plan.
    """
    if len(labels) > LABEL_COLUMNS:
        raise ProjectionError(
            f"story/epic carries {len(labels)} labels but the CSV has "
            f"{LABEL_COLUMNS} Labels columns: {labels}"
        )
    return [*labels, *[""] * (LABEL_COLUMNS - len(labels))]


def render_roadmap() -> str:
    lines = [
        "# Orbital-Drift Roadmap",
        "",
        _MD_BANNER,
        "",
        "Scope is owned by `specs/001-orbital-drift-ct/tasks.md`; this file is a",
        "projection of `roadmap_data.py` and cannot disagree with it (the",
        "`projections` CI stage byte-checks both projections).",
        "",
    ]
    for epic in EPICS:
        lines.append(f"## {epic.name} ({epic.priority})")
        lines.append("")
        lines.append(epic.summary)
        lines.append("")
        lines.append("| Story | Points | Priority | Acceptance |")
        lines.append("|---|---|---|---|")
        for story in STORIES:
            if story.epic != epic.key:
                continue
            points = "-" if story.points is None else str(story.points)
            lines.append(f"| {story.title} | {points} | {story.priority} | {story.acceptance} |")
        lines.append("")
    return "\n".join(lines)


def _relative(path: Path) -> str:
    """Repo-relative when possible; absolute otherwise (tests point the module
    at tmp paths outside the repo)."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _drift() -> list[str]:
    """Compare committed projections against freshly rendered ones, BYTE-wise.

    Deliberately ``read_bytes()`` and not ``read_text()``: text mode applies
    universal-newline translation, so a CRLF checkout (the Windows default,
    and this repo is authored on Windows) compared equal to LF output and the
    "byte-match" claim was not true.
    """
    drifted: list[str] = []
    for path, rendered in ((ROADMAP, render_roadmap()), (CSV_PATH, render_csv())):
        if not path.is_file():
            drifted.append(f"{_relative(path)}: missing")
        elif path.read_bytes() != rendered.encode("utf-8"):
            drifted.append(
                f"{_relative(path)}: differs from "
                "roadmap_data.py output (hand-edited or stale; regenerate with --write)"
            )
    return drifted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="fail if projections drift")
    mode.add_argument("--write", action="store_true", help="regenerate both projections")
    parser.add_argument("--json", action="store_true", help="emit a JSON report (--check)")
    args = parser.parse_args(argv)

    # sys.std*.write, not print(): ruff T20 bans print() in src/; this report
    # is the module's structured output.
    if args.write:
        PLANNING_DIR.mkdir(exist_ok=True)
        ROADMAP.write_text(render_roadmap(), encoding="utf-8", newline="\n")
        CSV_PATH.write_text(render_csv(), encoding="utf-8", newline="\n")
        sys.stdout.write("projections: regenerated roadmap.md and jira-import.csv\n")
        return 0

    drifted = _drift()
    if args.json:
        sys.stdout.write(json.dumps({"ok": not drifted, "drifted": drifted}, indent=2) + "\n")
    else:
        for item in drifted:
            sys.stderr.write(f"projections: {item}\n")
        if not drifted:
            sys.stdout.write("projections: projections match roadmap_data.py\n")
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main())
