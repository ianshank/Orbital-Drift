"""Behavioural tests for ``ci/checks.sh`` — the script is RUN, not grepped.

``tests/unit/test_ci_contract.py`` asserts against shell source. That caught real
regressions and still does, but a source grep only ever proves that a particular
spelling is present, and three of those greps were demonstrated to be defeatable
by refactors that keep the defect intact (see ``shell_harness`` for the exact
three). Worse, the whole grep suite stayed green through a genuine fail-open in
the Constitution VII gate.

Everything in this file drives the real script with ``git``, ``docker`` and
``python`` replaced by recording stubs on ``PATH``, and asserts on what the
script DID: which commands it ran, with which arguments, carrying which
environment, and whether it stopped when it should have.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Final

import pytest

from shell_harness import (
    CHECKS_SH,
    PINS,
    REPO_ROOT,
    WINDOWS_NPIPE_DAEMON_DOWN_STDERR,
    Call,
    Recording,
    Stubs,
    posix_shell,
    run_checks,
)

# Environment variables named as plausible bypasses. The point is NOT that this
# list is exhaustive — it cannot be, which is exactly why
# ``test_ci_contract.test_checks_sh_reads_only_declared_environment_variables``
# exists alongside it and enumerates every variable the script reads. This list
# covers the spellings a hurried operator or agent would actually reach for.
BYPASS_ATTEMPTS: Final[dict[str, str]] = {
    "ORBITAL_DRIFT_PREFLIGHT_DONE": "1",
    "OD_FAST": "1",
    "FAST": "1",
    "SKIP_PREFLIGHT": "1",
    "NO_PREFLIGHT": "1",
    "PREFLIGHT": "0",
    "CI": "false",
    # The script's own internal memo names, in case one of them is ever read
    # with a `${VAR:-0}` default instead of being initialised unconditionally.
    "python_preflight_done": "1",
    "pins_coverage_checked": "1",
    "preflight_done": "1",
    "checked_tools": "ruff mypy pytest pre-commit",
}


def _daemon_probes(recording: Recording) -> tuple[Call, ...]:
    """The ``docker info`` liveness probes ``docker_or_fail`` runs (round 10).

    Its own subject in one test below (the probe must re-run once per stage
    that needs Docker, never memoised), and excluded from ``_scans`` by
    everything else: it starts no container and scans nothing.
    """
    return tuple(call for call in recording.of("docker") if call.argv[:1] == ("info",))


def _scans(recording: Recording) -> tuple[Call, ...]:
    """Docker invocations that are neither the version probe nor the daemon probe.

    ``require_pinned_image`` runs ``docker run --rm IMAGE version`` (and
    ``--version`` for shellcheck) before anything else, and (round 10)
    ``docker_or_fail`` runs ``docker info`` before THAT. Everything else is a
    real scan, and "did the scan run?" is the question most of this file asks.
    """
    return tuple(
        call
        for call in recording.of("docker")
        if call.argv
        and call.argv[:1] != ("info",)
        and call.argv[-1] not in {"version", "--version"}
    )


def _worktree_overlay(recording: Recording) -> str:
    scans = _scans(recording)
    assert len(scans) >= 1, "the working-tree scan did not run"
    overlay = scans[0].flag_value("-e", "GITLEAKS_CONFIG_TOML=")
    assert overlay is not None, (
        "the working-tree scan carried no -e GITLEAKS_CONFIG_TOML=...; without it "
        "gitleaks falls back to its embedded default ruleset and every rule in "
        f"ci/gitleaks.toml is silently absent. argv was: {scans[0].argv!r}"
    )
    return overlay


def _overlay_paths(overlay: str) -> list[str]:
    parsed: dict[str, Any] = tomllib.loads(overlay)
    allowlists = parsed.get("allowlists", [])
    assert len(allowlists) == 1, f"expected exactly one generated allowlist, got {allowlists!r}"
    paths = allowlists[0]["paths"]
    assert isinstance(paths, list)
    return [str(item) for item in paths]


# =============================================================================
# CRITICAL 1 — a failing overlay must STOP the scan.
# =============================================================================


def test_a_failing_overlay_halts_the_scan_instead_of_scanning_with_no_config(
    tmp_path: Path,
) -> None:
    """The round-4 fail-open, reproduced and then asserted shut.

    ``worktree_overlay_config`` refuses to build an exclusion for a path
    containing a literal newline and returns 1. The scan used to be written as::

        docker run ... -e GITLEAKS_CONFIG_TOML="$(worktree_overlay_config)" ...

    and ``set -e`` does NOT abort on a failing command substitution used as a
    WORD of a simple command — verified in isolation under both bash-as-sh and
    dash: the argument-list form survives, the standalone ``v=$(f)`` form
    aborts. So the FAIL diagnostic went to stderr and the scan ran anyway with
    an EMPTY config, which makes gitleaks use its embedded default ruleset.
    Measured against the pinned image on the same planted secret: with the
    config, ``RuleID: orbital-drift-airflow-fernet-key``; with an empty config,
    ``RuleID: generic-api-key``. The four path rules (terraform state, tfvars,
    kubeconfig, dotenv) have no default equivalent at all, so a committed
    kubeconfig passed silently with a banner identical to a clean scan.
    """
    recording = run_checks(
        "gitleaks",
        tmp_path,
        stubs=Stubs(ignored=b"a-file-with-a\nnewline-in-its-name\x00"),
    )

    assert recording.returncode != 0, (
        "ci/checks.sh gitleaks exited 0 although the working-tree overlay could not "
        "be built. Any route that lets the scan proceed without the generated config "
        "is the Constitution VII fail-open."
    )
    assert "contains a newline character" in recording.output
    assert _scans(recording) == (), (
        "a scan ran after the overlay failed. Whatever config it used, it was not the "
        f"one this script generates: {[call.argv for call in _scans(recording)]!r}"
    )


def test_the_overlay_reaches_the_scanner_and_names_the_real_ruleset(tmp_path: Path) -> None:
    """The config route is ``GITLEAKS_CONFIG_TOML`` + ``[extend] path``.

    An empty or missing value is not a degraded scan, it is a different scanner:
    gitleaks silently falls back to its embedded defaults, losing all six
    orbital-drift rules.
    """
    recording = run_checks("gitleaks", tmp_path)
    assert recording.returncode == 0, recording.output

    overlay = _worktree_overlay(recording)
    assert overlay.strip(), "GITLEAKS_CONFIG_TOML was passed but empty"

    parsed = tomllib.loads(overlay)
    assert parsed["extend"]["path"] == "ci/gitleaks.toml", (
        f"the overlay does not extend the real ruleset: {parsed!r}"
    )


def test_the_worktree_scan_must_not_carry_config_but_the_history_scan_must(
    tmp_path: Path,
) -> None:
    """``--config`` on the working-tree scan silently discards the overlay.

    Measured against the pinned image, on a tree with one gitignored file
    containing a planted Fernet key:

        env overlay only            -> 1 leak  (the gitignored copy is pruned)
        env overlay AND --config    -> 2 leaks (the flag wins, overlay ignored)

    So adding ``--config`` to the working-tree invocation — which looks like a
    belt-and-braces improvement — reinstates the ``.env`` false positive the
    overlay exists to prevent, and trains the operator to shrug at a red
    gitleaks. The history scan takes no overlay and therefore must carry the
    flag.
    """
    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(head_rc=0))
    assert recording.returncode == 0, recording.output

    scans = _scans(recording)
    assert len(scans) == 2, f"expected a working-tree scan and a history scan, got {len(scans)}"
    worktree, history = scans

    assert "dir" in worktree.argv, f"first scan is not the `dir` walk: {worktree.argv!r}"
    assert "--config" not in worktree.argv, (
        "the working-tree scan passes --config, which makes gitleaks ignore "
        "GITLEAKS_CONFIG_TOML entirely and drop the derived .gitignore exclusions"
    )

    assert "git" in history.argv, f"second scan is not the history walk: {history.argv!r}"
    assert "--config" in history.argv, (
        "the history scan carries no --config and no overlay, so it would run on "
        "gitleaks' embedded defaults with none of ci/gitleaks.toml's rules"
    )
    assert history.flag_value("-e", "GITLEAKS_CONFIG_TOML=") is None


# =============================================================================
# MAJOR 3 (round 3) — the overlay must not be injectable, whatever its shape.
# =============================================================================


def test_a_filename_cannot_inject_extra_toml_patterns(tmp_path: Path) -> None:
    """``x''', '''`` used to close a literal string and open another.

    The result was two valid patterns, ``^x`` and ``$``. Go's ``regexp`` matches
    ``$`` against every input, so the ENTIRE working-tree walk was pruned: zero
    findings, exit 0, output indistinguishable from a clean scan.

    Asserted on the GENERATED TOML rather than on the source that generates it.
    The source-level form of this test (``"'''" not in body``) is satisfied by
    moving the emission into a one-line helper while the injection is fully
    restored.
    """
    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(ignored=b"x''', '''\x00"))
    assert recording.returncode == 0, recording.output

    paths = _overlay_paths(_worktree_overlay(recording))
    assert paths == ["^x''', '''$"], (
        f"one gitignored path produced {paths!r}. Anything other than a single "
        "anchored pattern means a filename injected TOML."
    )
    assert re.fullmatch(paths[0], "x''', '''"), "the emitted pattern does not match its own path"


@pytest.mark.parametrize(
    ("filename", "must_match", "must_not_match"),
    [
        ('weird"name', 'weird"name', "weirdXname"),
        ("back\\slash", "back\\slash", "backXslash"),
        ("dot.name", "dot.name", "dotXname"),
        ("star*name", "star*name", "starrrname"),
        ("br[ack]ets", "br[ack]ets", "bra"),
    ],
    ids=["quote", "backslash", "dot", "star", "brackets"],
)
def test_generated_patterns_are_literal_and_anchored(
    tmp_path: Path, filename: str, must_match: str, must_not_match: str
) -> None:
    """Two escaping passes: RE2 metacharacters, then TOML basic-string syntax.

    Getting either wrong is silent. Under-escaping for RE2 turns ``dot.name``
    into a pattern that also prunes ``dotXname``; under-escaping for TOML makes
    the config unparseable and gitleaks reports the error against a file that
    exists nowhere on disk.
    """
    recording = run_checks(
        "gitleaks", tmp_path, stubs=Stubs(ignored=filename.encode("utf-8") + b"\x00")
    )
    assert recording.returncode == 0, recording.output

    paths = _overlay_paths(_worktree_overlay(recording))
    assert len(paths) == 1, f"{filename!r} produced {paths!r}"
    pattern = paths[0]
    assert pattern.startswith("^") and pattern.endswith("$"), f"unanchored pattern {pattern!r}"
    assert re.fullmatch(pattern, must_match), f"{pattern!r} does not match {must_match!r}"
    assert re.fullmatch(pattern, must_not_match) is None, (
        f"{pattern!r} also matches {must_not_match!r} — a metacharacter survived escaping, "
        "so this exclusion prunes more of the walk than the one file it was derived from"
    )


def test_a_directory_entry_keeps_its_prefix_semantics(tmp_path: Path) -> None:
    """``git ls-files --directory`` emits ``cache/``; that must prune the tree."""
    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(ignored=b".mypy_cache/\x00"))
    assert recording.returncode == 0, recording.output

    paths = _overlay_paths(_worktree_overlay(recording))
    assert paths == ["^\\.mypy_cache/"], paths
    assert re.match(paths[0], ".mypy_cache/deep/file.json")


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (b"control\x01char\x00", "control character"),
        (b"invalid\xffutf8\x00", "not valid UTF-8"),
    ],
    ids=["control-char", "invalid-utf8"],
)
def test_an_unrepresentable_filename_fails_loudly_not_as_a_config_parse_error(
    tmp_path: Path, payload: bytes, expected_message: str
) -> None:
    """TOML basic strings allow no raw control character but tab, and a TOML
    document must be valid UTF-8. Both are legal in a POSIX filename.

    Before this, either produced ``FTL Failed to load config`` naming a path
    that exists nowhere — the same unreadable failure as the empty-allowlist
    bug, in a narrower form. ``LC_ALL=C`` stops *sed* from erroring on
    undecodable bytes; it says nothing about whether the RESULT is loadable
    TOML, and the comment that claimed otherwise was wrong.
    """
    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(ignored=payload))

    if expected_message == "not valid UTF-8" and "iconv is not on PATH" in recording.output:
        pytest.skip("capability-guard: iconv absent; ci/checks.sh documents and announces this gap")

    assert recording.returncode != 0, "an unrepresentable path did not stop the scan"
    assert expected_message in recording.output, recording.output
    assert _scans(recording) == (), "a scan ran after the overlay was rejected"


# =============================================================================
# MAJOR 6 — incomplete clones must not scan vacuously.
# =============================================================================


@pytest.mark.parametrize(
    ("stubs", "expected"),
    [
        (Stubs(is_shallow="true"), "shallow clone"),
        (Stubs(partial_rc=0), "partial (blobless/treeless) clone"),
    ],
    ids=["shallow", "partial"],
)
def test_an_incomplete_clone_stops_the_gate(tmp_path: Path, stubs: Stubs, expected: str) -> None:
    """Both make ``gitleaks git .`` pass while reading almost nothing.

    Measured for the partial case on git 2.52.0 and the pinned gitleaks image,
    against a repository whose first commit contained a secret the second
    removed, with the promisor remote unreachable::

        full clone     -> 2 commits scanned
        blobless clone -> could not fetch <oid> from promisor remote
                          0 commits scanned / no leaks found / exit 0

    ``git rev-parse --is-shallow-repository`` returns FALSE for a blobless
    clone, so the shallow guard does not cover it.
    """
    recording = run_checks("gitleaks", tmp_path, stubs=stubs)
    assert recording.returncode != 0
    assert expected in recording.output
    assert _scans(recording) == (), "a scan ran on an incomplete clone"


# =============================================================================
# MAJOR 7 (round 3) — the preflight must not be bypassable by ANY variable.
# =============================================================================


@pytest.mark.parametrize(("name", "value"), sorted(BYPASS_ATTEMPTS.items()))
def test_no_environment_variable_disables_the_pin_check(
    tmp_path: Path, name: str, value: str
) -> None:
    """A wrong pin must fail the stage no matter what is in the environment.

    The source-level version of this test pinned the single NAME
    ``ORBITAL_DRIFT_PREFLIGHT_DONE``, so ``|| [ "${OD_FAST:-0}" = "1" ]``
    disabled every pin check with the test still green.

    ROUND 7 — this remains valid coverage, but WHY it passes changed. Before
    round 7, ci/checks.sh actually declared variables named
    ``python_preflight_done`` / ``pins_coverage_checked`` / ``checked_tools``,
    so this test (plus the source-level detectors in
    ``tests/unit/test_ci_contract.py``) depended on the script never reading
    one of these specific NAMES from the environment — an enumeration rounds
    4-6 proved cannot be exhaustive. As of round 7 ci/checks.sh declares none
    of them at all (see
    ``test_ci_contract.test_no_memoized_flag_gates_pin_verification``); there
    is no flag left for any of these names, or any name not yet invented, to
    set. This test is kept as a black-box regression check on that property,
    not as the mechanism that establishes it — see
    ``test_the_actual_version_probe_still_runs_under_every_bypass_attempt``
    below for the stronger, WHY-shaped version of the same claim.
    """
    recording = run_checks("lint", tmp_path, stubs=Stubs(ruff="0.0.1"), extra_env={name: value})

    assert recording.returncode != 0, (
        f"{name}={value} let `sh ci/checks.sh lint` pass while ruff reported 0.0.1 "
        "instead of the pinned version"
    )
    assert "pinned-tool mismatch" in recording.output
    ran_ruff = [call for call in recording.of("python") if "check" in call.argv]
    assert ran_ruff == [], f"the gate ran anyway: {ran_ruff!r}"


@pytest.mark.parametrize(("name", "value"), sorted(BYPASS_ATTEMPTS.items()))
def test_the_actual_version_probe_still_runs_under_every_bypass_attempt(
    tmp_path: Path, name: str, value: str
) -> None:
    """ROUND 7 — proves WHY the bypass fails, not just THAT it fails.

    ``test_no_environment_variable_disables_the_pin_check`` above proves the
    stage exits non-zero and never reaches ``ruff check``. This proves the
    STRUCTURAL reason: ``python -m ruff --version`` — the actual subprocess
    probe ``require_tool()`` feeds into its byte-for-byte comparison against
    ``ci/versions.env`` — was invoked despite the bypass variable being set.
    Before round 7, a memoised skip-flag successfully set by one of these
    names would have made this exact call MISSING, not merely one whose
    result was then reported as a failure; that it is PRESENT for every one
    of ``BYPASS_ATTEMPTS`` is the direct behavioural evidence that none of
    them reach a skip-verification code path any more, because — after round
    7 — there is no longer one to reach.
    """
    recording = run_checks("lint", tmp_path, stubs=Stubs(ruff="0.0.1"), extra_env={name: value})
    probed = [call for call in recording.of("python") if call.joined == "-m ruff --version"]
    assert probed, (
        f"with {name}={value} set, `python -m ruff --version` was never invoked — "
        "some code path treated the environment variable as a substitute for the "
        "real probe instead of running it and finding the mismatch"
    )


# =============================================================================
# ROUND 7 — the structural fix, proved behaviourally. Complements
# tests/unit/test_ci_contract.py's source-level
# test_no_memoized_flag_gates_pin_verification and
# test_log_only_memos_never_gate_a_verification_return: those two prove the
# SOURCE contains no memoised flag; the two tests below prove what that
# absence actually DOES across a real `sh ci/checks.sh all` run — the
# comparison genuinely re-executes per stage (not once, memoised, the
# pre-round-7 shape), while the human-readable LOG banner is still only
# printed once, because the two concerns (verification-caching,
# log-caching) are independent and only one of them was ever meant to exist.
# =============================================================================


def test_the_actual_pin_check_reexecutes_once_per_stage_that_needs_it(tmp_path: Path) -> None:
    """The direct instrumented proof the round asked for: count how many times
    the real version-check subprocess runs across one full ``all`` invocation.

    Twelve of the fifteen ``preflight()`` calls a full ``all`` run makes carry
    a non-empty pin set — ``all`` itself (all eight tools), then ``lint``
    (ruff), ``typecheck`` (mypy), ``unit``/``contract``/``smoke`` (pytest,
    three times), ``coverage`` (pytest + pytest-cov + coverage), ``dead``
    (vulture), ``audit`` (pip-audit), ``traceability`` (pytest) and
    ``governance`` (pytest). ``projections`` is the THIRTEENTH interpreter
    probe and declares no pins at all: it executes ``python -m
    orbital_drift.projections`` with no pinned DISTRIBUTION to assert, so
    ``stage_runs_python`` keeps it in the preflight for the interpreter alone
    (RB-008 F2 — before that fix it verified nothing and this count was 12).
    ``gitleaks`` and ``specs`` run no Python whatsoever, so those two make no
    interpreter or tool call at all.

    If verification were still memoised the pre-round-7 way, every count below
    would be 1 — the tool's version would be probed once, by whichever stage
    happened to need it first, and every later stage that also needs it would
    trust the cached result instead of probing again.
    """
    recording = run_checks("all", tmp_path)
    assert recording.returncode == 0, recording.output
    python_calls = recording.of("python")

    def _count(*, exact: str | None = None, contains: str | None = None) -> int:
        if exact is not None:
            return sum(1 for call in python_calls if call.joined == exact)
        assert contains is not None
        return sum(1 for call in python_calls if contains in call.joined)

    # The interpreter itself: full + minor version probes, once per preflight()
    # call that verifies the interpreter — the twelve with a non-empty pin set
    # (all, lint, typecheck, unit, contract, smoke, coverage, dead, audit,
    # traceability, governance, hooks) PLUS projections, which has no pins and
    # runs Python anyway = 13.
    full_probes = _count(contains="version_info[:3]")
    minor_probes = _count(contains="version_info[:2]")
    assert full_probes == 13, (
        f"expected 13 python full-version probes (one per preflight() call that "
        f"verifies the interpreter), found {full_probes}: "
        f"{[c.joined for c in python_calls]!r}"
    )
    assert minor_probes == 13, f"expected 13 python minor-version probes, found {minor_probes}"

    # the ruff pin is needed by `all` and `lint` = 2 calls, not 1.
    assert _count(exact="-m ruff --version") == 2, "ruff --version did not re-probe for `lint`"
    # mypy: needed by `all` and `typecheck` = 2 calls, not 1.
    assert _count(exact="-m mypy --version") == 2, "mypy --version did not re-probe for `typecheck`"
    # pytest: needed by `all`, `unit`, `contract`, `smoke`, `coverage`,
    # `traceability`, `governance` = 7, not 1.
    #
    # `contains="import pytest"` counts the VERSION PROBE only. It deliberately
    # does not match `-m pytest ...` runs, nor the pytest-cov/coverage probes,
    # which go through importlib.metadata precisely so their argv carries no
    # `import pytest` substring — see tool_version() in ci/checks.sh and the
    # ordering note in shell_harness.PYTHON_STUB. (tool_version() briefly
    # carried a second, unreachable `pytest-cov)` arm that probed via
    # `import pytest_cov` directly — a real `case` never reaches a repeated
    # label, so it was dead code shadowed by the importlib.metadata arm above
    # it; removed rather than kept as a second, silently-ignored definition.)
    assert _count(contains="import pytest") == 7, (
        "the pytest probe did not re-run once each for "
        "unit/contract/smoke/coverage/traceability/governance"
    )
    # pytest-cov and coverage: needed by `all` and `coverage` = 2 calls each.
    assert _count(contains='m.version("pytest-cov")') == 2, (
        "the pytest-cov probe did not re-run for `coverage`"
    )
    assert _count(contains='m.version("coverage")') == 2, (
        "the coverage probe did not re-run for `coverage`"
    )
    # pre-commit: needed by `all` and `hooks` = 2 calls, not 1.
    assert _count(exact="-m pre_commit --version") == 2, (
        "pre-commit --version did not re-probe for `hooks`"
    )
    # vulture: needed by `all` and `dead` = 2 calls, not 1.
    assert _count(exact="-m vulture --version") == 2, (
        "vulture --version did not re-probe for `dead`"
    )
    # pip-audit: needed by `all` and `audit` = 2 calls, not 1.
    assert _count(exact="-m pip_audit --version") == 2, (
        "pip_audit --version did not re-probe for `audit`"
    )


def test_all_run_prints_the_preflight_banner_once_not_once_per_stage(tmp_path: Path) -> None:
    """The log-caching side of the round-7 distinction, checked at the same
    time as (not instead of)
    ``test_the_actual_pin_check_reexecutes_once_per_stage_that_needs_it``
    above proves the underlying comparison is NOT deduplicated. ``all``
    front-loads every pin in its own ``preflight all`` call, so the six
    per-stage ``preflight()`` calls that follow (inside stage_lint,
    stage_typecheck, stage_unit, stage_contract, stage_smoke, stage_hooks)
    each have nothing NEW to announce — see ``pf_new`` in ci/checks.sh's
    ``preflight()`` — and must not reprint the banner.

    Matched WITHOUT the em dash ``log()`` actually prints: this harness's
    subprocess capture decodes the child's UTF-8 output with this authoring
    box's (Windows) default text codec, which turns the multi-byte em dash
    into mojibake rather than raising — every other assertion in this suite
    that reads captured prose already avoids depending on that non-ASCII byte
    for the same reason; this is only the first assertion that needs the
    banner text specifically, so the substring here is chosen to still
    uniquely identify it without the dash.
    """
    recording = run_checks("all", tmp_path)
    assert recording.returncode == 0, recording.output
    banners = recording.stdout.count("pinned toolchain for stage")
    assert banners == 1, (
        f"expected exactly one preflight banner in a full `all` run (every pin is "
        f"front-loaded by the `preflight all` call at the top of stage_all), found "
        f"{banners}:\n{recording.stdout}"
    )


def test_a_wrong_interpreter_stops_the_stage(tmp_path: Path) -> None:
    """The interpreter is checked before any tool version is believed."""
    recording = run_checks("unit", tmp_path, stubs=Stubs(py_minor="3.11", py_full="3.11.9"))
    assert recording.returncode != 0
    assert "wrong Python interpreter" in recording.output
    assert [call for call in recording.of("python") if "-m" in call.argv] == []


# =============================================================================
# ROUND 8 — THE PRIMARY, AUTHORITATIVE no-memoization tests.
#
# Rounds 3-7 tried to prove "no cached flag can bypass a pin check" by banning
# SOURCE-TEXT SHAPES: a literal `return 0`, specific variable-name spellings, a
# `\breturn\s+(\d+)\b` regex over `require_python_interpreter`'s body. Round 7's
# OWN test suite was defeated within a single round by a lazy-init two-statement
# idiom (`_od_cache=1` assigned later in the function body, `${_od_cache:-0}`
# consulted at the top) that is invisible to every one of those shapes at once:
# a bare `return` (no digit) is invisible to `\d+`; a name assigned ANYWHERE in
# the function is classified as script-internal by the environment-read
# detector's assignment scan, with no notion of WHEN, relative to the read, that
# assignment happens.
#
# The operator's fix, adopted here: stop enumerating shapes and assert the ONE
# property that actually matters, BEHAVIOURALLY, as a pure black box — feed the
# SAME probe a correct answer, then (within the SAME `sh ci/checks.sh all`
# process, so whatever state a bypass would have accumulated by then is exactly
# what it would have) a WRONG one, and assert the wrong answer is what the stage
# acts on. This does not care what the implementation's control flow, variable
# names or caching mechanism look like — including ones nobody has invented yet
# — because it never reads the source at all. See `shell_harness.run_checks`'s
# `python_sequences` parameter for the mechanism.
#
# These two tests are now the PRIMARY defense; everything in
# `test_ci_contract.py` that bans a specific source-text shape (the four
# detectors culminating in `test_no_memoized_flag_gates_pin_verification`,
# rounds 3-7) is SECONDARY, defense-in-depth only, as of this round — see that
# file's own updated docstrings. The distinction matters operationally: a
# review that finds a new bypass shape which defeats every existing
# SOURCE-LEVEL detector still has to defeat THESE two, because these assert an
# outcome, not a spelling.
# =============================================================================


def test_the_interpreter_check_tracks_the_current_probe_result_not_a_cached_one(
    tmp_path: Path,
) -> None:
    """PRIMARY. ``require_python_interpreter()`` re-run behaviourally, not
    grepped.

    A full ``all`` run probes the interpreter's minor version 8 times (once per
    non-empty-pin preflight() call: all, lint, typecheck, unit, contract,
    smoke, coverage, hooks). Here call 1 (inside ``preflight all``) reports the CORRECT
    pinned minor version; call 2 (inside ``stage_lint``'s own ``preflight
    lint``, the very next thing ``stage_all`` runs) reports a WRONG one.

    If ANY caching mechanism — memoised flag, lazy-init cache variable, or any
    shape not yet invented — let call 2 be skipped or its result overridden by
    call 1's cached success, this run would sail through to completion (exit
    0), because every OTHER stub value here is the correct pin. It does not:
    the run must stop exactly at call 2, with call 2's wrong answer reported as
    the failure, proving the SECOND real probe — not the first, not a cache of
    the first — decided the outcome.
    """
    recording = run_checks(
        "all",
        tmp_path,
        python_sequences={"PY_MINOR": (PINS["PYTHON_VERSION"], "9.9")},
    )
    assert recording.returncode != 0, (
        "a wrong python minor version on the SECOND interpreter probe of this "
        f"`all` run did not fail the run at all:\n{recording.output}"
    )
    assert "wrong Python interpreter" in recording.output, recording.output

    python_calls = recording.of("python")
    minor_probes = [call for call in python_calls if "version_info[:2]" in call.joined]
    assert len(minor_probes) == 2, (
        "expected exactly two interpreter minor-version probes before the run "
        "stopped (call 1 correct -> preflight('all') passes; call 2 wrong -> "
        f"stage_lint's preflight fails), found {len(minor_probes)}: "
        f"{[call.joined for call in python_calls]!r}"
    )
    # Call 1's correct answer must have actually been BELIEVED: the run must
    # have gotten far enough, on the strength of that one call alone, to probe
    # every one of the four tool pins inside preflight("all") — proof this is
    # not simply "the run fails immediately no matter what call 1 said".
    for probe in ("-m ruff --version", "-m mypy --version", "-m pre_commit --version"):
        assert any(call.joined == probe for call in python_calls), (
            f"{probe} never ran; preflight('all') did not get past the first "
            f"(correct) interpreter probe: {[call.joined for call in python_calls]!r}"
        )
    assert any("import pytest" in call.joined for call in python_calls), (
        f"pytest was never probed either: {[call.joined for call in python_calls]!r}"
    )
    # And ruff must NOT have been re-probed a second time for stage_lint: that
    # would mean the interpreter check's failure did not actually stop the
    # script — i.e. `set -e` / the `return 1` chain is not doing what the rest
    # of this suite assumes.
    ruff_probes = [call for call in python_calls if call.joined == "-m ruff --version"]
    assert len(ruff_probes) == 1, (
        "ruff was re-probed after the interpreter check should have already "
        f"stopped the run: {[call.joined for call in python_calls]!r}"
    )


def test_require_tool_tracks_the_current_probe_result_not_a_cached_one(tmp_path: Path) -> None:
    """PRIMARY. Same claim as the interpreter test above, aimed at
    ``require_tool()`` specifically — spec-guardian's round-8 finding was that
    this exact function, the direct descendant of the removed
    ``checked_tools`` memo and the function whose subprocess comparison
    ``require_pinned_tool`` delegates to, was never inspected by ANY
    round-3-through-7 shape check.

    Ruff's version is probed twice in a full ``all`` run (``preflight all``,
    then ``stage_lint``'s own ``preflight lint``). Call 1 reports the correct
    pinned ruff version; call 2 reports a wrong one. As above: if a cached
    success from call 1 could stand in for call 2, the run would reach ``ruff
    check`` and exit 0. It must not.
    """
    recording = run_checks(
        "all",
        tmp_path,
        python_sequences={"RUFF": (PINS["RUFF_VERSION"], "0.0.1")},
    )
    assert recording.returncode != 0, (
        f"a wrong ruff version on the SECOND probe of this `all` run did not "
        f"fail the run:\n{recording.output}"
    )
    assert "pinned-tool mismatch" in recording.output, recording.output
    assert "tool:         ruff" in recording.output, recording.output

    python_calls = recording.of("python")
    ruff_probes = [call for call in python_calls if call.joined == "-m ruff --version"]
    assert len(ruff_probes) == 2, (
        "expected exactly two `-m ruff --version` probes before the run "
        "stopped (call 1 correct -> preflight('all') passes; call 2 wrong -> "
        "stage_lint's preflight fails on require_tool's REAL comparison), "
        f"found {len(ruff_probes)}: {[call.joined for call in python_calls]!r}"
    )
    # Call 1's correct answer must have been believed: proof the run reached
    # stage_lint at all, not merely failed on ruff's very first probe.
    minor_probes = [call for call in python_calls if "version_info[:2]" in call.joined]
    assert len(minor_probes) == 2, (
        "expected the interpreter to have been re-probed for stage_lint too, "
        "proving preflight('all') passed on ruff's correct call 1: "
        f"{[call.joined for call in python_calls]!r}"
    )
    for probe in ("-m mypy --version", "-m pre_commit --version"):
        assert any(call.joined == probe for call in python_calls), (
            f"{probe} never ran; preflight('all') did not get past ruff's "
            f"correct call 1: {[call.joined for call in python_calls]!r}"
        )
    ran_ruff_check = [call for call in python_calls if "check" in call.argv]
    assert ran_ruff_check == [], (
        f"`ruff check` ran despite the second (wrong) probe: {ran_ruff_check!r}"
    )


# =============================================================================
# ROUND 9 — the `_od_seq_next` clamp-to-last-value branch (calls beyond the
# queued sequence's length repeat the last queued value; see the mechanism's
# own block comment in shell_harness.py) was, until now, asserted only in
# that comment, never exercised: every existing consumer of `python_sequences`
# supplies exactly two values in a run that deliberately fails on the second
# one, so the run halts before a third call to that probe ever happens. This
# drives the SAME two-element PY_MINOR sequence shape used elsewhere in this
# file, but with BOTH values correct, against `run_checks("all", ...)` — the
# stage that probes PY_MINOR seven times per
# test_the_actual_pin_check_reexecutes_once_per_stage_that_needs_it above —
# so calls 3 through 7 all fall past the two-line queue and must take the
# clamp branch, repeating the correct second value, for the run to reach exit
# 0 at all. Not added to LOAD_BEARING_BEHAVIOURAL_TESTS: this exercises the
# TEST HARNESS's own stub mechanism, not a property of ci/checks.sh itself —
# out of scope for that guard, which is specifically about ci/checks.sh's
# anti-bypass machinery.
# =============================================================================


def test_sequenced_stub_repeats_the_last_value_once_the_queue_is_exhausted(
    tmp_path: Path,
) -> None:
    """Exercises the previously-dead clamp branch in ``_od_seq_next``.

    A two-value PY_MINOR sequence (both the correct pinned minor version)
    against a full ``all`` run, which probes PY_MINOR thirteen times (once per
    ``preflight()`` call that verifies the interpreter: all, lint, typecheck,
    unit, contract, smoke, coverage, dead, audit, traceability, governance,
    hooks, and — pin-less but Python-executing, RB-008 F2 — projections; the
    same count ``test_the_actual_pin_check_reexecutes_once_per_stage_that_
    needs_it`` measures for an unsequenced run). Calls 1 and 2 consume the two
    queued values directly; calls 3-13 each ask for an index past the two-line
    queue and must hit ``_od_seq_next``'s
    ``if [ "${_od_idx}" -gt "${_od_total}" ]; then _od_idx="${_od_total}"; fi``
    clamp, repeating line 2's value, for the run to still see the CORRECT
    minor version on every call and reach exit 0. If that branch were broken
    — returning an empty line past exhaustion, for example — the run would
    fail with a wrong-interpreter error well before stage_hooks, not exit 0.
    """
    recording = run_checks(
        "all",
        tmp_path,
        python_sequences={"PY_MINOR": (PINS["PYTHON_VERSION"], PINS["PYTHON_VERSION"])},
    )
    assert recording.returncode == 0, recording.output

    minor_probes = [call for call in recording.of("python") if "version_info[:2]" in call.joined]
    assert len(minor_probes) == 13, (
        "expected 13 interpreter minor-version probes across a full `all` run "
        "(2 from the queue, 11 past exhaustion via the clamp branch), found "
        f"{len(minor_probes)}: {[c.joined for c in recording.of('python')]!r}"
    )


# =============================================================================
# MAJOR 2 (round 5) — the "not installed" / "not a working interpreter"
# messages, previously undrivable through this harness at all.
#
# ``shell_harness.Stubs`` used to default every version knob to ``""`` and
# resolve blanks with ``stubs.X or PINS[...]``. Python's `or` makes an explicit
# ``Stubs(ruff="")`` indistinguishable from not overriding ``ruff`` at all, so
# there was no way to construct the state ci/checks.sh itself calls "(not
# installed, or not importable by this interpreter)" — the tool ran but printed
# nothing parseable. ``Stubs`` now treats ``None`` as "use the pin" and an
# explicit ``""`` as "the tool produced empty output", so these two branches —
# require_tool's and require_python_interpreter's — are exercised here for the
# first time in the suite.
# =============================================================================


def test_require_tool_reports_not_installed_when_the_tool_prints_nothing_parseable(
    tmp_path: Path,
) -> None:
    """``Stubs(ruff="")`` — the tool ran (rc 0) and produced no usable version.

    Before the ``None``-vs-``""`` sentinel this collapsed to "not overridden";
    the pinned ruff version was substituted in and the stage passed, so
    ``require_tool``'s ``[ -z "${rt_found}" ]`` branch — and its distinct
    "(not installed, or not importable by this interpreter)" message — was
    exercised by zero tests anywhere in this suite.
    """
    recording = run_checks("lint", tmp_path, stubs=Stubs(ruff=""))
    assert recording.returncode != 0, recording.output
    assert "pinned-tool mismatch" in recording.output
    assert "tool:         ruff" in recording.output
    assert "not installed, or not importable by this interpreter" in recording.output, (
        recording.output
    )
    ran_ruff = [call for call in recording.of("python") if "check" in call.argv]
    assert ran_ruff == [], f"the gate ran anyway: {ran_ruff!r}"


def test_require_python_interpreter_reports_not_working_when_python_prints_nothing_parseable(
    tmp_path: Path,
) -> None:
    """``Stubs(py_full="")`` — the interpreter ran (rc 0) but its version probe
    produced nothing parseable, distinct from ``test_a_wrong_interpreter_stops_the_stage``
    above, which reports a WRONG version rather than an unparseable one.

    ci/checks.sh's own diagnostic for this is
    ``fail_python("(PYTHON=${PYTHON} is not a working interpreter)")``; that
    exact parenthetical, not just the generic "wrong Python interpreter"
    header, is what is asserted here.
    """
    recording = run_checks("unit", tmp_path, stubs=Stubs(py_full=""))
    assert recording.returncode != 0, recording.output
    assert "wrong Python interpreter" in recording.output
    assert "is not a working interpreter" in recording.output, recording.output
    assert [call for call in recording.of("python") if "-m" in call.argv] == []


# =============================================================================
# MAJOR 5 (round 3) — per-stage scoping of the preflight.
# =============================================================================


def test_the_secrets_gate_consults_no_python_at_all(tmp_path: Path) -> None:
    """``sh ci/checks.sh gitleaks`` must work with Docker and git and nothing else.

    It used to call the full preflight, so a yanked hatchling, a PyPI blip or a
    stray ``mypy 2.3.1`` on PATH reddened a job named ``gitleaks`` with a message
    about mypy, and a fresh clone with Docker but no Python could not run the
    "check before I push" path at all. The stubs here report a broken
    interpreter and wrong versions for every tool; the stage must not care.
    """
    recording = run_checks(
        "gitleaks",
        tmp_path,
        stubs=Stubs(
            py_full="2.7.18",
            py_minor="2.7",
            ruff="0.0.1",
            mypy="0.0.1",
            pytest="0.0.1",
            pre_commit="0.0.1",
            python_rc=1,
        ),
    )
    assert recording.returncode == 0, recording.output
    assert recording.of("python") == (), (
        "the secrets gate invoked the Python interpreter: "
        f"{[call.argv for call in recording.of('python')]!r}"
    )


def test_the_specs_gate_consults_no_python_at_all(tmp_path: Path) -> None:
    """The second, and only other, genuinely Python-free stage.

    ``ci/validate_specs.sh`` is POSIX sh plus awk by design (design D13), which
    is why ``stage_specs`` declares no pins and why it is the one stage besides
    ``gitleaks`` exempt from the interpreter preflight. That exemption has to be
    pinned by a test in both directions: this asserts the stage really does run
    without Python, so the exemption is honest rather than a leftover.
    """
    recording = run_checks(
        "specs",
        tmp_path,
        stubs=Stubs(
            py_full="2.7.18",
            py_minor="2.7",
            ruff="0.0.1",
            mypy="0.0.1",
            pytest="0.0.1",
            pre_commit="0.0.1",
            python_rc=1,
        ),
    )
    assert recording.returncode == 0, recording.output
    assert recording.of("python") == (), (
        "the specs gate invoked the Python interpreter: "
        f"{[call.argv for call in recording.of('python')]!r}"
    )


def test_the_projections_stage_refuses_a_wrong_interpreter(tmp_path: Path) -> None:
    """RB-008 F2 — declaring no PINNED TOOL is not the same as running no Python.

    ``stage_projections`` executes ``python -m orbital_drift.projections``
    (ci/checks.sh), yet its ``stage_python_pins`` arm is empty and ``preflight``
    returned on an empty pin set BEFORE ``require_python_interpreter`` ever ran.
    The empty arm is honest — the module is pure stdlib, so there is no pinned
    DISTRIBUTION to assert — but the interpreter is itself a pin
    (ci/versions.env ``PYTHON_VERSION``), and this file's stated contract is
    that "the version a stage header PRINTS is the version that stage RUNS".

    ``projections`` was the only stage in that position: ``gitleaks`` and
    ``specs`` declare no pins AND execute no Python, and each has its own test
    above proving it. Asserted the same way ``test_a_wrong_interpreter_stops_
    the_stage`` asserts it for ``unit``, so the two cannot drift apart.
    """
    recording = run_checks("projections", tmp_path, stubs=Stubs(py_minor="3.11", py_full="3.11.9"))
    assert recording.returncode != 0, (
        f"the projections stage ran on an unpinned interpreter:\n{recording.output}"
    )
    assert "wrong Python interpreter" in recording.output, recording.output
    assert [call for call in recording.of("python") if "-m" in call.argv] == [], (
        "the module ran anyway, on the interpreter the preflight was supposed to "
        f"reject: {[call.argv for call in recording.of('python')]!r}"
    )


def _stubs_reporting_wrong(tool: str) -> Stubs:
    """A ``Stubs`` in which exactly ``tool`` reports an unpinned version."""
    if tool == "ruff":
        return Stubs(ruff="0.0.1")
    if tool == "mypy":
        return Stubs(mypy="0.0.1")
    if tool == "pytest":
        return Stubs(pytest="0.0.1")
    if tool == "pre-commit":
        return Stubs(pre_commit="0.0.1")
    raise AssertionError(f"no stub knob for {tool!r}")


@pytest.mark.parametrize(
    ("stage", "wrong", "other"),
    [
        ("lint", "ruff", "mypy"),
        ("typecheck", "mypy", "ruff"),
        ("unit", "pytest", "mypy"),
        ("hooks", "pre-commit", "ruff"),
    ],
)
def test_each_stage_asserts_its_own_pins_and_only_its_own(
    tmp_path: Path, stage: str, wrong: str, other: str
) -> None:
    """A stage fails on ITS pin and ignores a tool it does not execute."""
    broken = run_checks(stage, tmp_path / "broken", stubs=_stubs_reporting_wrong(wrong))
    assert broken.returncode != 0, f"{stage} did not check its own pin {wrong}"
    assert "pinned-tool mismatch" in broken.output
    assert f"tool:         {wrong}" in broken.output

    unrelated = run_checks(stage, tmp_path / "unrelated", stubs=_stubs_reporting_wrong(other))
    assert unrelated.returncode == 0, (
        f"{stage} failed because {other} is the wrong version, but it never runs {other}. "
        f"Output:\n{unrelated.output}"
    )


# =============================================================================
# MAJOR 9 — SKIP= and PRE_COMMIT_ALLOW_NO_CONFIG must not reach pre-commit.
# =============================================================================


def _pre_commit_call(recording: Recording) -> Call:
    matches = [call for call in recording.of("python") if "pre_commit" in call.argv]
    runs = [call for call in matches if "run" in call.argv]
    assert runs, f"pre-commit was never run: {[call.argv for call in recording.of('python')]!r}"
    return runs[-1]


@pytest.mark.parametrize(
    "hostile_env",
    [
        {"SKIP": "gitleaks,shellcheck"},
        {"SKIP": "shellcheck"},
        {"PRE_COMMIT_ALLOW_NO_CONFIG": "1"},
        {"SKIP": "gitleaks", "PRE_COMMIT_ALLOW_NO_CONFIG": "1"},
    ],
    ids=["skip-both", "skip-shellcheck", "allow-no-config", "both"],
)
def test_pre_commit_escape_hatches_do_not_survive_into_the_hook_run(
    tmp_path: Path, hostile_env: dict[str, str]
) -> None:
    """Asserted on the environment pre-commit actually received.

    The source-level version asserted the substring ``unset SKIP`` with no
    ordering constraint, which
    ``_saved="${SKIP:-}"; unset SKIP; export SKIP="${_saved}"`` satisfies while
    restoring the bypass. ``SKIP=gitleaks`` is named in
    ``.pre-commit-config.yaml`` as the exact reflex that must never be trained,
    and shellcheck has no other invocation anywhere in the repository — skipping
    it removes the only lint on ``ci/checks.sh`` itself.
    """
    recording = run_checks("hooks", tmp_path, extra_env=hostile_env)
    assert recording.returncode == 0, recording.output

    call = _pre_commit_call(recording)
    assert call.env["SKIP"] is None, (
        f"pre-commit was invoked with SKIP={call.env['SKIP']!r}; the stage is a gate "
        "and has no opt-out"
    )
    assert call.env["PRE_COMMIT_ALLOW_NO_CONFIG"] is None, (
        "pre-commit was invoked with PRE_COMMIT_ALLOW_NO_CONFIG set, which turns a "
        "missing hook config into a pass"
    )
    if "SKIP" in hostile_env:
        assert "ignoring SKIP=" in recording.output, (
            "the stage silently dropped SKIP; it must say so, or an operator who "
            "expects a skip reads the green result as confirmation that it happened"
        )


# =============================================================================
# MINOR 16 — stage_hooks must never hand pre-commit an empty file list.
# =============================================================================


def test_hooks_uses_all_files_once_anything_is_tracked(tmp_path: Path) -> None:
    """The branch CI takes has never run: this repository has no commits.

    It appears the moment the scaffold lands, so it is exercised here instead of
    on the operator's first push.
    """
    recording = run_checks("hooks", tmp_path, stubs=Stubs(tracked=b"README.md\npyproject.toml\n"))
    assert recording.returncode == 0, recording.output

    call = _pre_commit_call(recording)
    assert "--all-files" in call.argv, call.argv
    assert "--hook-stage" in call.argv and "manual" in call.argv, call.argv
    assert "--files" not in call.argv, call.argv


def test_hooks_passes_the_untracked_set_explicitly_before_the_first_commit(
    tmp_path: Path,
) -> None:
    """No ``xargs -0``: it is a GNU/BSD extension and node A's ``/bin/sh`` is dash."""
    recording = run_checks(
        "hooks", tmp_path, stubs=Stubs(tracked=b"", untracked=b"a b.md\x00c'd.md\x00")
    )
    assert recording.returncode == 0, recording.output

    call = _pre_commit_call(recording)
    assert "--files" in call.argv, call.argv
    files = call.argv[call.argv.index("--files") + 1 :]
    assert list(files) == ["a b.md", "c'd.md"], (
        f"file list was mangled: {files!r}. Paths with spaces and quotes must survive."
    )


def test_hooks_refuses_to_report_a_pass_with_nothing_to_check(tmp_path: Path) -> None:
    """GNU ``xargs`` without ``-r`` runs the command once on empty input.

    With both ``git ls-files`` sets empty that meant pre-commit ran with zero
    files, every hook reported "no files to check", and the stage exited 0
    having verified nothing — a vacuous pass in the stage whose entire purpose
    is to stop vacuous passes.
    """
    recording = run_checks("hooks", tmp_path, stubs=Stubs(tracked=b"", untracked=b""))
    assert recording.returncode != 0, "stage_hooks exited 0 with no files to check at all"
    assert "Refusing to report a vacuous pass" in recording.output
    assert [call for call in recording.of("python") if "run" in call.argv] == []


def test_hooks_does_not_announce_a_gitleaks_version_it_never_runs(tmp_path: Path) -> None:
    """``--hook-stage manual`` drops the gitleaks hook, so nothing gitleaks runs here.

    Printing ``gitleaks 8.30.1`` in this stage's header implied an enforcement
    that was not happening; the hook is enforced by
    tests/unit/test_gitleaks_positive_control.py instead.
    """
    recording = run_checks("hooks", tmp_path)
    assert recording.returncode == 0, recording.output
    assert "gitleaks" not in recording.stdout, (
        "stage_hooks still mentions gitleaks, but no gitleaks hook executes at "
        f"--hook-stage manual:\n{recording.stdout}"
    )
    probes = [call for call in recording.of("docker") if call.argv[-1] in {"version", "--version"}]
    assert [call.argv[-1] for call in probes] == ["--version"], (
        "stage_hooks probes an image other than shellcheck's; it runs no other container"
    )


# =============================================================================
# Infrastructure failures must not be reported as pin drift.
# =============================================================================


def test_a_docker_failure_is_not_reported_as_version_drift(tmp_path: Path) -> None:
    """The remediation printed must not send the operator to edit correct pins."""
    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(docker_rc=125))
    assert recording.returncode != 0
    assert "INFRASTRUCTURE" in recording.output
    assert "do not edit ci/versions.env" in recording.output
    assert _scans(recording) == ()


def test_a_genuine_version_mismatch_names_a_usable_repository(tmp_path: Path) -> None:
    """``${ref%%:*}`` mangled the remediation command on two kinds of reference.

    On a digest-only reference it produced ``repo@sha256``; on a registry with a
    port it produced the hostname alone. Either way the ``docker pull`` line the
    operator is told to run is garbage.
    """
    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(gitleaks_reports="9.9.9"))
    assert recording.returncode != 0
    assert "pinned-container version mismatch" in recording.output
    assert "docker pull ghcr.io/gitleaks/gitleaks:v" in recording.output, recording.output
    assert "@sha256:v" not in recording.output


# =============================================================================
# MAJOR 3 (round 5) — stage_unit must fail fast, not pytest.skip(), without
# Docker.
#
# The stub harness above puts a fake `docker` on PATH by construction (that is
# how it observes what ci/checks.sh would have run), so "Docker is genuinely
# absent from PATH" cannot be driven through `run_checks`/`Stubs` at all. This
# section drives the REAL `sh ci/checks.sh unit` directly, with a PATH that
# genuinely does not provide `docker`, which is the only way to prove
# docker_or_fail's branch fires for real rather than trusting that the stub
# harness's model of it is faithful. The git section below does the same thing
# with the two tools swapped.
#
# ROUND 11 — HOW THAT PATH IS BUILT, and the CI-only defect that came of
# building it the other way round. This is the standing "the workflow has never
# run on a real runner" risk paying out, on the first real GitHub Actions run of
# this repository's own CI.
#
# The original helper SUBTRACTED from the real PATH: drop every directory that
# provides the tool under test.
#
#     PATH = [d for d in PATH if not (d/"git").exists()]
#
# On the Windows/Git-Bash authoring box `git` lives in /mingw64/bin while
# `sed`, `tr`, `sort`, `tail` and `dirname` live in /usr/bin, so dropping git's
# directory left every coreutil in place and all three tests passed — by
# accident of that layout, not by design. On ubuntu-24.04 /usr/bin provides
# `git` AND every coreutil, so dropping it removed the lot, and ci/checks.sh
# died on its own first line:
#
#     /home/runner/work/.../ci/checks.sh: 63: dirname: not found
#     /home/runner/work/.../ci/checks.sh: 70: .: cannot open .../versions.env
#     assert 'git is not on PATH' in '<that stderr>'
#
# 3 failed / 234 passed, with an assertion message pointing at the guard while
# the actual fault was in the PATH the test itself had constructed. What those
# runs measured was "does ci/checks.sh survive a PATH with no coreutils", which
# is not what any of these three tests is about. (The script's own half of that
# defect — a `dirname` dependency on line one, before any diagnostic machinery
# exists — is fixed in ci/checks.sh and covered by its own tests at the end of
# this section.)
#
# SO: BUILD THE PATH, DO NOT SUBTRACT FROM IT. `_toolbox_without` populates one
# directory with a launcher for each command ci/checks.sh genuinely needs
# (enumerated and justified at `_UNIT_PREGUARD_COMMANDS`), deliberately omitting
# the one under test, and PATH becomes exactly that one directory. What is
# present is then a property of this file rather than of the host's filesystem
# layout — which is the property that was missing, and the reason a green local
# run said nothing about a Linux runner.
#
# ABSENCE HAS TO BE REAL ABSENCE. Both guards use `command -v`, which resolves a
# NAME to a FILE and succeeds for any executable file whatever that file does
# when run: a stub `docker` that exits 1 would leave `command -v docker`
# succeeding, the guard silently not firing, and these tests passing for the
# wrong reason. The tool under test therefore has no file in the toolbox at all,
# and `test_the_reduced_path_makes_command_v_fail_for_the_omitted_tool` asserts
# that directly rather than leaving it as a claim.
# =============================================================================

# Every external command `sh ci/checks.sh unit` runs BEFORE either of
# stage_unit's fail-fast guards can print anything.
#
# DETERMINED EMPIRICALLY, leave-one-out: each name below was removed from an
# otherwise-complete toolbox and both scenarios re-run. Every removal stopped
# the guard message appearing (or, for `rm`, corrupted the exit status), and no
# other command's removal changed anything. Recorded here so the next person
# does not have to rediscover it by reading ci/checks.sh top to bottom.
#
#   sh      `_healthy_docker_shim`'s `#!/usr/bin/env sh` line. `env` resolves
#           `sh` on PATH, so the toolbox has to carry it.
#   sed     pin_value() and versions_env_tools(), both reading ci/versions.env.
#   tail    pin_value() takes the last match.
#   tr      versions_env_tools(), require_python_interpreter(), tool_version().
#   sort    declared_stage_pins() -> `sort -u`, via require_pin_coverage().
#   mktemp  docker_probe_errfile(), reached once docker_or_fail is past
#           `command -v docker` and into the round-10 `docker info` probe.
#   rm      the EXIT trap docker_probe_errfile() installs. Measured: without it
#           the guard still fires and still prints, but the trap fails and the
#           process exits 127 instead of 1, so the returncode assertions end up
#           measuring the trap.
#
# NOT here, deliberately: `awk`. tool_version() uses it for the ruff, mypy and
# pre-commit probes, and the unit stage's only pin is pytest, which is probed
# with `python -c 'import pytest;...'`. If that ever changes, the run fails with
# `awk: not found` and `_assert_only_the_omitted_tool_was_missing` names it and
# points back here — rather than the failure surfacing as a guard assertion,
# which is exactly how the round-11 defect hid.
_UNIT_PREGUARD_COMMANDS: Final[tuple[str, ...]] = (
    "sh",
    "sed",
    "tail",
    "tr",
    "sort",
    "mktemp",
    "rm",
)

# "the shell could not find a command", in the spellings the shells this project
# runs on actually use. Used to turn "the toolbox is incomplete" into a failure
# that SAYS SO, instead of the unreadable
# ``assert 'git is not on PATH' in '<coreutils error>'`` the round-11 CI run
# produced.
_MISSING_COMMAND_RES: Final[tuple[re.Pattern[str], ...]] = (
    # dash:  ci/checks.sh: 63: dirname: not found
    # bash:  ci/checks.sh: line 63: dirname: command not found
    re.compile(r":\s*(?:line\s+)?\d+:\s*([^\s:]+):\s*(?:command )?not found"),
    # `#!/usr/bin/env sh` with no sh on PATH:
    #        /usr/bin/env: 'sh': No such file or directory
    re.compile(r"\benv: '([^']+)': No such file or directory"),
)


def _launcher(directory: Path, name: str) -> None:
    """Put a working ``name`` in ``directory``, resolved from the REAL ``PATH``.

    A ``#!/bin/sh`` wrapper that ``exec``s the real binary by absolute path,
    rather than a symlink or a copy, because that is the one mechanism that
    works unprivileged on both platforms this project runs on:

    * ``os.symlink`` needs SeCreateSymbolicLinkPrivilege on Windows. Measured on
      the authoring box: ``OSError: [WinError 1314] A required privilege is not
      held by the client``.
    * a COPY of an MSYS binary cannot start from a directory that is not
      /usr/bin — Windows looks for msys-2.0.dll next to the executable first,
      and a toolbox directory has no copy of it.

    One code path for both platforms, so there is no second path to be wrong
    only on the one nobody runs locally. That is the whole lesson of round 11.
    """
    resolved = shutil.which(name)
    if resolved is None:
        raise RuntimeError(
            f"{name!r} is not on this machine's PATH, so ci/checks.sh cannot be run "
            "with a reduced PATH at all. It is in _UNIT_PREGUARD_COMMANDS because "
            "ci/checks.sh needs it before either stage_unit guard can speak."
        )
    quoted = "'" + Path(resolved).as_posix().replace("'", "'\\''") + "'"
    launcher = directory / name
    launcher.write_text(f'#!/bin/sh\nexec {quoted} "$@"\n', encoding="utf-8", newline="\n")
    launcher.chmod(0o755)


def _toolbox_without(directory: Path, *, omitted: str) -> Path:
    """One directory holding everything ci/checks.sh needs except ``omitted``.

    The returned directory is intended to be the ENTIRE ``PATH`` of the child.
    ``omitted`` is not written at all — no stub, no shim, no file — so
    ``command -v <omitted>`` genuinely fails inside it.
    """
    assert omitted not in _UNIT_PREGUARD_COMMANDS, (
        f"{omitted!r} is in _UNIT_PREGUARD_COMMANDS, so a toolbox omitting it cannot "
        "run ci/checks.sh far enough to reach any guard; the test would pass on a "
        "startup failure rather than on the guard it names"
    )
    toolbox = directory / "toolbox"
    toolbox.mkdir(parents=True, exist_ok=True)
    for name in _UNIT_PREGUARD_COMMANDS:
        _launcher(toolbox, name)
    assert not (toolbox / omitted).exists(), f"the toolbox provides {omitted!r} after all"
    return toolbox


def _command_v_rc(path: str, name: str) -> int:
    """Exit status of ``command -v <name>`` with ``PATH`` set to ``path``.

    ``command -v`` is what both guards in ``stage_unit`` use, so this is the
    exact predicate the reduced PATH has to change — not "does running the tool
    fail", which is a different and much weaker thing.
    """
    return subprocess.run(
        [posix_shell(), "-c", 'command -v "$1" >/dev/null 2>&1', "sh", name],
        env={**os.environ, "PATH": path},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    ).returncode


def _assert_only_the_omitted_tool_was_missing(output: str, *, omitted: str) -> None:
    """Fail about the TOOLBOX when the shell, not the guard, was what spoke.

    Without this, an incomplete ``_UNIT_PREGUARD_COMMANDS`` surfaces as
    ``assert 'git is not on PATH' in '<some coreutils error>'`` — which is
    precisely the message the round-11 CI run produced, and precisely why it
    read as a guard regression rather than as a bug in the test's own PATH.
    """
    missing = set()
    for pattern in _MISSING_COMMAND_RES:
        missing |= {name for name in pattern.findall(output) if name != omitted}
    assert not missing, (
        f"the shell could not find {sorted(missing)} while running ci/checks.sh with a "
        f"PATH built to omit only {omitted!r}. That is a gap in "
        "_UNIT_PREGUARD_COMMANDS (this file), not a failure of the guard under "
        f"test: ci/checks.sh needs each of those before its guards run. Full "
        f"output:\n{output}"
    )


def _run_real_unit_stage_without_docker(workspace: Path) -> subprocess.CompletedProcess[str]:
    """``sh ci/checks.sh unit`` with a genuinely docker-free ``PATH``.

    ``PYTHON`` is pointed at ``sys.executable`` — the interpreter running this
    very test process — rather than a stub, because it must actually satisfy
    ``preflight unit``'s pytest-version pin for the docker_or_fail branch
    (which runs AFTER preflight, inside stage_unit) to be what stops the
    stage, and this test process could not be running under pytest at all
    unless its own interpreter already does. It is invoked by ABSOLUTE path, so
    the reduced ``PATH`` cannot affect which interpreter runs; the same is true
    of ``sh`` itself, resolved up front by ``posix_shell()`` from the real
    ``PATH``.
    """
    env = dict(os.environ)
    env["PATH"] = str(_toolbox_without(workspace, omitted="docker"))
    env["PYTHON"] = sys.executable
    return subprocess.run(
        [posix_shell(), str(CHECKS_SH), "unit"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_reduced_path_makes_command_v_fail_for_the_omitted_tool(tmp_path: Path) -> None:
    """The crux the other tests in this section rest on, asserted rather than assumed.

    Both guards ask ``command -v <tool>``, which resolves a NAME to a FILE and
    succeeds for any executable file regardless of what running it does. A stub
    that exists and exits non-zero therefore does NOT simulate absence: it would
    leave every test below passing while the guard never fired. So this measures
    the actual predicate — ``command -v`` returning non-zero for the omitted
    tool, and zero for everything ci/checks.sh needs — inside the very PATH the
    other tests hand the script.
    """
    for omitted in ("docker", "git"):
        toolbox = _toolbox_without(tmp_path / omitted, omitted=omitted)

        assert sorted(entry.name for entry in toolbox.iterdir()) == sorted(
            _UNIT_PREGUARD_COMMANDS
        ), (
            "the reduced PATH holds something other than exactly "
            "_UNIT_PREGUARD_COMMANDS. Its contents must be a property of this file, "
            "never of the host's filesystem layout — a PATH derived by SUBTRACTING "
            "from the host's is what made these tests pass on Windows and fail on "
            "ubuntu-24.04 (round 11)."
        )
        assert _command_v_rc(str(toolbox), omitted) != 0, (
            f"`command -v {omitted}` still SUCCEEDS under the reduced PATH {toolbox}. "
            "The guard tests below would then pass without the guard ever firing."
        )
        for needed in _UNIT_PREGUARD_COMMANDS:
            assert _command_v_rc(str(toolbox), needed) == 0, (
                f"`command -v {needed}` fails under the reduced PATH, so ci/checks.sh "
                "cannot reach its guards at all"
            )


def test_stage_unit_fails_fast_when_docker_is_genuinely_absent_from_path(
    tmp_path: Path,
) -> None:
    """A Docker-less machine used to see a false GREEN, silently.

    ``tests/unit/test_gitleaks_positive_control.py``'s own ``_tool("docker")``
    helper falls back to ``pytest.skip()`` outside CI, so before
    ``stage_unit`` gained its own ``docker_or_fail`` call,
    ``sh ci/checks.sh unit`` on a Docker-less machine ran pytest anyway,
    skipped the gitleaks-config-loading positive controls silently, and still
    exited 0 — false confidence that assertions README.md's own prerequisites
    table claims run had actually run. This measures the real script with a
    real docker-free PATH, not a simulation of one.
    """
    result = _run_real_unit_stage_without_docker(tmp_path)
    output = result.stdout + result.stderr

    _assert_only_the_omitted_tool_was_missing(output, omitted="docker")
    assert result.returncode != 0, f"stage_unit passed with no docker on PATH:\n{output}"
    assert "docker is not on PATH" in output, output
    assert "test session starts" not in output, (
        f"pytest ran despite docker being absent — the guard did not fail fast:\n{output}"
    )


def test_stage_unit_docker_message_is_distinct_from_the_other_two_stages(
    tmp_path: Path,
) -> None:
    """Three stages now call ``docker_or_fail``; each reason must name ITS OWN need.

    ``stage_gitleaks`` needs Docker for the Constitution VII scanner itself;
    ``stage_hooks`` needs it for the ``language: docker_image`` shellcheck
    hook; ``stage_unit`` needs it only for
    ``tests/unit/test_gitleaks_positive_control.py``'s positive controls. An
    operator reading any one of these three messages must be able to tell
    which stage they ran and why, not read three identical sentences.
    """
    unit_result = _run_real_unit_stage_without_docker(tmp_path / "no-docker")
    unit_output = unit_result.stdout + unit_result.stderr
    _assert_only_the_omitted_tool_was_missing(unit_output, omitted="docker")
    assert "test_gitleaks_positive_control.py" in unit_output, unit_output

    # gitleaks/hooks are exercised through the stub harness only as a
    # regression check that this fix has not broken their ordinary
    # docker-present path; their own docker_or_fail reason strings are static
    # literals in ci/checks.sh and are asserted directly below rather than by
    # also faking "docker absent" a second, different way for them.
    gitleaks_recording = run_checks("gitleaks", tmp_path / "gitleaks")
    gitleaks_reason = "the Constitution VII secret gate runs as a pinned container"
    hooks_reason = "the shellcheck pre-commit hook is language: docker_image"
    unit_reason = (
        "tests/unit/test_gitleaks_positive_control.py's positive controls run the "
        "pinned gitleaks container directly"
    )
    reasons = {gitleaks_reason, hooks_reason, unit_reason}
    assert len(reasons) == 3, f"two of the three docker_or_fail reasons are identical: {reasons}"
    assert gitleaks_reason not in unit_output
    assert hooks_reason not in unit_output
    # The stub-harness gitleaks run above is exercised only to confirm the
    # stage still passes normally with docker present, i.e. this test has not
    # broken the ordinary path while adding coverage of the message text.
    assert gitleaks_recording.returncode == 0, gitleaks_recording.output


# =============================================================================
# ROUND 6 / MAJOR 2 — the git analogue of the block above. spec-guardian and
# peer-reviewer split on whether stage_unit needed a git guard at all (see
# ci/checks.sh's git_or_fail comment and
# tests/unit/test_ci_contract.py's test_unit_stage_requires_git_for_its_own_named_reason
# for the full argument on both sides); the operator resolved it in favour of
# the guard. This section omits only git — not docker — from the built toolbox,
# so docker_or_fail (which runs first in stage_unit) still passes and
# git_or_fail is the guard actually exercised, exactly mirroring the docker
# section above with the two tools swapped.
#
# ROUND 11 — this is the test that made the platform dependency visible: on
# ubuntu-24.04 the /usr/bin that provides `git` also provides every coreutil
# ci/checks.sh needs, so the old subtract-from-PATH helper took them all out
# together. See the block comment at the head of the docker section for the
# measurement and the replacement.
# =============================================================================


def _healthy_docker_shim(directory: Path) -> Path:
    """A directory providing a ``docker`` that answers every probe successfully.

    ROUND 10 — ``stage_unit``'s docker guard now probes the DAEMON (``docker
    info``), not merely the binary, and it runs BEFORE the git guard this
    section is about. Without this shim the test below would pass only on a
    machine where Docker Desktop happened to be running and would otherwise
    fail with the daemon diagnostic — i.e. it would report "the git guard is
    broken" when the real cause was a stopped daemon, which is the exact
    misdiagnosis class round 10 exists to remove. Measured: it did precisely
    that on the authoring box, whose daemon was down while this was written.

    Only the tool this test is NOT about is faked. git stays genuinely absent
    from the child's PATH, which is the thing being measured.
    """
    shim_dir = directory / "docker-shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "docker"
    shim.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8", newline="\n")
    shim.chmod(0o755)
    return shim_dir


def _run_real_unit_stage_without_git(
    docker_shim: Path, workspace: Path
) -> subprocess.CompletedProcess[str]:
    """``sh ci/checks.sh unit`` with a genuinely git-free ``PATH``, docker healthy.

    Mirrors ``_run_real_unit_stage_without_docker`` exactly, omitting the other
    tool instead, so ``docker_or_fail`` (which runs first in ``stage_unit``)
    passes and ``git_or_fail`` is the guard actually exercised. ``sh`` itself is
    resolved once, up front, from the CURRENT (unmodified) ``PATH`` by
    ``posix_shell()`` and then invoked by its absolute path, so the reduced
    ``PATH`` cannot remove the shell needed to run the script — and the toolbox
    carries a second, PATH-resolvable ``sh`` besides, because
    ``_healthy_docker_shim``'s ``#!/usr/bin/env sh`` line needs one.
    """
    env = dict(os.environ)
    toolbox = _toolbox_without(workspace, omitted="git")
    env["PATH"] = str(docker_shim) + os.pathsep + str(toolbox)
    env["PYTHON"] = sys.executable
    return subprocess.run(
        [posix_shell(), str(CHECKS_SH), "unit"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_stage_unit_fails_fast_when_git_is_genuinely_absent_from_path(tmp_path: Path) -> None:
    """A Docker-having-but-git-less machine used to see a false GREEN, silently.

    5 of tests/unit/test_gitleaks_positive_control.py's 8 positive controls
    drive git directly (``git init``, ``git add -A``) — via ``_tool("git")``,
    directly or through the ``_scaffold``/``gate_root`` fixture — to build
    the synthetic repositories and staged indices those same tests then scan.
    That file's own ``_tool()`` helper falls back to ``pytest.skip()``
    outside CI, so before ``stage_unit`` gained its own ``git_or_fail`` call,
    ``sh ci/checks.sh unit`` on a machine with Docker but no git on PATH ran
    pytest anyway, skipped those five silently, and still exited 0 — the same
    false-confidence shape MAJOR 3 (round 5) found and fixed for Docker. This
    measures the real script with a real git-free PATH, not a simulation.
    """
    result = _run_real_unit_stage_without_git(_healthy_docker_shim(tmp_path), tmp_path)
    output = result.stdout + result.stderr

    _assert_only_the_omitted_tool_was_missing(output, omitted="git")
    assert result.returncode != 0, f"stage_unit passed with no git on PATH:\n{output}"
    assert "git is not on PATH" in output, output
    assert "test_gitleaks_positive_control.py" in output, (
        f"stage_unit's git_or_fail reason does not name what actually needs git:\n{output}"
    )
    assert "test session starts" not in output, (
        f"pytest ran despite git being absent — the guard did not fail fast:\n{output}"
    )
    assert "docker is not on PATH" not in output, (
        f"the git-absence guard produced the DOCKER message instead:\n{output}"
    )
    assert "the Docker daemon is not reachable" not in output, (
        "the git-absence guard produced the round-10 DAEMON message instead, which "
        f"means the docker shim above is no longer satisfying docker_or_fail:\n{output}"
    )


# =============================================================================
# ROUND 11 — THE PRODUCT HALF of the same defect.
#
# The three tests above had a bug. ci/checks.sh had one too, and it is the
# deeper of the two: its very first executable line resolved SCRIPT_DIR with
# `$(dirname -- "$0")` — an EXTERNAL command — before ci/versions.env is
# sourced, before a single diagnostic function is defined, before any guard
# exists to speak. So on a minimal, hostile or merely unusual PATH the operator
# got a coreutils error and a phantom missing file
#
#     ci/checks.sh: 63: dirname: not found
#     ci/checks.sh: 70: .: cannot open .../versions.env: No such file
#
# instead of this script's own message about whatever was actually wrong —
# precisely when good diagnostics matter most. That is a defect independent of
# the tests that exposed it: fixing only the tests would have left the script
# one unusual PATH away from the same unreadable failure, with nothing watching.
#
# SCRIPT_DIR is now derived with POSIX parameter expansion and shell builtins
# only. The two tests below pin both halves of that: the resolution itself
# (with NO PATH AT ALL), and the fact that a reduced PATH now reaches a real
# guard instead of dying at line one.
# =============================================================================


def test_checks_sh_resolves_script_dir_with_no_path_at_all() -> None:
    """``PATH=""`` and the script still speaks for itself.

    The unknown-stage branch of the dispatch ``case`` is chosen deliberately: it
    is the only path through ci/checks.sh that runs ZERO external commands, so
    an empty PATH isolates exactly the top-of-file resolution this test is
    about. Reaching it at all proves both halves of that resolution worked —
    SCRIPT_DIR pointed at ci/, and ``. "${VERSIONS_ENV}"`` succeeded, which
    under ``set -e`` it must have or the dispatch below it would never have run.

    (``case``, ``cd``, ``pwd``, ``[`` and ``printf`` are builtins in both dash —
    the shell ubuntu-24.04 and node A run — and bash. Nothing here needs PATH.)

    Measured before the fix, same invocation:
        ``dirname: not found`` then ``cannot open .../versions.env``, rc 1.
    """
    result = subprocess.run(
        [posix_shell(), str(CHECKS_SH), "definitely-not-a-stage"],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": ""},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr

    assert "dirname" not in output, (
        f"ci/checks.sh still shells out to dirname before it can diagnose anything:\n{output}"
    )
    assert "versions.env" not in output, (
        "ci/checks.sh failed to source ci/versions.env, which means SCRIPT_DIR did not "
        f"resolve to ci/:\n{output}"
    )
    assert result.returncode == 2, (
        f"expected the script's own usage exit (2), got {result.returncode}:\n{output}"
    )
    assert "unknown stage: definitely-not-a-stage" in output, output
    assert "usage: sh ci/checks.sh" in output, output


def test_more_than_one_stage_argument_is_refused_instead_of_silently_ignored() -> None:
    """MEASURED (round 3, item A4): ``ci/checks.sh a b c`` ran ``a`` and exited 0.

    The dispatch reads ``case "${1:-all}"`` and never looks at ``$2..$n``, so

        sh ci/checks.sh lint typecheck dead audit specs traceability
            projections governance

    printed the preflight and lint blocks ONLY and returned rc=0 — measured on
    this tree at 8a7f814. Seven of the eight named gates never ran, and the exit
    status said everything passed. Any "eight stages rc=0" claim produced that
    way is false, which is exactly the silently-green class RB-008 part (1)
    exists to close, sitting in the single gate source (design D1).

    Refusing is the only safe reading. Running all of them instead would be a
    behaviour change to the CI contract (``.github/workflows/ci.yml`` invokes
    one stage per job, and ``stage_all`` already exists for "run everything"),
    and guessing between the two is how a runner acquires a second, undocumented
    mode. rc=2 is the script's own usage exit, the same one an unknown stage
    already takes.

    Run directly rather than through ``run_checks``: the guard must fire BEFORE
    any preflight, tool probe or stage body, so no stub is needed — and needing
    none is part of the claim.
    """
    result = subprocess.run(
        [posix_shell(), str(CHECKS_SH), "lint", "typecheck"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 2, (
        "ci/checks.sh accepted two stage arguments instead of refusing them "
        f"(rc={result.returncode}). The dispatch `case` reads only $1, so every "
        "argument after the first is silently discarded and the exit status "
        f"reports on ONE stage while the command named several:\n{output}"
    )
    assert ">>> lint" not in output, (
        "the refusal came AFTER the first stage had already started — the point "
        "is that nothing executes when the invocation is ambiguous, so a partial "
        f"run cannot be mistaken for the whole one:\n{output}"
    )
    assert "typecheck" in output, (
        "the refusal does not echo the arguments it rejected, so an operator "
        f"cannot see which invocation was refused:\n{output}"
    )
    assert "usage: sh ci/checks.sh" in output, (
        f"the refusal prints no usage line, so it does not say what the accepted form is:\n{output}"
    )


def test_the_usage_line_still_names_every_stage_with_no_path_at_all() -> None:
    """``usage()`` must not go mute — or noisy — on the one path built for that.

    The dispatch refusals are the paths through this script that must still
    speak when nothing else works (see
    ``test_checks_sh_resolves_script_dir_with_no_path_at_all``), and both of
    them print ``usage()``. MEASURED under ``PATH=""`` before the fix, in dash
    AND bash: ``tr`` is not found, the command substitution yields nothing, and
    the operator gets

        tr: not found
        usage: sh ci/checks.sh <>

    — an error line about a helper they did not invoke, and a usage message
    naming zero of the fifteen stages, at exactly the moment the message is the
    only thing left working. ``command -p`` resolves ``tr`` from the standard
    utilities path (``getconf PATH``) rather than from ``PATH``, so the list
    renders regardless.
    """
    match = re.search(r"^STAGE_LABELS='([^']*)'$", CHECKS_SH.read_text(encoding="utf-8"), re.M)
    assert match, "could not parse STAGE_LABELS out of ci/checks.sh"
    rendered = "|".join(match.group(1).split())

    result = subprocess.run(
        [posix_shell(), str(CHECKS_SH), "lint", "typecheck"],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": ""},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 2, (
        f"the refusal did not reach its usage exit under an empty PATH:\n{output}"
    )
    assert f"usage: sh ci/checks.sh <{rendered}>" in output, (
        "the usage line lost its stage list under an empty PATH — it renders "
        f"STAGE_LABELS through an external command that PATH could not resolve:\n{output}"
    )
    assert "not found" not in output, (
        "the refusal wrote a 'not found' diagnostic about its own helper, which "
        f"an operator reads as a second failure:\n{output}"
    )


def test_a_reduced_path_reaches_the_guard_instead_of_dying_at_script_dir(
    tmp_path: Path,
) -> None:
    """The round-11 CI failure, as a regression test rather than as prose.

    Distinct from ``test_stage_unit_fails_fast_when_docker_is_genuinely_absent
    _from_path`` above, which asserts the GUARD's behaviour and would still pass
    if a future edit reintroduced an external-command dependency at the top of
    the script under a PATH that happened to provide it. This one asserts the
    absence of the specific startup failure by its own text, so a regression
    names itself.
    """
    result = _run_real_unit_stage_without_docker(tmp_path)
    output = result.stdout + result.stderr

    assert "dirname" not in output, output
    assert "cannot open" not in output, output
    assert "versions.env: No such file" not in output, output
    assert "docker is not on PATH" in output, (
        "the reduced PATH did not reach docker_or_fail, so something before it "
        f"stopped the script:\n{output}"
    )


# =============================================================================
# ROUND 10 — the two blocks above BOTH asked the wrong question about Docker,
# and this one is not a constructed mutation: the orchestrator hit it live
# while running the final confirmation gate.
#
# Docker Desktop stopped on the authoring box between test runs. `command -v
# docker` still succeeded — the client binary is on PATH at
# /c/Program Files/Docker/Docker/resources/bin/docker whether or not the daemon
# is up — so `docker_or_fail` passed, pytest started, and
# `PYTHON=<3.12> sh ci/checks.sh all` reported
#
#     8 failed, 211 passed
#
# with messages like "the gate passed on a planted Fernet key" and "a committed
# kubeconfig passed the gate", because tests/unit/test_gitleaks_positive_control.py
# asked the (unreachable) daemon to scan and got a connection error back where
# a leak report was expected. The operator was told the Constitution VII
# secrets gate was broken. It was not; Docker Desktop was not started.
#
# The stub harness cannot stop the real daemon, and does not have to: the state
# that caused this is precisely "a `docker` on PATH that fails when asked to do
# daemon work", which is a stub. ``Stubs.docker_info_rc`` drives it, separately
# from ``docker_rc`` so the pre-existing "the daemon is up but a `docker run`
# failed" tests above keep testing what they were written to test.
# =============================================================================


def _pytest_runs(recording: Recording) -> list[Call]:
    """Recorded ``python -m pytest <dir>`` invocations — a suite actually starting.

    Deliberately NOT ``"pytest" in call.joined``: the preflight probes the
    pinned pytest version with ``python -c 'import pytest;...'`` on every stage
    that needs it, and that probe running is correct and expected. What must
    not happen when a guard fires is a SUITE starting.
    """
    return [call for call in recording.of("python") if call.argv[:2] == ("-m", "pytest")]


def test_stage_unit_fails_fast_when_the_docker_daemon_is_unreachable(tmp_path: Path) -> None:
    """The live defect: docker on PATH, daemon down, 8 misleading gate failures.

    The guard must fire BEFORE pytest starts and the message must name the
    daemon, not leave the operator staring at eight assertions claiming the
    secrets gate let a Fernet key through.
    """
    recording = run_checks("unit", tmp_path, stubs=Stubs(docker_info_rc=1))
    output = recording.output

    assert recording.returncode != 0, f"stage_unit passed with the daemon down:\n{output}"
    assert "the Docker daemon is not reachable" in output, output
    assert "Docker Desktop may not be running" in output, (
        f"the message does not name the most likely cause on the authoring box:\n{output}"
    )
    assert "docker info" in output, (
        f"the message gives the operator no command to reproduce the failure with:\n{output}"
    )
    assert _pytest_runs(recording) == [], (
        "pytest started despite the daemon being unreachable — the guard did not fail "
        f"fast, which is the entire defect: {[call.joined for call in _pytest_runs(recording)]}"
    )
    assert "docker is not on PATH" not in output, (
        f"the daemon-down state was misreported as the binary being missing:\n{output}"
    )


def test_the_unit_stage_daemon_message_still_says_why_unit_needs_docker(
    tmp_path: Path,
) -> None:
    """Distinct-reason-per-stage survives the round-10 change.

    Three stages call ``docker_or_fail`` and the daemon-down message is now
    shared code; if the reason string stopped being threaded through it, all
    three stages would print one indistinguishable sentence and the operator
    would lose the one clue that says which dependency of which stage is unmet.
    """
    recording = run_checks("unit", tmp_path, stubs=Stubs(docker_info_rc=1))
    output = recording.output

    assert recording.returncode != 0, output
    assert "test_gitleaks_positive_control.py" in output, (
        f"the daemon message does not name what in tests/unit actually needs Docker:\n{output}"
    )
    assert "ci/gitleaks.toml" in output, (
        "the daemon message no longer explains that those positive controls are what "
        f"prove ci/gitleaks.toml is the ruleset that loads:\n{output}"
    )
    assert "the shellcheck pre-commit hook is language: docker_image" not in output, (
        f"stage_unit printed stage_hooks's reason for needing Docker:\n{output}"
    )
    assert "the Constitution VII secret gate runs as a pinned container" not in output, (
        f"stage_unit printed stage_gitleaks's reason for needing Docker:\n{output}"
    )


def test_the_windows_named_pipe_daemon_error_is_diagnosed_as_a_daemon_failure(
    tmp_path: Path,
) -> None:
    """The exact stderr measured on the authoring box must not read as "unrecognised".

    ci/checks.sh's daemon patterns were written against the Linux socket and
    the older Windows spellings. Docker Desktop's current client says "check if
    the path is correct and if the daemon is running" over ``npipe:``, which
    matched none of them — so the one message in the table that tells the
    operator to go and read raw docker stderr was what came out, for the most
    trivially fixable cause there is.
    """
    recording = run_checks(
        "unit",
        tmp_path,
        stubs=Stubs(docker_info_rc=1, docker_info_stderr=WINDOWS_NPIPE_DAEMON_DOWN_STDERR),
    )
    output = recording.output

    assert recording.returncode != 0, output
    assert "CAUSE: the Docker daemon is not reachable" in output, output
    assert "unrecognised docker failure" not in output, (
        f"the real Windows named-pipe stderr still falls through to the catch-all branch:\n{output}"
    )
    assert "start Docker Desktop" in output, (
        f"the daemon branch's remediation is missing from the diagnosis:\n{output}"
    )


def test_stage_hooks_fails_before_pre_commit_when_the_docker_daemon_is_unreachable(
    tmp_path: Path,
) -> None:
    """The hooks stage's guard had the same binary-only shape.

    Its consequence was milder — ``require_shellcheck_image`` runs a container
    two lines later and fails closed — but the diagnosis blamed the pinned
    image for a stopped daemon. It must now stop at the guard, name the daemon,
    and still never reach pre-commit.
    """
    recording = run_checks("hooks", tmp_path, stubs=Stubs(docker_info_rc=1))
    output = recording.output

    assert recording.returncode != 0, f"stage_hooks passed with the daemon down:\n{output}"
    assert "the Docker daemon is not reachable" in output, output
    assert "could not run the pinned shellcheck container" not in output, (
        f"a stopped daemon is still reported as a failure of the pinned image:\n{output}"
    )
    hook_runs = [
        call for call in recording.of("python") if "pre_commit" in call.argv and "run" in call.argv
    ]
    assert hook_runs == [], (
        f"pre-commit ran despite the daemon being unreachable: {[c.joined for c in hook_runs]}"
    )


def test_stage_gitleaks_blames_the_daemon_not_the_pinned_image(tmp_path: Path) -> None:
    """The secrets gate gets the same one-step-earlier, more precise diagnosis.

    Before round 10 this stage reached ``require_pinned_image`` and reported
    "could not run the pinned gitleaks container" — true, actionable, and one
    level of indirection away from "Docker Desktop is not running". No scan may
    run either way.
    """
    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(docker_info_rc=1))
    output = recording.output

    assert recording.returncode != 0, f"the secrets gate passed with the daemon down:\n{output}"
    assert "the Docker daemon is not reachable" in output, output
    assert _scans(recording) == (), (
        f"a scan ran with the daemon unreachable: {[call.argv for call in _scans(recording)]}"
    )
    assert "the Constitution VII secret gate runs as a pinned container" in output, (
        f"the daemon message does not say why THIS stage needs Docker:\n{output}"
    )


def test_the_daemon_probe_reruns_for_every_stage_that_needs_docker(tmp_path: Path) -> None:
    """No memoisation, by measurement — the round-7 rule applied to a new probe.

    ``docker info`` is a pass/fail decision, so caching "the daemon was up
    earlier in this run" would reintroduce exactly the class of defect rounds
    3-7 spent themselves closing: a stored answer standing in for the current
    one. ``sh ci/checks.sh all`` must probe once per stage that needs Docker —
    unit, coverage, gitleaks, hooks — not once per run.

    ``coverage`` is in that list for the same reason ``unit`` is, and it is the
    reason the expected count moved from 3 to 4: it re-runs tests/unit under
    measurement, so the same container-dependent positive controls are in scope
    and a skipped control would inflate the number it reports. (The other
    governance-kit stages — ``dead``, ``audit``, ``specs``, ``traceability``,
    ``projections``, ``governance`` — call neither ``docker_or_fail`` nor
    anything that shells out to Docker, so none of them add to this count.)
    """
    recording = run_checks("all", tmp_path)
    probes = _daemon_probes(recording)

    assert recording.returncode == 0, recording.output
    assert len(probes) == 4, (
        f"`all` ran {len(probes)} daemon probe(s), expected one each for unit, coverage, "
        "gitleaks and hooks. Fewer means a memo is standing in for the real probe "
        "somewhere; more means a stage grew a duplicate guard."
    )
    assert all(call.argv == ("info",) for call in probes), (
        f"the daemon probe is no longer a bare `docker info`: {[call.argv for call in probes]}"
    )


def test_a_reachable_daemon_leaves_the_unit_stage_running_pytest(tmp_path: Path) -> None:
    """The happy path must be untouched: probe, pass, run the suite.

    The guard added here fails closed; a guard that fails closed on a HEALTHY
    daemon would be worse than the defect it fixes, and the real daemon was
    down on the authoring box while this was written, so the healthy path is
    asserted here rather than assumed from a manual run.
    """
    recording = run_checks("unit", tmp_path)

    assert recording.returncode == 0, recording.output
    assert len(_daemon_probes(recording)) == 1, (
        f"expected exactly one daemon probe: {[call.argv for call in _daemon_probes(recording)]}"
    )
    assert [call.joined for call in _pytest_runs(recording)] == ["-m pytest tests/unit"], (
        f"the unit suite did not run on the healthy path: {recording.output}"
    )
    assert "the Docker daemon is not reachable" not in recording.output, (
        f"a reachable daemon was reported as unreachable:\n{recording.output}"
    )


# =============================================================================
# MINOR (round 5) — the generated overlay's size, as a canary, not a contract.
#
# `git ls-files --directory` collapses a whole ignored subtree into one entry,
# so the overlay should stay small even on a fully-populated dev checkout —
# but every other test in this file uses a two- or three-entry stub `ignored`
# fixture, so that was never actually measured at a realistic scale.
# =============================================================================


def test_the_generated_overlay_stays_a_reasonable_size_for_a_typical_checkout(
    tmp_path: Path,
) -> None:
    """Not exhaustive; representative of a checkout that has actually been used.

    Tool caches, a virtualenv, several scattered ``__pycache__`` directories
    (one per package/test directory, which ``--directory`` cannot collapse
    into a single entry the way it collapses ``.venv/``), editor/OS cruft —
    the shape ``git ls-files -z --others --ignored --exclude-standard
    --directory`` reports in practice, not the synthetic
    ``.mypy_cache/\\x00.pytest_cache/\\x00`` every other test defaults to.

    If this regresses sharply, something is emitting one allowlist entry per
    FILE instead of per ignored subtree, or ``--directory`` stopped
    collapsing — either is worth a human look. The threshold is deliberately
    generous (a canary, not a hard requirement): it exists so an unbounded
    regression is caught automatically instead of waiting for another external
    review round to notice it was never tested at scale at all.
    """
    typical_ignored = (
        b"\x00".join(
            [
                b".venv/",
                b".mypy_cache/",
                b".pytest_cache/",
                b".ruff_cache/",
                b".coverage",
                b"htmlcov/",
                b"src/orbital_drift/__pycache__/",
                b"src/orbital_drift/ingest/__pycache__/",
                b"src/orbital_drift/train/__pycache__/",
                b"src/orbital_drift/serve/__pycache__/",
                b"tests/unit/__pycache__/",
                b"tests/contract/__pycache__/",
                b"tests/smoke/__pycache__/",
                b"dags/__pycache__/",
                b"dist/",
                b"build/",
                b".terraform/",
                b".idea/",
                b".vscode/",
                b"scratch/",
                b"tmp/",
                b".hypothesis/",
                b".DS_Store",
                b"Thumbs.db",
            ]
        )
        + b"\x00"
    )

    recording = run_checks("gitleaks", tmp_path, stubs=Stubs(ignored=typical_ignored))
    assert recording.returncode == 0, recording.output

    overlay = _worktree_overlay(recording)
    paths = _overlay_paths(overlay)
    assert len(paths) == 24, f"fixture drift: expected 24 ignored entries, parsed {len(paths)}"

    overlay_bytes = len(overlay.encode("utf-8"))
    # 24 realistic entries currently produce well under 2 KiB. 8 KiB is
    # generous headroom for a repository that grows a lot more ignored
    # subtrees, while still catching a real regression (per-file entries
    # instead of per-directory would be tens of KB on a checkout this size).
    max_bytes = 8192
    assert overlay_bytes < max_bytes, (
        f"the generated gitleaks overlay is {overlay_bytes} bytes for {len(paths)} "
        f"typical ignored entries, over the {max_bytes}-byte canary threshold. Not "
        "necessarily a bug — .gitignore may have grown a lot — but look before "
        "raising the threshold: this was previously unbounded and untested at any "
        "realistic scale."
    )


# =============================================================================
# ROUND 11 — `pytest_suite()`'s exit-5 ladder, driven for the first time.
#
# `stage_contract` and `stage_smoke` are two of FR-011's six gates, and both are
# DECLARED-EMPTY today. The claim that they "arm automatically when a test module
# lands" is the only thing standing between that and a permanently vacuous pass,
# and until this section it had NO behavioural coverage at all: its entire test
# surface was three source-level greps in test_ci_contract.py (which that file's
# own docstring concedes are secondary), because the harness's python stub had no
# `-m pytest` arm — every collect probe fell through the `case` and exited 0, so
# `collect_rc` was ALWAYS 0 and the exit-5 ladder was unreachable by construction.
#
# The five branches below are the ones `ci/checks.sh:606-673` discriminates. Each
# gets a test that drives the real script against a synthetic repository root,
# with the collect probe's exit status supplied by the stub. Two of them assert
# a FAILURE, which is the half that matters: a gate that cannot tell "nobody has
# written this suite yet" from "this suite is broken" is not a gate.
# =============================================================================


def _synthetic_root(tmp_path: Path, *, pyproject: str | None = None) -> Path:
    """A minimal repo root holding a real ``ci/checks.sh`` and its own suites.

    ``run_checks`` runs the script with ``cwd`` set to the script's grandparent,
    and ``pytest_suite()`` resolves both the suite directory and its
    ``python_files``-override grep relative to that cwd. Copying the script into
    a scratch root therefore lets a test control exactly what the suite directory
    contains, without touching the real ``tests/contract``.

    The copied ``ci/versions.env`` is the real one on purpose: the preflight this
    stage runs must still assert the genuine pins, so a test cannot accidentally
    pass by running against a root where the pin file was weakened.
    """
    root = tmp_path / "root"
    (root / "ci").mkdir(parents=True)
    (root / "tests" / "contract").mkdir(parents=True)
    shutil.copy2(CHECKS_SH, root / "ci" / "checks.sh")
    shutil.copy2(REPO_ROOT / "ci" / "versions.env", root / "ci" / "versions.env")
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n" if pyproject is None else pyproject,
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_an_unauthored_suite_is_declared_empty_and_passes(tmp_path: Path) -> None:
    """Branch (a): nothing in the directory at all -> DECLARED-EMPTY, exit 0."""
    root = _synthetic_root(tmp_path)
    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=5),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode == 0, recording.output
    assert "DECLARED-EMPTY" in recording.output, recording.output
    assert "arms automatically" in recording.output, recording.output


def test_a_suite_holding_only_helper_modules_is_declared_empty_not_an_error(
    tmp_path: Path,
) -> None:
    """Branch (c): ``fixtures.py`` is not a collection failure.

    Reporting this one as a collection error would send the operator hunting an
    import failure that does not exist — the specific misdiagnosis the third
    branch was added to prevent.
    """
    root = _synthetic_root(tmp_path)
    (root / "tests" / "contract" / "fixtures.py").write_text("X = 1\n", encoding="utf-8")
    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=5),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode == 0, recording.output
    assert "DECLARED-EMPTY" in recording.output, recording.output
    assert "helper module" in recording.output, recording.output


@pytest.mark.parametrize("filename", ["test_stac.py", "stac_boundary_test.py"])
def test_a_collectable_module_that_collects_nothing_fails_as_a_collection_error(
    tmp_path: Path, filename: str
) -> None:
    """Branch (b), for BOTH of pytest's default ``python_files`` patterns.

    ``stac_boundary_test.py`` is the measured regression named in
    ``ci/checks.sh``'s own header: when the count was ``find -name 'test_*.py'``
    only, a ``*_test.py`` file containing no test functions produced exit 5, a
    find count of 0, and a green DECLARED-EMPTY — contract coverage could drop to
    zero with the gate still passing. Parametrizing both names is what stops a
    "simplification" back to one pattern from going unnoticed.
    """
    root = _synthetic_root(tmp_path)
    (root / "tests" / "contract" / filename).write_text("X = 1\n", encoding="utf-8")
    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=5, pytest_collect_stdout="no tests ran"),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode != 0, recording.output
    assert "collection error" in recording.output, recording.output
    assert "DECLARED-EMPTY" not in recording.output, recording.output


def test_a_non_five_collect_failure_is_reported_as_a_collection_failure(
    tmp_path: Path,
) -> None:
    """A collect probe that dies for any other reason must not be mistaken for empty."""
    root = _synthetic_root(tmp_path)
    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=2, pytest_collect_stdout="INTERNALERROR"),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode != 0, recording.output
    assert "failed collection" in recording.output, recording.output
    assert "DECLARED-EMPTY" not in recording.output, recording.output


def test_a_clean_collect_runs_the_suite_and_propagates_its_failure(tmp_path: Path) -> None:
    """Branch (e): collect succeeds -> the real run happens, and its status is the stage's.

    Asserts the run actually occurred rather than trusting the exit code alone:
    a stage that returned the collect probe's status would look identical from
    the outside if the second invocation were dropped.
    """
    root = _synthetic_root(tmp_path)
    (root / "tests" / "contract" / "test_stac.py").write_text("X = 1\n", encoding="utf-8")
    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=0, pytest_run_rc=1),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode != 0, recording.output

    real_runs = [
        call
        for call in recording.of("python")
        if "-m" in call.argv and "pytest" in call.argv and "--collect-only" not in call.argv
    ]
    assert real_runs, (
        "a clean collect must be followed by a real pytest run; "
        f"python was called with {[call.argv for call in recording.of('python')]!r}"
    )


def test_an_override_governs_before_the_default_pattern_count_is_trusted(
    tmp_path: Path,
) -> None:
    """A stray default-pattern file under an ACTIVE override is not a collection error.

    Both corroborating counts are built from pytest's DEFAULT ``python_files``
    patterns. Once an override governs, a file matching those defaults is not
    "a module matching pytest python_files" at all — pytest is looking for a
    different pattern, correctly found nothing, and that is expected, not a
    collection error. Checking ``collectable_count`` before
    ``python_files_overridden`` would misreport this correct outcome as a
    collection error and send the operator hunting an import failure that does
    not exist, on a suite with nothing wrong with it.
    """
    root = _synthetic_root(tmp_path, pyproject="")
    (root / "tox.ini").write_text(
        "[pytest]\npython_files = check_*.py\n", encoding="utf-8", newline="\n"
    )
    # Matches the DEFAULT pattern (test_*.py), not the ACTIVE override
    # (check_*.py) — under the override this file is legitimately never
    # collected, so pytest correctly finds nothing.
    (root / "tests" / "contract" / "test_stray.py").write_text("X = 1\n", encoding="utf-8")

    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=5),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode != 0, recording.output
    assert "overrides python_files" in recording.output, (
        f"expected the override diagnosis, not a collection-error one:\n{recording.output}"
    )
    # Not a plain "collection error" substring check: the CORRECT override
    # message's own prose ends "...cannot tell an unauthored suite from a
    # collection error", so that substring is present in BOTH the right and the
    # wrong diagnosis. The wrong one is specifically identifiable by naming a
    # module count.
    assert "module(s) matching pytest python_files" not in recording.output, (
        f"a default-pattern file under an active override was misreported as a "
        f"collection error instead of the override diagnosis:\n{recording.output}"
    )


def test_the_suite_is_not_declared_empty_merely_because_the_run_was_skipped(
    tmp_path: Path,
) -> None:
    """The default stubs must not make an authored suite look unauthored.

    Guards the round-11 harness change itself: ``pytest_collect_rc`` defaults to
    ``python_rc`` (0), so an authored suite under default stubs must take the
    real-run branch, not any DECLARED-EMPTY branch. If a future edit defaulted
    the knob to 5, every branch above would still pass while this one caught it.
    """
    root = _synthetic_root(tmp_path)
    (root / "tests" / "contract" / "test_stac.py").write_text("X = 1\n", encoding="utf-8")
    recording = run_checks("contract", tmp_path, checks_sh=root / "ci" / "checks.sh")
    assert recording.returncode == 0, recording.output
    assert "DECLARED-EMPTY" not in recording.output, recording.output


@pytest.mark.parametrize("pruned", [".venv", "build", "node_modules", "__pycache__"])
def test_a_stray_test_file_in_a_non_collected_directory_is_not_a_collection_error(
    tmp_path: Path, pruned: str
) -> None:
    """``find`` must prune what pytest's ``norecursedirs`` prunes.

    ``find`` descends unconditionally. Before round 11 a ``test_*.py`` under a
    nested virtualenv, build tree or ``__pycache__`` inside a suite directory
    counted towards ``collectable_count`` while pytest collected nothing from
    it — so the stage FAILED, reporting a "collection error" for a file pytest
    had never looked at, and the operator got sent hunting an import error that
    did not exist. The corroborating count has to corroborate what pytest
    actually does.
    """
    root = _synthetic_root(tmp_path)
    stray = root / "tests" / "contract" / pruned / "lib"
    stray.mkdir(parents=True)
    (stray / "test_stray.py").write_text("X = 1\n", encoding="utf-8")

    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=5),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode == 0, recording.output
    assert "DECLARED-EMPTY" in recording.output, recording.output
    assert "collection error" not in recording.output, recording.output


# (config file, section header, whether pyproject.toml must be emptied to let it
# govern). Section headers are NOT interchangeable and this is not cosmetic:
# `[pytest]` in setup.cfg is a HARD pytest error ("no longer supported, change
# to [tool:pytest] instead" — confirmed by running real pytest against it, not
# assumed from memory), and tox.ini's section is `[pytest]`, not `[tool:pytest]`.
# A test using the wrong header for either file would not reproduce what real
# pytest actually does with that file.
_OVERRIDE_CONFIG_CASES: Final = (
    ("pytest.ini", "[pytest]", False),
    ("tox.ini", "[pytest]", True),
    ("setup.cfg", "[tool:pytest]", True),
)


@pytest.mark.parametrize(("config", "section", "must_empty_pyproject"), _OVERRIDE_CONFIG_CASES)
def test_a_python_files_override_outside_pyproject_still_fails_closed(
    tmp_path: Path, config: str, section: str, must_empty_pyproject: bool
) -> None:
    """All four config files pytest reads ini options from, not just pyproject.

    ``pytest.ini`` is the sharp one: it overrides ``pyproject.toml`` ENTIRELY, so
    checking pyproject alone missed the single file whose override would actually
    win. With a `python_files` override in force neither count bounds what pytest
    collects, the (b)/(c) split is unsound, and the stage must say so instead of
    guessing. The message must name the file that carries it, or the operator has
    four places to look.

    ``tox.ini`` and ``setup.cfg`` need ``pyproject.toml`` emptied of its
    ``[tool.pytest.ini_options]`` table first — MEASURED, not assumed: with that
    table present (even empty, as ``_synthetic_root``'s default is), real pytest
    lets pyproject.toml govern and never reads tox.ini or setup.cfg at all, so a
    test that left the table in place would be asserting behaviour real pytest
    does not produce. ``pytest.ini`` needs no such adjustment — its mere
    existence governs unconditionally regardless of what pyproject.toml says.
    """
    root = _synthetic_root(tmp_path, pyproject="" if must_empty_pyproject else None)
    (root / config).write_text(
        f"{section}\npython_files = check_*.py\n", encoding="utf-8", newline="\n"
    )
    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=5),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode != 0, recording.output
    assert "overrides python_files" in recording.output, recording.output
    assert config in recording.output, (
        f"the failure must name {config!r} as the source of the override; got:\n{recording.output}"
    )


def test_a_non_governing_files_override_is_correctly_ignored(tmp_path: Path) -> None:
    """The precedence bug this replaces, pinned directly.

    pyproject.toml carries an EMPTY ``[tool.pytest.ini_options]`` table (no
    ``python_files`` line) and tox.ini carries a REAL override. MEASURED: real
    pytest governed by the empty pyproject.toml table and never consulted
    tox.ini at all — it collected the file matching pytest's DEFAULT patterns,
    not tox.ini's override pattern. A "check every candidate file independently,
    first text match wins" implementation would report tox.ini's override as
    live and fail this stage closed for a file pytest never reads. The correct
    behaviour is the opposite: no override in force, ordinary DECLARED-EMPTY.
    """
    root = _synthetic_root(tmp_path)  # default: pyproject.toml has the EMPTY table
    (root / "tox.ini").write_text(
        "[pytest]\npython_files = check_*.py\n", encoding="utf-8", newline="\n"
    )
    recording = run_checks(
        "contract",
        tmp_path,
        stubs=Stubs(pytest_collect_rc=5),
        checks_sh=root / "ci" / "checks.sh",
    )
    assert recording.returncode == 0, recording.output
    assert "DECLARED-EMPTY" in recording.output, recording.output
    assert "overrides python_files" not in recording.output, (
        f"tox.ini's override was reported as live even though pyproject.toml's empty "
        f"table governs and pytest never reads tox.ini in this state:\n{recording.output}"
    )


# =============================================================================
# ROUND 11 — the `coverage` stage (FR-011a).
#
# Two claims worth testing and one worth testing HARD. The easy two are the
# guards: this stage measures tests/unit, so without Docker and git it must fail
# fast rather than report a number computed from a run where eight
# container-dependent positive controls skipped themselves.
#
# The hard one is the threshold. Constitution III bans magic numbers in code, and
# "the threshold lives in ci/versions.env" is a claim that a literal in the shell
# script would satisfy the LETTER of while defeating entirely — the stage would
# still run, still pass, and nothing would notice the pin had stopped being
# consulted. So the argv assertion below compares against PINS, not against a
# number written in this file: hardcoding the threshold in ci/checks.sh fails
# here, and changing it in ci/versions.env alone does not.
# =============================================================================


def test_the_coverage_stage_fails_fast_when_the_docker_daemon_is_unreachable(
    tmp_path: Path,
) -> None:
    """A coverage number measured with the container controls skipped is a lie."""
    recording = run_checks("coverage", tmp_path, stubs=Stubs(docker_info_rc=1))
    assert recording.returncode != 0, recording.output
    assert "daemon is not reachable" in recording.output, recording.output
    assert not [call for call in recording.of("python") if "-m pytest" in call.joined], (
        "the coverage stage ran pytest with the daemon down: "
        f"{[call.argv for call in recording.of('python')]!r}"
    )


def test_the_coverage_stage_message_says_why_coverage_needs_docker(tmp_path: Path) -> None:
    """Distinct from unit's and hooks's, so the operator knows WHICH stage."""
    recording = run_checks("coverage", tmp_path, stubs=Stubs(docker_info_rc=1))
    assert "inflate the measured number" in recording.output, (
        f"the daemon message does not say why THIS stage needs Docker:\n{recording.output}"
    )


def test_the_coverage_stage_asserts_the_threshold_from_the_pin_file(tmp_path: Path) -> None:
    """The Principle III enforcement: the argv must carry the PINNED value.

    Compared against ``PINS["COVERAGE_MIN_PERCENT"]`` rather than a literal
    written here, so this keeps passing if the pin is legitimately changed; a
    test asserting ``85`` would fail a reviewed bump.

    IT DOES NOT, HOWEVER, CATCH THE NUMBER BEING MOVED INTO ci/checks.sh — an
    earlier version of this docstring claimed it did. ``--cov-fail-under=85``
    hardcoded there produces byte-identical argv and this assertion stays green
    (measured during the RB-008 review). The source-level half lives in
    ``test_ci_contract.py::test_both_coverage_floors_reach_the_gate_by_
    interpolation_not_by_literal``, which covers this floor and the per-file one.
    """
    recording = run_checks("coverage", tmp_path)
    assert recording.returncode == 0, recording.output

    runs = [call for call in recording.of("python") if "-m pytest" in call.joined]
    assert len(runs) == 1, (
        f"expected exactly one pytest invocation from the coverage stage, got "
        f"{[call.argv for call in runs]!r}"
    )
    argv = runs[0].argv

    assert f"--cov-fail-under={PINS['COVERAGE_MIN_PERCENT']}" in argv, (
        "the coverage stage does not pass ci/versions.env's COVERAGE_MIN_PERCENT "
        f"as its threshold; argv was {argv!r}. A literal in ci/checks.sh is a magic "
        "number (Constitution III) and silently decouples the gate from its pin."
    )
    assert "--cov=src/orbital_drift" in argv, (
        "the coverage stage does not measure src/orbital_drift by PATH. The package-name "
        f"form omits never-imported modules from the report entirely; argv was {argv!r}"
    )
    assert "--cov-report=term-missing" in argv, (
        "without term-missing a --cov-fail-under breach prints a percentage and no "
        f"indication of WHICH lines are uncovered; argv was {argv!r}"
    )


@pytest.mark.parametrize("hatch", ["--no-cov", "--collect-only", "--no-cov -p no:cacheprovider"])
def test_pytest_addopts_does_not_survive_into_the_coverage_run(tmp_path: Path, hatch: str) -> None:
    """The gate has no opt-out, and `PYTEST_ADDOPTS` is the one that nearly worked.

    pytest builds argv as ``[ini addopts] + [PYTEST_ADDOPTS] + [command line]``,
    so a ``--cov-fail-under=0`` injected through the variable LOSES to the flag
    the stage passes last. The boolean switches do not lose:

    * ``--no-cov`` is pytest-cov's documented "disable coverage completely"
      switch. It warns rather than erroring, ``--cov-fail-under`` is never
      applied, and pytest exits 0.
    * ``--collect-only`` never runs a test and never reports, so the same.

    Either turns this stage green over a run that measured nothing — the vacuous
    pass ``stage_hooks`` has two separate branches dedicated to refusing, and
    which README states flatly cannot happen. Asserted BEHAVIOURALLY, not by
    grepping for ``unset``, because ``_saved=$PYTEST_ADDOPTS; unset ...;
    export PYTEST_ADDOPTS=$_saved`` satisfies a grep — the same reasoning
    ``test_pre_commit_escape_hatches_do_not_survive_into_the_hook_run`` gives.
    """
    recording = run_checks("coverage", tmp_path, extra_env={"PYTEST_ADDOPTS": hatch})
    assert recording.returncode == 0, recording.output

    runs = [call for call in recording.of("python") if "-m pytest" in call.joined]
    assert runs, recording.output
    assert runs[0].env["PYTEST_ADDOPTS"] is None, (
        "PYTEST_ADDOPTS survived into the coverage run, so the gate can be "
        f"switched off from the environment: {runs[0].env!r}"
    )
    assert "ignoring PYTEST_ADDOPTS" in recording.output, (
        "the stage silently discarded PYTEST_ADDOPTS; it must say so, or an operator "
        f"is left wondering why their flag did nothing:\n{recording.output}"
    )


def test_the_coverage_stage_measures_every_suite_not_just_one(tmp_path: Path) -> None:
    """``tests``, not ``tests/unit``.

    A future "optimisation" narrowing this to the one suite that currently has
    tests would under-measure the moment contract or smoke exercise ``src/`` —
    and would do so silently, because the number would still be green.
    """
    recording = run_checks("coverage", tmp_path)
    runs = [call for call in recording.of("python") if "-m pytest" in call.joined]
    assert runs, recording.output
    assert "tests" in runs[0].argv, (
        f"the coverage stage does not run the whole tests/ tree: {runs[0].argv!r}"
    )
    for narrower in ("tests/unit", "tests/contract", "tests/smoke"):
        assert narrower not in runs[0].argv, (
            f"the coverage stage measures only {narrower}, so coverage from the other "
            f"suites is invisible: {narrower}: {runs[0].argv!r}"
        )


# =============================================================================
# Charter C-6 / DEC-004 — the per-file floor (orbital_drift.covcheck) is wired
# into stage_coverage, run only after the global floor passes. covcheck's own
# behaviour (real coverage.json fixtures, real per-file thresholds) is proved
# in isolation by tests/unit/test_covcheck.py; a stub-only test there would be
# a BLOCK per the adversarial-reviewer test-adequacy rule, so that suite drives
# the real module. What is missing without the two tests below is the WIRING
# claim ci/checks.sh's stage_coverage docstring makes — that a per-file breach
# still reddens the `coverage` job, and that covcheck runs only inside the
# success path so it can never mask a real global-floor breach's diagnosis.
#
# RB-008 F4 adds the third claim: WHICH BAR it runs at. The stage invoked
# covcheck with no `--floor` at all, so the module's own PER_FILE_FLOOR default
# WAS the gate bar while nothing asserted it — measured by lowering that default
# to 11.0, which left the entire suite green. The global floor has been bound to
# ci/versions.env since FR-011a (see
# test_the_coverage_stage_asserts_the_threshold_from_the_pin_file); the per-file
# floor now is too, at the same VALUE (90, ratified in RB-006 — RB-008 moves its
# home and its binding, not the number).
# =============================================================================


def _covcheck_calls(recording: Recording) -> list[Call]:
    """Every ``python -m orbital_drift.covcheck ...`` invocation, flags included.

    Prefix-matched rather than compared for equality against the bare module
    name: the stage now passes ``--floor``, and an equality check would silently
    stop matching the moment a flag is added — reporting "covcheck never ran"
    for a stage that ran it correctly, which is the misdiagnosis direction that
    costs an operator the most time.
    """
    return [
        call for call in recording.of("python") if call.argv[:2] == ("-m", "orbital_drift.covcheck")
    ]


def test_the_per_file_floor_is_the_pinned_one_not_the_module_default(tmp_path: Path) -> None:
    """Constitution III, for the second of the two coverage bars.

    ``stage_coverage`` ran ``python -m orbital_drift.covcheck`` with no
    ``--floor``, so the gate's bar was whatever ``covcheck.PER_FILE_FLOOR``
    happened to say — a number in application code, changeable by an edit to a
    src/ module that no test and no pin file would notice. Measured: lowering
    that default to 11.0 left every test in this repository green.

    Compared against ``PINS[...]`` rather than a literal ``90`` written here,
    for the same reason the global-floor assertion is: a literal would reject a
    legitimate, reviewed pin change.

    THIS HALF CANNOT SEE A HARDCODED FLOOR, and neither can the global-floor
    assertion above — ``--floor 90`` written into ci/checks.sh produces argv
    identical to ``--floor "${COVERAGE_PER_FILE_MIN_PERCENT}"``, and both stay
    green (measured during review). What this proves is that the value REACHING
    covcheck equals the pin. The source side — that ci/checks.sh interpolates
    the pin rather than repeating its value — is
    ``test_ci_contract.py::test_both_coverage_floors_reach_the_gate_by_
    interpolation_not_by_literal``. Neither is sufficient alone.
    """
    assert "COVERAGE_PER_FILE_MIN_PERCENT" in PINS, (
        "ci/versions.env does not pin COVERAGE_PER_FILE_MIN_PERCENT. The per-file "
        "coverage floor is a gate threshold exactly like COVERAGE_MIN_PERCENT "
        "beside it, and a gate threshold whose only home is a src/ module's "
        "default argument is a magic number (Constitution III)."
    )
    recording = run_checks("coverage", tmp_path)
    assert recording.returncode == 0, recording.output

    calls = _covcheck_calls(recording)
    assert len(calls) == 1, (
        f"expected exactly one covcheck invocation from the coverage stage, got "
        f"{[call.argv for call in calls]!r}"
    )
    argv = calls[0].argv
    assert "--floor" in argv, (
        "the coverage stage runs covcheck with no --floor, so the gate's per-file "
        f"bar is the module default rather than the pinned one; argv was {argv!r}"
    )
    assert argv[argv.index("--floor") + 1] == PINS["COVERAGE_PER_FILE_MIN_PERCENT"], (
        "the coverage stage does not pass ci/versions.env's "
        f"COVERAGE_PER_FILE_MIN_PERCENT as covcheck's floor; argv was {argv!r}"
    )


def test_a_covcheck_failure_after_a_passing_global_floor_reddens_the_stage(
    tmp_path: Path,
) -> None:
    """A per-file breach must propagate even though the global floor passed."""
    recording = run_checks("coverage", tmp_path, stubs=Stubs(covcheck_rc=1))
    covcheck_calls = _covcheck_calls(recording)
    assert covcheck_calls, (
        "the coverage stage did not run `python -m orbital_drift.covcheck` after "
        f"a passing global floor: {recording.output}"
    )
    assert recording.returncode != 0, (
        "a covcheck failure (per-file floor breach) did not redden the coverage "
        f"stage: {recording.output}"
    )


def test_covcheck_does_not_run_when_the_global_floor_already_failed(tmp_path: Path) -> None:
    """covcheck lives INSIDE the success path, not a second, unconditional gate.

    A global-floor breach must still short-circuit with the existing diagnosis
    unchanged (D-12) — covcheck must never run, and so can never mask it.
    """
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=1,
            pytest_run_stdout=(
                "TOTAL 4 4 0%\n"
                "FAIL Required test coverage of 85% not reached. Total coverage: 40.00%\n"
                "49 passed in 0.15s\n"
            ),
            covcheck_rc=1,
        ),
    )
    covcheck_calls = _covcheck_calls(recording)
    assert covcheck_calls == [], (
        f"covcheck ran even though the global coverage floor had already failed: {recording.output}"
    )
    assert recording.returncode != 0, recording.output
    assert "COVERAGE BREACH" in recording.output, (
        f"a failed global floor must still be diagnosed as a coverage breach: {recording.output}"
    )


# =============================================================================
# D-12 — the coverage stage's three-way diagnosis (test failure / coverage
# breach / collection error), driven through the harness for the first time.
#
# A second independent review pass found the original two-way version of this
# logic (a bare, unanchored grep for the coverage-breach PROSE) was not sound:
# pytest-cov's own `_should_report()` only suppresses its "Required test
# coverage" line on a test failure when `--no-cov-on-fail` is passed, which
# this stage does not — so a run with BOTH a real test failure and a coverage
# breach prints both lines, and the old grep would report "the tests are not
# the problem" while a test genuinely was. Worse, it was self-referential:
# tests/unit/test_coverage_positive_control.py asserts on the literal
# "Required test coverage of 85% not reached" string, so a FAILURE of that very
# assertion reprints the string in its own traceback, which the old unanchored
# grep would then match — misdiagnosing its own broken positive control as "not
# a test problem". Fixed by checking pytest's own `^FAILED ` marker FIRST,
# unconditionally, and anchoring the coverage-breach check to pytest-cov's
# exact line shape (`^FAIL Required test coverage...`) rather than a prose
# fragment that can appear inside unrelated text.
# =============================================================================


def test_a_pure_coverage_breach_is_diagnosed_as_such(tmp_path: Path) -> None:
    """No test failed; only the threshold was missed."""
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=1,
            pytest_run_stdout=(
                "TOTAL 4 4 0%\n"
                "FAIL Required test coverage of 85% not reached. Total coverage: 0.00%\n"
                "49 passed in 0.15s\n"
            ),
        ),
    )
    assert recording.returncode != 0, recording.output
    assert "COVERAGE BREACH" in recording.output, recording.output
    assert "tests failed UNDER MEASUREMENT" not in recording.output, recording.output


def test_a_real_test_failure_is_diagnosed_as_such_not_as_a_coverage_breach(
    tmp_path: Path,
) -> None:
    """A genuinely failing test, with NO coverage breach in the same run."""
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=1,
            pytest_run_stdout=(
                "FAILED tests/unit/test_repo_structure.py::test_x - AssertionError: boom\n"
                "1 failed, 48 passed in 0.15s\n"
            ),
        ),
    )
    assert recording.returncode != 0, recording.output
    assert "tests failed UNDER MEASUREMENT" in recording.output, recording.output
    assert "COVERAGE BREACH" not in recording.output, recording.output


def test_a_mixed_run_is_diagnosed_as_a_test_failure_first(tmp_path: Path) -> None:
    """Both a real test failure AND a coverage breach in the same run.

    This is the critical case: pytest-cov prints its coverage-breach line
    regardless of whether a test also failed (verified against the vendored
    plugin — `--no-cov-on-fail` is what would suppress it, and this stage never
    passes that flag). The diagnosis must lead with the test failure, not the
    coverage framing, because "the tests are not the problem" is false here.
    """
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=1,
            pytest_run_stdout=(
                "FAILED tests/unit/test_repo_structure.py::test_x - AssertionError: boom\n"
                "FAIL Required test coverage of 85% not reached. Total coverage: 40.00%\n"
                "1 failed, 48 passed in 0.15s\n"
            ),
        ),
    )
    assert recording.returncode != 0, recording.output
    assert "tests failed UNDER MEASUREMENT" in recording.output, recording.output
    assert "ALSO breached the threshold" in recording.output, (
        f"a mixed run should still mention the coverage breach as secondary "
        f"context, not omit it entirely:\n{recording.output}"
    )
    # The primary diagnosis line must not claim "the tests are not the
    # problem" -- that specific framing belongs only to the pure-breach case.
    assert "The tests are not the problem" not in recording.output, recording.output


def test_a_self_referential_traceback_does_not_masquerade_as_a_coverage_breach(
    tmp_path: Path,
) -> None:
    """The exact regression this round closes, reproduced directly.

    Simulates the shape of a FAILING assertion whose own text happens to
    contain the coverage-breach prose — e.g. test_coverage_positive_control.py's
    own positive control failing. pytest prefixes assertion-introspection lines
    with `>` or `E`, never a bare `FAILED ` at column 0 for that text alone; the
    REAL `^FAILED <nodeid>` summary line pytest also emits for the failing test
    is what must drive the diagnosis, not the prose inside the traceback.
    """
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=1,
            pytest_run_stdout=(
                '>       assert "Required test coverage of 85% not reached" in result.stdout\n'
                "E       assert 'Required test coverage of 85% not reached' in 'unrelated'\n"
                "FAILED tests/unit/test_coverage_positive_control.py::"
                "test_the_threshold_actually_fails_a_run_whose_tests_all_pass - AssertionError\n"
                "1 failed, 267 passed in 2.1s\n"
            ),
        ),
    )
    assert recording.returncode != 0, recording.output
    assert "tests failed UNDER MEASUREMENT" in recording.output, (
        f"a failing assertion whose traceback happens to quote the coverage-breach "
        f"prose was misdiagnosed as a pure coverage breach:\n{recording.output}"
    )
    assert "The tests are not the problem" not in recording.output, recording.output


def test_neither_signal_is_diagnosed_as_a_collection_error(tmp_path: Path) -> None:
    """Non-zero exit, no `^FAILED ` line, no coverage-breach line — a third cause."""
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=2,
            pytest_run_stdout="INTERNALERROR> some pytest-internal failure\n",
        ),
    )
    assert recording.returncode != 0, recording.output
    assert "COLLECTION error" in recording.output, recording.output
    assert "COVERAGE BREACH" not in recording.output, recording.output
    assert "tests failed UNDER MEASUREMENT" not in recording.output, recording.output


@pytest.mark.parametrize(
    ("failed_path", "stage"),
    [
        ("tests/unit/test_repo_structure.py", "unit"),
        ("tests/contract/test_stac_client.py", "contract"),
        ("tests/smoke/test_ingest_dag.py", "smoke"),
    ],
)
def test_the_failure_remediation_names_the_suite_that_actually_failed(
    tmp_path: Path, failed_path: str, stage: str
) -> None:
    """The suggested remediation command must match WHICH suite broke.

    ``stage_coverage`` runs ``tests/unit``, ``tests/contract`` and
    ``tests/smoke`` together in one process (D-06). A fixed, unconditional
    ``sh ci/checks.sh unit`` suggestion is actively misleading for a failure in
    either of the other two suites: that command would report GREEN — the
    broken test simply isn't in it — and the message's own next paragraph
    ("if they are GREEN and this is RED, ... not a broken test") would then
    argue the operator away from a perfectly ordinary, real bug. Latent only
    because tests/contract and tests/smoke are still empty; parametrized over
    all three so it stays caught the moment either gains its first test.
    """
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=1,
            pytest_run_stdout=(
                f"FAILED {failed_path}::test_x - AssertionError: boom\n1 failed, 1 passed in 0.1s\n"
            ),
        ),
    )
    assert recording.returncode != 0, recording.output
    assert f"sh ci/checks.sh {stage}" in recording.output, (
        f"a failure in {failed_path} did not suggest checking `{stage}`:\n{recording.output}"
    )
    for other in {"unit", "contract", "smoke"} - {stage}:
        assert f"sh ci/checks.sh {other}" not in recording.output, (
            f"a failure ONLY in {failed_path} (suite {stage!r}) also suggested checking "
            f"the unrelated `{other}` stage, which would report a misleading GREEN:\n"
            f"{recording.output}"
        )


def test_the_failure_remediation_suggests_everything_when_the_suite_is_unrecognised(
    tmp_path: Path,
) -> None:
    """A ``FAILED`` line whose path matches none of the three known suites.

    Fails towards suggesting ALL THREE stages rather than silently naming
    none — the same "loud, not silent" bias ``pytest_suite``'s own override
    detection uses for its false positive/negative tradeoff.
    """
    recording = run_checks(
        "coverage",
        tmp_path,
        stubs=Stubs(
            pytest_run_rc=1,
            pytest_run_stdout=(
                "FAILED weird/path/test_x.py::test_y - AssertionError\n1 failed in 0.1s\n"
            ),
        ),
    )
    assert recording.returncode != 0, recording.output
    for stage in ("unit", "contract", "smoke"):
        assert f"sh ci/checks.sh {stage}" in recording.output, (
            f"an unrecognised FAILED path did not fall back to suggesting {stage!r}:\n"
            f"{recording.output}"
        )


# =============================================================================
# RB-008 F1 — the `specs` gate must FAIL CLOSED, like every gate beside it.
#
# ci/validate_specs.sh resolved its change root from the cwd-relative literal
# `openspec/changes` and, when that directory was absent, printed "nothing to
# validate" and exited 0 — so a DIRECT `sh ci/validate_specs.sh` from any other
# directory reported a green OpenSpec gate having read no file at all. A second
# fall-through did the same for a root holding zero packages, printing "all
# change packages structurally valid" over nothing at all.
#
# NOT through `sh ci/checks.sh specs`, and the distinction matters enough to
# state: checks.sh does `cd "${REPO_ROOT}"` before dispatch, so the GATE path
# validated the real packages from any cwd — re-measured against the pre-fix
# file from /tmp and from /home/user, both rc 0, both "all change packages
# structurally valid". The caller MASKED the defect rather than inheriting it,
# which is how it survived this long. The three tests below therefore cover the
# two states reachable THROUGH the gate (a tree with no openspec/changes, and
# one holding no packages — a partial checkout produces both) plus the
# direct-invocation case scripts/_lib.sh actually recorded.
#
# Not hypothetical, and not this file's inference: scripts/_lib.sh:15-17 names
# THIS script as the measured instance of the bug ("ci/validate_specs.sh was
# measured green-lighting from /tmp because it trusted a relative literal").
# Every sibling gate in ci/checks.sh fails closed in the same situation —
# pytest_suite() spends fifty lines discriminating "declared but unauthored"
# from "collected nothing because something is broken" specifically so that an
# ambiguous emptiness never passes silently.
#
# Three properties, one test each: the validator resolves its root from its own
# location (the cwd cannot change the verdict), a MISSING root is an error, and
# an EMPTY root is an error.
# =============================================================================

VALIDATE_SPECS_SH: Final = REPO_ROOT / "ci" / "validate_specs.sh"


def _specs_root(tmp_path: Path, *, changes: bool) -> Path:
    """A scratch repo root carrying the real specs gate and nothing else.

    Same device as ``_synthetic_root`` above and for the same reason — the copy
    is the REAL ci/checks.sh, ci/versions.env and ci/validate_specs.sh, so a
    test cannot pass by running against a weakened gate — but this one controls
    the OpenSpec tree rather than a pytest suite directory.

    The root is named distinctively (not ``root``) so an assertion can prove the
    diagnostic names the RESOLVED absolute path rather than echoing the relative
    literal that caused the bug.
    """
    root = tmp_path / "scratch-repo"
    (root / "ci").mkdir(parents=True)
    shutil.copy2(CHECKS_SH, root / "ci" / "checks.sh")
    shutil.copy2(REPO_ROOT / "ci" / "versions.env", root / "ci" / "versions.env")
    shutil.copy2(VALIDATE_SPECS_SH, root / "ci" / "validate_specs.sh")
    if changes:
        (root / "openspec" / "changes").mkdir(parents=True)
    return root


def test_the_specs_validator_resolves_its_change_root_from_its_own_location(
    tmp_path: Path,
) -> None:
    """Run from an unrelated cwd, it must still validate THIS repository.

    The measured failure verbatim: invoked from /tmp, the validator looked for
    `./openspec/changes`, did not find it, announced "nothing to validate" and
    exited 0 — a green Constitution-level gate that had read no file. Running it
    from ``tmp_path`` reproduces exactly that, and the assertion that it reports
    the real packages as valid is what distinguishes "resolved its own root"
    from "found nothing and shrugged".
    """
    completed = subprocess.run(
        [posix_shell(), VALIDATE_SPECS_SH.as_posix()],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, (
        f"the specs validator failed when run from an unrelated cwd:\n{output}"
    )
    assert "all change packages structurally valid" in output, (
        "run from another directory, the validator did not validate this "
        f"repository's change packages:\n{output}"
    )
    assert "nothing to validate" not in output, (
        "the validator resolved its change root relative to the CALLER's cwd, so "
        f"it green-lit a tree it never looked at:\n{output}"
    )


def test_the_specs_gate_fails_closed_when_the_change_root_is_missing(tmp_path: Path) -> None:
    """No openspec/changes at all is a broken checkout, not a clean bill of health.

    A gate whose subject is absent cannot report success about it. The message
    must also name the RESOLVED path, so an operator can see WHICH tree was
    inspected rather than re-deriving it from a relative literal, and it must go
    to STDERR — asserted against ``recording.stderr`` specifically, because
    ``recording.output`` concatenates both streams and would be satisfied by a
    refusal printed to stdout, which a caller piping stdout to a log and
    watching stderr for problems would never see.
    """
    root = _specs_root(tmp_path, changes=False)
    recording = run_checks("specs", tmp_path, checks_sh=root / "ci" / "checks.sh")
    assert recording.returncode != 0, (
        f"the specs gate passed on a tree with no openspec/changes:\n{recording.output}"
    )
    assert "scratch-repo/openspec/changes" in recording.output, (
        "the diagnostic does not name the resolved change root, so it cannot "
        f"distinguish 'wrong tree' from 'missing directory':\n{recording.output}"
    )
    assert "does not exist" in recording.stderr, (
        "the refusal was not written to stderr; a diagnostic on stdout is "
        f"indistinguishable from ordinary gate chatter:\n{recording.output}"
    )
    assert "structurally valid" not in recording.output, recording.output


def test_the_specs_gate_fails_closed_when_the_change_root_holds_no_packages(
    tmp_path: Path,
) -> None:
    """The second fall-through: zero packages used to print the success line.

    `found_any=0` printed "nothing to validate" and then fell through to "all
    change packages structurally valid" and exit 0 — the gate asserting a
    property of an empty set, which is the shape every other stage in
    ci/checks.sh refuses. Stream-checked for the same reason as the
    missing-directory case above.
    """
    root = _specs_root(tmp_path, changes=True)
    recording = run_checks("specs", tmp_path, checks_sh=root / "ci" / "checks.sh")
    assert recording.returncode != 0, (
        f"the specs gate passed on an EMPTY openspec/changes:\n{recording.output}"
    )
    assert "scratch-repo/openspec/changes" in recording.output, recording.output
    assert "holds no change packages" in recording.stderr, (
        "the refusal was not written to stderr; a diagnostic on stdout is "
        f"indistinguishable from ordinary gate chatter:\n{recording.output}"
    )
    assert "structurally valid" not in recording.output, (
        "the gate announced every change package valid over zero change "
        f"packages:\n{recording.output}"
    )


# =============================================================================
# ROUND 8 — the newest, most important tests in this file must not be silently
# deletable. Mirrors test_ci_contract.py's own LOAD_BEARING_ANTI_BYPASS_TESTS /
# PRIMARY_STRUCTURAL_TESTS guard, kept LOCAL to this module rather than folded
# into that one: that guard already reads only its OWN module's source
# (``Path(__file__).read_text()``); widening it to parse a second file is a
# bigger change than the few lines below, for the same net effect.
#
#
# ROUND 9 — this LOCAL guard had the identical shape-only weakness as
# test_ci_contract.py's: it checked that a `def test_name(...)` line still
# existed, not that the function still asserted anything real. Both
# reviewers demonstrated the same PoC against this file's own guarded tests
# (gut `test_require_tool_tracks_the_current_probe_result_not_a_cached_one`'s
# body to `assert recording.returncode in (0, 1, 2)`, or decorate it with
# `@pytest.mark.skip`), leaving the `def` line untouched. Hardened below the
# same way and for the same reason as test_ci_contract.py's guard: for every
# name in LOAD_BEARING_BEHAVIOURAL_TESTS, also verify (parsed via `ast`, not
# regex) that the function carries no skip/xfail decorator, and that its body
# still contains at least the calibrated MIN_ASSERT_COUNTS floor for that
# name.
#
# ROUND 9b — THOSE TWO CHECKS DID NOT CLOSE THE CLASS THEY WERE SCOPED TO
# CLOSE, and THIS FILE'S OWN GUARDED PRIMARY TEST is where the orchestrator
# verified it live. Against
# `test_the_interpreter_check_tracks_the_current_probe_result_not_a_cached_one`
# (floor 4, six real asserts, all left syntactically intact):
#
#   variant A — `pytest.skip("wip")` as the FIRST statement of the body:
#               the round-9 guard PASSED; pytest reported "2 passed, 1
#               skipped".
#   variant B — a bare `return` as the FIRST statement of the body: the
#               round-9 guard PASSED; pytest reported "3 passed" — no
#               SKIPPED line, no signal at all, indistinguishable from
#               genuine success in a green summary.
#
# Root cause: `_assert_count` counts Assert NODES with no reachability
# awareness, and `_skip_or_xfail_decorator_name` never looked inside the
# body, so any first statement that makes the body unreachable satisfies the
# floor while executing nothing. Four vectors are closed in
# `_assert_guarded_behavioural_test_not_neutered` below — (1) imperative
# skip/xfail CALL in the body, (2) non-final `return`/`raise`, (3)
# statically-false `if`/`while` wrapper, (4) module-level import-alias or
# rebinding of a skip/xfail decorator. See test_ci_contract.py's own "ROUND
# 9b" block comment for the full rationale, the measurement trail and the
# residual-risk disclosure; the mechanism is duplicated here rather than
# imported, per the round-8 LOCAL-module design choice restated above, and
# the two copies are kept deliberately identical.
#
# One case matters specifically to THIS file and is verified, not assumed:
# `test_an_unrepresentable_filename_fails_loudly_not_as_a_config_parse_error`
# calls `pytest.skip(...)` LEGITIMATELY, as an iconv-capability guard. It is
# NOT in LOAD_BEARING_BEHAVIOURAL_TESTS, so check (1) — which only ever runs
# against names in that frozenset — does not look at it, and it continues to
# run and skip exactly as before. If a load-bearing test ever genuinely needs
# a conditional skip, exempt it explicitly by name; do not widen the check.
#
# WHAT THIS DOES NOT DO, BY DESIGN, PER THE OPERATOR'S BOUNDED-ROUND
# DIRECTIVE: it does not protect ITSELF. Deleting or gutting
# `test_every_load_bearing_behavioural_test_still_exists`,
# `LOAD_BEARING_BEHAVIOURAL_TESTS`, `MIN_ASSERT_COUNTS` below, or the
# `_assert_count` / `_skip_or_xfail_decorator_name` / `_skip_or_xfail_call` /
# `_early_exit_statement` / `_statically_false_conditional` /
# `_resolve_module_alias` helpers is caught by NOTHING automated in this
# suite. That is the same accepted, documented residual gap as
# test_ci_contract.py's equivalent guard — one hardening pass, not an
# infinite meta-guard regress, per spec-guardian's and the operator's own
# round-8 recommendation; closing it further is out of scope and is expected
# to be caught by human/agent code review of this file's diff, not by
# automation. Three further limits, stated in full in test_ci_contract.py and
# repeated here so this module discloses its own gaps: ARBITRARY DECORATOR
# INDIRECTION beyond module-level aliases (a decorator built by `getattr`, by
# a helper function, or out of a dict) is NOT covered; NOT EVERY WAY TO MAKE A
# BODY UNREACHABLE is covered, only the three checked here — `_assert_count`
# is unchanged and still has no reachability model of its own, so e.g.
# wrapping the body in `for _ in []:` is statically decidable and still slips
# through; and the assert floor is a QUANTITY check that CANNOT catch a
# same-count-but-vacuous rewrite —
# replacing this file's six-assert PRIMARY interpreter test with six copies
# of `assert True` satisfies its floor of 4 and passes every check here while
# proving nothing. One name below
# (`test_the_actual_version_probe_still_runs_under_every_bypass_attempt`)
# already had exactly one legitimate `assert` before round 9; its floor
# cannot be raised without breaking the real test, so for that one name
# specifically the assert-count check adds nothing beyond the four
# reachability/skip checks and existence — a narrower instance of the same
# limitation, not a new one.
# =============================================================================

_BEHAVIOUR_MODULE_SRC: Final[str] = Path(__file__).read_text(encoding="utf-8")
_BEHAVIOUR_TEST_FUNCTION_NAMES: Final[frozenset[str]] = frozenset(
    re.findall(r"^def (test_[A-Za-z0-9_]+)\(", _BEHAVIOUR_MODULE_SRC, re.MULTILINE)
)

# ROUND 9 — same AST-based approach as test_ci_contract.py, kept as an
# independent local copy rather than an import for the same LOCAL-module
# reason given above.
_BEHAVIOUR_MODULE_AST: Final[ast.Module] = ast.parse(
    _BEHAVIOUR_MODULE_SRC, filename=str(Path(__file__))
)
_BEHAVIOUR_TEST_FUNCTION_DEFS: Final[dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {
    node.name: node
    for node in ast.walk(_BEHAVIOUR_MODULE_AST)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
}


def _decorator_qualname(node: ast.expr) -> str:
    """Render a decorator expression back to the dotted name AS SPELLED IN
    THIS SOURCE FILE, e.g. ``@pytest.mark.skip(reason=...)`` ->
    ``"pytest.mark.skip"``, whether it was called or bare.

    ROUND 9b — it renders the SPELLING and nothing more: ``@sk`` renders as
    ``"sk"``. Resolving the two common ways ``sk`` could be bound to something
    skip-shaped is a separate, explicitly-bounded step (``_resolve_module_alias``);
    round 9's claim that this worked "regardless of import aliasing" was wrong.
    """
    if isinstance(node, ast.Call):
        return _decorator_qualname(node.func)
    if isinstance(node, ast.Attribute):
        return f"{_decorator_qualname(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ast.dump(node)


def _dotted_name(node: ast.expr) -> str | None:
    """``a.b.c`` for a PURE ``Name``/``Attribute`` chain; ``None`` for anything
    else (a call, a subscript, a literal, ...).

    Deliberately stricter than ``_decorator_qualname``, whose ``ast.dump``
    fallback embeds string literals. This function's results feed the alias
    table and the imperative-call scan, and a dumped ``BYPASS_ATTEMPTS``
    literal — which contains the key ``"SKIP_PREFLIGHT"`` and is referenced
    from inside the ``@pytest.mark.parametrize`` decorator of the GUARDED
    test ``test_the_actual_version_probe_still_runs_under_every_bypass_attempt``
    — would otherwise smuggle the substring "skip" into a name and fail a
    legitimate test. A real false positive avoided, not a hypothetical one.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _module_name_bindings(module: ast.Module) -> dict[str, str]:
    """MODULE-LEVEL name -> dotted target, for the two concrete aliasing
    patterns vector 4 covers: ``import X as Y`` / ``from M import N as Y``,
    and a top-level ``Y = <dotted.name>`` (e.g. ``_disable =
    pytest.mark.skip``). One pass over the module's own top-level statements;
    nothing nested, nothing computed.
    """
    bindings: dict[str, str] = {}
    for node in module.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            prefix = f"{node.module}." if node.module else ""
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = f"{prefix}{alias.name}"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target_value = node.value
            dotted = _dotted_name(target_value) if target_value is not None else None
            if dotted is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = dotted
    return bindings


_MODULE_NAME_BINDINGS: Final[dict[str, str]] = _module_name_bindings(_BEHAVIOUR_MODULE_AST)
# Bounded, cycle-safe: `a = b` / `b = a` terminates on the seen-set, and a
# chain longer than this is indirection this round deliberately does not chase.
_ALIAS_RESOLUTION_STEPS: Final[int] = 8


def _resolve_module_alias(rendered: str) -> str:
    """Rewrite the LEADING segment of ``rendered`` through
    ``_MODULE_NAME_BINDINGS`` until it stops changing. ``"sk"`` ->
    ``"pytest.skip"`` given ``from pytest import skip as sk``. Returns the
    input unchanged when no binding applies — the case for every name in this
    module today, where the table is empty.
    """
    seen: set[str] = set()
    for _ in range(_ALIAS_RESOLUTION_STEPS):
        head, _, tail = rendered.partition(".")
        target = _MODULE_NAME_BINDINGS.get(head)
        if target is None or head in seen:
            break
        seen.add(head)
        rendered = f"{target}.{tail}" if tail else target
    return rendered


def _is_skip_or_xfail(rendered: str) -> bool:
    """``skip``/``xfail`` as a case-insensitive substring of the rendered name
    OR of its alias-resolved form. Both, so resolution can only ADD coverage.
    Does NOT match ``parametrize``, which every parametrised test in this file
    legitimately carries.
    """
    for candidate in (rendered, _resolve_module_alias(rendered)):
        lowered = candidate.lower()
        if "skip" in lowered or "xfail" in lowered:
            return True
    return False


def _skip_or_xfail_decorator_name(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The rendered name of the first skip/xfail-shaped decorator on ``func``,
    or ``None``. Catches ``@pytest.mark.skip``, ``@pytest.mark.skipif(...)``
    and ``@pytest.mark.xfail(...)``, bare or called, plus (round 9b) the
    module-level-alias spellings ``_resolve_module_alias`` knows about.
    """
    for decorator in func.decorator_list:
        name = _decorator_qualname(decorator)
        if _is_skip_or_xfail(name):
            return name
    return None


def _skip_or_xfail_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, int] | None:
    """ROUND 9b / VECTOR 1. The first imperative skip/xfail-shaped CALL in
    ``func``'s BODY as ``(rendered name, line number)``, or ``None``.

    ``pytest.skip("wip")`` as the first statement leaves every assert below it
    syntactically present — floor satisfied — and executes none of them
    (verified live against this file's own PRIMARY interpreter test: "2
    passed, 1 skipped", guard green). Only the callee's dotted name is
    examined; arguments never are, so prose mentioning "skip" is not flagged.

    Scans ``func.body``, NOT the whole function node, so a
    ``@pytest.mark.parametrize(...)`` decorator's arguments stay out of scope
    here — decorators are the other check's job.
    """
    for statement in func.body:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted_name(node.func)
            if name is not None and _is_skip_or_xfail(name):
                return name, node.lineno
    return None


# Nodes that open a NEW scope. The reachability checks below do not descend
# into them: a `return` inside a nested helper is that helper's control flow,
# not an early exit from the test. `test_the_actual_pin_check_reexecutes_once_
# per_stage_that_needs_it` in this very file has exactly such a helper
# (`_count`, whose two `return`s are legitimate) and must not be flagged.
_NESTED_SCOPE_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
)


def _statements_outside_nested_scopes(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.stmt]:
    """Every statement belonging to ``func``'s own control flow, at any depth
    (inside ``if``/``for``/``while``/``with``/``try``), excluding everything
    inside a nested function, lambda or class. Sorted by position so a failure
    message names the FIRST offending line rather than whichever one
    ``ast.walk``'s breadth-first order reached first.
    """
    nested: set[int] = set()
    for node in ast.walk(func):
        if node is func or not isinstance(node, _NESTED_SCOPE_NODES):
            continue
        for inner in ast.walk(node):
            if inner is not node:
                nested.add(id(inner))
    own = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.stmt) and node is not func and id(node) not in nested
    ]
    return sorted(own, key=lambda node: (node.lineno, node.col_offset))


def _early_exit_statement(func: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt | None:
    """ROUND 9b / VECTOR 2. The first ``return``/``raise`` in ``func``'s own
    control flow that is not its FINAL top-level statement, or ``None``.

    A bare ``return`` as the first statement is the worst variant found this
    round — verified live against this file's own PRIMARY interpreter test,
    which then reported as a plain PASS ("3 passed") while executing zero
    assertions, with the round-9 guard still green. ``raise`` is included for
    the same reason a bare ``return`` is banned inside
    ``require_python_interpreter`` by
    ``test_ci_contract.test_no_memoized_flag_gates_pin_verification``, and
    (measured) no guarded test here uses either, so it costs nothing today. A
    trailing ``return``/``raise`` is allowed: it can swallow nothing.
    """
    final = func.body[-1] if func.body else None
    for node in _statements_outside_nested_scopes(func):
        if isinstance(node, (ast.Return, ast.Raise)) and node is not final:
            return node
    return None


def _statically_false_conditional(func: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt | None:
    """ROUND 9b / VECTOR 3. The first ``if``/``while`` in ``func``'s own
    control flow whose test is a statically-false CONSTANT, or ``None``.

    ``if False:`` (or ``if 0:``, ``if None:``, ``if "":``) wrapped round the
    body swallows it whole while every assert inside stays present and
    counted. Deliberately limited to ``ast.Constant`` tests: no dataflow
    analysis, no constant folding, no tracking of a module-level
    ``ENABLED = False``. Narrow and decidable beats broad and approximate.
    """
    for node in _statements_outside_nested_scopes(func):
        if (
            isinstance(node, (ast.If, ast.While))
            and isinstance(node.test, ast.Constant)
            and not node.test.value
        ):
            return node
    return None


def _assert_count(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Total ``assert`` STATEMENTS anywhere in ``func``'s body (including
    nested loops and any nested helper function defined inside it). A
    quantity floor only — not a check of WHAT is asserted, which stays a
    human/review judgement — used as a cheap tripwire against the
    demonstrated "gut the body to one trivial always-true assert" PoC.
    """
    return sum(1 for node in ast.walk(func) if isinstance(node, ast.Assert))


# ROUND 9 — calibrated floors, one per guarded name: the ACTUAL assert count
# measured in each test when this guard was hardened, minus a safety margin
# WHERE THERE WAS ROOM FOR ONE (round 9b: the unqualified "minus a safety
# margin" wording was inaccurate — the last entry below has NO margin, floor
# 1 == measured 1, because the real test has exactly one legitimate assert
# and the floor cannot go higher; that is a deliberate exception, already
# disclosed in the "WHAT THIS DOES NOT DO" paragraph above).
# The other three carry a real margin and are high enough that
# peer-reviewer's exact PoC (a single trivial
# `assert recording.returncode in (0, 1, 2)`) fails them. Lower a floor
# explicitly, with review sign-off, if a legitimate refactor needs to — do
# not let it drift down silently.
MIN_ASSERT_COUNTS: Final[dict[str, int]] = {
    "test_the_interpreter_check_tracks_the_current_probe_result_not_a_cached_one": 4,  # measured: 6
    "test_require_tool_tracks_the_current_probe_result_not_a_cached_one": 4,  # measured: 7
    "test_the_actual_pin_check_reexecutes_once_per_stage_that_needs_it": 4,  # measured: 8
    "test_the_actual_version_probe_still_runs_under_every_bypass_attempt": 1,  # measured: 1
}


def _assert_guarded_behavioural_test_not_neutered(name: str) -> None:
    """Shared body: skip/xfail-freedom (decorator AND — round 9b — imperative
    call), reachability of the body (no early exit, no statically-false
    wrapper), plus the calibrated assert-count floor from
    ``MIN_ASSERT_COUNTS``. Existence is checked separately by the caller
    (against ``_BEHAVIOUR_TEST_FUNCTION_NAMES``, matching this guard's existing
    convention) so the failure message for a fully-deleted test stays the
    original, simpler one.

    The reachability checks run BEFORE the count floor deliberately: when a
    body has been made unreachable the count is still satisfied — that
    combination IS the round-9b defect — so reporting "floor met" first would
    be actively misleading.
    """
    func = _BEHAVIOUR_TEST_FUNCTION_DEFS.get(name)
    assert func is not None, (
        f"{name} is in _BEHAVIOUR_TEST_FUNCTION_NAMES (matched by the top-level `def` "
        "regex) but ast.parse did not find it as a FunctionDef — investigate "
        "before trusting either scan"
    )

    skip_marker = _skip_or_xfail_decorator_name(func)
    assert skip_marker is None, (
        f"{name} is decorated with `@{skip_marker}`. A skipped or xfailed test "
        "still shows as part of a green (or green-ish) run while asserting "
        "nothing. Remove the marker; if the test is genuinely flaky, fix or "
        "replace it instead of silencing it."
    )

    # Each site is rendered under an explicit `is not None` narrowing rather
    # than indexed inside the assert message: the message is only EVALUATED
    # on failure, but mypy type-checks it either way and does not narrow from
    # the assert condition.
    skip_call = _skip_or_xfail_call(func)
    skip_call_site = (
        f"`{skip_call[0]}(...)` at line {skip_call[1]}" if skip_call is not None else ""
    )
    assert skip_call is None, (
        f"{name} calls {skip_call_site}. An imperative skip inside the body "
        "leaves every `assert` below it syntactically present — so the "
        "MIN_ASSERT_COUNTS floor is still satisfied — while the test executes "
        "none of them and reports as a SKIP in an otherwise green run. This is "
        "round 9b's variant A, verified live against this very test. The only "
        "legitimate pytest.skip in this module belongs to "
        "test_an_unrepresentable_filename_fails_loudly_not_as_a_config_parse_error, "
        "which is deliberately NOT load-bearing; if a load-bearing test "
        "genuinely needs a conditional skip, exempt it here by name, with "
        "review sign-off."
    )

    early_exit = _early_exit_statement(func)
    early_exit_site = (
        f"`{type(early_exit).__name__.lower()}` at line {early_exit.lineno}"
        if early_exit is not None
        else ""
    )
    assert early_exit is None, (
        f"{name} has an unconditional {early_exit_site} that is not its final "
        "statement. Everything after it is dead code whose `assert`s still count "
        "toward the MIN_ASSERT_COUNTS floor. A bare `return` as the first "
        "statement is round 9b's variant B — the dangerous one: the test reports "
        "as an ordinary PASS while executing zero assertions, with no SKIPPED "
        "line and no signal anywhere. (A nested helper function's own `return` "
        "is not flagged; this check does not descend into nested scopes.)"
    )

    dead_branch = _statically_false_conditional(func)
    dead_branch_site = f"line {dead_branch.lineno}" if dead_branch is not None else ""
    assert dead_branch is None, (
        f"{name} contains an `if`/`while` with a statically-false constant test at "
        f"{dead_branch_site}. `if False:` wrapped round the body swallows it whole "
        "while every `assert` inside stays present and counted, satisfying the "
        "MIN_ASSERT_COUNTS floor with a test that runs nothing — round 9b's "
        "vector 3."
    )

    floor = MIN_ASSERT_COUNTS[name]
    actual = _assert_count(func)
    assert actual >= floor, (
        f"{name} contains only {actual} `assert` statement(s) in its body, "
        f"below the calibrated floor of {floor} (see MIN_ASSERT_COUNTS). This "
        "is the exact shape of the round-8/9 PoC: a PRIMARY behavioural "
        "test's body reduced to one trivial, always-true assert that still "
        "passes and still satisfies the OLD existence-only guard. If you have "
        "genuinely simplified this test on purpose, lower its entry in "
        "MIN_ASSERT_COUNTS explicitly, in the same change, with a reviewer's "
        "sign-off."
    )


LOAD_BEARING_BEHAVIOURAL_TESTS: Final[frozenset[str]] = frozenset(
    {
        # ROUND 8 — PRIMARY. These prove the no-memoization property as a pure
        # black box (changing stub output across real, repeated invocations of
        # the actual code path); see their own "PRIMARY" docstrings above.
        "test_the_interpreter_check_tracks_the_current_probe_result_not_a_cached_one",
        "test_require_tool_tracks_the_current_probe_result_not_a_cached_one",
        # ROUND 7 — the direct instrumented proof that pin verification
        # re-executes once per stage (not once, memoised) across a real `all`
        # run, and that the real subprocess probe still runs under every
        # listed bypass-variable spelling. Named explicitly by spec-guardian
        # in round 8 as previously unprotected.
        "test_the_actual_pin_check_reexecutes_once_per_stage_that_needs_it",
        "test_the_actual_version_probe_still_runs_under_every_bypass_attempt",
        # RB-008's five new behavioural tests (the three specs fail-closed
        # cases, the projections interpreter refusal, the per-file floor's argv)
        # are deliberately NOT registered here. This set is scoped to the
        # anti-bypass property — "no memoisation can stand in for the current
        # probe" — where the guard exists because those tests are the ONLY
        # evidence for a claim about all possible runs, and a quiet deletion
        # would leave nothing red. The RB-008 tests each assert a fixed,
        # single-run behaviour that a second mechanism already watches: the
        # specs and floor cases have source-level counterparts in
        # test_ci_contract.py, and deleting the projections test leaves
        # test_the_python_free_stage_exemptions_are_derived_from_the_source
        # asserting the same exemption from the other side. Registering them
        # would also mean calibrating MIN_ASSERT_COUNTS entries whose floors
        # nothing has yet had reason to defend.
    }
)


def test_every_load_bearing_behavioural_test_still_exists() -> None:
    """A deleted PRIMARY (or round-7 instrumented) behavioural test fails
    nothing else in this suite, and nothing in test_ci_contract.py either —
    that file's own anti-deletion guard only scans ITS OWN module's source.

    Each test named in ``LOAD_BEARING_BEHAVIOURAL_TESTS`` is the ONLY place
    that proves its specific claim by actually running ``ci/checks.sh`` with
    changing stub output; no other test in either file would notice its
    removal, which is exactly what makes each one load-bearing rather than
    redundant.

    ROUND 9 / 9b — existence alone is no longer enough; see the "ROUND 9" and
    "ROUND 9b" block comments above ``MIN_ASSERT_COUNTS`` for why, and for
    what ``_assert_guarded_behavioural_test_not_neutered`` additionally checks
    for every name in ``LOAD_BEARING_BEHAVIOURAL_TESTS``: no skip/xfail
    decorator, no imperative skip/xfail call, no non-final ``return``/
    ``raise``, no statically-false conditional, and a minimum assert count.
    Round 9 checked only the first and last of those; the other three were
    each demonstrated live against a guarded test in THIS file while round
    9's guard stayed green.
    """
    missing = sorted(LOAD_BEARING_BEHAVIOURAL_TESTS - _BEHAVIOUR_TEST_FUNCTION_NAMES)
    assert not missing, (
        f"{missing} no longer exist as `def test_...` functions in this module. "
        "These are the PRIMARY, authoritative proof (plus their round-7 "
        "supporting instrumented tests) that no memoization mechanism — of any "
        "shape or name — can let a cached result stand in for the CURRENT real "
        "probe. The shape-based source-text tests in test_ci_contract.py are "
        "secondary defense-in-depth and would not catch this deletion."
    )
    for name in sorted(LOAD_BEARING_BEHAVIOURAL_TESTS):
        _assert_guarded_behavioural_test_not_neutered(name)
