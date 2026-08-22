"""Governance meta-tests — tests that watch the PROCESS, not the product.

The donor kit's core insight: every hand-maintained governance artifact rots
unless a test fails when it does (adopt-governance-kit design.md). Four rot
vectors are covered here and in test_zero_skip_guard.py:

1. Makefile/checks.sh divergence (this file, design D1 — direction inverted
   from the donor template: HERE checks.sh is canonical and the Makefile is the
   thin front-end).
2. A stale governance-skill decision summary (this file).
3. A tracked file neither governed nor explicitly public (this file).
4. A zero-skip guard nobody has watched fire (test_zero_skip_guard.py).
"""

from __future__ import annotations

import fnmatch
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
