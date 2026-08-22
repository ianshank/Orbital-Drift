"""Deterministic structural validation of .claude/agents and .claude/skills.

"Enterprise fashion" governance-as-code for the agent/skill roster: every
artifact carries the metadata the orchestration layer depends on, no agent is
orphaned from CLAUDE.md's delegation table, no skill's frontmatter is
malformed, and none of this depends on wall-clock time, network access, or
call order — the same tree produces the same verdict every time, which is
what makes it fit to gate a merge rather than merely inform a reviewer.

Parsed with a regex, not a YAML library: PyYAML is not a dependency of this
project (the same reasoning as tests/unit/test_version_pins.py), and the
frontmatter shape here is fixed and simple enough that a library would add a
dependency to solve a problem three lines of regex already solve.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, NamedTuple

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
AGENTS_DIR: Final = REPO_ROOT / ".claude" / "agents"
SKILLS_DIR: Final = REPO_ROOT / ".claude" / "skills"
CLAUDE_MD: Final = REPO_ROOT / "CLAUDE.md"

#: Agents whose scope is "every artifact" rather than a `[A:name]` task tag —
#: CLAUDE.md names them in prose, not in the delegation table's left column,
#: so the roster cross-check looks for their bare name instead of a tag.
UNTAGGED_REVIEW_AGENTS: Final[frozenset[str]] = frozenset(
    {"spec-guardian", "adversarial-reviewer", "spec-implementer"}
)

_FRONTMATTER = re.compile(r"^---\n(?P<body>.*?)\n---\n", re.DOTALL)
_FIELD = re.compile(r"^(?P<key>[a-zA-Z_]+):[ \t]*(?P<value>.*)$", re.MULTILINE)


class Frontmatter(NamedTuple):
    fields: dict[str, str]
    raw: str


def _parse_frontmatter(path: Path) -> Frontmatter:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    assert match, f"{path.relative_to(REPO_ROOT)}: no --- frontmatter block at the top of the file"
    fields = {
        m.group("key"): m.group("value").strip() for m in _FIELD.finditer(match.group("body"))
    }
    return Frontmatter(fields=fields, raw=match.group("body"))


def _agent_paths() -> list[Path]:
    paths = sorted(AGENTS_DIR.glob("*.md"))
    assert paths, f"{AGENTS_DIR.relative_to(REPO_ROOT)} has no agents — the suite would be vacuous"
    return paths


def _skill_paths() -> list[Path]:
    paths = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert paths, f"{SKILLS_DIR.relative_to(REPO_ROOT)} has no skills — the suite would be vacuous"
    return paths


AGENT_PATHS: Final = _agent_paths()
SKILL_PATHS: Final = _skill_paths()
CLAUDE_MD_TEXT: Final = CLAUDE_MD.read_text(encoding="utf-8")


# --- agents ------------------------------------------------------------


@pytest.mark.parametrize("path", AGENT_PATHS, ids=lambda p: p.stem)
def test_agent_has_required_frontmatter_fields(path: Path) -> None:
    fields = _parse_frontmatter(path).fields
    for required in ("name", "description", "tools"):
        assert fields.get(required), (
            f"{path.name}: frontmatter is missing a non-empty '{required}:' field"
        )


@pytest.mark.parametrize("path", AGENT_PATHS, ids=lambda p: p.stem)
def test_agent_name_matches_its_filename(path: Path) -> None:
    """A mismatched name is how an orphaned or renamed agent goes unnoticed —
    the roster table below is keyed on the frontmatter name, not the path."""
    fields = _parse_frontmatter(path).fields
    assert fields["name"] == path.stem, (
        f"{path.name}: frontmatter name {fields['name']!r} does not match "
        f"the filename stem {path.stem!r}"
    )


@pytest.mark.parametrize("path", AGENT_PATHS, ids=lambda p: p.stem)
def test_agent_tools_field_is_a_clean_comma_separated_list(path: Path) -> None:
    """Catches the easy-to-introduce typo: a trailing comma, doubled comma, or
    inconsistent spacing silently gives an agent zero or a malformed tool
    grant. The house style is "A, B, C" (comma-space); canonicalizing and
    comparing catches any deviation without hand-writing a spacing regex."""
    tools = _parse_frontmatter(path).fields["tools"]
    entries = [entry.strip() for entry in tools.split(",")]
    assert all(entries), (
        f"{path.name}: tools field {tools!r} has an empty entry (trailing comma or doubled comma)"
    )
    assert ", ".join(entries) == tools, (
        f"{path.name}: tools field {tools!r} is not in the house style 'A, B, C' "
        f"(canonical form: {', '.join(entries)!r})"
    )
    assert len(entries) == len(set(entries)), f"{path.name}: tools field lists a duplicate: {tools}"


@pytest.mark.parametrize("path", AGENT_PATHS, ids=lambda p: p.stem)
def test_agent_is_referenced_in_claude_md(path: Path) -> None:
    """Every agent must be reachable from the orchestration layer — an agent
    file nobody's delegation table points at is dead configuration, the exact
    class of rot vulture cannot see because it is markdown, not Python."""
    name = _parse_frontmatter(path).fields["name"]
    if name in UNTAGGED_REVIEW_AGENTS:
        assert name in CLAUDE_MD_TEXT, (
            f"{name}: an every-artifact review agent must be named in CLAUDE.md"
        )
        return
    assert f"[A:{name}]" in CLAUDE_MD_TEXT, (
        f"{name}: no `[A:{name}]` delegation tag in CLAUDE.md's roster table"
    )


def test_every_claude_md_delegation_tag_has_a_live_agent() -> None:
    """The inverse direction: a tag in CLAUDE.md pointing at a deleted agent
    file is a dangling reference — this caught the D5 peer-reviewer retirement
    if CLAUDE.md's prose had not been updated in the same PR."""
    known = {_parse_frontmatter(path).fields["name"] for path in AGENT_PATHS}
    tagged = set(re.findall(r"\[A:([a-z][a-z-]*)\]", CLAUDE_MD_TEXT))
    dangling = tagged - known
    assert not dangling, f"CLAUDE.md tags agents that do not exist: {sorted(dangling)}"


def test_no_duplicate_agent_names() -> None:
    names = [_parse_frontmatter(path).fields["name"] for path in AGENT_PATHS]
    assert len(names) == len(set(names)), f"duplicate agent name across files: {names}"


def test_agent_description_names_no_dangling_supersession() -> None:
    """adversarial-reviewer's description says it supersedes peer-reviewer.
    If peer-reviewer.md is ever re-added without updating that sentence, the
    two agents would silently overlap — the exact class of duplication D5
    rejected. This does not forbid the word; it forbids the word AND the file
    coexisting."""
    superseded_mentions = {
        path.stem: match.group(1)
        for path in AGENT_PATHS
        if (match := re.search(r"[Ss]upersedes ([a-z][a-z-]*)", path.read_text(encoding="utf-8")))
    }
    for agent, superseded in superseded_mentions.items():
        assert not (AGENTS_DIR / f"{superseded}.md").is_file(), (
            f"{agent}.md claims to supersede {superseded}, but {superseded}.md still exists"
        )


# --- skills --------------------------------------------------------------


@pytest.mark.parametrize("path", SKILL_PATHS, ids=lambda p: p.parent.name)
def test_skill_has_required_frontmatter_fields(path: Path) -> None:
    fields = _parse_frontmatter(path).fields
    for required in ("name", "description"):
        assert fields.get(required), (
            f"{path.parent.name}/SKILL.md: frontmatter is missing a non-empty '{required}:' field"
        )


@pytest.mark.parametrize("path", SKILL_PATHS, ids=lambda p: p.parent.name)
def test_skill_name_matches_its_directory(path: Path) -> None:
    fields = _parse_frontmatter(path).fields
    assert fields["name"] == path.parent.name, (
        f"{path.parent.name}/SKILL.md: frontmatter name {fields['name']!r} does not match "
        f"the directory name {path.parent.name!r}"
    )


@pytest.mark.parametrize("path", SKILL_PATHS, ids=lambda p: p.parent.name)
def test_skill_description_is_a_trigger_not_a_summary(path: Path) -> None:
    """A description with no imperative/trigger language ('use when', 'run
    when') degrades to a label a human has to already know to reach for —
    the whole point of a skill is that its description is how it gets found."""
    description = _parse_frontmatter(path).fields["description"].lower()
    trigger_words = (
        "use when",
        "use whenever",
        "use for",
        "run when",
        "trigger",
        "consult before",
        "consult when",
        "invoke when",
    )
    assert any(word in description for word in trigger_words), (
        f"{path.parent.name}: description has no trigger phrase "
        f"({', '.join(trigger_words)}) — it will not be reliably invoked"
    )


def test_no_duplicate_skill_names() -> None:
    names = [_parse_frontmatter(path).fields["name"] for path in SKILL_PATHS]
    assert len(names) == len(set(names)), f"duplicate skill name across files: {names}"


def test_matcher_is_not_vacuous() -> None:
    """Negative control: a frontmatter parse that always 'succeeds' with an
    empty dict would satisfy every assertion above for the wrong reason."""
    fields = _parse_frontmatter(AGENTS_DIR / "spec-guardian.md").fields
    assert fields.get("name") == "spec-guardian"
    assert fields.get("tools"), "tools field parsed as empty on a file known to declare tools"
