"""Governance meta-tests — tests that watch the PROCESS, not the product.

The donor kit's core insight: every hand-maintained governance artifact rots
unless a test fails when it does (adopt-governance-kit design.md). Five rot
vectors are covered here and in test_zero_skip_guard.py:

1. Makefile/checks.sh divergence (this file, design D1 — direction inverted
   from the donor template: HERE checks.sh is canonical and the Makefile is the
   thin front-end).
2. A stale governance-skill decision summary (this file).
3. A tracked file neither governed nor explicitly public (this file).
4. A zero-skip guard nobody has watched fire (test_zero_skip_guard.py).
5. A decision log that has stopped reading in chronological order, making
   "the last entry is the latest decision" false (this file).
"""

from __future__ import annotations

import fnmatch
import itertools
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
#: Subprocess ceiling (see test_zero_skip_guard for the rationale).
TIMEOUT: Final = 60.0

MAKEFILE: Final = REPO_ROOT / "Makefile"
CHECKS: Final = REPO_ROOT / "ci" / "checks.sh"
SKILL: Final = REPO_ROOT / ".claude" / "skills" / "orbital-drift-governance" / "SKILL.md"
DECISION_LOG: Final = REPO_ROOT / "docs" / "decision-log.md"
PYPROJECT: Final = REPO_ROOT / "pyproject.toml"


# ── 1. The Makefile delegates every gate to checks.sh and reconstructs none ──
# Rot vector: a Makefile recipe grows its own `pytest`/`ruff` invocation, local
# `make <gate>` and CI (which calls `sh ci/checks.sh <stage>`) silently diverge,
# and "it passed for me" stops meaning anything. Direction per design D1: every
# EXISTING gate target maps to a checks.sh dispatch label — never the inverse
# ("every stage has a target"), which would re-litigate checks.sh as canonical.

_GATE_RECIPE = re.compile(r"^(?P<target>[a-z-]+):[^\n]*\n(?P<recipe>(?:\t[^\n]*\n?)+)", re.M)
# Recipes allowed to run something other than `sh ci/checks.sh <stage>`:
_NON_GATE_TARGETS: Final = frozenset({"help", "install", "format", "guard-probe", "clean"})
# Raw tool invocations that would reconstruct a gate inline:
_GATE_TOOLS = re.compile(r"-m (pytest|ruff check|mypy|pre_commit|vulture|pip_audit)\b")


def _dispatch_labels() -> set[str]:
    match = re.search(r"^STAGE_LABELS='([^']*)'$", CHECKS.read_text(encoding="utf-8"), re.M)
    assert match, "could not parse STAGE_LABELS out of ci/checks.sh"
    return set(match.group(1).split())


def test_every_gate_target_delegates_to_a_checks_sh_stage() -> None:
    labels = _dispatch_labels()
    text = MAKEFILE.read_text(encoding="utf-8")
    seen_gate_target = False
    for match in _GATE_RECIPE.finditer(text):
        target, recipe = match.group("target"), match.group("recipe")
        if target in _NON_GATE_TARGETS:
            continue
        seen_gate_target = True
        # Accepts the quoted form: every $(ROOT) interpolation is quoted so a
        # repo path containing a space cannot split an argument (the `find
        # $(ROOT) ... -exec rm -rf` recipe made that a real hazard).
        stages = re.findall(r'sh "?\$\(ROOT\)/ci/checks\.sh"? ([a-z-]+)', recipe)
        assert stages, (
            f"Makefile target `{target}` is a gate target but never invokes "
            '`sh "$(ROOT)/ci/checks.sh" <stage>` — gates are canonical in checks.sh (D1)'
        )
        for stage in stages:
            assert stage in labels, (
                f"Makefile target `{target}` delegates to checks.sh stage `{stage}`, "
                f"which is not a dispatch label ({sorted(labels)}) — a dangling target"
            )
        assert not _GATE_TOOLS.search(recipe), (
            f"Makefile target `{target}` reconstructs a gate tool inline:\n{recipe}"
        )
    assert seen_gate_target, "no gate targets parsed from the Makefile — assertions vacuous"


def test_non_gate_targets_do_not_hide_gate_reconstructions() -> None:
    """`format` may run ruff format; nothing else in the exempt set may run a
    gate tool at all."""
    text = MAKEFILE.read_text(encoding="utf-8")
    for match in _GATE_RECIPE.finditer(text):
        target, recipe = match.group("target"), match.group("recipe")
        if target not in _NON_GATE_TARGETS or target == "format":
            continue
        assert not _GATE_TOOLS.search(recipe), (
            f"non-gate Makefile target `{target}` invokes a gate tool:\n{recipe}"
        )


# ── 2. The governance skill's decision summary must not go stale ─────────────
# Rot vector: a decision lands in docs/decision-log.md, the skill's summary
# section isn't updated, and future sessions operate on stale rules.

_ENTRY = re.compile(r"^(\d{4}-\d{2}-\d{2}) \| (DEC-\d+|RB-\d+[a-z]?|G-\d+) \|", re.M)


def test_skill_decision_section_is_fresh() -> None:
    skill_text = SKILL.read_text(encoding="utf-8")
    since_match = re.search(r"## Decisions since (\d{4}-\d{2}-\d{2})", skill_text)
    assert since_match, "skill lost its 'Decisions since <date>' section"
    since = since_match.group(1)

    entries = _ENTRY.findall(DECISION_LOG.read_text(encoding="utf-8"))
    assert entries, "decision log parsed to zero entries — the freshness check is vacuous"
    missing = [
        entry_id for date, entry_id in entries if date >= since and entry_id not in skill_text
    ]
    assert missing == [], (
        f"decision-log entries missing from the skill's decision section: {missing} — "
        "add at least one line naming each ID before proceeding with anything else"
    )


# ── 3. Every tracked file is governed or explicitly public ───────────────────
# Rot vector: a new file lands neither covered by the governed-path globs nor
# on the public-candidate allowlist, and ships somewhere it shouldn't. The
# matcher replicates the consuming hook's semantics (bash `case` with `**`→`*`
# == fnmatchcase after the same rewrite) — scripts/pre_push_scan.sh reads the
# same pyproject key.

PUBLIC_CANDIDATE_ALLOWLIST: Final = (
    # Root-level, deliberately public repo plumbing; everything else is
    # governed by default.
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    "CHANGELOG.md",
    "CLAUDE.md",
    "Makefile",
    "README.md",
    "pyproject.toml",
    "docs/*",
)


def _governed_globs() -> list[str]:
    # Load from the same config the pre-push hook reads — never a hand-kept
    # copy here (donor template rule).
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    globs = config["tool"]["orbital_drift"]["governance"]["governed_path_globs"]
    assert isinstance(globs, list) and globs, "governed_path_globs missing or empty"
    return [str(glob) for glob in globs]


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern.replace("**", "*")) for pattern in patterns)


def test_every_tracked_path_is_globbed_or_allowlisted() -> None:
    git = shutil.which("git")
    assert git, "git is required to enumerate tracked paths"
    proc = subprocess.run(
        [git, "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT,
    )
    tracked = [line for line in proc.stdout.splitlines() if line]
    assert tracked, "git ls-files returned nothing — the invariant would be vacuous"

    patterns = [*_governed_globs(), *PUBLIC_CANDIDATE_ALLOWLIST]
    uncovered = [path for path in tracked if not _matches_any(path, patterns)]
    assert uncovered == [], (
        f"tracked paths outside every governed glob and the public allowlist: {uncovered} "
        "— classify them (governed by default) rather than widening the allowlist"
    )


def test_matcher_is_not_vacuous() -> None:
    """Negative control: the invariant CAN fail. If a deliberately-unmatched
    path passes, the coverage assertion is satisfied by a matcher bug — the
    green-with-the-fix-removed failure mode."""
    patterns = [*_governed_globs(), *PUBLIC_CANDIDATE_ALLOWLIST]
    assert not _matches_any("some-new-root-file.xyz", patterns)


# ── 5. The decision log reads in chronological order ─────────────────────────
# Rot vector: entries get appended wherever the editing agent's cursor happened
# to be, and "the last line is the latest decision" — the way every human and
# every skill describes this file — quietly becomes false. It HAD become false:
# RB-006 (08-21) sat below RB-007/RB-008/RB-007a (08-22), which three separate
# reviewers flagged independently, because reading the tail of the file gave
# the wrong answer about what was decided most recently. A log whose order
# cannot be trusted has to be read in full to be read at all.
#
# Note what the freshness check in section 2 does NOT do: it presence-checks
# IDs, so it stayed green throughout. Ordering needs its own assertion.
#
# Non-DECREASING, not strictly increasing: several decisions are legitimately
# logged on the same day, and rule 6 ("owner approval given BEFORE work starts
# may be logged after") means same-day clusters are expected, not a smell.


def _chronology_regressions(entries: list[tuple[str, str]]) -> list[str]:
    """``(date, id)`` pairs where the date goes backwards, described in prose.

    ONE implementation, called by both the real check and its negative control.
    The control used to re-implement this comparison over a hardcoded list,
    which meant it guarded a copy rather than the logic: a regression in the
    real comparison, or in ``_ENTRY``, left it green.

    ISO-8601 dates compare lexicographically, so this is a plain string
    comparison — no date parsing, nothing to get wrong about time zones.
    """
    return [
        f"{previous_id} ({previous_date}) is followed by {entry_id} ({date})"
        for (previous_date, previous_id), (date, entry_id) in itertools.pairwise(entries)
        if date < previous_date
    ]


def test_decision_log_entries_are_in_chronological_order() -> None:
    entries = _ENTRY.findall(DECISION_LOG.read_text(encoding="utf-8"))
    assert entries, "decision log parsed to zero entries — the ordering check is vacuous"

    regressions = _chronology_regressions(entries)
    assert regressions == [], (
        "docs/decision-log.md is not in chronological order, so 'the last entry is the "
        f"latest decision' is false: {regressions}. Move the line, and change not one "
        "character of any entry's text — the text is the decision."
    )


#: The governance skill's summary bullets: ``- **RB-008a** (08-22): ...``. The
#: date is MM-DD there, not ISO — fine for ordering WITHIN a year, which is all
#: this list has ever spanned; a January entry under a December one would be a
#: false positive, and the fix then is to put the year in the bullet.
_SKILL_BULLET = re.compile(r"^- \*\*(DEC-\d+|RB-\d+[a-z]?|G-\d+)\*\* \((\d{2}-\d{2})\):", re.M)


def test_the_skill_decision_summary_lists_decisions_in_the_logs_order() -> None:
    """The skill is what agents read FIRST; a stale mirror outranks a fresh log.

    CLAUDE.md step 0 sends every agent to this skill before the log, so an
    inverted summary is read more often than the thing it summarises. The
    freshness check in section 2 does not help — it presence-checks IDs, so it
    stayed green while the skill listed RB-008 ABOVE RB-007a and the log listed
    them the other way round: the very defect section 5 exists to prevent,
    reproduced one file over.

    FULL SEQUENCE EQUALITY, not a date-order check. Two weaker properties were
    tried first and MEASURED useless against the real inversion: non-decreasing
    dates cannot separate RB-007a from RB-008 (same day), and "last bullet ==
    last log entry" passes as soon as a newer entry is appended to both. Only
    comparing the whole sequence catches a reordering in the middle, which is
    where it happened.

    The skill previously grouped ``G-0`` with the DEC entries rather than at its
    logged position; that grouping is what made an order check impossible, so it
    was removed. The summary now mirrors the log line for line, which is the
    only arrangement in which "read the skill instead" is safe advice.

    ID AND DATE, not the id alone. ``_SKILL_BULLET`` has always captured the
    bullet's ``MM-DD``; this check used to discard it, and MEASURED 2026-08-22
    at 11af312 that left a whole class of mirror drift invisible — rewriting
    ``- **RB-009** (08-22)`` to ``(08-19)`` reddened NOTHING in either
    governance suite, so the skill could date a decision to a day the log does
    not and every gate stayed green. A wrong date is the same defect as a wrong
    order: both answer "what was decided most recently" differently from the
    log. Comparing the pair costs one slice, because the regex already extracts
    the date. The log's dates are ISO and the bullets' are ``MM-DD``, hence
    ``[5:]`` — the same within-one-year assumption ``_SKILL_BULLET``'s own
    comment documents, with the same fix if it ever breaks (year in the bullet).

    STILL NOT COVERED, deliberately: drift in the TEXT of a bullet or of a
    logged entry. Word-level agreement between a one-line summary and a
    paragraph-long entry is not a mechanical property, and the log's own rule
    ("change not one character of any entry's text") is review-enforced. That
    gap is real; it is not this check's shape.
    """
    bullets = _SKILL_BULLET.findall(SKILL.read_text(encoding="utf-8"))
    assert len(bullets) > 5, f"parsed only {len(bullets)} skill bullets — the check is vacuous"

    skill_text = SKILL.read_text(encoding="utf-8")
    since_match = re.search(r"## Decisions since (\d{4}-\d{2}-\d{2})", skill_text)
    assert since_match, "skill lost its 'Decisions since <date>' section"
    since = since_match.group(1)

    logged = [
        (entry_id, date[5:])
        for date, entry_id in _ENTRY.findall(DECISION_LOG.read_text(encoding="utf-8"))
        if date >= since
    ]
    assert logged, "decision log parsed to zero in-range entries"

    assert bullets == logged, (
        "the governance skill's decision summary does not match the decision log's "
        f"(id, date) sequence.\n  skill: {bullets}\n  log:   {logged}\n"
        "Agents read the skill first (CLAUDE.md step 0), so a divergent order — or a "
        "bullet dated to a day the log does not — answers 'what was decided most "
        "recently' differently from the log."
    )


def test_the_chronology_check_can_actually_fail() -> None:
    """Negative control for the check above — through the SAME code path.

    Drives ``_ENTRY`` and ``_chronology_regressions``, the two things the real
    check is made of, over a synthetic two-line log in the file's own format.
    That is what makes it a control: if ``_ENTRY`` stops capturing the date
    group every comparison silently becomes ``"" < ""`` and the real check
    passes on any file at all, and this test is what notices. An earlier
    version hardcoded the parsed pairs and re-implemented the comparison
    inline, so it could not have noticed either failure.
    """
    out_of_order = (
        "2026-08-22 | RB-008 | a later decision | agent\n"
        "2026-08-21 | RB-006 | an earlier decision, wrongly placed after it | agent\n"
    )
    entries = _ENTRY.findall(out_of_order)
    assert len(entries) == 2, (
        f"_ENTRY no longer parses the decision log's own line format: {entries}"
    )
    assert _chronology_regressions(entries) == [
        "RB-008 (2026-08-22) is followed by RB-006 (2026-08-21)"
    ]

    in_order = (
        "2026-08-21 | RB-006 | an earlier decision | agent\n"
        "2026-08-22 | RB-008 | a later decision | agent\n"
    )
    assert _chronology_regressions(_ENTRY.findall(in_order)) == [], (
        "the correctly-ordered case must produce no regressions, or the check "
        "would fail on every log including a valid one"
    )
