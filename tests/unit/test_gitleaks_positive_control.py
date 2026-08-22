"""Positive controls for the Constitution VII gate, run against the pinned image.

WHY A POSITIVE CONTROL IS THE ONLY THING THAT COUNTS HERE
---------------------------------------------------------
Rounds 2 and 3 verified the secret scan by planting an AWS key and observing a
red gate. That proves the scanner ran. It does NOT prove the scanner loaded
``ci/gitleaks.toml``, because ``aws-access-token`` is one of gitleaks' ~150
EMBEDDED DEFAULT rules and fires with no config at all. Measured on the pinned
image, on one planted Airflow Fernet key:

    proper config -> RuleID: orbital-drift-airflow-fernet-key
    empty  config -> RuleID: generic-api-key

So a red gate carried no information about which ruleset produced it, and the
gate spent a release running on defaults while every test stayed green. The four
PATH rules — terraform state, ``*.tfvars``, kubeconfig, ``.env`` — have no
default equivalent whatsoever, so a committed ``.tfstate`` passed in silence.

Every test in this file therefore asserts a SPECIFIC ``RuleID`` that exists only
in ``ci/gitleaks.toml``, and :func:`test_default_rules_alone_would_not_produce_the_custom_rule_id`
guards that guard by measuring what the defaults produce on the same bytes.

These tests need Docker. They do not need the network after the first run: the
image is digest-pinned, so a cached copy is byte-identical.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from shell_harness import PINS

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CHECKS_SH: Final = REPO_ROOT / "ci" / "checks.sh"
GITLEAKS_TOML: Final = REPO_ROOT / "ci" / "gitleaks.toml"
PRE_COMMIT_CONFIG: Final = REPO_ROOT / ".pre-commit-config.yaml"

FERNET_RULE: Final = "orbital-drift-airflow-fernet-key"
KUBECONFIG_RULE: Final = "orbital-drift-kubeconfig-file"

# Split so that no literal in this file is itself a 42-44 character
# high-entropy blob next to a keyword. This repository is public and its own
# gitleaks gate scans this file; a test fixture that reddens the gate it tests
# would be removed within a day.
_FERNET_HALVES: Final = ("kZ3vQ9pL2mX8tR5wY7", "nB4jH6sD1gF0cA3eU9iO2pQ7M")
_AWS_PREFIX: Final = "AKIA"
_AWS_BODY: Final = "IOSFODNN7EXAMPLF"


def fernet_key() -> str:
    """A 44-character urlsafe-base64 value, the shape ``airflow fernet`` emits."""
    return "".join(_FERNET_HALVES) + "="


def aws_access_key_id() -> str:
    """A value the DEFAULT ``aws-access-token`` rule matches, and only that rule."""
    return _AWS_PREFIX + _AWS_BODY


def _fernet_document() -> str:
    """Prose of exactly the shape the first runbook in ``docs/runbooks/`` will have."""
    return (
        "# Rotating the Airflow Fernet key\n\n"
        "Set the value in the cluster secret:\n\n"
        "    AIRFLOW__CORE__FERNET_KEY=" + fernet_key() + "\n"
    )


GITLEAKS_IMAGE: Final[str] = PINS["GITLEAKS_IMAGE"]


def _tool(name: str) -> str:
    """Absolute path to a required external tool.

    Skips locally when it is genuinely absent, but never in CI: a positive
    control that quietly turns itself off is worth less than no control, because
    it is counted as coverage. GitHub-hosted runners set ``CI=true`` and always
    provide Docker and git.
    """
    found = shutil.which(name)
    if found is not None:
        return found
    if os.environ.get("CI"):
        raise RuntimeError(
            f"{name} is not on PATH. These are the only tests that prove the "
            "Constitution VII gate loads ci/gitleaks.toml rather than gitleaks' "
            "embedded defaults; they may not be skipped in CI."
        )
    pytest.skip(
        f"capability-guard: {name} is not on PATH; the pinned-container positive "
        "controls need it (never skipped in CI — see the raise above)"
    )


def _run(
    argv: list[str], cwd: Path | None = None, *, msys_passthrough: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command and capture everything.

    ``msys_passthrough`` suppresses Git Bash's rewriting of POSIX-looking
    arguments, which turned ``-w /repo`` into a Windows path and made docker
    exit 125. It is set only when THIS test invokes docker directly. Runs of
    ``ci/checks.sh`` deliberately leave it off: the script scopes those two
    variables to its own ``docker run`` wrapper, and inheriting them from the
    test environment would hide a regression in that scoping.

    That makes ``_run_gate`` — and every test below it that calls into a real
    ``sh ci/checks.sh gitleaks`` — a live behavioural check of
    ``ci/checks.sh``'s ``docker_run`` wrapper, not just source-level coverage
    of it: measured (round 5) by reverting that wrapper to a bare
    ``docker run "$@"`` and re-running this file on this project's own
    Windows/Git-Bash box, which reproduces the original
    ``the working directory '...' is invalid`` failure exactly, on
    ``test_a_custom_rule_only_secret_reddens_the_real_gate``,
    ``test_a_gitignored_copy_of_the_same_secret_does_not_redden_the_gate`` and
    ``test_a_committed_kubeconfig_is_caught_by_a_rule_with_no_default_equivalent``.
    """
    env = dict(os.environ)
    if msys_passthrough:
        env["MSYS_NO_PATHCONV"] = "1"
        env["MSYS2_ARG_CONV_EXCL"] = "*"
    else:
        env.pop("MSYS_NO_PATHCONV", None)
        env.pop("MSYS2_ARG_CONV_EXCL", None)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        cwd=None if cwd is None else str(cwd),
        timeout=600,
        check=False,
    )


def _run_gate(root: Path) -> subprocess.CompletedProcess[str]:
    """``sh <root>/ci/checks.sh gitleaks`` — the real gate on a scaffolded repo."""
    return _run(
        [_tool("sh"), (root / "ci" / "checks.sh").as_posix(), "gitleaks"],
        msys_passthrough=False,
    )


def _posix(path: Path) -> str:
    """A bind-mount source docker accepts on both Windows and Linux."""
    text = path.resolve().as_posix()
    if len(text) > 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _scaffold(root: Path) -> None:
    """A minimal repository that ``ci/checks.sh gitleaks`` will run against."""
    (root / "ci").mkdir(parents=True, exist_ok=True)
    for name in ("checks.sh", "versions.env", "gitleaks.toml"):
        shutil.copyfile(REPO_ROOT / "ci" / name, root / "ci" / name)
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    git = _tool("git")
    _run([git, "init", "--quiet", "--initial-branch=main", "."], cwd=root)


@pytest.fixture
def gate_root() -> Iterator[Path]:
    """A scratch repository ``ci/checks.sh`` can be run against AND bind-mounted.

    Not ``tmp_path`` on Windows, and this is a measurement rather than a
    preference. MSYS maps ``%TEMP%`` onto ``/tmp``, so inside Git Bash
    ``pwd`` in ``C:/Users/me/AppData/Local/Temp/pytest-of-me/x`` returns
    ``/tmp/pytest-of-me/x``. ci/checks.sh derives REPO_ROOT from ``pwd`` and
    hands it to ``docker -v``, where ``/tmp/...`` names a path inside the Docker
    VM instead. Docker then creates it empty and the scan runs against nothing:

        FTL Failed to load config error="failed to load extended config,
            err: open ci/gitleaks.toml: no such file or directory"

    That is an artifact of scanning a directory under %TEMP% on Windows, not a
    defect in ci/checks.sh — the repository is never there. On Linux, where CI
    runs, tmp_path is already outside any such alias.
    """
    if os.name == "nt":
        base = Path.home() / ".cache" / "orbital-drift-gate-tests"
        base.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(dir=base))
    else:
        root = Path(tempfile.mkdtemp(prefix="orbital-drift-gate-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# =============================================================================
# The gate, end to end, on the real container.
# =============================================================================


def test_a_custom_rule_only_secret_reddens_the_real_gate(gate_root: Path) -> None:
    """An Airflow Fernet key in ``docs/`` must be caught BY NAME.

    ``docs/`` is deliberate: an earlier revision of ci/gitleaks.toml carried a
    global ``[[allowlists]] paths`` covering ``^docs/``, which in gitleaks v8
    prunes the WALK rather than filtering findings, so every rule — including
    all the defaults — was switched off across roughly 59% of the repository,
    including the two directories Constitution VI requires operators to write
    into.

    The rule id is asserted, not merely "a leak was found". Rounds 2 and 3 used
    an AWS key, which the embedded defaults also catch, so their evidence could
    not distinguish "config loaded" from "config never loaded" — which is
    exactly the state the gate was in.
    """
    _tool("docker")
    _scaffold(gate_root)
    (gate_root / "docs" / "runbooks").mkdir(parents=True)
    (gate_root / "docs" / "runbooks" / "rotate-fernet.md").write_text(
        _fernet_document(), encoding="utf-8"
    )

    result = _run_gate(gate_root)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"the gate passed on a planted Fernet key:\n{output}"
    assert FERNET_RULE in output, (
        "the scan fired, but not on the custom rule. If the finding names a default "
        f"rule instead, ci/gitleaks.toml was never loaded.\n{output}"
    )
    assert "rotate-fernet.md" in output, output


def test_a_gitignored_copy_of_the_same_secret_does_not_redden_the_gate(
    gate_root: Path,
) -> None:
    """The derived working-tree exclusion, exercised against the real scanner.

    ``.env.example`` tells the operator ``cp .env.example .env``; ``.gitignore``
    excludes ``.env``; the working-tree walk used to flag it anyway, training the
    operator to shrug at a red gitleaks. The exclusion is derived from
    ``git ls-files`` and must still be in force when the scan actually runs —
    which is precisely what adding ``--config`` to that invocation would break.
    """
    _tool("docker")
    _scaffold(gate_root)
    (gate_root / "ignored").mkdir()
    (gate_root / "ignored" / "local.md").write_text(_fernet_document(), encoding="utf-8")

    result = _run_gate(gate_root)
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        "a .gitignore-excluded file reddened the working-tree scan. It is not "
        f"repository content and cannot leak through the repository.\n{output}"
    )
    assert FERNET_RULE not in output, output


def test_a_committed_kubeconfig_is_caught_by_a_rule_with_no_default_equivalent(
    gate_root: Path,
) -> None:
    """The path rules are the half of the ruleset defaults cannot cover.

    ``orbital-drift-kubeconfig-file`` fires on the FILENAME, independent of
    content entropy. When the empty-config fail-open was live, a leaked
    kubeconfig or ``.tfstate`` passed with exit 0 and a banner identical to a
    clean scan — no default rule exists to pick it up.
    """
    _tool("docker")
    _scaffold(gate_root)
    (gate_root / "infra").mkdir()
    (gate_root / "infra" / "kubeconfig").write_text(
        "apiVersion: v1\nkind: Config\nclusters: []\n", encoding="utf-8"
    )

    result = _run_gate(gate_root)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"a committed kubeconfig passed the gate:\n{output}"
    assert KUBECONFIG_RULE in output, output


# =============================================================================
# Guarding the guard: what the defaults do on the same bytes.
# =============================================================================


def test_default_rules_alone_would_not_produce_the_custom_rule_id(tmp_path: Path) -> None:
    """Without the config, the same key is reported under a DEFAULT rule id.

    This is what makes the assertions above real positive controls rather than
    "something fired". It also records the measurement that motivated the fix:
    an empty ``GITLEAKS_CONFIG_TOML`` does not disable scanning, it silently
    swaps the ruleset — which is far harder to notice than a crash.
    """
    docker = _tool("docker")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text(_fernet_document(), encoding="utf-8")

    result = _run(
        [
            docker,
            "run",
            "--rm",
            "-e",
            "GITLEAKS_CONFIG_TOML=",
            "-v",
            f"{_posix(tmp_path)}:/repo:ro",
            "-w",
            "/repo",
            GITLEAKS_IMAGE,
            "dir",
            ".",
            "--redact",
            "--verbose",
            "--no-banner",
        ]
    )
    output = result.stdout + result.stderr

    assert FERNET_RULE not in output, (
        "the custom rule fired with an EMPTY config, so it is not custom-only and "
        f"the positive controls above prove nothing.\n{output}"
    )
    assert "RuleID" in output, (
        "gitleaks with default rules found nothing at all on this fixture, so the "
        f"comparison this test makes is meaningless.\n{output}"
    )


def test_default_rules_survive_the_two_level_extend_chain(tmp_path: Path) -> None:
    """overlay -> ``ci/gitleaks.toml`` -> ``useDefault``: depth 2, measured.

    ``ci/checks.sh`` reaches the defaults through two levels of ``[extend]``, so
    the whole default ruleset is one ``maxExtendDepth`` change away from silently
    vanishing from the working-tree scan while the custom rules keep firing.

    Collapsing the chain is NOT an option: gitleaks v8.30.1 refuses a config
    that sets both ``extend.path`` and ``extend.useDefault`` — "unable to load
    config due to extend.path and extend.useDefault being set". So the depth is
    load-bearing and is measured here instead.
    """
    docker = _tool("docker")
    (tmp_path / "ci").mkdir()
    shutil.copyfile(GITLEAKS_TOML, tmp_path / "ci" / "gitleaks.toml")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keys.md").write_text(
        f"aws_access_key_id = {aws_access_key_id()}\n", encoding="utf-8"
    )

    overlay = (
        '[extend]\npath = "ci/gitleaks.toml"\n\n'
        "[[allowlists]]\n"
        'description = "shaped like the one ci/checks.sh generates"\n'
        'paths = [\n  "^nothing-matches-this/",\n]\n'
    )
    result = _run(
        [
            docker,
            "run",
            "--rm",
            "-e",
            f"GITLEAKS_CONFIG_TOML={overlay}",
            "-v",
            f"{_posix(tmp_path)}:/repo:ro",
            "-w",
            "/repo",
            GITLEAKS_IMAGE,
            "dir",
            ".",
            "--redact",
            "--verbose",
            "--no-banner",
        ]
    )
    output = result.stdout + result.stderr

    assert "FTL" not in output, f"the two-level extend chain no longer loads:\n{output}"
    assert "aws-access-token" in output, (
        "a DEFAULT rule did not fire through overlay -> ci/gitleaks.toml -> useDefault. "
        "The ~150 default rules are absent from the working-tree scan; only the six "
        f"custom rules remain.\n{output}"
    )


# =============================================================================
# MAJOR 3 — the pre-commit hook runs in no CI job, so it is exercised here.
# =============================================================================


def _folded_entry(hook_id: str) -> list[str]:
    """The ``entry:`` of a local hook, as the argv pre-commit will execute.

    Read out of ``.pre-commit-config.yaml`` rather than duplicated here, so this
    test cannot pass against a command line the hook does not use. A YAML folded
    scalar (``>-``) joins its lines with single spaces.
    """
    lines = PRE_COMMIT_CONFIG.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == f"- id: {hook_id}"]
    assert len(starts) == 1, f"expected exactly one `- id: {hook_id}` block, found {len(starts)}"

    entry_lines = [i for i in range(starts[0], len(lines)) if lines[i].strip() == "entry: >-"]
    assert entry_lines, f"hook {hook_id} has no folded `entry: >-`"
    start = entry_lines[0]
    indent = len(lines[start]) - len(lines[start].lstrip())

    parts: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        if len(line) - len(line.lstrip()) <= indent:
            break
        parts.append(line.strip())
    assert parts, f"hook {hook_id}'s entry block is empty"
    return " ".join(parts).split()


def test_the_gitleaks_hook_entry_uses_the_pinned_image() -> None:
    """Guard the parser: everything below asserts against this argv."""
    entry = _folded_entry("gitleaks")
    assert entry[0] == GITLEAKS_IMAGE, (
        f"the gitleaks hook runs {entry[0]!r}; ci/versions.env pins {GITLEAKS_IMAGE!r}"
    )
    assert "--config" in entry and "ci/gitleaks.toml" in entry, (
        "the staged-diff hook does not name ci/gitleaks.toml, so it would scan with "
        f"gitleaks' embedded defaults: {entry!r}"
    )


def test_the_gitleaks_hook_entry_actually_parses_against_the_pinned_image(
    tmp_path: Path,
) -> None:
    """``--pre-commit`` was a ``detect``/``protect`` flag in older gitleaks.

    If cobra rejects the flag combination on v8.30.1, EVERY commit is blocked by
    a broken secrets hook and the documented escape is ``--no-verify`` — the one
    reflex ci/gitleaks.toml spends forty lines forbidding. The repository has no
    commits, so this hook has never run; without this test its first execution
    would be on the operator's first ``git commit``.
    """
    docker = _tool("docker")
    git = _tool("git")
    _run([git, "init", "--quiet", "--initial-branch=main", "."], cwd=tmp_path)
    (tmp_path / "ci").mkdir()
    shutil.copyfile(GITLEAKS_TOML, tmp_path / "ci" / "gitleaks.toml")
    (tmp_path / "clean.md").write_text("nothing to see here\n", encoding="utf-8")
    _run([git, "add", "-A"], cwd=tmp_path)

    result = _run(
        [
            docker,
            "run",
            "--rm",
            "-e",
            "GIT_CONFIG_COUNT=1",
            "-e",
            "GIT_CONFIG_KEY_0=safe.directory",
            "-e",
            "GIT_CONFIG_VALUE_0=/src",
            "-v",
            f"{_posix(tmp_path)}:/src:rw",
            "-w",
            "/src",
            *_folded_entry("gitleaks"),
        ]
    )
    output = result.stdout + result.stderr

    assert "unknown flag" not in output and "unknown shorthand" not in output, (
        f"the pinned gitleaks rejects the hook's command line:\n{output}"
    )
    assert result.returncode == 0, f"a clean staged index must not fail the hook:\n{output}"


def test_the_gitleaks_hook_catches_a_custom_rule_secret_in_the_staged_index(
    tmp_path: Path,
) -> None:
    """The hook's only real job, exercised on a synthetic staged index.

    ``--hook-stage manual`` drops this hook, so it runs in no CI job at all;
    round 3 traded a vacuous pass for zero validation. Here it scans a real
    staged diff and must name the custom rule.
    """
    docker = _tool("docker")
    git = _tool("git")
    _run([git, "init", "--quiet", "--initial-branch=main", "."], cwd=tmp_path)
    (tmp_path / "ci").mkdir()
    shutil.copyfile(GITLEAKS_TOML, tmp_path / "ci" / "gitleaks.toml")
    (tmp_path / "values.yaml").write_text(
        "airflow:\n  fernet_key: " + fernet_key() + "\n", encoding="utf-8"
    )
    _run([git, "add", "-A"], cwd=tmp_path)

    result = _run(
        [
            docker,
            "run",
            "--rm",
            "-e",
            "GIT_CONFIG_COUNT=1",
            "-e",
            "GIT_CONFIG_KEY_0=safe.directory",
            "-e",
            "GIT_CONFIG_VALUE_0=/src",
            "-v",
            f"{_posix(tmp_path)}:/src:rw",
            "-w",
            "/src",
            *_folded_entry("gitleaks"),
        ]
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"the staged-diff hook passed on a staged Airflow Fernet key:\n{output}"
    )
    assert FERNET_RULE in output, (
        f"the hook fired, but not on the custom rule — ci/gitleaks.toml was not loaded:\n{output}"
    )
