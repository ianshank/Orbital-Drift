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
  not actually collect (the rule that matters as milestones close);
* a requirement declared in ``spec.md`` with no row in the matrix, or a row
  citing a requirement ``spec.md`` never declares (RB-012).

The last rule is the only one that reads a second file. The matrix's own header
names ``specs/001-orbital-drift-ct/spec.md`` as its source of truth, and until
RB-012 nothing checked that claim — measured instance: ``SC-002`` (the
specification's only performance budget) was declared by the spec and carried by
no row, invisible to this stage because the linter read the matrix alone.

HONEST SCOPE of that rule, so no reader over-trusts it: it compares which
requirement IDS appear on each side. It does NOT check that a row's summary
faithfully compresses the spec text for that id — the matrix's summaries are
deliberate compressions ("summaries below are compressions, not restatements"),
and no regex separates a good compression from a wrong one. Summary fidelity
stays reviewer-enforced; see ``docs/decisions/013-plan-artifact-reconciliation.md``.

Stdlib-only, no I/O beyond the matrix file, the spec file, and one pytest
subprocess, so it can never drift with the environment.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
MATRIX: Final = REPO_ROOT / "traceability" / "REQUIREMENT-TRACEABILITY.md"
#: The specification the matrix declares as its source of truth, read so that
#: claim is checked rather than trusted (RB-012).
SPEC: Final = REPO_ROOT / "specs" / "001-orbital-drift-ct" / "spec.md"

#: Ceiling for the node-id collection subprocess. A hung collection would
#: otherwise hang the `traceability` CI stage until the job's own 20-minute
#: timeout, with no stage-level diagnosis. Every other subprocess in this repo
#: sets one; this is production code, so it matters most here.
COLLECT_TIMEOUT: Final = 300.0  # pin: subprocess timeout ceiling, see doc comment above

#: How much of an offending spec line to echo back in a near-miss report.
#:
#: A display width, not a configuration value: it exists so one malformed line
#: cannot flood the gate's output, and no behaviour depends on it. Pinned rather
#: than made configurable for that reason — RB-012 itself found that `# pin:`
#: is used in this repo to launder genuinely config-shaped values (D-013/05
#: item 3), so this one says explicitly which kind it is.
_ECHO_WIDTH: Final = 80  # pin: operator-facing truncation width, see doc comment above

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
#: :data:`CELLS_PER_ROW`-column row; anything else is a malformed row, not a
#: non-row. (Stated as the constant, not as "7": the arity is derived from
#: :class:`Row`, so a literal here would be the fourth copy of the number this
#: module just finished removing three of.)
_REQUIREMENT_ID = re.compile(r"^(?:FR|NFR|SC|C|R|DEC)-\d+", re.IGNORECASE)
#: The prefixes parity covers. NARROWER than :data:`_REQUIREMENT_ID`, which the
#: row parser uses, and the difference is deliberate: ``spec.md`` declares only
#: ``FR-`` and ``SC-``. Charter constraints (``C-n``) and risks (``R-nn``) are
#: declared in `charter/PROJECT-CHARTER.md` and `plan.md`, which this module does
#: not read — so demanding a spec declaration for a ``C-1`` row would be a rule
#: no edit to `spec.md` could satisfy. Matrix rows outside these prefixes are
#: therefore skipped by parity rather than reported as undeclared.
_PARITY_PREFIX = re.compile(r"^(?:FR|SC)-", re.IGNORECASE)

#: A requirement declaration in spec.md: ``- **FR-011a** CI enforces...``.
#:
#: The trailing ``[a-z]?`` is load-bearing, not defensive. ``FR-011a`` and
#: ``FR-011b`` are real, distinct requirements added after the original draft
#: (T001a, T001b). MEASURED, because the first version of this comment guessed
#: wrong and said the opposite: dropping ``[a-z]?`` does NOT collapse the three
#: into one ``FR-011`` — the trailing ``\*\*`` then fails to match, so
#: ``FR-011a`` and ``FR-011b`` are DROPPED entirely and the committed matrix
#: goes loudly red (6 tests). Loud, not silent; the pattern is still required,
#: for the opposite reason to the one originally recorded.
_SPEC_REQUIREMENT = re.compile(r"^- \*\*((?:FR|SC)-\d+[a-z]?)\*\*", re.MULTILINE)

#: A line that LOOKS like a requirement declaration but may not be canonical.
#:
#: WHY THIS EXISTS — a measured fail-open, found by adversarial review of the
#: very change that added the parity rule. :data:`_SPEC_REQUIREMENT` matches ONE
#: markdown shape. Indenting a single declaration by two spaces
#: (``  - **SC-002**``) removed it from the declared set, so deleting its matrix
#: row lint()ed CLEAN — reproducing D-013/03b, the untraced performance budget,
#: THROUGH the guard written to prevent it. Also invisible: ``* **FR-x**``,
#: ``> - **FR-x**``, ``1. **FR-x**``, ``__FR-x__``, a tab after the dash.
#:
#: This module's contract is that every failure to understand is a reported
#: problem — :func:`_parse_rows` already honours it for partially-malformed
#: matrix rows. Parity now honours it for partially-malformed declarations:
#: anything matching here and NOT :data:`_SPEC_REQUIREMENT` is reported, so a
#: near-miss fails the gate loudly instead of shrinking the declared set.
_SPEC_NEAR_MISS = re.compile(
    r"^[ \t>]*(?:[-*+]|\d+[.)])?[ \t]*(?:\*\*|__)?[ \t]*"
    r"(?:FR|NFR|SC|C|R|DEC)-\d+[a-z]?",
    re.MULTILINE | re.IGNORECASE,
)


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


#: Cells a data row must have — DERIVED from :class:`Row`, never typed again.
#:
#: ``Row`` already declares the matrix's columns; the arity was additionally
#: written as a literal ``7`` in three places below (the skip check, the
#: malformed-row check, and the operator-facing message) plus once more in this
#: module's test. Adding an eighth matrix column meant finding all four, and
#: missing the message alone produced a linter that rejects every correct row
#: while insisting it "expected 7" about an eight-column table.
#:
#: ``- 1`` drops ``line``, which is the file position ``_parse_rows`` supplies
#: from ``enumerate`` — the one field that is not a cell read out of the table.
CELLS_PER_ROW: Final = len(dataclasses.fields(Row)) - 1


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
        wrong_arity = len(cells) != CELLS_PER_ROW
        if wrong_arity or cells[0] in {"Req", ""} or set(cells[0]) <= {"-", " ", ":"}:
            if _REQUIREMENT_ID.match(cells[0]) and wrong_arity:
                problems.append(
                    f"line {line_number}: row {cells[0]!r} has {len(cells)} cells, "
                    f"expected {CELLS_PER_ROW} "
                    "(a literal '|' inside a cell must be escaped as '\\|')"
                )
            continue
        rows.append(
            Row(
                requirement=cells[0],
                summary=cells[1],
                modules=cells[2],
                tests=cells[3],  # pin: fixed table-column position, matches Row field order
                milestone=cells[4],  # pin: fixed table-column position, matches Row field order
                status=cells[5],  # pin: fixed table-column position, matches Row field order
                notes=cells[6],  # pin: fixed table-column position, matches Row field order
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
    if proc.returncode not in (0, 5):  # pin: pytest's own exit-code convention, see comment above
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        tail = detail[-1] if detail else "no output"
        return frozenset(), (
            f"pytest --collect-only failed (exit {proc.returncode}): {tail[:300]}"  # pin: truncate
        )
    return frozenset(line.strip() for line in proc.stdout.splitlines() if "::" in line), None


def spec_requirement_ids() -> tuple[frozenset[str], str | None]:
    """Every requirement id ``spec.md`` declares, plus an error when it could not.

    Returns the error rather than an empty set for the same reason
    :func:`_collected_node_ids` does: the two are indistinguishable downstream
    and mean opposite things. An empty set makes EVERY matrix row report
    "spec.md does not declare this", pointing the operator at twenty correct
    rows when the real fault is a moved, deleted, or unreadable spec.
    """
    if not SPEC.is_file():
        return frozenset(), f"{_relative(SPEC)} is missing (the matrix's declared source of truth)"
    try:
        text = SPEC.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return frozenset(), f"{_relative(SPEC)} could not be read: {error}"

    ids = frozenset(_SPEC_REQUIREMENT.findall(text))

    # Fail CLOSED on a near-miss, mirroring _parse_rows' malformed-row branch:
    # a declaration the canonical pattern cannot see would otherwise shrink the
    # declared set SILENTLY, which is the measured fail-open this guard exists
    # for. Reported as an error rather than added to `ids`, because guessing
    # what a malformed line meant is how a linter starts inventing requirements.
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if _SPEC_NEAR_MISS.match(raw) and _SPEC_REQUIREMENT.match(raw) is None:
            return frozenset(), (
                f"{_relative(SPEC)} line {line_number}: "
                f"{raw.strip()[:_ECHO_WIDTH]!r} looks like a "
                "requirement declaration but is not in the canonical "
                "'- **FR-001** ...' shape, so parity cannot see it"
            )

    if not ids:
        return frozenset(), (
            f"{_relative(SPEC)} declares no requirements in the expected "
            "'- **FR-001** ...' shape — parity would be vacuous"
        )
    return ids, None


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

    declared, spec_error = spec_requirement_ids()
    if spec_error is not None:
        problems.append(spec_error)
    else:
        # Only FR-/SC- rows take part: see _PARITY_PREFIX for why demanding a
        # spec.md declaration for a C-n or R-nn row would be unsatisfiable.
        traced = {row.requirement for row in rows if _PARITY_PREFIX.match(row.requirement)}
        problems += [
            f"requirement {requirement_id!r} is declared in {_relative(SPEC)} "
            "but has no row in the matrix"
            for requirement_id in sorted(declared - traced)
        ]
        problems += [
            f"row {requirement_id!r}: {_relative(SPEC)} does not declare this requirement"
            for requirement_id in sorted(traced - declared)
        ]

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
