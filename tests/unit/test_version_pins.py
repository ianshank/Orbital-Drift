"""Meta-test: every version pin agrees across all three of its homes.

A pin in this repo is written down in up to three places:

* ``ci/versions.env``          — the single source of truth, sourced by
  ``ci/checks.sh`` and read by ``.github/workflows/ci.yml``;
* ``pyproject.toml``           — ``[project.optional-dependencies].dev``, which
  is what ``pip install -e ".[dev]"`` actually resolves;
* ``.pre-commit-config.yaml``  — the ``rev:`` of each hook repo, plus the tag of
  each pinned container image.

Nothing in the toolchain couples them. Three files drifting apart is the classic
"green locally, red in CI" generator, and in the gitleaks case it is worse than
an inconvenience: the pre-commit hook and the CI stage would be scanning with
different rule engines while both reported success.

``ci/checks.sh``'s preflight enforces that the INSTALLED tools match
``ci/versions.env``. This file enforces that the three files agree about what
that should be. Both halves are needed: the preflight cannot notice that
pyproject asks for a different version than versions.env, because by then only
one of them has been obeyed.

When a pin is bumped, bump it in all three files in the same commit — this test
is what tells you which one you missed.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

import pytest

# The ci/versions.env parser has ONE home (`shell_harness`), not a private copy
# per module — this file used to carry the fourth of five byte-identical
# copies. Sharing it does not weaken this gate: `shell_harness` is imported by
# `test_checks_sh_behaviour` and `test_coverage_positive_control` already, so a
# broken harness reddens the unit stage regardless of what this file does; what
# it buys is that the parse this lockstep gate performs is the same parse every
# other pin assertion in the suite performs, and is itself now under test.
from shell_harness import VERSIONS_ENV, read_versions_env

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

PYPROJECT: Final = REPO_ROOT / "pyproject.toml"
PRE_COMMIT_CONFIG: Final = REPO_ROOT / ".pre-commit-config.yaml"
WORKFLOW: Final = REPO_ROOT / ".github" / "workflows" / "ci.yml"

RUFF_PRE_COMMIT: Final = "https://github.com/astral-sh/ruff-pre-commit"
MYPY_PRE_COMMIT: Final = "https://github.com/pre-commit/mirrors-mypy"


def _read_pyproject() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _read_dev_extras() -> dict[str, str]:
    """``{distribution: version}`` from ``[project.optional-dependencies].dev``."""
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    optional = project["optional-dependencies"]
    assert isinstance(optional, dict)
    entries = optional["dev"]
    assert isinstance(entries, list)

    pinned: dict[str, str] = {}
    for entry in entries:
        requirement = str(entry)
        name, separator, version = requirement.partition("==")
        assert separator, f"pyproject [dev] entry is not pinned with '==': {requirement!r}"
        pinned[name.strip()] = version.strip()
    return pinned


def _read_hook_revs() -> dict[str, str]:
    """``{repo url: rev}`` for every remote hook repo in the pre-commit config.

    Parsed with a regex rather than a YAML library on purpose: PyYAML is not a
    dependency of this project, and adding one so a meta-test can read four
    strings would be exactly the scope creep pyproject's header warns about.
    ``- repo:`` immediately followed by ``rev:`` is the only shape pre-commit
    accepts, so the regex is not guessing. ``- repo: local`` blocks have no
    ``rev:`` and are matched by the image-tag assertions instead.
    """
    text = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^[ \t]*-[ \t]*repo:[ \t]*(?P<repo>\S+)[ \t]*$\n[ \t]*rev:[ \t]*(?P<rev>\S+)",
        re.MULTILINE,
    )
    return {match.group("repo"): match.group("rev") for match in pattern.finditer(text)}


VERSIONS: Final = read_versions_env()
DEV_EXTRAS: Final = _read_dev_extras()
HOOK_REVS: Final = _read_hook_revs()
PRE_COMMIT_TEXT: Final = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")


# ``<NAME>_VERSION`` keys in ci/versions.env that are NOT distributions in
# pyproject's ``[dev]`` extra, each with the reason. Every other pin in that file
# MUST appear in ``[dev]`` at the same version.
#
# DERIVED, not hand-listed, and that is the whole point of this block. This test
# used to carry a hardcoded parametrize list of four ``(pin_key, distribution)``
# tuples, which meant a fifth pin added to BOTH ci/versions.env and
# pyproject.toml was lockstep-checked by nothing at all — the one silent drift
# path in a file whose entire job is catching drift. Every other pin mechanism in
# the repo screams when you extend it: ci/checks.sh's require_pin_coverage() hard
# errors on an unclaimed pin, tool_version()'s ``*)`` arm hard errors on an
# unprobed one. This one failed quietly.
#
# ci/versions.env:12-14 already states the rule — the preflight "DERIVES its tool
# list from the <NAME>_VERSION keys in this file, so adding a pin here and
# forgetting to enforce it is a hard error rather than a silent gap". That was
# stated for the shell side and simply never applied here. It is now.
#
# Adding a name below is therefore a deliberate, reviewable act, exactly as
# adding one to ci/checks.sh's PREFLIGHT_EXEMPT_PINS is:
#
#   PYTHON      the interpreter, not a pip-installable distribution. Its
#               provenance is pyproject requires-python; asserted separately by
#               test_python_pin_agrees_with_requires_python.
#   PIP         bootstrap-level: it is what INSTALLS the [dev] extra, so it
#               cannot be a member of it. Pinned in .github/workflows/ci.yml.
#   HATCHLING   PEP 517 build backend, resolved by pip in an isolated build
#               environment; asserted against [build-system].requires instead by
#               test_build_backend_is_pinned_and_agrees_with_versions_env.
#   GITLEAKS    runs as a pinned container, not a Python distribution.
#   SHELLCHECK  same.
#   TERRAFORM   same: a digest-pinned container, not a Python distribution.
#
# Note this set does NOT need to name VULTURE or PIP_AUDIT: both are plain
# pip-installable distributions pinned in pyproject [dev] (vulture==2.16,
# pip-audit==2.10.1 — adopt-governance-kit design D3), so the derivation below
# picks them up automatically like any other pin; they were only ever
# hand-listed in an earlier, now-replaced version of this test.
NOT_A_DEV_EXTRA: Final = frozenset(
    {"PYTHON", "PIP", "HATCHLING", "GITLEAKS", "SHELLCHECK", "TERRAFORM"}
)


def _dev_extra_pins() -> list[tuple[str, str]]:
    """``[(pin_key, distribution)]`` for every pin ``[dev]`` is required to carry.

    ``PRE_COMMIT_VERSION`` -> ``pre-commit``, ``PYTEST_COV_VERSION`` ->
    ``pytest-cov``: the same ``lower()`` + ``_``-to-``-`` fold that
    ``ci/checks.sh``'s ``versions_env_tools()`` applies, so the two derivations
    cannot disagree about what a pin is called.
    """
    pairs: list[tuple[str, str]] = []
    for key in sorted(VERSIONS):
        if not key.endswith("_VERSION"):
            continue
        stem = key[: -len("_VERSION")]
        if stem in NOT_A_DEV_EXTRA:
            continue
        pairs.append((key, stem.lower().replace("_", "-")))
    return pairs


DEV_EXTRA_PINS: Final = _dev_extra_pins()


def test_the_dev_extra_pin_list_is_still_derived_from_versions_env() -> None:
    """The parametrize below must keep coming from the pin file, not from a list.

    Without this, the derivation above could be quietly replaced by a literal
    list again and every other test in this file would still pass. It also fails
    if ``NOT_A_DEV_EXTRA`` names something that is not a pin at all, so the
    exempt set cannot rot into a place where typos go to hide.
    """
    assert DEV_EXTRA_PINS, "no [dev] pins derived from ci/versions.env at all"

    for stem in sorted(NOT_A_DEV_EXTRA):
        assert f"{stem}_VERSION" in VERSIONS, (
            f"NOT_A_DEV_EXTRA names {stem}, but ci/versions.env has no {stem}_VERSION pin to exempt"
        )

    # The four this list named by hand before it was derived. Pinned here so a
    # careless edit to NOT_A_DEV_EXTRA cannot silently drop one back out of the
    # lockstep check.
    derived = {distribution for _, distribution in DEV_EXTRA_PINS}
    assert {"ruff", "mypy", "pytest", "pre-commit"} <= derived, (
        f"the originally-checked pins must still be derived; got {sorted(derived)}"
    )


@pytest.mark.parametrize(("pin_key", "distribution"), DEV_EXTRA_PINS)
def test_versions_env_matches_pyproject_dev_extra(pin_key: str, distribution: str) -> None:
    """What CI announces is what ``pip install -e ".[dev]"`` will install."""
    assert pin_key in VERSIONS, f"ci/versions.env has no {pin_key}"
    assert distribution in DEV_EXTRAS, f"pyproject [dev] does not pin {distribution}"
    assert DEV_EXTRAS[distribution] == VERSIONS[pin_key], (
        f"{distribution}: ci/versions.env says {VERSIONS[pin_key]}, "
        f"pyproject [dev] says {DEV_EXTRAS[distribution]}"
    )


@pytest.mark.parametrize(
    ("pin_key", "repo_url"),
    [
        ("RUFF_VERSION", RUFF_PRE_COMMIT),
        ("MYPY_VERSION", MYPY_PRE_COMMIT),
    ],
)
def test_versions_env_matches_pre_commit_rev(pin_key: str, repo_url: str) -> None:
    """The hook runs the same linter version the CI stage does."""
    assert repo_url in HOOK_REVS, f".pre-commit-config.yaml has no hook repo {repo_url}"
    expected = f"v{VERSIONS[pin_key]}"
    assert HOOK_REVS[repo_url] == expected, (
        f"{repo_url}: ci/versions.env implies rev {expected}, config says {HOOK_REVS[repo_url]}"
    )


def test_mypy_hook_pins_the_same_pytest() -> None:
    """mypy's isolated hook env needs pytest stubs at the pinned version.

    The hook resolves imports in its own virtualenv, so a stale ``pytest==`` in
    ``additional_dependencies`` type-checks the test suite against a different
    pytest than the unit/contract/smoke stages import at runtime.
    """
    found = set(re.findall(r'"pytest==([^"]+)"', PRE_COMMIT_TEXT))
    assert found == {VERSIONS["PYTEST_VERSION"]}, (
        f"pre-commit additional_dependencies pin pytest {sorted(found)}, "
        f"ci/versions.env says {VERSIONS['PYTEST_VERSION']}"
    )


@pytest.mark.parametrize(
    ("image_key", "version_key", "digest_key", "repository", "tag_prefix"),
    [
        ("GITLEAKS_IMAGE", "GITLEAKS_VERSION", "GITLEAKS_DIGEST", "ghcr.io/gitleaks/gitleaks", "v"),
        ("SHELLCHECK_IMAGE", "SHELLCHECK_VERSION", "SHELLCHECK_DIGEST", "koalaman/shellcheck", "v"),
        ("TERRAFORM_IMAGE", "TERRAFORM_VERSION", "TERRAFORM_DIGEST", "hashicorp/terraform", ""),
    ],
)
def test_container_image_is_digest_pinned_and_agrees_everywhere(
    image_key: str, version_key: str, digest_key: str, repository: str, tag_prefix: str
) -> None:
    """A pinned container is a pin: repository, tag, digest and hook all agree.

    This is the one that actually bites. ``ci/checks.sh`` and the pre-commit
    hook both run the gitleaks container; if the hook's reference drifts from
    ``GITLEAKS_IMAGE`` the two gates scan with different rule engines and the
    local one can pass while CI fails, or worse, the reverse.

    DIGEST, not just tag. ``.github/workflows/ci.yml`` pins its actions to a
    commit SHA and argues in its own header that a mutable reference running
    with repository access is unacceptable. A tag is exactly that: ``v8.30.1``
    can be re-pushed pointing at different bytes, and ``ci/checks.sh``'s runtime
    version assertion would still pass because the rewritten image would keep
    printing ``v8.30.1``. Only the digest catches a content rewrite.

    The ``repo:tag@sha256:`` form is used rather than bare ``repo@sha256:`` so
    the human-readable version survives in the reference, while docker resolves
    the content by digest — the tag becomes a comment that cannot affect what
    runs.

    ``tag_prefix`` exists because not every pinned repository shares
    gitleaks/shellcheck's ``v``-prefixed tag convention: ``hashicorp/terraform``'s
    Docker Hub tags carry no ``v`` at all — confirmed against the live registry's
    own tag list (``1.15.8`` exists, ``v1.15.8`` does not), not assumed from the
    other two entries in this table. Hardcoding ``v`` here would force a choice
    between a test that fails on the real, pullable terraform image, or a pin
    that satisfies the test but does not exist on the registry.
    """
    image = VERSIONS[image_key]
    version = VERSIONS[version_key]
    digest = VERSIONS[digest_key]

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), (
        f"{digest_key}={digest!r} is not a sha256 digest"
    )
    assert image == f"{repository}:{tag_prefix}{version}@{digest}", (
        f"{image_key}={image} must be exactly {repository}:{tag_prefix}{version}@{digest} "
        f"(from {version_key} and {digest_key})"
    )

    # Any reference to this repository anywhere in the hook config must be the
    # fully digest-pinned one. The charset stops at whitespace so a mention in
    # prose cannot absorb trailing punctuation.
    references = set(re.findall(rf"{re.escape(repository)}[:@][A-Za-z0-9._:@-]+", PRE_COMMIT_TEXT))
    assert references == {image}, (
        f".pre-commit-config.yaml references {sorted(references)}; ci/versions.env pins {image}"
    )


def test_build_backend_is_pinned_and_agrees_with_versions_env() -> None:
    """``hatchling`` is a pin like any other and had no single source of truth.

    It was written into ``pyproject.toml [build-system].requires`` and appeared
    in neither ``ci/versions.env`` nor this test, and carried no provenance URL.
    That matters more than it looks: ``pip install -e ".[dev]"`` is the one
    documented bootstrap command and is now what CI runs, and pip resolves the
    build backend in an isolated PEP 517 environment on every single install.
    An unpinned or drifting backend is therefore a floating dependency of every
    install anyone ever makes of this project.
    """
    build_system = _read_pyproject()["build-system"]
    assert isinstance(build_system, dict)
    requires = build_system["requires"]
    assert isinstance(requires, list)
    assert len(requires) == 1, f"expected exactly one build requirement, got {requires}"

    name, separator, version = str(requires[0]).partition("==")
    assert separator, f"build-system requirement is not pinned with '==': {requires[0]!r}"
    assert name.strip() == "hatchling"
    assert version.strip() == VERSIONS["HATCHLING_VERSION"], (
        f"pyproject [build-system] pins hatchling {version.strip()}, "
        f"ci/versions.env HATCHLING_VERSION={VERSIONS['HATCHLING_VERSION']}"
    )


def test_every_pin_in_versions_env_carries_a_provenance_url() -> None:
    """A pin nobody can re-verify is a number somebody once typed.

    ``ci/versions.env``'s header says "Verified ... against pypi.org / ghcr.io /
    docker.io. Re-verify before bumping" — which is only actionable if each pin
    says where it came from. hatchling was added to pyproject with no URL
    anywhere; this makes that omission fail.
    """
    lines = VERSIONS_ENV.read_text(encoding="utf-8").splitlines()
    url_pattern = re.compile(r"(pypi\.org|github\.com|hub\.docker\.com|ghcr\.io|docker\.io)")

    missing: list[str] = []
    for index, raw_line in enumerate(lines):
        match = re.match(r"^([A-Z0-9_]+)_VERSION=", raw_line)
        if not match:
            continue
        # Walk back over the contiguous comment block introducing this pin.
        block: list[str] = []
        cursor = index - 1
        while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
            block.append(lines[cursor])
            cursor -= 1
        if match.group(1) == "PYTHON":
            continue  # the interpreter's provenance is pyproject requires-python
        if not any(url_pattern.search(line) for line in block):
            missing.append(match.group(1))

    assert not missing, (
        f"ci/versions.env pins {missing} with no provenance URL in the comment block "
        "above them; nobody can re-verify the pin before bumping it"
    )


def test_python_pin_agrees_with_requires_python() -> None:
    """``PYTHON_VERSION`` and ``requires-python`` describe one interpreter.

    ``ci/checks.sh`` refuses to run unless the interpreter's major.minor equals
    ``PYTHON_VERSION``; pip refuses to install unless it satisfies
    ``requires-python``. If these disagree, one of the two is unsatisfiable and
    the repo cannot be bootstrapped at all.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    major, minor = VERSIONS["PYTHON_VERSION"].split(".")
    expected = f">={major}.{minor},<{major}.{int(minor) + 1}"
    assert project["requires-python"] == expected, (
        f"ci/versions.env PYTHON_VERSION={VERSIONS['PYTHON_VERSION']} implies "
        f"requires-python {expected}, pyproject says {project['requires-python']!r}"
    )


def test_tool_configs_target_the_pinned_python() -> None:
    """ruff and mypy analyse the version of Python the gates actually run."""
    tools = _read_pyproject()["tool"]
    assert isinstance(tools, dict)
    pin = VERSIONS["PYTHON_VERSION"]

    mypy_config = tools["mypy"]
    assert isinstance(mypy_config, dict)
    assert mypy_config["python_version"] == pin

    ruff_config = tools["ruff"]
    assert isinstance(ruff_config, dict)
    assert ruff_config["target-version"] == f"py{pin.replace('.', '')}"


def test_workflow_pins_the_runner_image() -> None:
    """No mutable runner label, and the one literal matches ``CI_RUNNER_IMAGE``.

    ``ubuntu-latest`` moved from 22.04 to 24.04 mid-2025 and will move again.
    The workflow header argues against mutable pins; this makes the workflow
    obey its own argument. Every job but ``versions`` takes the label from
    ``ci/versions.env`` at runtime, so exactly one literal may remain.
    """
    text = WORKFLOW.read_text(encoding="utf-8")

    # Comment lines are exempt: the header explains why `ubuntu-latest` is
    # banned and has to name it to do so.
    directives = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "ubuntu-latest" not in directives, "workflow uses the mutable label ubuntu-latest"

    literals = set(
        re.findall(r"^[ \t]*runs-on:[ \t]*(?!\$\{\{)(\S+)[ \t]*$", directives, re.MULTILINE)
    )
    assert literals == {VERSIONS["CI_RUNNER_IMAGE"]}, (
        f"workflow hardcodes runner {sorted(literals)}; "
        f"ci/versions.env CI_RUNNER_IMAGE={VERSIONS['CI_RUNNER_IMAGE']}"
    )
