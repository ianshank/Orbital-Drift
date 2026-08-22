"""Per-file coverage floor (adopt-governance-kit; charter C-6, DEC-004).

coverage.py has a global ``fail_under`` and no per-file equivalent, so a
fully-untested new module can hide behind a healthy aggregate: at this repo's
~230 statements, a 15-statement untested module still clears a 95% global bar,
and the headroom only grows as the codebase does.

A global floor answers "is the average acceptable". This answers "is anything
unwatched" — the question a gate should actually ask. Both run: ``stage_coverage``
in ci/checks.sh invokes pytest with the global floor first, then this, only
after the global floor has passed.

READS THE JSON REPORT, NOT coverage.xml. The first version parsed the XML with
``xml.etree``, whose stdlib parsers are vulnerable to XXE and billion-laughs
inputs. The report is self-generated and therefore not attacker-controlled, but
the choice between "add defusedxml as a dependency for a file we produce
ourselves" and "read the JSON coverage.py also emits" is not close: JSON has no
entity-expansion surface at all, and it costs no new pin.

WHAT THIS FLOOR MEASURES (RB-008 part 3) — READ BEFORE COMPARING NUMBERS
-----------------------------------------------------------------------
``ci/checks.sh``'s ``stage_coverage`` passes ``--cov-branch``, asserted by
``test_the_coverage_stage_measures_branches_not_only_statements``. With branch
measurement on, coverage.py's ``summary.percent_covered`` — the field
:func:`check` compares against the floor — is the COMBINED statement+branch
rate::

    (covered_lines + covered_branches) / (num_statements + num_branches)

not the statement rate it was before. The floor's VALUE is unchanged at 90
(RB-008 forbids moving any gate bar); the QUANTITY it compares is now strictly
harder for every file whose arcs are less covered than its statements.
Measured 2026-08-22 at 9de5a0e, statement-only -> combined: guard.py
97.81 -> 96.55, remotes.py 97.22 -> 95.45, projections.py 98.85 -> 98.29,
covcheck.py 98.15 -> 97.30, traceability.py 90.74 -> 91.45 (up, because that
file's arcs are better covered than its statements), global 96.81 -> 96.18.
**A global number that fell is this flag working, not a regression.**

DECISION: ONE COMBINED BAR, NOT A SEPARATE BRANCH BAR. coverage.py 7.15.4 also
emits ``percent_statements_covered`` and ``percent_branches_covered`` per file,
so splitting the floor in two is available and was considered and rejected:

1. A second bar needs a second THRESHOLD VALUE, and RB-008's explicit limit is
   that no gate bar value changes anywhere in that batch — introducing one is
   as much a bar change as moving one. That alone settles it; the rest is why
   the answer would not differ without the constraint.
2. The combined rate at 90 is already strictly harder than the statement rate
   at 90 for the four files above, so the gate tightened with no threshold edit
   — which is the whole point of the change.
3. A branch-only bar is VACUOUS on exactly the file it would most need to
   judge: coverage.py reports ``percent_branches_covered = 100.0`` when
   ``num_branches == 0`` (measured 2026-08-22 against coverage 7.15.4 with a
   purpose-built branchless module), so a file with no arcs passes a branch
   floor by having nothing to fail. The combined rate degrades gracefully to
   the statement rate in that same case.
4. It needs no new machinery here — zero lines change — so there is no new
   code path to test and none to rot.

THE COST, NAMED: a breach message reports a blended percentage and does not say
whether statements or arcs caused it. That is tolerable only because the split
is already on the operator's screen — ``stage_coverage`` prints its
``--cov-report=term-missing`` table (``Branch``, ``BrPart``, and the exact
missing arcs, e.g. ``214->220``) before it runs this module, never after. If a
future decision does want two bars, ``percent_statements_covered`` and
``percent_branches_covered`` are already in the report; the reversal costs a
DEC/RB entry for the new value, not a redesign.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
COVERAGE_JSON: Final = REPO_ROOT / "coverage.json"

#: Per-file minimum, in percent. Deliberately BELOW the global floor: the
#: global bar asserts the aggregate is healthy, this one asserts no single
#: module is abandoned. A file legitimately below it needs a documented
#: exemption, not a lowered bar.
PER_FILE_FLOOR: Final = 90.0

#: Files exempt from the per-file floor, each with the reason it cannot be
#: measured meaningfully. Keep this list short and justified; an unexplained
#: exemption is indistinguishable from a mistake.
EXEMPT: Final[dict[str, str]] = {}


def check(json_path: Path = COVERAGE_JSON, floor: float = PER_FILE_FLOOR) -> list[str]:
    """Return one message per file below the floor; empty means clean."""
    if not json_path.is_file():
        return [
            f"{json_path.name} not found — run the coverage stage first "
            "(pytest --cov=src/orbital_drift --cov-report=json)"
        ]
    try:
        report: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return [f"{json_path.name} could not be read: {error}"]

    files = report.get("files")
    if not isinstance(files, dict) or not files:
        return [f"{json_path.name} reported no files — the check would be vacuous"]

    failures: list[str] = []
    measured = 0
    for filename, entry in sorted(files.items()):
        summary = entry.get("summary", {}) if isinstance(entry, dict) else {}
        statements = summary.get("num_statements", 0)
        if not isinstance(statements, int) or statements == 0:
            continue  # 0-statement module (docstring-only __init__)
        measured += 1
        if filename in EXEMPT:
            continue
        percent = summary.get("percent_covered", 0.0)
        rate = float(percent) if isinstance(percent, (int, float)) else 0.0
        if rate < floor:
            failures.append(
                f"{filename}: {rate:.1f}% over {statements} statements "
                f"< per-file floor {floor:.0f}%"
            )

    if measured == 0:
        return [f"{json_path.name} reported no measurable files — the check would be vacuous"]
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-report", type=Path, default=COVERAGE_JSON)
    parser.add_argument("--floor", type=float, default=PER_FILE_FLOOR)
    args = parser.parse_args(argv)
    json_path: Path = args.json_report
    floor: float = args.floor

    failures = check(json_path, floor)
    # sys.std*.write, not print(): ruff T20 bans print() in src/.
    for failure in failures:
        sys.stderr.write(f"covcheck: {failure}\n")
    if failures:
        sys.stderr.write(
            "covcheck: a module below the per-file floor is unwatched even when the "
            "aggregate is healthy. Add tests, or exempt it with a reason in covcheck.EXEMPT.\n"
        )
        return 1
    sys.stdout.write(f"covcheck: every measured module is at or above {floor:.0f}%\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
