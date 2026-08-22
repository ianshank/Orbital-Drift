"""Governance meta-tests — tests that watch the PROCESS, not the product.

The donor kit's core insight: every hand-maintained governance artifact rots
unless a test fails when it does (adopt-governance-kit design.md). Seven rot
vectors are covered here and in test_zero_skip_guard.py:

1. Makefile/checks.sh divergence (this file, design D1 — direction inverted
   from the donor template: HERE checks.sh is canonical and the Makefile is the
   thin front-end).
2. A stale governance-skill decision summary (this file).
3. A tracked file neither governed nor explicitly public (this file).
4. A zero-skip guard nobody has watched fire (test_zero_skip_guard.py).
5. An entry quoting a decision-log rule the log never states (this file).
6. An in-code decision-log citation left behind by the record it points at
   (this file).
7. An entry's file:line citation that no longer resolves to the text it claims
   (this file).
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Final, NamedTuple

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


# ── 4. No entry quotes a log rule the log does not state ─────────────────────
# Rot vector: an entry attributes its reasoning to "the log's rule '<quote>'",
# the quote is nowhere in the RULES block above the entries, and a later session
# enforces — or is refused under — a rule that never existed. MEASURED instance:
# RB-008c cited "change not one character of any entry's text" as a log rule; a
# repo-wide git grep for that phrase returned that citation and nothing else.
# The constraint is one-directional: an entry may STATE a new rule (the RULES
# block is amended in the same change), but it may not quote the block for text
# the block does not contain.

#: Straight AND curly quote marks, written as escapes so this source stays
#: ASCII — ruff RUF001 rejects a literal curly quote inside a string.
_QUOTE_MARKS: Final = "'\"\u2018\u2019\u201c\u201d"
#: `rules?[ ,]+` and IGNORECASE because `RULE 'x'`, `rules 'x'` and `rule, 'x'`
#: are the same claim as `rule 'x'`. MEASURED at 7ec4e1c before keeping the
#: widening: over the whole log — entries AND rules block — the widened form
#: extracts exactly as much as the narrow one did (nothing), so it introduces no
#: false positive on prose like `namespace rule (design D7)` or `per rule 4
#: (decide, then execute)`. Number tolerance (`rule 2, 'x'`) is deliberately NOT
#: attempted: `rule <n>` runs straight into the log's live `rule 2 (file:line)`
#: citations, which check 6 resolves rather than matches.
_QUOTED_RULE = re.compile(
    f"rules?[ ,]+[{_QUOTE_MARKS}]([^{_QUOTE_MARKS}]{{4,}})[{_QUOTE_MARKS}]", re.IGNORECASE
)


def _normalise(text: str) -> str:
    """Fold the markdown the RULES block carries but a quotation would not."""
    return " ".join(text.replace("*", "").replace("`", "").split()).lower()


def _quoted_rule_citations(text: str) -> list[str]:
    """Every ``rule '<quote>'`` fragment in ``text``, normalised for comparison."""
    return [_normalise(match) for match in _QUOTED_RULE.findall(text)]


def _rules_block(log_text: str) -> str:
    """The blockquoted RULES preamble: every line starting with ``>``. Entries
    never do, so this cannot accidentally include an entry's own prose."""
    lines = [line[1:] for line in log_text.splitlines() if line.startswith(">")]
    assert lines, "docs/decision-log.md lost its blockquoted RULES block"
    return _normalise("\n".join(lines))


def test_quoted_rule_citation_parser_has_positive_and_negative_controls() -> None:
    """The invariant below can only bite if the parser SEES a fabricated quote
    and IGNORES the log's ordinary prose. Both directions, on synthetic text."""
    assert _quoted_rule_citations("the log's rule 'change not one character' applies") == [
        "change not one character"
    ]
    assert _quoted_rule_citations('per the rule "decide, then execute" above') == [
        "decide, then execute"
    ]
    # A citation is not always lowercase, singular, or space-separated, and the
    # invariant is about what an entry CLAIMS, not about its typography.
    for evasion, quoted in (
        ("the log's RULE 'change not one character' applies", "change not one character"),
        ("per the rules 'never spoof a gate' above", "never spoof a gate"),
        ("the rule, 'decide, then execute' is clear", "decide, then execute"),
    ):
        assert _quoted_rule_citations(evasion) == [quoted], evasion

    for benign in (
        "logged after per rule 6, landing with the commits it authorizes",
        "the operator's disposition was 'record honestly', NOT 'widen the glob'",
        "namespace rule (design D7) - this file owns DEC-/RB-/G- IDs only",
        "logged BEFORE execution per rule 4 (decide, then execute)",
    ):
        assert _quoted_rule_citations(benign) == [], benign

    # The `quote in rules` branch of the invariant below is never exercised by
    # the real log (no entry quotes a rule today), so nothing else notices if
    # _rules_block stops finding the block — and a _rules_block that returns the
    # wrong text turns every future LEGITIMATE citation into a false BLOCK.
    assert "decide, then execute" in _rules_block(DECISION_LOG.read_text(encoding="utf-8")), (
        "_rules_block no longer returns the log's rule 4 — the invariant below would "
        "BLOCK a legitimate citation, and nothing else would notice"
    )


def test_no_entry_quotes_a_log_rule_the_log_does_not_state() -> None:
    log_text = DECISION_LOG.read_text(encoding="utf-8")
    rules = _rules_block(log_text)
    entries = "\n".join(line for line in log_text.splitlines() if _ENTRY.match(line))
    assert entries, "decision log parsed to zero entries — the check would be vacuous"

    fabricated = [quote for quote in _quoted_rule_citations(entries) if quote not in rules]
    assert fabricated == [], (
        f"decision-log entries quote rules the RULES block does not state: {fabricated} — "
        "cite a rule that exists (by number), or amend the RULES block in the same change"
    )


# ── 5. An in-code decision citation resolves to an entry about that file ─────
# Rot vector: a record moves from one entry to another, the in-code back-
# reference does not move with it, and the comment sends its reader to an entry
# that says nothing about the code it annotates. MEASURED instance: ci/checks.sh
# cited RB-008b for the multi-argument dispatch defect, while RB-008b records a
# workflow `schedule:` move and two covcheck.py edits; the defect is recorded by
# RB-008c(c). Scoped to ci/checks.sh on purpose — scripts/*.sh cite the DONOR
# kit's RB numbers ("donor kit RB-023"), which this log does not own (D7).

_CITED_ENTRY = re.compile(r"\bRB-\d+[a-z]?\b")


def _entry_line(log_text: str, entry_id: str) -> str:
    for line in log_text.splitlines():
        match = _ENTRY.match(line)
        if match and match.group(2) == entry_id:
            return line
    raise AssertionError(f"{entry_id} is cited in ci/checks.sh but is not in the decision log")


def _ids_cited_in_comments(source: str) -> set[str]:
    return {
        entry_id
        for line in source.splitlines()
        if line.lstrip().startswith("#")
        for entry_id in _CITED_ENTRY.findall(line)
    }


def test_every_decision_id_cited_in_checks_sh_names_that_file() -> None:
    log_text = DECISION_LOG.read_text(encoding="utf-8")
    cited = _ids_cited_in_comments(CHECKS.read_text(encoding="utf-8"))
    assert cited, "no decision-log citation found in ci/checks.sh — the check would be vacuous"

    dangling = sorted(
        entry_id for entry_id in cited if "ci/checks.sh" not in _entry_line(log_text, entry_id)
    )
    assert dangling == [], (
        f"ci/checks.sh cites {dangling}, whose log entries never mention this file — "
        "cite the entry that records the item (a record that moves between entries takes "
        "its in-code back-reference with it)"
    )


def test_citation_check_discriminates() -> None:
    """Negative control: "the entry mentions ci/checks.sh" must not be true of
    every entry, or the invariant above is satisfied by a lookup bug."""
    log_text = DECISION_LOG.read_text(encoding="utf-8")
    assert "ci/checks.sh" not in _entry_line(log_text, "RB-009")


# ── 6. Every file:line citation in an entry resolves to what it claims ───────
# Rot vector: an entry backs its reasoning with a `file.md:12-14` pointer, the
# cited file is renamed or its lines shift, and the citation sends its reader to
# unrelated text — or to nothing at all. Check 4 above cannot see this class: it
# matches `rule '<quote>'` and nothing else, while the log's live citations are
# written `rule 2 (decision-log.md:14-15)` (no quote mark) and as a file:line
# followed by a quotation (no preceding word "rule"). MEASURED at 7ec4e1c: over
# 18 entries containing 12 occurrences of "rule(s)", check 4 extracted ZERO
# quotes and opened ZERO files, so it passed by finding nothing to look at. This
# form needs range RESOLUTION, not a wider regex — a wider regex still opens no
# file — so this check opens each cited file and reads the cited lines.

#: A `path.md:12` or `path.md:12-14` pointer, as the log writes them.
_CITATION = re.compile(r"(?P<path>[\w./-]+\.md):(?P<start>\d+)(?:-(?P<end>\d+))?")

#: A quotation, matched by pairing each opening mark with ITS OWN closing mark
#: rather than with any mark: RB-008c quotes single-quoted text that CONTAINS
#: double quotes, and check 4's `[marks](...)[marks]` character-class shape
#: would cut that at the first inner `"`. The word look-arounds stop a
#: word-internal apostrophe ("the operator's disposition") opening a quotation.
#: Curly marks are escapes, not literals, so this source stays ASCII (RUF001).
_QUOTED_FRAGMENT = re.compile(
    r"(?<!\w)'([^']{4,})'(?!\w)"
    r'|(?<!\w)"([^"]{4,})"(?!\w)'
    "|\u2018([^\u2019]{4,})\u2019"
    "|\u201c([^\u201d]{4,})\u201d"
)

#: End of the clause a citation lives in. A quotation beyond one of these is
#: attributed to no citation. Under-attributing is the safe direction: it can
#: only weaken this check, never manufacture a false BLOCK against an entry
#: whose quotation was never a claim about the cited lines.
_CLAUSE_END = re.compile(r"[.;] ")


class _Citation(NamedTuple):
    """One file:line pointer plus the quotation attached to it, if any."""

    entry: str
    text: str
    path: str
    start: int
    end: int
    quote: str | None


def _entry_id(line: str) -> str:
    match = _ENTRY.match(line)
    return match.group(2) if match else "(line)"


def _resolve_cited_path(raw: str) -> Path | None:
    """Resolve a cited path. THE RULE, stated so that it is not an accident:
    repository root FIRST, then relative to the CITING file's own directory
    (``docs/``, the directory of docs/decision-log.md). Both forms are live in
    the log — `.claude/skills/log-decision/SKILL.md:42-44` is repo-root
    relative, while `decision-log.md:14-15` is a same-directory reference
    written without its `docs/` prefix. Root first, so a sibling name can never
    shadow a repo-root file of the same name. A path that escapes the
    repository is not a citation this log can make: refused, not resolved.
    """
    for candidate in (REPO_ROOT / raw, DECISION_LOG.parent / raw):
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(REPO_ROOT):
            return resolved
    return None


def _attached_quote(span: str) -> str | None:
    """The quotation a citation vouches for: the first one in ``span`` (the text
    between the citation and the next citation), provided no clause end
    separates them."""
    match = _QUOTED_FRAGMENT.search(span)
    if match is None or _CLAUSE_END.search(span[: match.start()]):
        return None
    return next(group for group in match.groups() if group is not None)


def _file_line_citations(text: str) -> list[_Citation]:
    """Every citation in ``text``, each carrying the quotation that follows it
    before the next citation on the same line."""
    citations: list[_Citation] = []
    for line in text.splitlines():
        matches = list(_CITATION.finditer(line))
        for index, match in enumerate(matches):
            stop = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            start = int(match.group("start"))
            citations.append(
                _Citation(
                    entry=_entry_id(line),
                    text=match.group(0),
                    path=match.group("path"),
                    start=start,
                    end=int(match.group("end") or start),
                    quote=_attached_quote(line[match.end() : stop]),
                )
            )
    return citations


def _citation_defects(text: str) -> list[str]:
    """One line per citation that does not resolve to what it claims — a missing
    file, a range outside the file, or a quotation the cited lines do not say."""
    defects: list[str] = []
    for cite in _file_line_citations(text):
        where = f"{cite.entry} cites {cite.text}"
        target = _resolve_cited_path(cite.path)
        if target is None:
            defects.append(f"{where}: no such file at the repo root or beside the log")
            continue
        if cite.start < 1 or cite.start > cite.end:
            defects.append(f"{where}: line range is empty or starts below 1")
            continue
        lines = target.read_text(encoding="utf-8").splitlines()
        if cite.end > len(lines):
            defects.append(f"{where}: range ends past EOF ({len(lines)} lines)")
            continue
        if cite.quote is None:
            continue
        if _normalise(cite.quote) not in _normalise("\n".join(lines[cite.start - 1 : cite.end])):
            defects.append(f"{where}: the cited lines do not say {cite.quote!r}")
    return defects


def test_every_file_line_citation_in_the_log_resolves_to_what_it_claims() -> None:
    log_text = DECISION_LOG.read_text(encoding="utf-8")
    entries = "\n".join(line for line in log_text.splitlines() if _ENTRY.match(line))
    citations = _file_line_citations(entries)
    assert citations, (
        "no file:line citation found in decision-log entries — the check would be vacuous, "
        "which is the defect it exists to end: a green result that opened no file"
    )

    defects = _citation_defects(entries)
    assert defects == [], (
        "decision-log entries carry file:line citations that do not resolve: "
        + "; ".join(defects)
        + " — a citation moves with the text it points at; quote what the lines say"
    )


def test_citation_resolver_has_positive_and_negative_controls(tmp_path: Path) -> None:
    """Both directions on synthetic text. The live citation forms resolve, and
    each way a citation can be wrong is reported FOR ITS OWN REASON — otherwise
    the invariant above is satisfied by a parser that resolves nothing."""
    outside = tmp_path / "outside.md"
    outside.write_text("not part of this repository\n", encoding="utf-8")
    assert _resolve_cited_path(str(outside)) is None, "a citation may not escape the repo"

    live = "rule 2 (decision-log.md:14-15) and .claude/skills/log-decision/SKILL.md:42-44 agree"
    assert _file_line_citations(live) != []
    assert _citation_defects(live) == []
    assert _citation_defects("decision-log.md:23-24 says 'Decide, then execute.'") == []

    for text, reason in (
        ("cited no-such-document.md:1-2 here", "no such file"),
        ("decision-log.md:9000-9001 says so", "past EOF"),
        ("decision-log.md:24-23 says so", "range is empty"),
        ("decision-log.md:0-2 says so", "range is empty"),
        ("decision-log.md:23-24 says 'no rule of that description'", "do not say"),
    ):
        defects = _citation_defects(text)
        assert len(defects) == 1, (text, defects)
        assert reason in defects[0], (text, defects)


def test_attached_quote_pairs_marks_and_respects_clause_ends() -> None:
    """The nesting RB-008c's live citation has (single-quoted text CONTAINING
    double quotes) survives extraction; ordinary prose does not become a claim
    about the cited lines."""
    inner = 'Never bundle "we decided X" and "X is done" into one entry.'
    assert _attached_quote(f" — '{inner}'") == inner, "a quotation was cut at an inner quote mark"
    assert _attached_quote(" the operator's disposition was recorded") is None, (
        "a word-internal apostrophe opened a quotation"
    )
    assert _attached_quote(" ends here. Then 'an unrelated quotation'") is None, (
        "a quotation past the citation's clause was attributed to the citation"
    )
