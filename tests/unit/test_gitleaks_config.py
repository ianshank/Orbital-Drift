"""Regression tests that pin the shape of ``ci/gitleaks.toml``.

Every assertion here encodes a defect that ALREADY HAPPENED in this repository.
None of them is hypothetical, and none of them was caught by a test at the time
— each was found by reading the file, which is not a control that survives the
next edit.

The one that matters most is :func:`test_no_global_allowlist_carries_paths`.
In gitleaks v8 a GLOBAL allowlist's ``paths`` is a WALK filter, not a finding
filter: matching files and directories are pruned from the traversal before
their bytes are read, so every rule — including all ~150 defaults — is switched
off for them. An earlier revision of this file carried

    [[allowlists]]
    regexes = [...stopwords...]
    paths   = ['''^docs/''', '''^specs/''', ...]

meaning "let prose quote a dummy credential". Measured effect: a byte-identical
AWS key was CAUGHT in ``infra/k3s/`` and MISSED in ``docs/runbooks/``,
``docs/incidents/``, ``specs/``, ``.specify/`` and ``CLAUDE.md`` — roughly 59%
of the repository, including the two directories Constitution VI *requires*
operators to write incident and runbook notes into.

That was fixed by hand. Re-adding a single ``paths`` key would restore the blind
spot in full, and until this file existed the entire suite stayed green while it
did. The comment at the top of ``ci/gitleaks.toml`` asks the next editor not to;
this test is what happens when they do it anyway.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
GITLEAKS_TOML: Final = REPO_ROOT / "ci" / "gitleaks.toml"

RAW: Final[str] = GITLEAKS_TOML.read_text(encoding="utf-8")
CONFIG: Final[dict[str, Any]] = tomllib.loads(RAW)

GLOBAL_ALLOWLISTS: Final[list[dict[str, Any]]] = list(CONFIG.get("allowlists", []))
RULES: Final[list[dict[str, Any]]] = list(CONFIG.get("rules", []))

# The placeholder-interpolation exemption. Identified by the shape of its regex
# rather than by list position, so reordering the file does not break the test
# and deleting the entry does not silently pass it.
INTERPOLATION_MARKER: Final = r"\$\{"


def _interpolation_allowlist() -> dict[str, Any]:
    matches = [
        entry
        for entry in GLOBAL_ALLOWLISTS
        if any(INTERPOLATION_MARKER in regex for regex in entry.get("regexes", []))
    ]
    assert len(matches) == 1, (
        f"expected exactly one global allowlist for ${{VAR}} interpolation, found {len(matches)}"
    )
    return matches[0]


def test_config_parses_as_toml() -> None:
    """A malformed config makes gitleaks exit 1 naming a file nobody can find.

    ``ci/checks.sh`` hands the working-tree scan a generated overlay via
    ``GITLEAKS_CONFIG_TOML``, so a parse error is reported against a path that
    exists nowhere on disk. Catching it here, where the failure names a real
    file and a real line, is worth the two lines it costs.
    """
    assert CONFIG["title"] == "Orbital-Drift"
    assert CONFIG["extend"]["useDefault"] is True


@pytest.mark.parametrize("index", range(len(GLOBAL_ALLOWLISTS)))
def test_no_global_allowlist_carries_paths(index: int) -> None:
    """A ``paths`` key on a GLOBAL allowlist prunes the walk, not the findings.

    This is the ~59% blind spot described in the module docstring. Parametrised
    per entry so the failure names which block regrew the key.
    """
    entry = GLOBAL_ALLOWLISTS[index]
    description = str(entry.get("description", "<no description>")).strip().splitlines()[0]
    assert "paths" not in entry, (
        f"global [[allowlists]] #{index} ({description!r}) has a `paths` key. "
        "In gitleaks v8 that prunes matching files from the traversal before any "
        "rule reads them, disabling EVERY rule for those paths — not just this "
        "block's. Attach the exemption to the rule that misfires via "
        "[[rules.allowlists]] instead. See the header of ci/gitleaks.toml."
    )


def test_at_least_one_global_allowlist_exists() -> None:
    """Guard the guard: ``test_no_global_allowlist_carries_paths`` is
    parametrised over the allowlists, so if they all vanished it would generate
    zero test cases and report success by generating nothing at all.
    """
    assert GLOBAL_ALLOWLISTS, "ci/gitleaks.toml has no [[allowlists]] blocks to check"


def test_interpolation_allowlist_does_not_target_the_match() -> None:
    """``regexTarget = "match"`` made this exemption far wider than intended.

    With ``regexTarget = "match"`` the regex is applied to the whole matched
    region rather than to the extracted secret, so any finding that merely
    CONTAINED a ``${VAR}`` anywhere was exempted::

        postgres_password: "${NODE_A_PREFIX}Sup3rSecretRealPassword"

    The default target is the secret itself, which is what "this value is a
    placeholder" actually means.
    """
    entry = _interpolation_allowlist()
    assert "regexTarget" not in entry, (
        "the ${VAR} allowlist sets regexTarget="
        f"{entry.get('regexTarget')!r}; it must use the default (the extracted "
        "secret), or it exempts real secrets that sit next to a placeholder"
    )


def test_interpolation_allowlist_regex_is_anchored() -> None:
    """Unanchored, the pattern matches a substring and exempts too much."""
    entry = _interpolation_allowlist()
    regexes = list(entry["regexes"])
    assert regexes == [r"^\$\{[A-Za-z0-9_]+\}$"], (
        f"the ${{VAR}} allowlist regex must be anchored at both ends; found {regexes}. "
        "Unanchored it exempts any secret that merely contains an interpolation."
    )

    # And prove the anchors do what the docstring claims, against the engine's
    # own semantics rather than by inspection.
    pattern = re.compile(regexes[0])
    assert pattern.fullmatch("${NODE_A_LAN_IP}"), "a bare placeholder must still be exempt"
    assert pattern.match("${NODE_A_PREFIX}Sup3rSecretRealPassword") is None, (
        "a real secret prefixed by a placeholder must NOT be exempt"
    )


def test_every_rule_allowlist_is_scoped_not_global() -> None:
    """Per-rule exemptions must stay per-rule: no ``paths`` there either.

    ``[[rules.allowlists]]`` is evaluated per finding and prunes nothing, which
    is exactly why exemptions belong there — but a ``paths`` key inside one is
    still a filename-shaped exemption, and the reviewer of the next PR should
    have to argue for it rather than inherit it.
    """
    for rule in RULES:
        for allowlist in rule.get("allowlists", []):
            assert "paths" not in allowlist, (
                f"rule {rule.get('id')!r} has a [[rules.allowlists]] with a `paths` key; "
                "scope the exemption by stopwords or an anchored regex instead"
            )


def test_config_names_no_container_image() -> None:
    """The gitleaks image reference lives in ``ci/versions.env``, once.

    A version written into a comment here is outside the reach of
    ``tests/unit/test_version_pins.py``, so it is the one copy of the pin that
    can go stale unnoticed — and a comment that names the wrong scanner version
    is worse than no comment, because it is read as authoritative.
    """
    references = re.findall(r"(?:ghcr\.io/)?gitleaks/gitleaks[:@][A-Za-z0-9._:@-]+", RAW)
    assert not references, (
        f"ci/gitleaks.toml names container image(s) {references}; "
        "the pin belongs in ci/versions.env (GITLEAKS_IMAGE) only"
    )


def test_every_rule_has_an_id_and_a_description() -> None:
    """A finding is only actionable if it names the rule that produced it.

    The escalation procedure in this file's header tells the operator to add a
    ``stopwords`` entry to "the rule that fired", which requires the rule to
    have printed an id.
    """
    for index, rule in enumerate(RULES):
        assert rule.get("id"), f"[[rules]] #{index} has no id"
        assert rule.get("description"), f"rule {rule['id']!r} has no description"


def test_targeted_allowlist_names_only_rules_that_exist() -> None:
    """``targetRules`` referring to a deleted rule silently exempts nothing.

    That direction is safe, but it is also invisible: the block keeps claiming
    to scope an exemption that no longer applies, and the next person to widen
    it does so believing it is already narrow.
    """
    known = {str(rule["id"]) for rule in RULES}
    for entry in GLOBAL_ALLOWLISTS:
        for target in entry.get("targetRules", []):
            assert target in known, (
                f"global allowlist targetRules names {target!r}, which is not a rule "
                f"defined in this file (known: {sorted(known)})"
            )


def test_escalation_procedure_is_documented_here() -> None:
    """The only sanctioned response to a false positive is written down.

    ``docs/runbooks/`` is empty at T001, and this project's own rules forbid a
    gate-owning agent from inventing runbook prose. The procedure therefore
    lives next to the mechanism it describes, and README.md points at it. If
    this header is ever trimmed, the operator's first false positive is answered
    by whatever Stack Overflow suggests — which is ``--no-verify``.
    """
    header = RAW.split("title =", 1)[0]
    for required in ("stopwords", "--no-verify", "SKIP=gitleaks"):
        assert required in header, (
            f"the false-positive escalation section in ci/gitleaks.toml no longer "
            f"mentions {required!r}"
        )
