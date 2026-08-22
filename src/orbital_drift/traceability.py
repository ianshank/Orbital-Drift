"""Requirement-traceability matrix linter (adopt-governance-kit, spec scenario
"Traceability matrix is linted"; charter C-6 companion).

Lints ``traceability/REQUIREMENT-TRACEABILITY.md`` and runs as the
``traceability`` stage of ``ci/checks.sh``::

    python -m orbital_drift.traceability --json

Failures (exit 1, each named in the report):
* a missing or duplicated requirement id;
* a status outside the fixed enum;
* an empty cell;
* a ``Green`` row citing a pytest node id that ``pytest --collect-only`` does
  not actually collect (the rule that matters as milestones close).

Stdlib-only, no I/O beyond the matrix file and one pytest subprocess, so it can
never drift with the environment.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
MATRIX: Final = REPO_ROOT / "traceability" / "REQUIREMENT-TRACEABILITY.md"

#: Ceiling for the node-id collection subprocess. A hung collection would
#: otherwise hang the `traceability` CI stage until the job's own 20-minute
#: timeout, with no stage-level diagnosis. Every other subprocess in this repo
#: sets one; this is production code, so it matters most here.
COLLECT_TIMEOUT: Final = 300.0

STATUS_ENUM: Final = frozenset(
    {"Planned-gated", "In-progress", "Green", "Uncured-see-owner", "N/A-by-design"}
)

_ROW = re.compile(r"^\|\s*(?P<cells>[^|].*)\|\s*$")
#: Node ids as pytest ACTUALLY emits them. The first version was
#: ``tests/[\w/]+\.py::\w+``, which truncates a parametrized id at the
#: bracket and misses hyphenated paths entirely — 189 of this suite's 341
#: collected ids contain "[", so the first Green row citing a parametrized
#: test would have failed the gate on a correct matrix.
_NODE_ID = re.compile(r"tests/[\w./-]+\.py(?:::[\w.\-\[\]=: ]+)+")
#: A cell whose first column looks like a requirement id must be a full
#: 7-column row; anything else is a malformed row, not a non-row.
_REQUIREMENT_ID = re.compile(r"^(?:FR|NFR|SC|C|R|DEC)-\d+", re.IGNORECASE)


def _relative(path: Path) -> str:
    """Repo-relative when possible; absolute otherwise (tests point the module
    at tmp paths outside the repo)."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass(frozen=True)
class Row:
    requirement: str
    summary: str
    modules: str
    tests: str
    milestone: str
    status: str
    notes: str
    line: int


def _parse_rows(text: str) -> tuple[list[Row], list[str]]:
    """Parse data rows, and report rows that LOOK like data but are malformed.

    The arity check legitimately skips the legend table and separator rows, but
    on its own it cannot tell "not a data row" from "data row with the wrong
    number of cells" — measured: a row containing a pipe inside a code span
    silently vanished, taking an invalid status with it and leaving the lint
    green. Anything whose first column matches a requirement id must therefore
    be a complete row or be reported.
    """
    rows: list[Row] = []
    problems: list[str] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        match = _ROW.match(raw)
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group("cells").split("|")]
        if len(cells) != 7 or cells[0] in {"Req", ""} or set(cells[0]) <= {"-", " ", ":"}:
            if _REQUIREMENT_ID.match(cells[0]) and len(cells) != 7:
                problems.append(
                    f"line {line_number}: row {cells[0]!r} has {len(cells)} cells, expected 7 "
                    "(a literal '|' inside a cell must be escaped as '\\|')"
                )
            continue
        rows.append(
            Row(
                requirement=cells[0],
                summary=cells[1],
                modules=cells[2],
                tests=cells[3],
                milestone=cells[4],
                status=cells[5],
                notes=cells[6],
                line=line_number,
            )
        )
    return rows, problems


def _collected_node_ids() -> tuple[frozenset[str], str | None]:
    """Every node id pytest collects, plus an error string when it could not.

    Returns the error rather than an empty set because the two are
    indistinguishable downstream and mean opposite things: an empty set makes
    every Green row report "cites a test that does not collect", pointing the
    operator at the matrix when the real fault is a broken import somewhere
    under tests/.
    """
    sys.stderr.write("traceability: collecting pytest node ids...\n")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
            timeout=COLLECT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return frozenset(), (
            f"pytest --collect-only did not finish within {COLLECT_TIMEOUT:.0f}s "
            "(a hung collection, not a matrix defect)"
        )
    # 0 = collected, 5 = collected nothing. Anything else is a collection error.
    if proc.returncode not in (0, 5):
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        tail = detail[-1] if detail else "no output"
        return frozenset(), (f"pytest --collect-only failed (exit {proc.returncode}): {tail[:300]}")
    return frozenset(line.strip() for line in proc.stdout.splitlines() if "::" in line), None


def lint() -> list[str]:
    """Return every violation as a human-readable string; empty means clean."""
    if not MATRIX.is_file():
        return [f"{_relative(MATRIX)} is missing"]

    try:
        text = MATRIX.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{_relative(MATRIX)} could not be read: {error}"]

    rows, problems = _parse_rows(text)
    if not rows:
        problems.append("matrix parsed to zero data rows — the lint would be vacuous")
        return problems

    seen: dict[str, int] = {}
    collected: frozenset[str] | None = None

    for row in rows:
        where = f"row {row.requirement!r} (line {row.line})"
        if row.requirement in seen:
            problems.append(
                f"{where}: duplicate requirement id (first at line {seen[row.requirement]})"
            )
        seen.setdefault(row.requirement, row.line)

        for field_name, value in (
            ("summary", row.summary),
            ("modules", row.modules),
            ("tests", row.tests),
            ("milestone", row.milestone),
            ("status", row.status),
            ("notes", row.notes),
        ):
            if not value:
                problems.append(f"{where}: empty {field_name} cell")

        if row.status not in STATUS_ENUM:
            problems.append(
                f"{where}: status {row.status!r} outside the enum {sorted(STATUS_ENUM)}"
            )

        if row.status == "Green":
            node_ids = _NODE_ID.findall(row.tests)
            if not node_ids:
                problems.append(f"{where}: Green but cites no pytest node id")
                continue
            if collected is None:
                collected, collect_error = _collected_node_ids()
                if collect_error is not None:
                    problems.append(collect_error)
                    return problems
            for node_id in node_ids:
                if node_id not in collected:
                    problems.append(
                        f"{where}: Green cites {node_id}, which pytest --collect-only "
                        "does not collect"
                    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    problems = lint()
    # sys.std*.write, not print(): ruff T20 bans print() in src/ (structured
    # output only); a linter's report IS its structured output.
    if args.json:
        report = json.dumps({"ok": not problems, "problems": problems}, indent=2)
        sys.stdout.write(report + "\n")
    else:
        for problem in problems:
            sys.stderr.write(f"traceability: {problem}\n")
        if not problems:
            sys.stdout.write("traceability: matrix clean\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
