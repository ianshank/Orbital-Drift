"""Structural regression tests for the gate runner and its callers.

Every assertion in this file encodes a defect that already occurred in
``ci/checks.sh``, ``.pre-commit-config.yaml``, ``.github/workflows/ci.yml``,
``pyproject.toml`` or ``.gitignore``, and that a full green test run did nothing
to prevent.

WHAT LIVES HERE AND WHAT DOES NOT
---------------------------------
Anything that can be observed by RUNNING the script belongs in
``test_checks_sh_behaviour.py``, which executes ``ci/checks.sh`` with stubbed
``git``/``docker``/``python`` and asserts on what it did. That file exists
because three assertions that used to live here were shown to be defeatable by
refactors that preserve the defect exactly:

* ``"'''" not in worktree_overlay_config`` — move the emission into a helper and
  the body no longer contains the delimiter, while the TOML injection is fully
  restored;
* pinning the NAME ``ORBITAL_DRIFT_PREFLIGHT_DONE`` — ``|| [ "${OD_FAST:-0}" =
  "1" ]`` disables every pin check under a different spelling;
* asserting the substring ``unset SKIP`` with no ordering constraint —
  ``_saved="${SKIP:-}"; unset SKIP; export SKIP="${_saved}"`` satisfies it.

What remains here is what genuinely cannot be exercised: properties of files
that are configuration rather than code (the workflow, the hook config, the pin
file), and STRUCTURAL properties of the shell source that no single execution
can reveal — for example "no environment variable outside this declared set is
ever read", which is a statement about all possible runs.

Two tests here do run a subprocess, and say why in their docstrings: the set of
hooks that execute at ``--hook-stage manual`` is a property of the MERGED local
plus upstream hook definition and cannot be read off ``.pre-commit-config.yaml``
at all.

ROUND 8 — the same lesson, one more time, for the preflight anti-bypass
machinery specifically. This file's source-text shape checks (the four
detectors culminating in ``test_no_memoized_flag_gates_pin_verification`` and
``test_log_only_memos_never_gate_a_verification_return``) were believed, as of
round 7, to be the PRIMARY proof that no memoisation gates pin verification.
They were defeated within that same round by a lazy-init caching idiom no
regex over source text rules out in general. They remain in this file as
cheap, fast, secondary checks — see each one's own "ROUND 8" docstring
paragraph — but the PRIMARY, authoritative proof of that property is now
BEHAVIOURAL and lives in ``test_checks_sh_behaviour.py``: it runs the real
script, changes what the stubbed tool-version probe reports BETWEEN repeated
calls within one process, and asserts the current call's answer — not a
cached earlier one — decides the outcome. That test cannot be defeated by any
source-text shape, because it does not read the source at all.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

CHECKS_SH: Final = REPO_ROOT / "ci" / "checks.sh"
WORKFLOW: Final = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PRE_COMMIT_CONFIG: Final = REPO_ROOT / ".pre-commit-config.yaml"
VERSIONS_ENV: Final = REPO_ROOT / "ci" / "versions.env"
GITIGNORE: Final = REPO_ROOT / ".gitignore"
PYPROJECT: Final = REPO_ROOT / "pyproject.toml"

CHECKS_SRC: Final[str] = CHECKS_SH.read_text(encoding="utf-8")
WORKFLOW_SRC: Final[str] = WORKFLOW.read_text(encoding="utf-8")
PRE_COMMIT_SRC: Final[str] = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Drop whole-line comments.

    Necessary, not cosmetic. These files explain their own defects at length, so
    the prose quotes the very constructs the tests below look for: the header of
    ``.pre-commit-config.yaml`` discusses ``stages: [pre-commit]``, and
    ``ci/checks.sh`` names the constructs it no longer uses in the comments
    explaining why. Matching against raw source found those comments and
    reported defects that were not there — just as bad as missing real ones,
    because a test that cries wolf gets deleted.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


CHECKS_DIRECTIVES: Final[str] = _strip_comments(CHECKS_SRC)
WORKFLOW_DIRECTIVES: Final[str] = _strip_comments(WORKFLOW_SRC)
PRE_COMMIT_DIRECTIVES: Final[str] = _strip_comments(PRE_COMMIT_SRC)

# ``name() {`` ... ``}`` with the closing brace in column 0. That is the only
# shape ci/checks.sh uses for a multi-line function, and the tests below assert
# the parser found every function the dispatch table dispatches to, so a style
# change cannot make this file quietly stop checking anything.
_FUNCTION_RE: Final = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(\)[ \t]*\{[ \t]*$(?P<body>.*?)^\}[ \t]*$",
    re.MULTILINE | re.DOTALL,
)


def _shell_functions() -> dict[str, str]:
    return {match.group("name"): match.group("body") for match in _FUNCTION_RE.finditer(CHECKS_SRC)}


FUNCTIONS: Final[dict[str, str]] = _shell_functions()

# Everything outside a multi-line function body: the top-level prologue and the
# dispatch case.
TOP_LEVEL: Final[str] = _strip_comments(_FUNCTION_RE.sub("", CHECKS_SRC))

# Stage labels accepted by the dispatch `case` at the bottom of ci/checks.sh.
_DISPATCH_RE: Final = re.compile(
    r"^[ \t]*([a-z][a-z0-9_-]*)\)[ \t]+(stage_[a-z_]+)[ \t]+;;", re.MULTILINE
)
DISPATCH: Final[dict[str, str]] = {
    match.group(1): match.group(2) for match in _DISPATCH_RE.finditer(CHECKS_SRC)
}

STAGE_FUNCTIONS: Final[tuple[str, ...]] = tuple(
    sorted(name for name in FUNCTIONS if name.startswith("stage_"))
)


def _read_versions_env() -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in VERSIONS_ENV.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pins[key.strip()] = value.strip()
    return pins


VERSIONS: Final[dict[str, str]] = _read_versions_env()


# =============================================================================
# The parsers themselves must not silently degrade.
# =============================================================================


def test_shell_function_parser_found_every_dispatched_stage() -> None:
    """If the regex stops matching, every grep below passes vacuously.

    This is the load-bearing assertion of the whole file: a parser that finds
    nothing makes a parametrised test generate zero cases and report success.
    """
    assert DISPATCH, "no stage labels parsed out of ci/checks.sh's dispatch case"
    missing = sorted(set(DISPATCH.values()) - set(FUNCTIONS))
    assert not missing, (
        f"ci/checks.sh dispatches to {missing} but the function-body parser did not "
        "find them. Either the functions were renamed or their formatting changed; "
        "fix _FUNCTION_RE, because until you do, every assertion in this file is "
        "checking an empty string."
    )
    assert len(STAGE_FUNCTIONS) >= len(DISPATCH), "fewer stage_* bodies than dispatch labels"


# =============================================================================
# CRITICAL 1 — the secrets gate's config must not be able to go missing.
# =============================================================================


def test_the_generated_overlay_is_not_passed_as_a_command_substitution() -> None:
    """``set -e`` does not fire for a substitution used as a WORD of a command.

    Verified in isolation under both bash-as-sh and dash: with ``f()`` returning
    1, ``printf '[%s]' "$(f)"`` continues and the script exits 0, while
    ``v=$(f)`` aborts. The gate was written the first way::

        docker run ... -e GITLEAKS_CONFIG_TOML="$(worktree_overlay_config)" ...

    so the overlay's own FAIL diagnostic went to stderr and the scan ran anyway
    with an EMPTY config — which does not disable gitleaks, it silently swaps it
    onto its embedded default ruleset, losing all six orbital-drift rules. The
    four path rules have no default equivalent at all.

    ``test_checks_sh_behaviour`` proves the fixed behaviour by running the
    script; this asserts the shape, because the shape is subtle enough that
    someone will reintroduce it while "tidying up".
    """
    body = _strip_comments(FUNCTIONS["stage_gitleaks"])
    assert 'GITLEAKS_CONFIG_TOML="$(' not in body, (
        "stage_gitleaks builds the gitleaks config inline in the docker argument "
        "list again. `set -e` does not abort on a failing command substitution used "
        "as a word of a simple command, so a failing overlay leaves the scan running "
        "with an empty config."
    )
    offenders = [
        line
        for line in body.splitlines()
        if "$(" in line and not re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*=\$\(", line)
    ]
    assert not offenders, (
        "stage_gitleaks uses a command substitution somewhere other than a standalone "
        "assignment. `set -e` only sees such a substitution fail when it IS the "
        f"command, not when it is a word of one:\n{offenders}"
    )
    assert re.search(
        r"^\s*gitleaks_overlay=\$\(worktree_overlay_config\)\s*$", body, re.MULTILINE
    ), "the overlay is no longer hoisted into its own assignment"
    assert re.search(r'if \[ -z "\$\{gitleaks_overlay\}" \]', body), (
        "stage_gitleaks no longer refuses to scan with an empty overlay"
    )


def test_the_worktree_scan_does_not_take_a_config_flag() -> None:
    """``--config`` overrides ``GITLEAKS_CONFIG_TOML`` and drops the exclusions.

    Measured against the pinned image on a tree with one gitignored copy of a
    planted secret: overlay alone found 1 leak (the gitignored copy pruned);
    overlay plus ``--config ci/gitleaks.toml`` found 2, because the flag wins and
    the env var is never parsed. Adding the flag here looks like belt and
    braces; it reinstates the ``.env`` false positive the overlay exists to stop.
    """
    body = _strip_comments(FUNCTIONS["stage_gitleaks"])
    worktree = body.split("--- working tree", 1)[1].split("--- full git history", 1)[0]
    assert "--config" not in worktree, (
        "the working-tree scan passes --config, which makes gitleaks ignore the "
        "generated GITLEAKS_CONFIG_TOML overlay entirely"
    )
    history = body.split("--- full git history", 1)[1]
    assert "--config ci/gitleaks.toml" in history, (
        "the history scan takes no overlay, so without --config it runs on gitleaks' "
        "embedded defaults with none of ci/gitleaks.toml's rules"
    )


def test_worktree_overlay_guards_the_empty_path_list() -> None:
    """``paths = [`` ``]`` is a fatal gitleaks config error, and it is the
    NORMAL case in CI.

    ``git ls-files --others --ignored --exclude-standard --directory`` lists
    tool caches (``.mypy_cache``, ``.pytest_cache``, ``__pycache__``). The CI
    gitleaks job does a fresh checkout and never installs Python or runs pytest,
    so none of them exist and the list is empty. Reproduced locally by deleting
    the caches::

        ignored count now: 0
        --- working tree (.gitignore-excluded paths omitted) ---
        FTL Failed to load config error="[[allowlists]] must contain at least one
        check for: commits, paths, regexes, or stopwords"
        EXIT = 1

    Green locally (caches present), dead in CI (caches absent), on a public
    repository, with an error naming a config file that exists nowhere on disk.
    """
    body = FUNCTIONS["worktree_overlay_config"]

    guard = body.find('[ -n "${wo_paths}" ]')
    assert guard != -1, (
        "worktree_overlay_config no longer tests whether the derived path list is "
        "empty before emitting [[allowlists]]. An empty `paths = [ ]` makes gitleaks "
        'exit 1 with FTL "must contain at least one check", which is the normal '
        "state of the CI gitleaks job — the Constitution VII gate would be dead on "
        "arrival there while still passing locally."
    )

    emit = body.find("[[allowlists]]")
    assert emit != -1, "worktree_overlay_config no longer emits an allowlist at all"
    assert guard < emit, (
        "the emptiness guard must come BEFORE the [[allowlists]] block is printed; "
        f"found guard at {guard}, emission at {emit}"
    )

    paths_key = body.find("paths = [")
    assert paths_key != -1 and guard < paths_key, (
        "`paths = [` is emitted outside the emptiness guard"
    )


def test_overlay_reads_nul_delimited_paths() -> None:
    """Without ``-z``, git C-quotes non-ASCII names and the pattern misses.

    With the default ``core.quotePath=true``, ``git ls-files`` renders
    ``notes-café.env`` as ``"notes-caf\\303\\251.env"`` — quotes, octal escapes
    and all. The escaping pass then escaped those backslashes, the generated
    pattern matched nothing, the file stayed in the walk and reddened the scan.
    Whether the gate passed therefore depended on an unpinned local git setting.

    Not behavioural: the stub harness feeds ``overlay_ignored_paths`` raw bytes
    by construction, so it cannot distinguish ``-z`` from its absence. The flag
    is the whole mechanism, so the flag is asserted.
    """
    producer = FUNCTIONS["overlay_ignored_paths"]
    assert "git ls-files -z" in producer, (
        "overlay_ignored_paths must use `git ls-files -z`; without it git applies "
        "core.quotePath C-style quoting to non-ASCII names and the derived exclusion "
        "silently stops matching"
    )


def test_overlay_forces_a_byte_oriented_locale() -> None:
    """``sed`` in a UTF-8 locale can error on a non-UTF-8 filename.

    Not behavioural for the same reason as the ``-z`` assertion: reproducing a
    locale-dependent ``sed`` abort would require controlling the host's locale,
    and the fix is one directive.
    """
    assert "LC_ALL=C sed" in FUNCTIONS["worktree_overlay_config"], (
        "the overlay's escaping sed must run under LC_ALL=C so an undecodable "
        "filename cannot abort the scan"
    )


# =============================================================================
# MAJOR 7 — nothing in the environment may weaken a gate.
# =============================================================================

# Environment variables ``ci/checks.sh`` is allowed to read. Each one is a
# deliberate, documented interface. Adding to this set is a reviewable decision;
# the test below makes it impossible to add one silently, which is what makes it
# a general anti-bypass control rather than a list of forbidden spellings.
DECLARED_ENVIRONMENT_READS: Final[frozenset[str]] = frozenset(
    {
        # README.md's documented way to point at a 3.12 interpreter.
        "PYTHON",
        # Read only to say "ignoring SKIP=..." before unsetting it.
        "SKIP",
        # Same shape, same reason, for the coverage stage: read only to say
        # "ignoring PYTEST_ADDOPTS=..." before unsetting it. pytest splices that
        # variable into argv, so `--no-cov` or `--collect-only` set there would
        # turn the coverage gate into a green number over a run that measured
        # nothing — a boolean switch no later flag overrides.
        "PYTEST_ADDOPTS",
        # Where the docker stderr probe file is created.
        "TMPDIR",
        # Dumps the generated gitleaks overlay to stderr.
        "DEBUG",
    }
)

_REFERENCE_RE: Final = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
# Deliberately NARROW. A regex that accepts `NAME=` after any whitespace also
# matches inside `printf 'NOTE: ignoring SKIP=%s'`, which would mark SKIP as
# assigned and quietly excuse it from the allowlist below. Each shape here is a
# real assignment site in ci/checks.sh.
_ASSIGNMENT_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE),  # plain / prefix
    re.compile(r"\bif[ \t]+([A-Za-z_][A-Za-z0-9_]*)=\$\("),  # if NAME=$(...)
    re.compile(r"\)[ \t]+([A-Za-z_][A-Za-z0-9_]*)=\$?\{"),  # case arm) NAME=${...}
    re.compile(r"\bfor[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]+in\b"),
    re.compile(r"\bread(?:[ \t]+-r)?[ \t]+([A-Za-z_][A-Za-z0-9_]*)"),
)
# `NAME="${NAME:-default}"` assigns NAME and is STILL an environment read — that
# exact form is how the preflight memo used to be bypassable. Only the
# defaulting operators count; `x="${x} more"` is an append, not a read.
_SELF_DEFAULT_RE: Final = re.compile(
    r'^[ \t]*([A-Za-z_][A-Za-z0-9_]*)="?\$\{\1:?[-=]', re.MULTILINE
)
# `$NF` and `$NR` appear inside single-quoted awk programs, where they are awk's
# field/record variables and have nothing to do with this shell's environment.
_NOT_SHELL_VARIABLES: Final[frozenset[str]] = frozenset({"NF", "NR"})

# ROUND 5 / MAJOR 1a. POSIX arithmetic expansion reads a bare identifier as a
# variable NAME with no leading `$` — inside `$(( OD_FAST ))`, `OD_FAST` is read
# exactly as `$OD_FAST` would be, but `_REFERENCE_RE` requires a literal `$`
# immediately before the identifier and cannot see it. Measured concretely:
# `if [ "$((OD_FAST))" -eq 1 ] 2>/dev/null; then ...` reads OD_FAST from the
# environment with the token `OD_FAST` never once preceded by `$` anywhere in
# the source, so the old mechanism reported the script clean while it silently
# grew a second preflight bypass.
#
# One level of nested parentheses is handled (`$(( (a + b) * c ))`); this
# codebase has zero `$((` constructs today — grep ``\$((`` against ci/checks.sh
# to check — so this is headroom for the day one is added, not a workaround for
# one that exists. The scan runs unconditionally either way: it does not first
# check the count is zero and skip itself, so it stays live the moment a `$((`
# block is introduced.
_ARITH_BLOCK_RE: Final = re.compile(r"\$\(\((?P<body>(?:[^()]|\([^()]*\))*)\)\)")
# A bare identifier NOT already preceded by `$`. `$VAR` used inside `$((...))`
# is legal POSIX and is already found by `_REFERENCE_RE`; only the `$`-less
# form is new here, so the lookbehind avoids double-counting (harmless, since
# the result feeds a `set`, but double-counting would hide a broken lookbehind).
_ARITH_BARE_IDENTIFIER_RE: Final = re.compile(r"(?<!\$)\b([A-Za-z_][A-Za-z0-9_]*)\b")


def _arithmetic_bare_references(text: str) -> set[str]:
    found: set[str] = set()
    for block in _ARITH_BLOCK_RE.finditer(text):
        found |= set(_ARITH_BARE_IDENTIFIER_RE.findall(block.group("body")))
    return found


def _environment_reads() -> set[str]:
    referenced = set(_REFERENCE_RE.findall(CHECKS_DIRECTIVES))
    referenced |= _arithmetic_bare_references(CHECKS_DIRECTIVES)
    assigned: set[str] = set(VERSIONS)  # sourced from ci/versions.env
    for pattern in _ASSIGNMENT_RES:
        assigned |= set(pattern.findall(CHECKS_DIRECTIVES))
    self_defaults = set(_SELF_DEFAULT_RE.findall(CHECKS_DIRECTIVES))
    return ((referenced - assigned) | self_defaults) - _NOT_SHELL_VARIABLES


def test_checks_sh_reads_only_declared_environment_variables() -> None:
    """A name-agnostic replacement for grepping one bypass spelling.

    PRECISELY WHAT THIS CATCHES, no more (round 5: the previous docstring's
    claim to fail "on ANY addition, whatever it is called" was itself an
    overclaim of exactly the kind
    ``test_msys_comment_states_only_what_was_observed`` polices elsewhere in
    this file — it is corrected here to the same bar):

    * every ``$NAME`` / ``${NAME}`` textual reference in the source, matched by
      ``_REFERENCE_RE``; and
    * every bare identifier inside a POSIX arithmetic expansion
      ``$(( ... ))``, matched by ``_arithmetic_bare_references`` — arithmetic
      context reads ``NAME`` with no leading ``$``, which the first bullet
      cannot see. Concretely, ``if [ "$((OD_FAST))" -eq 1 ]`` used to defeat
      this test's predecessor entirely: ``OD_FAST`` never once appears
      preceded by ``$`` in the source.

    each name found is then checked against every known ASSIGNMENT site
    (``_ASSIGNMENT_RES``, ``_SELF_DEFAULT_RE``, and the keys sourced from
    ``ci/versions.env``); what is left over is "read from the environment,
    assigned nowhere in this script" and must be in ``DECLARED_ENVIRONMENT_READS``.

    THIS FILE'S FULL SET OF ANTI-BYPASS DETECTORS (round 6 — stated here once,
    precisely, rather than reassembled from four separate docstrings that each
    only described their own piece, which is the exact "PRECISELY WHAT THIS
    CATCHES" bar this docstring already claimed to meet twice and, per rounds
    4 and 5's findings, had not):

    1. THIS TEST — every ``$``/``${``-prefixed textual reference
       (``_REFERENCE_RE``), plus every bare arithmetic-expansion identifier
       (``_arithmetic_bare_references``). Catches any reference where the
       target's name is a literal token in the source, ``$``-prefixed or not.
    2. ``test_checks_sh_never_uses_eval`` — bans ``eval`` outright. An
       ``eval``-constructed reference (``eval "v=\\${$n:-}"``) is invisible to
       (1) by construction: no regex over source text can see a name a second
       interpretation pass builds at runtime.
    3. ``test_checks_sh_never_uses_env_printenv_export_or_bare_set`` (round 6)
       — bans ``env``, ``printenv``, ``export`` and a bare (argument-less)
       ``set`` outright. Each dumps every variable as ``NAME=value`` lines
       with the target's name, if it appears at all, only inside a RUNTIME
       comparison against the dump (e.g. a ``sed`` pattern's ``^NAME=``
       anchor) — never preceded by a literal ``$`` and never adjacent to
       ``eval`` — invisible to both (1) and (2).
    4. ``test_checks_sh_behaviour.test_no_environment_variable_disables_the_pin_check``
       — behavioural, not source-level: it actually RUNS the script under a
       list of plausible bypass-variable spellings and asserts the pin check
       still fires. It complements (1)-(3); it cannot replace them, because it
       only tries spellings someone thought to list, against the CURRENT
       script — it would not have caught (3) before (3) existed, for example.

    WHAT NONE OF THE FOUR CATCH — tried and found inapplicable in round 6,
    not left unconsidered: indirect expansion (``${!name}``) is a bash/ksh
    extension, a syntax error under dash (the shell node A runs), so it
    cannot appear in a script this project could actually deploy. Sourcing
    attacker-controlled code through a second file is not reachable via an
    ENVIRONMENT VARIABLE — the one line this script sources,
    ``. "${VERSIONS_ENV}"``, resolves its path from ``SCRIPT_DIR`` (derived
    from ``$0``), which no operator environment variable touches — and
    steering it would mean editing a tracked file, the same
    repository-content-tampering threat that applies to editing
    ``ci/checks.sh`` directly, out of scope for a test about
    environment-VARIABLE interfaces specifically. ``export`` (bare, or
    ``-p``) WAS a live, undetected gap along these lines — found while
    checking for a fifth mechanism — and is now closed by detector 3; see its
    docstring and the block comment above it for the measurement.

    ROUND 7 — SECONDARY ROLE. This test (detector 1 above) is no longer the
    primary defense against a preflight bypass. Round 6's review found a
    bypass shape (NUL-to-newline translation of ``/proc/self/environ`` before
    the value crosses a ``$()`` boundary, plus quote-splitting and awk's
    ``ENVIRON``) that defeats this detector — and the other three below it —
    simultaneously, which is proof that widening this scan is an unbounded
    task, not evidence this scan is wrong. The primary defense as of round 7
    is structural: ``ci/checks.sh`` no longer has a memoised skip-flag
    ANYWHERE for a successfully-read variable to set, so what this detector
    catches or misses no longer determines whether a pin check can be
    bypassed — see ``test_no_memoized_flag_gates_pin_verification`` above and
    the preflight header comment in ``ci/checks.sh``. This test stays: a
    bypass attempt that ALSO happens to leave a literal, undeclared ``$NAME``
    reference in the source is still worth catching, for free, as a second
    layer.
    """
    reads = _environment_reads()
    assert reads, "the reference/assignment parser found nothing; fix it before trusting it"

    undeclared = sorted(reads - DECLARED_ENVIRONMENT_READS)
    assert not undeclared, (
        f"ci/checks.sh reads undeclared environment variable(s) {undeclared}. Every "
        "environment read is an interface an operator (or an agent in a hurry) can "
        "use to change what a gate does. If one of these is deliberate, add it to "
        "DECLARED_ENVIRONMENT_READS with a comment saying what it is for."
    )

    retired = sorted(DECLARED_ENVIRONMENT_READS - reads)
    assert not retired, (
        f"DECLARED_ENVIRONMENT_READS lists {retired}, which ci/checks.sh no longer "
        "reads. Stale entries widen the allowlist for free."
    )


def test_checks_sh_never_uses_eval() -> None:
    """MAJOR 1b: `eval` can read a variable whose name is nowhere a literal token.

    ``for n in OD_HAX; do eval "v=\\${$n:-}"; ...; done`` reads ``OD_HAX``'s
    value; the only literal token in the source is ``n``, so no amount of
    widening ``_REFERENCE_RE`` or ``_arithmetic_bare_references`` can see the
    real name — a regex over source text cannot enumerate what a second
    interpretation pass would construct at runtime. This is not a proxy for
    behaviour the way most tests in this file try not to be (see the module
    docstring's warning against grepping for a symptom instead of driving the
    script): ``eval`` genuinely is the mechanism the finding describes, so
    banning the literal token IS banning the behaviour, completely, not just one
    spelling of it.

    ci/checks.sh has never needed it: ``pin_value()`` reads a version by a
    DERIVED key using ``sed -n "s/^$1=//p" ...`` for exactly this reason — see
    its own comment, which now doubles as the reason this ban costs nothing
    today. If a future stage genuinely needs indirection, the tests this one
    is paired with — ``test_checks_sh_reads_only_declared_environment_variables``,
    ``test_checks_sh_never_uses_env_printenv_export_or_bare_set`` (round 6:
    a different unconditional-command ban, not indirection, but closing the
    same "invisible to the reference/arithmetic scan" gap by a different
    route), and ``test_no_environment_variable_disables_the_pin_check`` in
    ``test_checks_sh_behaviour.py`` — need architectural rework FIRST, not a
    quiet removal of this assertion.

    ROUND 7 — SECONDARY ROLE. As of round 7 this ban is defense-in-depth, not
    the primary defense: ``ci/checks.sh`` no longer has any memoised flag for
    an ``eval``-constructed reference (or any other mechanism) to set, so an
    ``eval`` reappearing would have nothing left to bypass even if this test
    were deleted — see ``test_no_memoized_flag_gates_pin_verification`` and
    the preflight header comment in ``ci/checks.sh``. Kept in place because
    ``eval`` is still worth banning on its own general-hygiene merits (it is
    the paradigm case of "a regex over source text cannot see what this
    constructs at runtime"), and because a bypass attempt that also happens
    to use ``eval`` is still caught here as a second, independent layer.
    """
    assert "eval" not in CHECKS_DIRECTIVES, (
        "ci/checks.sh now contains `eval`, which can read or construct an "
        "environment-variable reference whose name appears nowhere as a literal "
        "token in the source — defeating "
        "test_checks_sh_reads_only_declared_environment_variables and the "
        "arithmetic-expansion extension by construction, regardless of how far "
        "either regex is widened. Remove the eval, or rework the anti-bypass "
        "tests to reason about behaviour rather than source text before adding it."
    )


# =============================================================================
# ROUND 6 / MAJOR 1 — CONVERGENT FINDING. spec-guardian and peer-reviewer each
# independently constructed a near-identical proof-of-concept for a FOURTH
# environment-read shape none of the three detectors above can see:
#
#     _od_bypass=$(env | sed -n 's/^ORBITAL_DRIFT_PREFLIGHT_DONE=//p')
#     if [ "${_od_bypass}" = "1" ]; then
#       python_preflight_done=1
#     fi
#
# `env` (and `printenv`, and a bare `set` with no arguments) prints every
# environment/shell variable as `NAME=value` lines with none of the three
# signatures the existing detectors look for: the target name is never
# preceded by `$` anywhere in the source (the sed pattern's `^` is an anchor,
# not a sigil, so `_REFERENCE_RE` misses it); it is not inside `$(( ))` (so
# `_arithmetic_bare_references` misses it); and the literal token `eval`
# never appears (so the eval ban misses it). `_od_bypass=$(...)` is itself a
# plain assignment — `_ASSIGNMENT_RES` correctly classifies `_od_bypass` as a
# local variable, which it is; the LEAK is in what feeds it, not in that
# variable's own name.
#
# Verified empirically (round 6), on this project's own authoring-box shell,
# that `env`, bare `set`, bare `export` and `export -p` all leak an exported
# value through exactly this shape:
#
#     ORBITAL_DRIFT_PREFLIGHT_DONE=1 sh -c \
#       'export -p | sed -n "s/^export ORBITAL_DRIFT_PREFLIGHT_DONE=//p"'
#     -> "1"
#     ORBITAL_DRIFT_PREFLIGHT_DONE=1 sh -c \
#       'export | sed -n "s/^export ORBITAL_DRIFT_PREFLIGHT_DONE=//p"'
#     -> "1"
#
# `export` is the round-6 brief's own further-bypass-attempt requirement,
# satisfied: the brief asked for bans on `env`/`printenv`/bare `set` only,
# but `export -p` (and bare `export`, which every shell this project targets
# treats identically to `-p` even though POSIX itself calls that case
# "unspecified") leaks the same information through a literal token —
# "export" — that is neither "env", "printenv" nor "set", so it survives a
# ban scoped to only those three. This IS a fifth, previously-open hole, not
# a hypothetical one, and it is closed below alongside the three the brief
# named. ci/checks.sh has zero uses of `export` as a WORD anywhere outside a
# stripped comment (grep with a word-boundary confirms it; a plain substring
# check does not suffice — "exported" appears in real, non-comment printf
# prose ("...exported tarball or a copied directory...") and must NOT trip
# this ban, so the check below matches `export` only as a whole word, exactly
# as `\bset\b` already has to for the same reason). Banning the WORD is
# consistent with the script's own design, which scopes variables to a single
# command (`MSYS_NO_PATHCONV=1 ... docker run "$@"`) rather than exporting
# anything module-wide — so the ban costs nothing today, the same argument
# that already justifies the `eval` ban.
#
# TRIED AND FOUND NOT TO APPLY, rather than left unconsidered, per the
# brief's "genuinely try" instruction:
#
#   IFS manipulation / positional parameters (`set --`) — still require a
#   literal `$NAME` to select what gets assigned, which `_REFERENCE_RE`
#   already sees; word-splitting changes HOW a value already named with `$`
#   gets distributed across `$1 $2 ...`, not WHETHER its name was `$`-prefixed
#   in the first place.
#
#   Sourcing a second file — the one source line in this script,
#   `. "${VERSIONS_ENV}"`, resolves its path from `SCRIPT_DIR` (derived from
#   `$0`), never from anything an operator's environment variable controls.
#   An environment-variable-driven bypass cannot steer WHAT gets sourced here;
#   steering the CONTENT of ci/versions.env would mean editing a tracked
#   file, which is the same repository-content-tampering threat that applies
#   to editing ci/checks.sh directly — a different threat model than the one
#   this group of tests defends (an operator or agent changing behaviour via
#   the command line, not via a commit).
#
#   Indirect expansion `${!name}` — a bash/ksh93 extension, not POSIX; a
#   syntax error under dash, the shell node A actually runs, so it cannot
#   appear in a script this project could deploy there.
#
# The `env` lookbehind below excludes it from matching inside `versions.env`,
# `.env`, `.env.example` and `VERSIONS_ENV` — all genuine, frequent
# substrings elsewhere in ci/checks.sh. The `set` lookahead requires that NO
# argument follows, so `set -eu` / `set +e` / `set --` (all real, load-bearing
# uses in this file) are untouched; only `set` invoked with zero arguments —
# which dumps every shell variable — is forbidden.
# =============================================================================

_ENV_COMMAND_RE: Final = re.compile(r"(?<![./\w$-])env\b")
_BARE_SET_COMMAND_RE: Final = re.compile(r"\bset\b(?=[ \t]*(?:[;|&)<>]|$))", re.MULTILINE)
# Word-boundary, not a plain substring: unlike `eval` (which never occurs as a
# substring of anything else in this file), `export` is a substring of the
# real, non-comment prose "...exported tarball..." in
# require_git_history_is_scannable's NOT-A-REPOSITORY message, and `\bexport\b`
# is what correctly leaves that alone while still catching a real invocation.
_UNCONDITIONALLY_BANNED_DUMP_RES: Final[dict[str, re.Pattern[str]]] = {
    "printenv": re.compile(r"\bprintenv\b"),
    "export": re.compile(r"\bexport\b"),
}


def test_checks_sh_never_uses_env_printenv_export_or_bare_set() -> None:
    """ROUND 6 / MAJOR 1: a fourth (and, for `export`, fifth) environment-dump shape.

    See the block comment immediately above for the converged proof-of-concept,
    the empirical verification, and the further-bypass-attempt trail (what was
    tried, what was found — `export` — and what was tried and rejected as
    inapplicable). Same shape as ``test_checks_sh_never_uses_eval``: an
    unconditional ban on a MECHANISM, not a scan for one spelling of its
    output, because each of these commands dumps every variable regardless of
    what it is named.

    ROUND 7 — SECONDARY ROLE. Even this detector's own review round produced a
    bypass that defeats it (and the other three) at once — piping
    ``/proc/self/environ`` through ``tr '\\0' '\\n'`` before the value crosses
    a ``$()`` boundary, plus quote-splitting and awk's ``ENVIRON`` — which is
    exactly the "one more spelling" pattern this whole family of tests kept
    reproducing. As of round 7 this ban is defense-in-depth: the primary
    defense is that ``ci/checks.sh`` has no memoised flag left for ANY of
    these mechanisms — caught here or not — to set. See
    ``test_no_memoized_flag_gates_pin_verification`` and the preflight header
    comment in ``ci/checks.sh`` for the structural claim this test now
    supplements rather than carries alone.
    """
    for token, pattern in _UNCONDITIONALLY_BANNED_DUMP_RES.items():
        assert pattern.search(CHECKS_DIRECTIVES) is None, (
            f"ci/checks.sh now invokes `{token}` as a command, which — bare, or with "
            "the flags POSIX defines for it — prints every environment/shell "
            "variable as NAME=value lines. That defeats "
            "test_checks_sh_reads_only_declared_environment_variables and the "
            "arithmetic-expansion extension by construction: the target variable's "
            "name never has to appear preceded by `$` anywhere in the source, only "
            "inside a runtime comparison against the dumped output."
        )

    assert _ENV_COMMAND_RE.search(CHECKS_DIRECTIVES) is None, (
        "ci/checks.sh now invokes `env` as a command. `env` with no arguments "
        "prints every environment variable as NAME=value lines, defeating the "
        "$-token and arithmetic-expansion scans by construction — the target's "
        "name is never preceded by `$`."
    )

    assert _BARE_SET_COMMAND_RE.search(CHECKS_DIRECTIVES) is None, (
        "ci/checks.sh now invokes a bare `set` (no arguments). A bare `set` "
        "prints every shell variable as NAME=value lines, the same class of leak "
        "as `env`. `set -eu` / `set +e` / `set --` all carry arguments and are "
        "unaffected by this check."
    )


# =============================================================================
# MINOR (round 6) — nothing stops a future edit from silently deleting one of
# this file's own anti-bypass tests; none of the OTHER tests in this suite
# would notice, because each anti-bypass test bans a mechanism no other test
# asserts against. Mirrors test_shell_function_parser_found_every_dispatched_
# stage's shape (a guard on the parser/set itself), generalised once for every
# load-bearing anti-bypass test rather than added one at a time per detector.
#
# ROUND 7 — these three are demoted to defense-in-depth (see each one's own
# "ROUND 7 — SECONDARY ROLE" docstring paragraph above): the PRIMARY defense
# against a preflight bypass is now structural
# (``test_no_memoized_flag_gates_pin_verification``, below), because rounds
# 3-6 proved that enumerating bypass mechanisms one at a time does not
# converge. They stay in ``LOAD_BEARING_ANTI_BYPASS_TESTS`` and this guard
# still applies to them: "secondary" is not "disposable" — a bypass attempt
# that also happens to use one of these three mechanisms should still be
# caught twice, and a silent deletion of any of the three should still fail
# loudly rather than just narrow coverage unnoticed.
#
# ROUND 8 — the round-7 "structural = PRIMARY" belief above was itself wrong:
# ``test_no_memoized_flag_gates_pin_verification`` (the source of
# PRIMARY_STRUCTURAL_TESTS, below) is a source-TEXT shape check exactly like
# the three in LOAD_BEARING_ANTI_BYPASS_TESTS, and was defeated within the
# same round it shipped by a lazy-init idiom no regex over source text can
# rule out in general — see that test's own "ROUND 8" docstring paragraph.
# EVERY test guarded by either frozenset below is now defense-in-depth only.
# The actual PRIMARY, authoritative proof lives in
# ``test_checks_sh_behaviour.py`` (``LOAD_BEARING_BEHAVIOURAL_TESTS`` there),
# which asserts the property as a black box — feed the real script a correct
# probe, then a wrong one, within one process, and assert the wrong one wins —
# rather than by matching a shape in the source. This file's two frozensets
# are NOT renamed to reflect that (churn with no behavioural benefit — anyone
# reading this comment gets the correction, and PRIMARY_STRUCTURAL_TESTS is
# still an accurate name for "asserts the STRUCTURAL claim", just not for
# "is the primary defense" any more); read the name historically, not as a
# current ranking.
#
# ROUND 9 — this guard itself was, until now, SHAPE-based rather than
# BEHAVIOUR-based: it only checked that a `def test_name(...)` line with the
# right name still existed, not that the function still did anything. Both
# reviewers demonstrated a concrete PoC — gut a guarded test's body to
# `assert recording.returncode in (0, 1, 2)` (always true), or decorate it
# with `@pytest.mark.skip`/`@pytest.mark.xfail` — that leaves the `def` line
# untouched, so this guard kept passing while the test it names stopped
# proving anything. Fixed below: for every name in EITHER frozenset, the
# guard now ALSO verifies (a) the function carries no skip/xfail decorator,
# parsed from the AST rather than grepped, and (b) the function's body still
# contains at least the number of `assert` statements recorded in
# `MIN_ASSERT_COUNTS` below.
#
# ROUND 9b — (a) and (b) ABOVE DID NOT CLOSE THE CLASS THEY WERE SCOPED TO
# CLOSE. Both reviewers found this independently and converged on the same
# root cause; the orchestrator then verified both variants LIVE against
# test_checks_sh_behaviour.py's guarded PRIMARY test
# `test_the_interpreter_check_tracks_the_current_probe_result_not_a_cached_one`
# (floor 4, six real asserts, every one of them left syntactically intact):
#
#   variant A — `pytest.skip("wip")` as the FIRST statement of the body:
#               the round-9 guard PASSED; pytest reported "2 passed, 1
#               skipped". The PRIMARY test silently skipped; the guard
#               reported clean.
#   variant B — a bare `return` as the FIRST statement of the body: the
#               round-9 guard PASSED; pytest reported "3 passed". No SKIPPED
#               line, no signal anywhere — indistinguishable from genuine
#               success in a green summary. This is the dangerous one.
#
# ROOT CAUSE, one sentence: `_assert_count` counts Assert NODES anywhere in
# the AST subtree with NO reachability awareness, and
# `_skip_or_xfail_decorator_name` only ever inspected `func.decorator_list`,
# never the body — so ANY statement that makes the body unreachable, inserted
# first, leaves every original assert syntactically present (floor satisfied)
# while the test executes none of them. Four vectors are closed below, all in
# `_assert_guarded_test_not_neutered`:
#
#   1. an imperative skip/xfail CALL anywhere in the body
#      (`_skip_or_xfail_call`) — closes variant A;
#   2. a `return` or `raise` anywhere in the body other than as its FINAL
#      statement (`_early_exit_statement`) — closes variant B. Same reasoning
#      `test_no_memoized_flag_gates_pin_verification` already applies to a
#      bare `return` in require_python_interpreter, for the identical
#      underlying reason: an early exit is indistinguishable from success;
#   3. an `if`/`while` whose test is a statically-false CONSTANT
#      (`_statically_false_conditional`) — `if False:` / `if 0:` / `if None:`
#      wrapped round the body swallows it whole while every assert inside
#      stays present and counted. Neither reviewer's own fix bullet named
#      this vector, so it would have been left open by a naive reading of
#      either review;
#   4. a decorator (or an imperative call) reached through a MODULE-LEVEL
#      import alias or rebinding (`_MODULE_NAME_BINDINGS`,
#      `_resolve_module_alias`). `from pytest import skip as sk` then `@sk`,
#      or `_disable = pytest.mark.skip` then `@_disable`, renders as a bare
#      `ast.Name` containing neither "skip" nor "xfail", so the substring
#      match alone missed it entirely.
#
# MEASURED BEFORE IMPLEMENTING, so that none of the four costs anything
# today: no guarded test in either file contains a skip/xfail call, a
# non-final `return`/`raise`, a constant-test `if`/`while`, or a module-level
# alias. (The nested `_count` helper inside
# `test_the_actual_pin_check_reexecutes_once_per_stage_that_needs_it` does
# `return` — it is excluded because it opens a NEW SCOPE, which check 2 does
# not descend into.) `_MODULE_NAME_BINDINGS` is EMPTY for both modules as of
# this round: vector 4's machinery is headroom for the day an alias is
# introduced, exactly like `_arithmetic_bare_references` above, not a
# workaround for one that exists. The one legitimate `pytest.skip(...)` in
# this project's test suite — `test_checks_sh_behaviour.
# test_an_unrepresentable_filename_fails_loudly_not_as_a_config_parse_error`,
# an iconv-capability guard — is in NO load-bearing set (verified, not
# assumed) and is therefore untouched by check 1. If a guarded test ever
# genuinely needs a conditional skip, add an explicit per-name exemption
# there and then; do not weaken the check for every name.
#
# WHAT THIS DOES NOT DO, BY DESIGN, PER THE OPERATOR'S BOUNDED-ROUND
# DIRECTIVE — stated to the same standard this file's own
# `test_msys_comment_states_only_what_was_observed` polices, because round
# 9's version of this paragraph overclaimed twice ("This closes the
# demonstrated PoC exactly"; the decorator check "does not care how the
# decorator was imported or spelled") and BOTH claims were false, as variants
# A and B and vector 4 above each demonstrate:
#
#   * IT DOES NOT PROTECT ITSELF. Deleting or gutting
#     `test_every_load_bearing_anti_bypass_test_still_exists`,
#     `test_every_primary_structural_test_still_exists`, the two frozensets
#     above, `MIN_ASSERT_COUNTS`, or any of the `_assert_count` /
#     `_skip_or_xfail_decorator_name` / `_skip_or_xfail_call` /
#     `_early_exit_statement` / `_statically_false_conditional` /
#     `_resolve_module_alias` helpers below is caught by NOTHING automated in
#     this suite — the same "who guards the guard" regress spec-guardian and
#     the operator both named as unbounded, and both agreed one hardening
#     pass is the deliberate stopping point rather than chasing a third
#     layer. Accepted, not fixed; expected to be caught by human/agent review
#     (this file's diff is small and central enough to review directly).
#
#   * IT DOES NOT COVER ARBITRARY DECORATOR INDIRECTION — vector 4's tail.
#     Alias resolution is a single scan of MODULE-LEVEL bindings only:
#     `import X as Y`, `from M import N as Y`, and a top-level
#     `Y = <dotted.name>` (plus short chains of those). A decorator computed
#     at runtime (`getattr(pytest.mark, "sk" + "ip")`), returned by a helper
#     function, pulled out of a dict/list, or bound anywhere other than
#     module level still renders with neither substring present and is NOT
#     caught. Closing that is the unbounded arms race rounds 3-8 already
#     proved does not converge for the probe-level checks; disclosed here
#     rather than chased.
#
#   * IT DOES NOT CATCH EVERY WAY TO MAKE A BODY UNREACHABLE — only the
#     three above. `_assert_count` is deliberately UNCHANGED and still counts
#     Assert nodes with no reachability model of its own; reachability is
#     enforced by checks 1-3 beside it, so any dead-code shape those three do
#     not name survives. Known and left open: iterating a constant-empty
#     collection (`for _ in []:` wrapped round the body) is statically
#     decidable and is NOT checked; a skip made CONDITIONAL on something
#     true-in-practice (`if not shutil.which("docker"): pytest.skip(...)`) is
#     caught only because check 1 ignores the condition entirely and flags
#     the call outright — which is why a guarded test that legitimately needs
#     one must be exempted by name rather than the check relaxed. Adding a
#     fourth dead-code shape each time one is thought of is the same
#     non-converging enumeration rounds 3-8 already ran; the operator bounded
#     this round at the four vectors above, so the rest is disclosed here.
#
#   * IT DOES NOT LOOK AT WHAT IS ASSERTED, ONLY AT HOW MANY. A quantity
#     floor cannot distinguish a real assertion from a vacuous one — both
#     reviewers agree this is an architectural limit of a non-semantic check,
#     not an oversight. Concretely, the worked example, so a future reader
#     does not have to reconstruct its shape: replacing
#     `test_no_memoized_flag_gates_pin_verification`'s twelve real asserts
#     with twelve copies of
#
#         assert True
#
#     satisfies the floor of 6, carries no decorator, calls no skip, exits
#     nowhere early and wraps nothing in `if False:` — every check below
#     passes and the test proves nothing at all. Catching that requires
#     judging MEANING, which stays a human/agent review responsibility. The
#     floors raise the cost of a silent gutting; they do not make it
#     impossible.
#
# Two of the five/two-of-four names guarded below
# (`test_checks_sh_never_uses_eval` here, and
# `test_the_actual_version_probe_still_runs_under_every_bypass_attempt` in
# `test_checks_sh_behaviour.py`) already consisted of exactly one legitimate
# `assert` before round 9; for those specifically, `MIN_ASSERT_COUNTS` cannot
# be set above 1 without breaking the real test, so the assert-count floor
# adds no incremental protection for those two beyond checks 1-4 and plain
# existence — a narrower instance of the same accepted, documented
# limitation, not a new one.
# =============================================================================

_TEST_MODULE_SRC: Final[str] = Path(__file__).read_text(encoding="utf-8")
_TEST_FUNCTION_NAMES: Final[frozenset[str]] = frozenset(
    re.findall(r"^def (test_[A-Za-z0-9_]+)\(", _TEST_MODULE_SRC, re.MULTILINE)
)

# ROUND 9 — parsed once, via `ast` rather than regex (this is Python source,
# unlike ci/checks.sh, so a real parser is available and is not fooled by a
# decorator or assert spanning multiple lines, a triple-quoted string that
# happens to contain the word "assert", etc.). `ast.walk` over the whole
# module also reaches nested/async defs, which none of these guarded names
# are, but costs nothing to handle generally.
_TEST_MODULE_AST: Final[ast.Module] = ast.parse(_TEST_MODULE_SRC, filename=str(Path(__file__)))
_TEST_FUNCTION_DEFS: Final[dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {
    node.name: node
    for node in ast.walk(_TEST_MODULE_AST)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
}


def _decorator_qualname(node: ast.expr) -> str:
    """Render a decorator expression back to the dotted name AS SPELLED IN
    THIS SOURCE FILE, e.g. ``@pytest.mark.skip(reason=...)`` ->
    ``"pytest.mark.skip"``, whether it was called or bare.

    ROUND 9b — precisely what this is and is not: it renders the SPELLING,
    nothing more. It does NOT resolve where that spelling came from; a
    decorator written ``@sk`` renders as ``"sk"``. Resolving the two common
    ways ``sk`` could be bound to something skip-shaped is a separate,
    explicitly-bounded step — see ``_resolve_module_alias`` — and round 9's
    claim here that this function works "regardless of import aliasing" was
    simply wrong.
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

    Deliberately stricter than ``_decorator_qualname``, which falls back to
    ``ast.dump`` so a human reading a failure message can see what the
    unrenderable decorator was. That dump embeds string literals, and this
    function's results feed the alias table and the imperative-call scan —
    where a dumped ``{"SKIP_PREFLIGHT": "1", ...}`` would smuggle the
    substring "skip" into a name and fail a legitimate test. ``BYPASS_ATTEMPTS``
    in ``test_checks_sh_behaviour.py`` is exactly such a dict, and it is
    referenced from inside a ``@pytest.mark.parametrize`` decorator on a
    GUARDED test, so this is a real false positive that was avoided, not a
    hypothetical one.
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
    nothing nested, nothing computed. See the "WHAT THIS DOES NOT DO" block
    comment above for the indirection this cannot and does not try to follow.
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


_MODULE_NAME_BINDINGS: Final[dict[str, str]] = _module_name_bindings(_TEST_MODULE_AST)
# Bounded, cycle-safe: `a = b` / `b = a` terminates on the seen-set, and a
# chain longer than this is indirection this round deliberately does not chase.
_ALIAS_RESOLUTION_STEPS: Final[int] = 8


def _resolve_module_alias(rendered: str) -> str:
    """Rewrite the LEADING segment of ``rendered`` through
    ``_MODULE_NAME_BINDINGS`` until it stops changing. ``"sk"`` ->
    ``"pytest.skip"`` given ``from pytest import skip as sk``; ``"pt.mark.skip"``
    -> ``"pytest.mark.skip"`` given ``import pytest as pt``. Returns the input
    unchanged when no binding applies, which is the case for every name in
    both modules today.
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
    OR of its alias-resolved form. Both, not just the resolved one, so
    resolution can only ever ADD coverage — a perverse binding such as
    ``skip = pytest.mark.parametrize`` cannot subtract it. Does not match
    ``parametrize``: neither substring occurs in it.
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
    syntactically present — the ``MIN_ASSERT_COUNTS`` floor is satisfied — and
    executes none of them, reporting as a SKIP inside an otherwise green run.
    Same substring test as the decorator check, applied to the callee's dotted
    name only: arguments are never inspected, so a test that merely mentions
    the word "skip" in a message string is not flagged.

    Scans ``func.body``, NOT the whole function node, so a
    ``@pytest.mark.parametrize(...)`` decorator's own arguments are out of
    scope here — decorators are the other check's job.
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
# per_stage_that_needs_it` in test_checks_sh_behaviour.py has exactly such a
# helper (`_count`), and must not be flagged for it.
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
    inside a nested function, lambda or class. Sorted by line number so a
    failure message names the FIRST offending line rather than whichever one
    ``ast.walk``'s breadth-first order happened to reach first.
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

    A bare ``return`` as the first statement is the WORST variant found this
    round: it produces no SKIPPED line, no warning, nothing — the test reports
    as an ordinary PASS while executing zero assertions, indistinguishable
    from genuine success in a green summary. ``raise`` is included for the
    same reason a bare ``return`` is banned inside
    ``require_python_interpreter`` by
    ``test_no_memoized_flag_gates_pin_verification``: an unconditional early
    exit is exactly the shape being defended against, and (measured) no
    guarded test uses one, so banning it costs nothing today. A ``return`` or
    ``raise`` as the final statement is allowed — it can swallow nothing.
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
    counted, so the ``MIN_ASSERT_COUNTS`` floor is satisfied by a test that
    runs nothing. Deliberately limited to ``ast.Constant`` tests: no general
    dataflow analysis, no constant folding of expressions, no tracking of a
    module-level ``ENABLED = False``. Narrow and decidable beats broad and
    approximate here — a checker that guesses is a checker that cries wolf,
    which this file's ``_strip_comments`` docstring already explains gets a
    test deleted.
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
    """Total ``assert`` STATEMENTS anywhere in ``func``'s body, including
    inside ``for``/``with`` blocks and nested helper functions defined inside
    it. This is a floor on QUANTITY, calibrated per test below — it is
    deliberately NOT a check on WHAT is asserted (matching specific strings,
    specific call patterns, ...), because that is exactly the kind of
    source-shape detector rounds 3-8 already proved is an unbounded arms
    race for the PROBE-level checks; here it is used only as a cheap,
    generic tripwire against the demonstrated "gut the body to one trivial
    always-true assert" PoC, not as a substitute for a human judging whether
    the remaining asserts are meaningful.
    """
    return sum(1 for node in ast.walk(func) if isinstance(node, ast.Assert))


# ROUND 9 — calibrated floors. Each value is the ACTUAL assert count measured
# in this test, at the time this guard was hardened, minus a safety margin
# WHERE THERE WAS ROOM FOR ONE — not an arbitrary small constant.
#
# ROUND 9b correction: the blanket "minus a safety margin" claim was
# inaccurate. TWO of the five entries have ZERO margin and are deliberate
# exceptions, stated here rather than left to be inferred by subtracting the
# trailing `# measured:` comments:
#
#   * test_checks_sh_never_uses_eval — floor 1, measured 1. The real test has
#     exactly one legitimate assert, so the floor cannot go higher (already
#     disclosed in the "WHAT THIS DOES NOT DO" block above).
#   * test_log_only_memos_never_gate_a_verification_return — floor 2,
#     measured 2. Zero margin ON PURPOSE: both asserts are load-bearing (the
#     first proves the guard-block regex matched anything at all, the second
#     is the actual claim), so losing either is a regression, not a refactor.
#     The price is that a legitimate refactor merging them must lower this
#     floor in the same change.
#
# The remaining three carry a real margin and are high enough that
# peer-reviewer's exact PoC (a single trivial
# `assert recording.returncode in (0, 1, 2)`) fails them. If a legitimate
# refactor deliberately shrinks a test below its floor here, lower the floor
# explicitly in the same change, with review sign-off — do not let it drift
# down silently.
MIN_ASSERT_COUNTS: Final[dict[str, int]] = {
    "test_checks_sh_reads_only_declared_environment_variables": 2,  # measured: 3
    "test_checks_sh_never_uses_eval": 1,  # measured: 1 (see disclosure above)
    "test_checks_sh_never_uses_env_printenv_export_or_bare_set": 2,  # measured: 3
    "test_no_memoized_flag_gates_pin_verification": 6,  # measured: 12
    "test_log_only_memos_never_gate_a_verification_return": 2,  # measured: 2
}


def _assert_guarded_test_not_neutered(name: str) -> None:
    """Shared body for both guard tests below: existence (already checked by
    the caller via the ``missing`` set), skip/xfail-freedom (decorator AND —
    round 9b — imperative call), reachability of the body (no early exit, no
    statically-false wrapper), and the calibrated assert-count floor from
    ``MIN_ASSERT_COUNTS``.

    Order matters for the diagnostics, not for correctness: the reachability
    checks run BEFORE the count floor, because when a body has been made
    unreachable the count is still satisfied — that combination is the whole
    round-9b defect, and reporting "floor met" first would be actively
    misleading.
    """
    func = _TEST_FUNCTION_DEFS.get(name)
    assert func is not None, (
        f"{name} is in _TEST_FUNCTION_NAMES (matched by the top-level `def` regex) "
        "but ast.parse did not find it as a FunctionDef — investigate before "
        "trusting either scan"
    )

    skip_marker = _skip_or_xfail_decorator_name(func)
    assert skip_marker is None, (
        f"{name} is decorated with `@{skip_marker}`. A skipped or xfailed test "
        "still shows as part of a green (or green-ish) run while asserting "
        "nothing — worse than deleting it outright, because deletion at least "
        "shows up as a missing name. Remove the marker; if the test is "
        "genuinely flaky, fix or replace it instead of silencing it."
    )

    # Each description is rendered under an explicit `is not None` narrowing
    # rather than indexed inside the assert message: an assert's message is
    # only EVALUATED when the assert fails, but mypy type-checks it either
    # way and does not narrow from the condition, so the direct form needs a
    # `type: ignore` on every line. This costs one local and keeps the module
    # clean under the repository's own typecheck stage.
    skip_call = _skip_or_xfail_call(func)
    skip_call_site = (
        f"`{skip_call[0]}(...)` at line {skip_call[1]}" if skip_call is not None else ""
    )
    assert skip_call is None, (
        f"{name} calls {skip_call_site}. An imperative "
        "skip inside the body leaves every `assert` below it syntactically "
        "present — so the MIN_ASSERT_COUNTS floor is still satisfied — while the "
        "test executes none of them and reports as a SKIP in an otherwise green "
        "run. This is round 9b's variant A, verified live against a guarded "
        "PRIMARY test. No load-bearing test needs a conditional skip today; if "
        "one genuinely does, exempt it here by name, with review sign-off."
    )

    early_exit = _early_exit_statement(func)
    early_exit_site = (
        f"`{type(early_exit).__name__.lower()}` at line {early_exit.lineno}"
        if early_exit is not None
        else ""
    )
    assert early_exit is None, (
        f"{name} has an unconditional {early_exit_site} that is not its final "
        "statement. "
        "Everything after it is dead code whose `assert`s still count toward the "
        "MIN_ASSERT_COUNTS floor. A bare `return` as the first statement is round "
        "9b's variant B — the dangerous one: the test reports as an ordinary PASS "
        "while executing zero assertions, with no SKIPPED line and no signal "
        "anywhere. (A nested helper function's own `return` is not flagged; this "
        "check does not descend into nested scopes.)"
    )

    dead_branch = _statically_false_conditional(func)
    dead_branch_site = f"line {dead_branch.lineno}" if dead_branch is not None else ""
    assert dead_branch is None, (
        f"{name} contains an `if`/`while` with a statically-false constant test at "
        f"{dead_branch_site}. "
        "`if False:` wrapped round the body swallows it whole while every `assert` "
        "inside stays present and counted, satisfying the MIN_ASSERT_COUNTS floor "
        "with a test that runs nothing — round 9b's vector 3."
    )

    floor = MIN_ASSERT_COUNTS[name]
    actual = _assert_count(func)
    assert actual >= floor, (
        f"{name} contains only {actual} `assert` statement(s) in its body, below "
        f"the calibrated floor of {floor} (see MIN_ASSERT_COUNTS). This is the "
        "exact shape of the round-8/9 PoC: a load-bearing test's body reduced to "
        "one (or a handful of) trivial, always-true assert(s) that still passes "
        "and still satisfies the OLD existence-only guard. If you have "
        "genuinely simplified this test on purpose, lower its entry in "
        "MIN_ASSERT_COUNTS explicitly, in the same change, with a reviewer's "
        "sign-off — do not let the floor silently drift down with the code."
    )


LOAD_BEARING_ANTI_BYPASS_TESTS: Final[frozenset[str]] = frozenset(
    {
        "test_checks_sh_reads_only_declared_environment_variables",
        "test_checks_sh_never_uses_eval",
        "test_checks_sh_never_uses_env_printenv_export_or_bare_set",
    }
)

# ROUND 7 — the structural, PRIMARY defense gets the same "cannot be silently
# deleted without this suite noticing" guard the three secondary detectors
# above already had. Distinct from LOAD_BEARING_ANTI_BYPASS_TESTS on purpose:
# these two prove "there is no flag to bypass" rather than "this one
# spelling of reading a flag is banned", which is a different claim that
# would not be caught by widening the anti-bypass set above.
PRIMARY_STRUCTURAL_TESTS: Final[frozenset[str]] = frozenset(
    {
        "test_no_memoized_flag_gates_pin_verification",
        "test_log_only_memos_never_gate_a_verification_return",
    }
)


def test_every_load_bearing_anti_bypass_test_still_exists() -> None:
    """A deleted anti-bypass test fails nothing else in this suite.

    Each test named in ``LOAD_BEARING_ANTI_BYPASS_TESTS`` bans a distinct
    MECHANISM (textual/arithmetic reference, ``eval``, or the
    env/printenv/export/bare-set dump family). None of the others would catch
    its removal — that is precisely what makes each one load-bearing rather
    than redundant — so this is checked directly rather than trusted to stay
    reviewed forever, the same reasoning as
    ``test_shell_function_parser_found_every_dispatched_stage`` for the shell
    function parser it guards. As of round 7 these three are secondary
    defense-in-depth rather than the primary defense (see
    ``PRIMARY_STRUCTURAL_TESTS`` immediately below) — that demotion is a
    reason to keep this guard, not drop it: deleting a secondary layer should
    still be loud.

    ROUND 9 / 9b — existence alone is no longer enough; see the "ROUND 9" and
    "ROUND 9b" block comments above ``MIN_ASSERT_COUNTS`` for why, and for
    what ``_assert_guarded_test_not_neutered`` additionally checks for every
    name in this set: no skip/xfail decorator, no imperative skip/xfail call,
    no non-final ``return``/``raise``, no statically-false conditional, and a
    minimum assert count. Round 9 checked only the first and last of those,
    and was defeated by the other three.
    """
    missing = sorted(LOAD_BEARING_ANTI_BYPASS_TESTS - _TEST_FUNCTION_NAMES)
    assert not missing, (
        f"{missing} no longer exist as `def test_...` functions in this module. "
        "Each closes a distinct environment-variable-bypass mechanism found in "
        "an earlier review round; deleting one silently reopens it."
    )
    for name in sorted(LOAD_BEARING_ANTI_BYPASS_TESTS):
        _assert_guarded_test_not_neutered(name)


def test_every_primary_structural_test_still_exists() -> None:
    """ROUND 7: the structural claim itself must not be silently deletable.

    Mirrors ``test_every_load_bearing_anti_bypass_test_still_exists`` exactly,
    one layer up: PRIMARY_STRUCTURAL_TESTS asserts the structural claim (no
    memoised flag anywhere gates pin verification) rather than one more
    banned spelling, and nothing else in THIS suite would notice either of
    these two tests going missing.

    ROUND 8 — the name is historical, not a current ranking: as of round 8
    these two are also SECONDARY, defense-in-depth checks (see
    ``test_no_memoized_flag_gates_pin_verification``'s own "ROUND 8"
    paragraph) — the actual primary, authoritative proof is
    ``test_checks_sh_behaviour.LOAD_BEARING_BEHAVIOURAL_TESTS``, guarded by
    that file's own ``test_every_load_bearing_behavioural_test_still_exists``.
    Kept here under the original name and still guarded, for the same reason
    the anti-bypass tests above stay guarded after their own round-7 demotion:
    secondary is not disposable.

    ROUND 9 / 9b — existence alone is no longer enough; see the "ROUND 9" and
    "ROUND 9b" block comments above ``MIN_ASSERT_COUNTS`` for why, and for
    what ``_assert_guarded_test_not_neutered`` additionally checks for every
    name in this set: no skip/xfail decorator, no imperative skip/xfail call,
    no non-final ``return``/``raise``, no statically-false conditional, and a
    minimum assert count. Round 9 checked only the first and last of those,
    and was defeated by the other three.
    """
    missing = sorted(PRIMARY_STRUCTURAL_TESTS - _TEST_FUNCTION_NAMES)
    assert not missing, (
        f"{missing} no longer exist as `def test_...` functions in this module. "
        "These prove the round-7 structural fix (no memoised flag gates pin "
        "verification); deleting one leaves that claim untested."
    )
    for name in sorted(PRIMARY_STRUCTURAL_TESTS):
        _assert_guarded_test_not_neutered(name)


def test_no_memoized_flag_gates_pin_verification() -> None:
    """ROUND 7 — the structural fix, asserted directly. Round 7 believed this
    was the PRIMARY defense against a preflight bypass, demoting the three
    token-ban tests above to defense-in-depth.

    ROUND 8 — THIS TEST IS ITSELF NOW SECONDARY, NOT PRIMARY. It was defeated,
    within the very round that shipped it, by a two-statement lazy-init idiom
    (``_od_cache=1`` assigned later in the function body; ``${_od_cache:-0}``
    consulted at the top) that a bare ``return`` (invisible to the
    ``\\breturn\\s+(\\d+)\\b`` regex below, before this round's fix) combined
    with an "assigned ANYWHERE counts as script-internal" environment-read
    scan (no notion of assignment happening AFTER the read it would need to
    guard, in ``test_checks_sh_reads_only_declared_environment_variables``)
    made invisible to every shape check in this file at once. That is proof,
    not a one-off miss: a regex over source TEXT can always be defeated by one
    more shape of the same underlying trick, the same lesson rounds 3-6
    already taught about environment-read spellings specifically. The new
    PRIMARY, authoritative proof of "no memoization anywhere in the preflight
    machinery" is
    ``test_checks_sh_behaviour.test_the_interpreter_check_tracks_the_current_probe_result_not_a_cached_one``
    and
    ``test_checks_sh_behaviour.test_require_tool_tracks_the_current_probe_result_not_a_cached_one``
    — black-box, behavioural, and indifferent to source shape entirely: they
    feed the real script a correct probe result and then, within the same
    process, a wrong one, and assert the wrong one governs. THIS test (and the
    three token-ban tests above it) remain in the suite as cheap, fast,
    early-warning secondary checks — a `return 0` or a bare `return` in one of
    these four functions is still worth flagging on sight, and still costs
    nothing to keep — but none of them, including this one, is what PROVES the
    property holds any more. Fixed alongside this demotion: the bare-`return`
    gap itself (the regex below now also catches it) and require_tool()'s
    total absence from this test before round 8, both named in the PoC above.

    Rounds 3 through 6 each closed one more SPELLING of an environment read
    that could flip a memoised "already checked" flag
    (``python_preflight_done``, ``pins_coverage_checked``, ``checked_tools`` /
    ``tool_already_checked``) and skip a pin comparison outright. Round 6's
    review found a bypass that defeated all four detectors built for those
    spellings AT ONCE (piping ``/proc/self/environ`` through
    ``tr '\\0' '\\n'`` before the value crosses a ``$()`` boundary — the same
    idiom this file already ships and trusts in
    ``nul_records_survive_newline_translation()``), and two more beyond that
    (quote-splitting, POSIX awk's ``ENVIRON`` array) — proof that "enumerate
    every mechanism that can read an environment variable" does not converge.
    The operator's fix: delete every flag a bypass could target, rather than
    add a fifth detector for a fifth mechanism.

    This asserts the flags are actually gone from the source (not merely
    renamed while something equivalent still gates verification), and that
    the functions which used to consult them — ``require_python_interpreter``,
    ``require_pinned_tool``, ``require_pin_coverage`` — have no early
    ``return 0`` a reintroduced flag could hide behind. ``require_python_
    interpreter``'s only early returns must be failure paths (``return 1``,
    after ``fail_python``); a ``return 0`` there would be exactly the shape of
    the removed ``python_preflight_done`` skip.
    """
    # CHECKS_DIRECTIVES (comments stripped), not raw CHECKS_SRC: the header
    # comment above the preflight machinery documents this exact list of
    # retired names ON PURPOSE, as the WHY for a future reader (round 7's own
    # brief asked for that). Matching raw source here would fail on the very
    # explanation this test's own docstring quotes — the same false-positive
    # trap _strip_comments()'s own docstring warns about.
    for retired in (
        "python_preflight_done",
        "pins_coverage_checked",
        "checked_tools",
        "tool_already_checked",
    ):
        assert retired not in CHECKS_DIRECTIVES, (
            f"{retired!r} still appears in ci/checks.sh outside a comment. Round 7 "
            "removed every memoised skip-state in the preflight machinery so there "
            "is nothing left for a bypass — known or undiscovered — to target; its "
            "reappearance in executable code suggests the removal was partially "
            "reverted."
        )

    interpreter_body = _strip_comments(FUNCTIONS["require_python_interpreter"])
    # `[0-9]*`, not `[0-9]+` (round 8 fix): a BARE `return` (no digit at all)
    # must still show up in `returns`, as an empty-string "code", rather than
    # being invisible to this scan the way it was under the old `\d+`-only
    # form. A bare `return` returns the exit status of the LAST command run
    # before it, which can be 0 — the exact PoC this round's brief supplied.
    returns = re.findall(r"\breturn\b[ \t]*([0-9]*)", interpreter_body)
    assert returns, "require_python_interpreter has no return statements to check"
    assert set(returns) == {"1"}, (
        f"require_python_interpreter contains return statement(s) with code(s) "
        f"{sorted(set(returns))!r} (an empty string denotes a BARE `return`, which "
        "returns the exit status of the last command run and can be 0); only "
        "explicit `return 1` (a FAILURE path, after fail_python) may appear. "
        "SECONDARY check only — see this test's own docstring — the PRIMARY proof "
        "is test_checks_sh_behaviour."
        "test_the_interpreter_check_tracks_the_current_probe_result_not_a_cached_one."
    )

    tool_body = _strip_comments(FUNCTIONS["require_pinned_tool"])
    assert "return 0" not in tool_body, (
        "require_pinned_tool contains `return 0` before calling require_tool — "
        "that is exactly the shape of the removed tool_already_checked() skip"
    )
    assert re.search(r"\brequire_tool\b", tool_body), (
        "require_pinned_tool no longer unconditionally calls require_tool"
    )

    # ROUND 8 — require_tool() ITSELF, spec-guardian's finding: the direct
    # descendant of the removed checked_tools memo, and the function that
    # actually calls tool_version() and compares it to the pin. Every shape
    # check through round 7 stopped one function short of this one (only
    # require_pinned_tool, its caller, was ever inspected) — a `return 0` or
    # bare `return` inserted directly inside require_tool(), before or between
    # its two comparisons, was invisible to this entire file before this.
    require_tool_body = _strip_comments(FUNCTIONS["require_tool"])
    require_tool_returns = re.findall(r"\breturn\b[ \t]*([0-9]*)", require_tool_body)
    assert require_tool_returns, "require_tool has no return statements to check"
    assert set(require_tool_returns) == {"1"}, (
        f"require_tool contains return statement(s) with code(s) "
        f"{sorted(set(require_tool_returns))!r}; only `return 1` (a FAILURE path, "
        "after fail_pin) may appear. A `return 0` or bare `return` here — before "
        "the tool_version() comparison, or between it and the failure branch below "
        "it — would be exactly the removed tool_already_checked() skip, one "
        "function deeper than this file checked before round 8. SECONDARY check "
        "only; the PRIMARY proof is test_checks_sh_behaviour."
        "test_require_tool_tracks_the_current_probe_result_not_a_cached_one."
    )
    assert re.search(r"\btool_version\b", require_tool_body), (
        "require_tool no longer calls tool_version() — the real probe"
    )

    coverage_body = _strip_comments(FUNCTIONS["require_pin_coverage"])
    assert "return 0" not in coverage_body, (
        "require_pin_coverage contains an early `return 0` — that is exactly the "
        "shape of the removed pins_coverage_checked skip"
    )

    preflight_body = _strip_comments(FUNCTIONS["preflight"])
    # The one legitimate early `return 0` in preflight() is the empty-pins
    # short-circuit for stages that declare none (gitleaks) — everything after
    # it (require_python_interpreter, the require_pinned_tool loop) must run
    # unconditionally for every other stage, every call.
    assert preflight_body.count("return 0") == 1, (
        f"preflight() contains {preflight_body.count('return 0')} `return 0` "
        "statements; only the empty-`pf_pins` short-circuit (stages that declare "
        "no pins, i.e. gitleaks) is legitimate. A second one would be a "
        "reintroduced log-or-verification skip."
    )
    assert "require_python_interpreter" in preflight_body
    assert "require_pinned_tool" in preflight_body, (
        "preflight() no longer calls require_python_interpreter / require_pinned_tool "
        "unconditionally"
    )


def test_log_only_memos_never_gate_a_verification_return() -> None:
    """The replacement memos (``python_logged``, ``logged_tools`` /
    ``tool_already_logged``, and ``preflight()``'s own ``pf_new`` accumulator)
    suppress OUTPUT only — see the "What IS still memoised" paragraph in
    ci/checks.sh's preflight header comment. Every ``if`` block keyed on one of
    them must contain no ``return``: a log-dedup flag that also gated a
    ``return`` would silently reintroduce a verification skip under a
    different name, which is precisely the mistake this round's own brief
    warned against repeating ("do not let a shortcut for one concern — log
    spam — also disable the other — re-verification").

    ``pf_new`` in ``preflight()`` is included even though its own two
    accumulator guards (inside the tool loop) do not themselves ``printf`` —
    they only feed the SINGLE ``log`` call that follows, which is why this
    test checks for the absence of ``return`` rather than the presence of
    ``printf``: the shape that matters is "never skips a verification call",
    not "always prints something".
    """
    for name in ("require_python_interpreter", "require_tool", "preflight"):
        body = _strip_comments(FUNCTIONS[name])
        guards = re.findall(
            r'if\s+(?:!\s*tool_already_logged\b|\[\s*"\$\{python_logged\}"|\[\s*"\$\{pf_new\}")'
            r"[^\n]*\n(?P<block>(?:.*\n)*?)[ \t]*fi\b",
            body,
        )
        assert guards, f"{name}: expected at least one log-only guard, found none"
        for block in guards:
            assert "return" not in block, (
                f"{name}: a log-only guard (python_logged / tool_already_logged / "
                f"pf_new) contains a `return`:\n{block}\nthat would make the "
                "log-dedup memo also gate verification"
            )


# =============================================================================
# MAJOR 5 — the preflight is scoped per stage, and nothing falls out of it.
# =============================================================================


def _declared_stage_labels() -> list[str]:
    match = re.search(r"^STAGE_LABELS='([^']*)'$", CHECKS_SRC, re.MULTILINE)
    assert match, "could not parse STAGE_LABELS out of ci/checks.sh"
    return match.group(1).split()


def _stage_pin_table() -> dict[str, set[str]]:
    """Parse the ``stage_python_pins`` case into ``{label: {tool, ...}}``."""
    body = FUNCTIONS["stage_python_pins"]
    table: dict[str, set[str]] = {}
    for arm in re.finditer(r"^\s{4}([a-z|]+)\)(.*?);;", body, re.MULTILINE | re.DOTALL):
        labels = arm.group(1).split("|")
        tools = set(re.findall(r"printf '([a-z\\n-]+)\\n'", arm.group(2)))
        flat: set[str] = set()
        for chunk in tools:
            flat |= {part for part in chunk.split("\\n") if part}
        for label in labels:
            table[label] = flat
    return table


STAGE_PINS: Final[dict[str, set[str]]] = _stage_pin_table()


def test_the_stage_pin_table_covers_exactly_the_dispatch_labels() -> None:
    """Guard the parser and the table together.

    ``stage_python_pins`` decides what each stage asserts. A label missing from
    it makes ``preflight`` take the ``*)`` internal-error branch; a label present
    but never dispatched is dead configuration that hides a gap.
    """
    assert STAGE_PINS, "could not parse stage_python_pins; every assertion below is vacuous"
    labels = set(_declared_stage_labels())
    assert labels == set(DISPATCH), (
        f"STAGE_LABELS is {sorted(labels)} but the dispatch case accepts {sorted(DISPATCH)}"
    )
    assert set(STAGE_PINS) == labels, (
        f"stage_python_pins declares {sorted(STAGE_PINS)} but the stages are {sorted(labels)}"
    )


@pytest.mark.parametrize("label", sorted(DISPATCH))
def test_every_stage_calls_the_preflight_for_its_own_label(label: str) -> None:
    """A stage without ``preflight`` announces pins it never checked.

    Measured before the preflight existed: announced ruff 0.16.2 / ran 0.15.20,
    announced mypy 2.3.0 / ran 2.1.0, announced pytest 9.1.1 / ran 8.4.2,
    pyproject requires 3.12 / ran 3.11.9.

    The label must be the stage's OWN: ``preflight lint`` inside
    ``stage_typecheck`` would assert the wrong pin and skip the right one.
    """
    body = FUNCTIONS[DISPATCH[label]]
    assert re.search(rf"^\s*preflight {label}\s*$", body, re.MULTILINE), (
        f"{DISPATCH[label]} does not call `preflight {label}`. Every stage header "
        "prints a version from ci/versions.env; without the preflight that header is "
        "decoration and the stage may be running something else entirely."
    )


def test_the_secrets_gate_declares_no_python_pins() -> None:
    """MAJOR 5: the stage with the fewest prerequisites must keep them.

    It used to assert Python 3.12 plus exact ruff/mypy/pytest/pre-commit while
    executing none of them, so a PyPI blip or a stray mypy reddened a job named
    ``gitleaks``, and a fresh clone with Docker but no Python could not run
    ``sh ci/checks.sh gitleaks`` at all.
    """
    assert STAGE_PINS["gitleaks"] == set(), (
        f"the gitleaks stage now asserts Python pins {sorted(STAGE_PINS['gitleaks'])}, "
        "none of which it executes"
    )
    body = FUNCTIONS["stage_gitleaks"]
    assert "${PYTHON}" not in body, "stage_gitleaks invokes the Python interpreter"
    assert "docker_or_fail" in body and "git_or_fail" in body, (
        "stage_gitleaks must assert the two prerequisites it does have"
    )


def test_every_versions_env_pin_is_claimed_by_a_stage_or_explicitly_exempt() -> None:
    """Per-stage scoping only helps if nothing falls through it unnoticed.

    With one preflight for everything, a new pin was enforced by accident. Now
    it has to be assigned to a stage, or exempted in writing.
    """
    exempt_match = re.search(r"^PREFLIGHT_EXEMPT_PINS='([^']*)'$", CHECKS_SRC, re.MULTILINE)
    assert exempt_match, "could not parse PREFLIGHT_EXEMPT_PINS out of ci/checks.sh"
    exempt = set(exempt_match.group(1).split())

    claimed: set[str] = set()
    for tools in STAGE_PINS.values():
        claimed |= tools

    pinned = {
        key[: -len("_VERSION")].lower().replace("_", "-")
        for key in VERSIONS
        if key.endswith("_VERSION")
    }
    unhandled = pinned - claimed - exempt
    assert not unhandled, (
        f"ci/versions.env pins {sorted(unhandled)} but no stage in stage_python_pins "
        "claims them and PREFLIGHT_EXEMPT_PINS does not list them. An unchecked pin "
        "is an announcement, not a pin."
    )

    probe_block = CHECKS_SRC.split("tool_version() {", 1)[1].split("\n}", 1)[0]
    probed = set(re.findall(r"^\s{4}([a-z][a-z-]*)\)", probe_block, re.MULTILINE))
    unprobeable = claimed - probed
    assert not unprobeable, (
        f"stages claim {sorted(unprobeable)} but tool_version() has no probe for them; "
        "the preflight would take its internal-error branch"
    )


def test_the_preflight_tool_list_is_still_derived_from_versions_env() -> None:
    """A hardcoded pin list falls behind ``ci/versions.env`` silently."""
    assert "versions_env_tools" in FUNCTIONS, (
        "ci/checks.sh no longer derives the pin list from ci/versions.env"
    )
    assert "_VERSION" in FUNCTIONS["versions_env_tools"], (
        "versions_env_tools no longer reads the <NAME>_VERSION keys out of the pin file"
    )
    assert "versions_env_tools" in FUNCTIONS["require_pin_coverage"], (
        "the coverage check no longer consults the derived list, so a pin added to "
        "ci/versions.env and forgotten everywhere else is silently unenforced"
    )


# =============================================================================
# MINOR 15 / round-1 — pytest_suite's emptiness corroboration.
# =============================================================================


def test_pytest_suite_counts_both_default_python_files_patterns() -> None:
    """``test_*.py`` alone missed ``*_test.py`` and passed DECLARED-EMPTY.

    Measured: ``tests/contract/stac_boundary_test.py`` containing no test
    functions produced pytest exit 5, a ``find`` count of 0, and a green
    DECLARED-EMPTY. Contract coverage could drop to zero with Constitution V's
    gate still reporting success.

    The assertion is scoped to the ``find`` COMMAND, not to the function body.
    Scoping it to the body was itself a defect, found by mutation-testing this
    file: ``pytest_suite``'s DECLARED-EMPTY message contains the words
    "test_*.py or *_test.py", so reverting the glob to ``-name 'test_*.py'``
    alone left both literals present in the body and the test went on passing.
    """
    body = FUNCTIONS["pytest_suite"]
    # Scoped to the `find` that BUILDS the list, not to the `collectable_count=`
    # assignment. Those were the same command until the walk was made to fail
    # closed; the count is now taken from a captured list, so the assignment
    # itself no longer mentions any glob and asserting against it would check
    # `printf | sed | wc` for `test_*.py` and always fail.
    match = re.search(r"collectable_list=\$\((?P<cmd>.*?)\) \|\| \{\n", body, re.DOTALL)
    assert match, (
        "pytest_suite has no `collectable_list=$(find ...)` command; the corroborating "
        "count that distinguishes an unauthored suite from a collection error is gone"
    )
    command = match.group("cmd")

    for pattern in ("test_*.py", "*_test.py"):
        assert pattern in command, (
            f"pytest_suite's collectable_count find command does not match {pattern!r}: "
            f"{command!r}\n"
            "pytest's default python_files is `test_*.py` AND `*_test.py`. With only "
            "one of them, a suite made of the other kind is reported DECLARED-EMPTY "
            "and the stage passes with zero coverage."
        )


def test_pytest_suite_separates_helper_modules_from_collection_errors() -> None:
    """A ``fixtures.py`` in an unauthored suite is not a collection error."""
    body = FUNCTIONS["pytest_suite"]
    assert "collectable_count" in body and "module_count" in body, (
        "pytest_suite no longer distinguishes files pytest would collect from "
        "helper modules, so an unauthored suite containing only helpers is "
        "misreported as a collection error"
    )
    assert "python_files_overridden" in body, (
        "pytest_suite no longer notices a pyproject `python_files` override, under "
        "which neither count bounds what pytest collects and the (b)/(c) split is "
        "unsound"
    )


def test_pyproject_does_not_override_python_files_without_telling_checks_sh() -> None:
    """Keep the assumption pytest_suite's counts rest on true."""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pytest_config = config["tool"]["pytest"]["ini_options"]
    assert "python_files" not in pytest_config, (
        "pyproject.toml now overrides python_files. ci/checks.sh pytest_suite "
        "detects this and fails closed, which is correct but blocks the contract "
        "and smoke stages — teach pytest_suite the new patterns in the same commit."
    )


def test_coverage_flags_never_enter_the_global_pytest_addopts() -> None:
    """``--cov`` belongs to the coverage stage's command line, nowhere else.

    ``addopts`` applies to EVERY pytest invocation in the repo. Putting a
    coverage flag there would:

    * attach coverage to ``pytest_suite()``'s ``--collect-only`` probe, which
      exists to answer one question about emptiness and should measure nothing;
    * make the lint/unit/contract/smoke stages hard-fail with "unrecognized
      arguments: --cov" on any machine without pytest-cov — including two stages
      whose ``stage_python_pins`` arm does not assert that pin, so the failure
      would name a tool the stage never claimed to need;
    * silently change what ``--cov-fail-under`` measures depending on which
      stage happened to invoke pytest.

    The coverage stage passes its own flags explicitly and
    tests/unit/test_checks_sh_behaviour.py asserts that argv. This test guards
    the other direction: that nothing put them in the shared config too.
    """
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    addopts = str(config["tool"]["pytest"]["ini_options"].get("addopts", ""))
    for flag in ("--cov", "--no-cov"):
        assert flag not in addopts, (
            f"pyproject addopts carries {flag!r}: {addopts!r}. Coverage flags belong to "
            "ci/checks.sh's stage_coverage command line only."
        )


def test_no_coverage_config_silently_redefines_what_the_gate_measures() -> None:
    """The coverage-config surface gets the same guard `python_files` gets.

    ``pytest_suite`` checks all four files pytest reads ini options from, because
    an unnoticed ``python_files`` override would unsound its counts. coverage.py
    has exactly the same exposure and a sharper failure mode. It reads config
    from ``.coveragerc``, ``setup.cfg [coverage:*]``, ``tox.ini [coverage:*]``
    and ``pyproject.toml [tool.coverage]``, and this one line in any of them:

        [tool.coverage.report]
        exclude_also = ["."]

    matches every line in the tree, drops the statement count to zero, and makes
    coverage report 100% — so ``--cov-fail-under`` passes forever, with real
    product code present and untested. That is *verbatim* the failure mode
    ``stage_coverage``'s header claims to have designed out by refusing a
    filename heuristic: a gate disarmed permanently and silently. Rejecting one
    mechanism and leaving an easier one unguarded is not a design.

    Fails closed and loudly: if coverage config is ever genuinely wanted, this
    test is where the reasoning gets written down, exactly as
    ``test_pyproject_does_not_override_python_files_without_telling_checks_sh``
    is for the pytest side.
    """
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert "coverage" not in config.get("tool", {}), (
        "pyproject.toml now carries a [tool.coverage] section. Keys like "
        "`exclude_also`, `omit` and `include` silently change what the FR-011a gate "
        "measures — `exclude_also = ['.']` makes it report 100% forever. If this is "
        "deliberate, assert the specific keys here and record why in "
        "docs/decisions/001-coverage-gate.md."
    )

    for name in (".coveragerc", "setup.cfg", "tox.ini"):
        path = REPO_ROOT / name
        assert not path.exists(), (
            f"{name} exists and coverage.py reads config from it. Either remove it or "
            "teach this test which coverage keys it is allowed to set — an unwatched "
            "coverage config can turn the gate permanently green."
        )


COVERAGE_GATE_DECISION_DOC: Final = REPO_ROOT / "docs" / "decisions" / "001-coverage-gate.md"

# Every bare `D-NN` citation ci/checks.sh makes, in a form that unambiguously
# means docs/decisions/001-coverage-gate.md — i.e. NOT prefixed `D-000/`, which
# is this repo's own disambiguation convention for citing a DIFFERENT decision
# doc's namespace (see that doc's own header, and docs/decisions/001-coverage-gate.md's
# follow-up item 1 for why the convention exists at all: this repo has a real,
# recorded history of D-nn citations pointing at the wrong entry). Scoped to
# ci/checks.sh specifically because every bare D-nn citation in that file is,
# as of this test's writing, about the coverage gate — verified by reading all
# of them, not assumed.
_CHECKS_SH_DNN_RE: Final = re.compile(r"(?<!D-000/)\bD-(\d+)\b")
_DECISION_DOC_HEADER_RE: Final = re.compile(r"^## D-(\d+)\b", re.MULTILINE)


def _read_decision_doc() -> str:
    return COVERAGE_GATE_DECISION_DOC.read_text(encoding="utf-8")


def test_every_d_nn_citation_in_checks_sh_points_at_a_real_decision() -> None:
    """A dangling `D-nn` reference — one that names no decision at all — fails here.

    WHAT THIS DOES NOT CATCH, stated plainly rather than left to be discovered
    the hard way: a citation that names the WRONG *real* decision is invisible
    to an existence check. This file shipped exactly that bug once — a comment
    cited ``D-11`` for the diagnosis-logic fix that
    ``docs/decisions/001-coverage-gate.md`` actually recorded as ``D-12``
    (``D-11`` is a different, real, earlier fix in the same doc) — and a
    mutation check proved this test does NOT catch a reversion to it, because
    ``D-11`` is a genuine heading. Verifying "is this citation the SEMANTICALLY
    correct one for this comment" would need understanding what the comment
    claims, not just whether the number exists, which is outside what a
    regex-based structural test can do. What this test DOES catch — a citation
    to a number with no heading at all, e.g. after a section is renumbered,
    split, deleted, or mistyped as a typo rather than a swap — is still a real,
    narrower gap than existed before it: previously nothing here was checked
    mechanically at all.
    """
    real_ids = {int(m.group(1)) for m in _DECISION_DOC_HEADER_RE.finditer(_read_decision_doc())}
    assert real_ids, (
        "found zero '## D-NN' headings in docs/decisions/001-coverage-gate.md — "
        "fix the heading regex before trusting this test's other assertion"
    )

    cited_ids = {int(m.group(1)) for m in _CHECKS_SH_DNN_RE.finditer(CHECKS_SRC)}
    assert cited_ids, (
        "found zero bare D-NN citations in ci/checks.sh — if the coverage-stage "
        "comments no longer cite decisions/001-coverage-gate.md at all, this test "
        "has nothing left to guard and should be reconsidered, not left green by "
        "accident"
    )

    dangling = sorted(cited_ids - real_ids)
    assert not dangling, (
        f"ci/checks.sh cites D-{dangling} but docs/decisions/001-coverage-gate.md has "
        f"no such heading (real IDs: D-{sorted(real_ids)}). Update the citation to the "
        "decision that actually covers this, or the heading if it was renumbered."
    )


# =============================================================================
# MAJOR 4 — a docker failure is not a version mismatch.
# =============================================================================


def test_container_probe_branches_on_exit_status() -> None:
    """``2>/dev/null`` on the version probe misdiagnosed every outage."""
    body = FUNCTIONS["require_pinned_image"]
    assert "2>/dev/null" not in body, (
        "require_pinned_image discards the container probe's stderr again; without "
        "it every infrastructure failure is indistinguishable from version drift"
    )
    assert "rp_rc" in body and 'if [ "${rp_rc}" -ne 0 ]' in body, (
        "require_pinned_image no longer branches on docker's exit status before "
        "asserting the version"
    )
    assert "docker_failure_report" in body, (
        "require_pinned_image no longer routes an infrastructure failure to a distinct diagnostic"
    )


@pytest.mark.parametrize(
    "cause",
    [
        "the Docker daemon is not reachable",
        "may not talk to the Docker socket",
        "the registry is rate-limiting this IP",
        "no network route to the registry",
        "SUPPLY-CHAIN event",
        "the registry refused this client",
        "unrecognised docker failure",
    ],
)
def test_docker_failure_report_names_each_failure_mode(cause: str) -> None:
    """Each distinguishable failure gets its own actionable message.

    ROUND 10 — the table moved out of ``docker_failure_report`` into
    ``docker_error_cause`` so the fail-fast daemon guard and the pinned-image
    probe share one diagnosis instead of the guard having none. Same seven
    causes, one home; ``test_both_docker_reporters_use_the_shared_cause_table``
    below is what keeps the two callers attached to it.
    """
    body = FUNCTIONS["docker_error_cause"]
    assert cause in body, f"docker_error_cause no longer distinguishes: {cause}"


def test_both_docker_reporters_use_the_shared_cause_table() -> None:
    """A second, weaker copy of the diagnosis is how round 10 happened.

    ``stage_gitleaks`` had a good message for a stopped daemon because
    ``require_pinned_image`` routed through ``docker_failure_report``;
    ``stage_unit``'s guard was a bare ``command -v`` with no diagnosis at all,
    and the operator got eight assertion failures about Fernet keys instead.
    Both reporters must keep sharing the one table, or the asymmetry returns.
    """
    for reporter in ("docker_failure_report", "docker_daemon_report"):
        assert "docker_error_cause" in FUNCTIONS[reporter], (
            f"{reporter} no longer routes through the shared failure-mode table; a "
            "second, divergent copy of the docker diagnosis is exactly the asymmetry "
            "round 10 removed"
        )


@pytest.mark.parametrize(
    "spelling",
    [
        # Docker Desktop on Windows, current client — measured on the authoring
        # box while the round-10 defect was live.
        "if the daemon is running",
        "failed to connect to the docker api",
        "npipe:",
        # Linux socket and the older Windows named-pipe spellings, which the
        # table already recognised and must keep recognising.
        "cannot connect to the docker daemon",
        "dial unix /var/run/docker.sock",
        "docker_engine",
    ],
)
def test_the_daemon_branch_recognises_every_measured_spelling(spelling: str) -> None:
    """One unmatched spelling turns the best diagnosis into the worst one.

    Docker Desktop's current Windows stderr matched none of the pre-round-10
    patterns, so a stopped daemon — the single most common and most trivially
    fixable cause in this table — fell through to ``unrecognised docker
    failure``, whose remediation is "read the raw stderr below".
    """
    body = FUNCTIONS["docker_error_cause"]
    assert f"*'{spelling}'*" in body, (
        f"docker_error_cause's daemon branch no longer matches {spelling!r}; the stderr "
        "carrying it would be diagnosed as an unrecognised failure"
    )


def test_docker_failure_report_does_not_blame_the_pin_file() -> None:
    """The whole point: an outage must not send the operator to edit pins."""
    body = FUNCTIONS["docker_failure_report"]
    assert "do not edit ci/versions.env" in body, (
        "docker_failure_report no longer states that an infrastructure failure is not a pin problem"
    )
    assert "do not edit ci/versions.env" in FUNCTIONS["docker_daemon_report"], (
        "the daemon-unreachable message no longer states that an unreachable daemon is "
        "not a pin problem — the operator who reads it is one stage away from the "
        "eight secrets-gate assertion failures that made this fix necessary"
    )


# =============================================================================
# ROUND 10 — `command -v docker` proves the CLIENT exists, not that the DAEMON
# answers. Reproduced live, not constructed: Docker Desktop stopped, the binary
# still on PATH, guard passes, pytest runs, 8 failures claiming the secrets
# gate let a planted Fernet key and a committed kubeconfig through.
#
# These are source-shape checks and therefore SECONDARY, in this file's own
# established sense: the authoritative proof is behavioural and lives in
# tests/unit/test_checks_sh_behaviour.py, which runs the real script against a
# `docker` that is on PATH and fails `info` — the exact real-world state — and
# asserts the stage stops before pytest starts.
# =============================================================================


def test_the_docker_guard_probes_the_daemon_not_only_the_binary() -> None:
    """``command -v docker`` succeeds with Docker Desktop stopped."""
    guard = _strip_comments(FUNCTIONS["docker_or_fail"])
    assert "docker_daemon_or_fail" in guard, (
        "docker_or_fail checks only that the docker binary exists again. That check "
        "passes with Docker Desktop stopped — the client is on PATH either way — so "
        "the stage runs and the operator gets failures about the gate instead of "
        "about the daemon"
    )

    probe = _strip_comments(FUNCTIONS["docker_daemon_or_fail"])
    assert "docker info" in probe, (
        "docker_daemon_or_fail no longer asks the daemon anything. Its whole purpose "
        "is one round trip to the API that `command -v` cannot make"
    )
    assert 'if [ "${dd_rc}" -ne 0 ]' in probe, (
        "docker_daemon_or_fail no longer branches on the probe's exit status"
    )
    assert "docker_daemon_report" in probe, (
        "docker_daemon_or_fail no longer routes a failed probe to the diagnostic"
    )


def test_the_daemon_probe_is_not_memoised() -> None:
    """The round-7 rule, applied to the newest pass/fail probe in this file.

    A "daemon already checked" flag would be the same defect class rounds 3-7
    closed for the pin checks: a stored answer standing in for the current one.
    The probe is cheap and the decision is a gate, so it re-runs on every call.
    The behavioural counterpart (``all`` probes three times, once per stage
    that needs Docker) is in test_checks_sh_behaviour.py; this is the cheap
    shape check beside it.
    """
    probe = _strip_comments(FUNCTIONS["docker_daemon_or_fail"])
    assert not re.search(r"^\s*return 0\s*$", probe, re.MULTILINE), (
        "docker_daemon_or_fail has an early `return 0`. The only sanctioned exits are "
        "falling off the end after a successful probe and `return 1` after a failed "
        "one; anything else is a skip condition, and a skip condition is a memo "
        "waiting for a name"
    )
    for memo in ("_done", "_checked", "_cached"):
        assert memo not in probe, (
            f"docker_daemon_or_fail now carries a {memo!r}-shaped flag. Caching a "
            "liveness answer across a pass/fail decision reintroduces the round-7 "
            "defect class under a new name"
        )


def test_the_daemon_guard_keeps_its_message_distinct_from_the_binary_guard() -> None:
    """Two states, two remediations: "install Docker" vs "start Docker Desktop".

    Conflating them is how the operator ends up installing a Docker that is
    already installed, or reading a message about a pinned image when the
    daemon is simply stopped.
    """
    binary_branch = FUNCTIONS["docker_or_fail"]
    daemon_message = FUNCTIONS["docker_daemon_report"]

    assert "docker is not on PATH" in binary_branch, (
        "the binary-missing message is gone from docker_or_fail"
    )
    assert "docker is not on PATH" not in daemon_message, (
        "the daemon-unreachable message claims docker is not on PATH, which is false "
        "in the state it reports on and sends the operator to reinstall a working CLI"
    )
    assert "Docker Desktop may not be running" in daemon_message, (
        "the daemon-unreachable message no longer names the most likely cause"
    )
    assert "docker info" in daemon_message, (
        "the daemon-unreachable message no longer names the command that reproduces it"
    )


def test_the_git_guard_stays_a_binary_presence_check() -> None:
    """ROUND 10, stated rather than skipped: git has no daemon.

    ``docker`` is a client: the binary on PATH and the daemon it talks to are
    two different things, and only the second can do any work — which is the
    whole defect above. Everything ci/checks.sh asks git to do (``init``,
    ``add``, ``ls-files``, ``rev-parse``, ``config``) is a local process and
    filesystem operation with no server component, so an executable ``git`` on
    PATH IS the capability and there is no "installed but not answering" state
    to probe for. This test pins that conclusion so a future reader does not
    re-derive it, or "fix" git_or_fail by symmetry with docker_or_fail.

    The genuinely non-trivial git preconditions — is this a repository, is its
    history complete enough to scan — are asserted separately and specifically
    by require_git_history_is_scannable, which is checked by its own tests
    elsewhere in this file.
    """
    guard = _strip_comments(FUNCTIONS["git_or_fail"])
    assert "command -v git" in guard, "git_or_fail no longer checks that git is on PATH at all"
    assert "daemon" not in guard.lower(), (
        "git_or_fail now talks about a daemon. git has none; if this is an attempt to "
        "mirror docker_or_fail's round-10 fix, the asymmetry is correct and deliberate "
        "— see this test's docstring"
    )
    assert "require_git_history_is_scannable" in FUNCTIONS["stage_gitleaks"], (
        "the stage that walks the commit graph no longer asserts that the history is "
        "scannable, which is the git precondition that is NOT answered by `command -v`"
    )


def test_the_version_extractor_tolerates_a_missing_v_prefix() -> None:
    """``1s/^v//p`` prints nothing unless the substitution matched.

    An upstream that dropped the ``v`` would therefore report "(image printed no
    parseable version)" — a pin-drift diagnostic telling the operator to
    re-resolve a digest, for a formatting change.
    """
    body = FUNCTIONS.get("gitleaks_reported_version") or ""
    inline = re.search(r"^gitleaks_reported_version\(\) \{(.*)\}$", CHECKS_SRC, re.MULTILINE)
    text = body or (inline.group(1) if inline else "")
    assert "1s/^v//p" not in text, (
        "the gitleaks version extractor requires the `v` prefix again; without it the "
        "sed prints nothing and a formatting change is reported as pin drift"
    )
    assert re.search(r"1s\^?/\^v\\\{0,1\\\}//p|1s/\^v\\\{0,1\\\}//p", text), (
        f"expected an optional-`v` extractor, found: {text!r}"
    )


def test_the_pin_drift_message_reconstructs_a_usable_repository() -> None:
    """``${ref%%:*}`` yields ``repo@sha256`` on a digest-only reference.

    It also yields the bare hostname for a registry with a port
    (``localhost:5000/x``). Either way the ``docker pull`` line the operator is
    told to run is garbage, in the one diagnostic that IS about editing pins.
    """
    body = FUNCTIONS["require_pinned_image"]
    assert "${rp_image%%:*}" not in body, (
        "require_pinned_image builds the remediation command with `%%:*` again"
    )
    assert "${rp_image%%@*}" in body and "${rp_ref%:*}" in body, (
        "the remediation command no longer strips the digest before the tag"
    )


def test_hooks_stage_requires_docker_and_asserts_the_image_it_runs() -> None:
    """``stage_hooks`` runs one ``language: docker_image`` hook at manual stage.

    It previously called neither ``docker_or_fail`` nor any image assertion, so
    a missing daemon surfaced as pre-commit's own error and the shellcheck
    container — the only lint on ``ci/checks.sh``, POSIX sh destined for dash on
    node A — was never version-checked at all.

    It must NOT assert the gitleaks image: that hook does not execute at
    ``--hook-stage manual``, and printing its version here implied an
    enforcement that was not happening.
    """
    body = FUNCTIONS["stage_hooks"]
    assert "docker_or_fail" in body, "stage_hooks does not check for docker"
    assert "require_shellcheck_image" in body, (
        "stage_hooks does not assert the shellcheck container's version"
    )
    assert "require_terraform_image" in body, (
        "stage_hooks does not assert the terraform container's version"
    )
    assert "require_gitleaks_image" not in body, (
        "stage_hooks asserts the gitleaks image again, but no gitleaks hook runs at "
        "--hook-stage manual. The printed version implies an enforcement that is not "
        "happening — see tests/unit/test_gitleaks_positive_control.py for where the "
        "hook is actually exercised."
    )


# =============================================================================
# MAJOR 3 (round 5) — the same defect class as the block above, one stage over.
# =============================================================================


def test_unit_stage_requires_docker_for_its_own_named_reason() -> None:
    """``stage_unit`` never asserted the Docker dependency README.md already claimed.

    tests/unit/test_gitleaks_positive_control.py's positive controls run the
    PINNED gitleaks container directly, to prove ci/gitleaks.toml — not
    gitleaks' embedded defaults — is what actually loads. Without a guard, a
    Docker-less machine did not fail here: that file's own ``_tool("docker")``
    helper falls back to ``pytest.skip()`` outside CI, so
    ``sh ci/checks.sh unit`` reported GREEN having run zero of them. The
    behavioural proof that this measurably fails fast (not a deep pytest skip)
    lives in ``test_checks_sh_behaviour.py``, which drives the real script with
    a genuinely docker-free ``PATH`` — a source grep alone cannot show WHEN in
    the stage's execution the check fires, only that the token is present.
    """
    body = FUNCTIONS["stage_unit"]
    assert "docker_or_fail" in body, "stage_unit does not check for docker"
    assert "test_gitleaks_positive_control.py" in body, (
        "stage_unit's docker_or_fail reason does not name what actually needs docker"
    )


_GIT_OR_FAIL_REASON_RE: Final = re.compile(r'git_or_fail\s+"([^"]+)"')


def test_unit_stage_requires_git_for_its_own_named_reason() -> None:
    """ROUND 6 / MAJOR 2: symmetric with the docker guard immediately above.

    5 of tests/unit/test_gitleaks_positive_control.py's 8 tests drive git
    directly (``git init``, ``git add -A``) via the same ``_tool()`` helper
    used for docker — ``_tool("git")``, directly or through the
    ``_scaffold``/``gate_root`` fixture — to build the synthetic repositories
    and staged indices those tests then scan. Without this guard, Docker
    present + Python present + git ABSENT from PATH passed this stage having
    run zero of those five positive controls, silently, the same fail-open
    shape MAJOR 3 (round 5) found for Docker. The behavioural proof that this
    fails fast lives in ``test_checks_sh_behaviour.py``, driven with a
    genuinely git-free ``PATH``.
    """
    body = FUNCTIONS["stage_unit"]
    match = _GIT_OR_FAIL_REASON_RE.search(body)
    assert match, "stage_unit does not call git_or_fail with a single literal-string reason"
    assert "test_gitleaks_positive_control.py" in match.group(1), (
        "stage_unit's git_or_fail reason does not name what actually needs git"
    )


_DOCKER_OR_FAIL_REASON_RE: Final = re.compile(r'docker_or_fail\s+"([^"]+)"')


# Every stage that guards on Docker/git. ONE list, consumed by all four tests
# below, and cross-checked against ci/checks.sh itself by the test immediately
# after it — so a stage that grows a `docker_or_fail` call cannot quietly escape
# the distinctness check by nobody remembering to extend four literal lists.
# (These were four separate hardcoded lists until the `coverage` stage was added
# and had to be pasted into all of them. Same failure shape as the hand-written
# parametrize in test_version_pins.py: the guard that does not extend itself is
# the guard that silently stops covering things.)
_OR_FAIL_STAGES: Final[tuple[str, ...]] = (
    "stage_gitleaks",
    "stage_hooks",
    "stage_unit",
    "stage_coverage",
)


def test_the_or_fail_stage_list_matches_the_script() -> None:
    """``_OR_FAIL_STAGES`` must name exactly the stages that really guard.

    Derived from ci/checks.sh rather than trusted, so adding a stage with a
    Docker or git dependency fails here until it is listed — at which point the
    distinctness tests below start covering it automatically.
    """
    expected = set(_OR_FAIL_STAGES)
    assert expected, "_OR_FAIL_STAGES is empty, so every assertion below is vacuous"

    for call in ("docker_or_fail", "git_or_fail"):
        actual = {
            name for name, body in FUNCTIONS.items() if name.startswith("stage_") and call in body
        }
        assert actual == expected, (
            f"stages calling {call} are {sorted(actual)}, but _OR_FAIL_STAGES says "
            f"{sorted(expected)}. Update the list so the distinctness tests cover the "
            "new stage, or remove the guard that should not be there."
        )


@pytest.mark.parametrize("stage_function", _OR_FAIL_STAGES)
def test_every_docker_or_fail_reason_is_a_literal_string(stage_function: str) -> None:
    """Guard the parser used by the distinctness test below."""
    assert _DOCKER_OR_FAIL_REASON_RE.search(FUNCTIONS[stage_function]), (
        f"{stage_function} calls docker_or_fail without a single literal-string reason "
        "argument the parser above can extract"
    )


def test_the_docker_or_fail_reasons_are_all_distinct() -> None:
    """Every guarding stage must explain ITS OWN need, not a copy of another's.

    Asserting the string ``docker_or_fail`` is present (as the sibling tests
    above and below do) is satisfied by a copy-pasted reason that names the
    wrong stage's dependency. This is the stronger claim: no two of the reasons
    collide, however many stages _OR_FAIL_STAGES grows to.
    """
    reasons = {
        stage: _DOCKER_OR_FAIL_REASON_RE.search(FUNCTIONS[stage]).group(1)  # type: ignore[union-attr]
        for stage in _OR_FAIL_STAGES
    }
    assert len(set(reasons.values())) == len(_OR_FAIL_STAGES), (
        f"two docker_or_fail reasons are identical: {reasons}"
    )


@pytest.mark.parametrize("stage_function", _OR_FAIL_STAGES)
def test_every_git_or_fail_reason_is_a_literal_string(stage_function: str) -> None:
    """ROUND 6 / MAJOR 2: the git analogue of the docker parser guard above."""
    assert _GIT_OR_FAIL_REASON_RE.search(FUNCTIONS[stage_function]), (
        f"{stage_function} calls git_or_fail without a single literal-string reason "
        "argument the parser above can extract"
    )


def test_the_git_or_fail_reasons_are_all_distinct() -> None:
    """ROUND 6 / MAJOR 2: the git analogue of the docker distinctness test above.

    ``git_or_fail`` used to take no reason at all (a single generic message
    shared by ``stage_gitleaks`` and ``stage_hooks``); round 6 gave it the
    same required-reason contract as ``docker_or_fail`` when ``stage_unit``
    gained its own call, so this is now checkable the same way.
    """
    reasons = {
        stage: _GIT_OR_FAIL_REASON_RE.search(FUNCTIONS[stage]).group(1)  # type: ignore[union-attr]
        for stage in _OR_FAIL_STAGES
    }
    assert len(set(reasons.values())) == len(_OR_FAIL_STAGES), (
        f"two git_or_fail reasons are identical: {reasons}"
    )


# Every stage label mapped to the test directories pytest would run for it, so
# the sweep below can look for real (non-stub) docker- or git-driving code
# near each stage rather than trusting that MAJOR 3's one finding, made by
# inspection, was the only instance.
_STAGE_TEST_DIRS: Final[dict[str, tuple[str, ...]]] = {
    "stage_unit": ("tests/unit",),
    "stage_contract": ("tests/contract",),
    "stage_smoke": ("tests/smoke",),
    # `coverage` runs ALL THREE suites in one pytest process, so it inherits
    # tests/unit's real docker and git dependencies wholesale. Listing it here is
    # not bookkeeping: without the entry this sweep simply would not look at the
    # stage, and it would pass vacuously while the stage reported a green
    # coverage number computed from a run in which eight container-dependent
    # positive controls had silently skipped.
    "stage_coverage": ("tests/unit", "tests/contract", "tests/smoke"),
}
# `_tool("docker")` / `_tool("git")` are tests/unit/test_gitleaks_positive_control.py's
# own helper for "resolve a real binary, skipping outside CI if absent".
# `shell_harness.py` is EXCLUDED below: it stubs `docker`/`git` on PATH so that
# ci/checks.sh can be driven without either being genuinely installed, which is
# the opposite of a real dependency.
_REAL_TOOL_MARKER_RES: Final[dict[str, re.Pattern[str]]] = {
    "docker": re.compile(r'_tool\(\s*"docker"\s*\)'),
    "git": re.compile(r'_tool\(\s*"git"\s*\)'),
}
_OR_FAIL_CALLS: Final[dict[str, str]] = {"docker": "docker_or_fail", "git": "git_or_fail"}


def test_no_stage_silently_needs_docker_or_git_without_asserting_it() -> None:
    """The systematic sweep MAJOR 3 asked for, generalised to git in round 6.

    Scans each stage's own test directory for real (non-stub) docker- or
    git-driving code and requires that stage to call ``docker_or_fail`` /
    ``git_or_fail`` respectively before pytest ever starts collecting it —
    otherwise a missing dependency surfaces as a deep, possibly-silent skip
    instead of a clear, stage-level failure.

    As of round 6: tests/unit drives both real docker and real git
    (tests/unit/test_gitleaks_positive_control.py — 5 of its 8 tests reach
    ``_tool("git")``, directly or through the ``_scaffold``/``gate_root``
    fixture) and stage_unit now guards both. tests/contract and tests/smoke
    are DECLARED-EMPTY — a lone ``.gitkeep`` in each, per ``pytest_suite``'s
    own handling of that state — so neither has anything to sweep yet; this
    re-evaluates automatically the moment either directory gains a module,
    rather than waiting for the next external review round to notice by hand.

    Round 5 left git deliberately unswept here, reasoning that git is this
    whole repository's unconditional, stage-independent baseline requirement
    in a way Docker — needed by exactly three of eight stages — is not, so a
    missing git was a plausibility argument rather than a code-level
    guarantee. Round 6 was a SPLIT VERDICT between spec-guardian (who
    reiterated that argument) and peer-reviewer (who traced the concrete
    failure: Docker present, Python present, git absent, 5 positive controls
    silently skipped, stage still green). The operator chose the stricter
    reading — add the guard, and sweep for it, for consistency with the
    standard this file already holds Docker to — so git is included here now,
    symmetrically, rather than carrying the round-5 exemption forward.

    CAVEAT (MINOR, round 6): this sweep only recognises the literal
    ``_tool("docker")`` / ``_tool("git")`` helper-call pattern. A hypothetical
    future test that drove either tool via ``subprocess`` or
    ``shutil.which`` directly, under a different name, would not be found by
    this scan. Currently inert either way: tests/contract and tests/smoke are
    placeholder-only, so there is nothing else to miss yet.
    """
    for stage, dirs in _STAGE_TEST_DIRS.items():
        drives_real: set[str] = set()
        for rel_dir in dirs:
            for path in sorted((REPO_ROOT / rel_dir).glob("*.py")):
                if path.name == "shell_harness.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for tool, marker in _REAL_TOOL_MARKER_RES.items():
                    if marker.search(text):
                        drives_real.add(tool)
        for tool in sorted(drives_real):
            guard = _OR_FAIL_CALLS[tool]
            assert guard in FUNCTIONS[stage], (
                f"{stage}'s own test directory drives a real {tool} invocation via "
                f'`_tool("{tool}")`, but {stage} never calls {guard} — a missing '
                f"{tool} will surface as a deep, possibly-silent pytest.skip() instead "
                "of a clear stage-level failure"
            )


def test_docker_is_always_invoked_through_the_msys_scoping_wrapper() -> None:
    """MSYS suppression was exported at module scope for a two-site problem.

    ``MSYS_NO_PATHCONV`` and ``MSYS2_ARG_CONV_EXCL='*'`` were exported once and
    inherited by ruff, mypy, pytest and pre-commit, to fix argument rewriting
    that was only ever measured on ``docker run -w``. A prefix assignment on the
    wrapper is the scope the measurement supports.
    """
    wrapper = FUNCTIONS["docker_run"]
    assert "MSYS_NO_PATHCONV=1" in wrapper and "MSYS2_ARG_CONV_EXCL='*'" in wrapper, (
        "docker_run no longer scopes the MSYS suppression to the docker invocation"
    )
    assert "export MSYS_NO_PATHCONV" not in CHECKS_DIRECTIVES, (
        "the MSYS variables are exported at module scope again, changing the "
        "environment of every stage to fix a docker-only problem"
    )

    elsewhere = {
        name: body
        for name, body in FUNCTIONS.items()
        if name != "docker_run" and re.search(r"\bdocker run\b", _strip_comments(body))
    }
    assert not elsewhere, (
        f"{sorted(elsewhere)} call `docker run` directly instead of the docker_run "
        "wrapper, so the MSYS argument suppression does not apply to them"
    )


def test_msys_comment_states_only_what_was_observed() -> None:
    """The comment claimed a general rewrite; only ``-w`` was demonstrated."""
    assert "only `-w` was observed" in CHECKS_SRC, (
        "the MSYS_NO_PATHCONV comment no longer scopes its claim to the argument "
        "that was actually measured"
    )


# =============================================================================
# MAJOR 8 / MAJOR 6 — shallow, partial and non-repositories.
# =============================================================================


def test_gitleaks_stage_refuses_incomplete_clones() -> None:
    """Both kinds pass vacuously, and only one of them is "shallow".

    ``git rev-parse --is-shallow-repository`` returns FALSE for
    ``git clone --filter=blob:none`` (measured, git 2.52.0), so the shallow guard
    alone left the blobless case wide open: 0 commits scanned, "no leaks found",
    exit 0. ``git rev-parse --is-partial-clone`` is NOT a real option — rev-parse
    echoes the unrecognised argument and exits 0, which would make the guard fire
    always — so the discriminator is the clone's own config.
    """
    body = FUNCTIONS["require_git_history_is_scannable"]
    assert "--is-shallow-repository" in body, "no shallow-clone guard in ci/checks.sh"
    assert "--unshallow" in body, "the shallow-clone failure does not name the fix"
    assert "partialclonefilter" in body and "promisor" in body, (
        "no partial-clone guard: `git clone --filter=blob:none` reports "
        "is-shallow-repository=false and then scans nothing while exiting 0"
    )
    assert "--is-partial-clone" not in body, (
        "ci/checks.sh uses `git rev-parse --is-partial-clone`, which git 2.52 does "
        "not implement: rev-parse echoes the argument back and exits 0, so the guard "
        "would fire on every repository"
    )
    assert "require_git_history_is_scannable" in FUNCTIONS["stage_gitleaks"], (
        "stage_gitleaks does not call the history precondition check"
    )


def test_no_history_branch_is_gated_on_being_a_repository() -> None:
    """NO-HISTORY conflated "no commits" with "not a git repo at all"."""
    assert "git rev-parse --git-dir" in FUNCTIONS["require_git_history_is_scannable"], (
        "ci/checks.sh no longer distinguishes 'not a git repository' from "
        "'a repository with no commits yet'"
    )
    assert "is not a git repository" in FUNCTIONS["require_git_history_is_scannable"], (
        "the not-a-repository case no longer has its own message"
    )


# =============================================================================
# MAJOR 5 / MAJOR 6 — hooks that report Passed having checked nothing.
# =============================================================================


def test_large_file_hook_enforces_all_files() -> None:
    """Without ``--enforce-all`` the hook inspects only files staged as added.

    ``stage_hooks`` never has anything staged: the index is empty before the
    first commit and CI runs on a clean checkout. The hook therefore checked
    ZERO files on every run and reported Passed, while
    ``.github/workflows/ci.yml`` cited it as what stops a ``--no-verify``
    bypass. That claim was false.
    """
    match = re.search(
        r"id:\s*check-added-large-files\s*\n\s*args:\s*(\[[^\]]*\])", PRE_COMMIT_DIRECTIVES
    )
    assert match, "check-added-large-files has no args block"
    args = match.group(1)
    assert "--enforce-all" in args, (
        "check-added-large-files lacks --enforce-all, so it checks only files staged "
        "as added — of which this hook never has any. It is permanently vacuous."
    )
    expected_kb = VERSIONS["MAX_ADDED_FILE_KB"]
    assert f"--maxkb={expected_kb}" in args, (
        f"check-added-large-files --maxkb must equal ci/versions.env "
        f"MAX_ADDED_FILE_KB={expected_kb}; found {args}"
    )


def _configured_hook_ids() -> list[str]:
    return re.findall(r"^\s*-\s*id:\s*(\S+)\s*$", PRE_COMMIT_DIRECTIVES, re.MULTILINE)


def _hooks_that_run(stage: str | None) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    """Hook ids pre-commit actually executes, by running it with no files.

    ``--files`` with an empty list makes every hook report "no files to check"
    and rewrite nothing, so this is safe to run from a unit test and takes under
    a second warm — but pre-commit still resolves and prints the full merged
    hook list for the requested stage, which is the thing being measured.
    """
    argv = [sys.executable, "-m", "pre_commit", "run", "--verbose"]
    if stage is not None:
        argv += ["--hook-stage", stage]
    argv += ["--files"]
    result = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=900,
        check=False,
    )
    return re.findall(r"^- hook id:\s*(\S+)\s*$", result.stdout, re.MULTILINE), result


def test_the_hooks_that_actually_run_in_ci_are_the_configured_set_minus_gitleaks() -> None:
    """MAJOR 4: ``--hook-stage manual`` filters the MERGED hook definition.

    The previous test grepped ``.pre-commit-config.yaml`` for
    ``stages: [pre-commit]``. That reads only the LOCAL half. pre-commit merges
    each hook's local declaration with the upstream repository's
    ``.pre-commit-hooks.yaml``, so a ``rev:`` bump in which upstream narrows a
    hook's ``stages`` drops it from CI entirely with the config-only test still
    green — the same class of defect, relocated upstream where nobody looks.

    So: run pre-commit, parse the ids that executed, and compare.
    """
    executed, result = _hooks_that_run("manual")
    assert executed, (
        "pre-commit produced no hook ids at --hook-stage manual. This test cannot "
        f"pass vacuously.\nexit {result.returncode}\n{result.stdout}\n{result.stderr}"
    )

    configured = set(_configured_hook_ids())
    assert configured, "no hook ids parsed out of .pre-commit-config.yaml"
    assert set(executed) == configured - {"gitleaks"}, (
        f"hooks executing at --hook-stage manual: {sorted(set(executed))}\n"
        f"configured minus gitleaks:              {sorted(configured - {'gitleaks'})}\n"
        "A hook in the config but not in the run is enforced NOWHERE in CI; a hook "
        "in the run but not in the config means this test's parser is wrong."
    )


def test_the_gitleaks_hook_is_present_at_commit_stage_and_only_excluded_by_stage() -> None:
    """Guard the guard above: absence must be caused by ``stages:``, not a typo.

    If the hook id were misspelled it would be missing from BOTH stages and the
    previous test would still pass, having quietly accepted a hook that does not
    exist.
    """
    executed, result = _hooks_that_run(None)
    assert executed, f"pre-commit produced no hook ids at the default stage\n{result.stderr}"
    assert "gitleaks" in executed, (
        "the gitleaks hook does not run at the pre-commit stage either, so it is "
        f"enforced nowhere at all. Hooks that ran: {executed}"
    )

    restricted = re.findall(
        r"-\s*id:\s*(\S+)(?:(?!\n\s*-\s*id:).)*?stages:\s*\[pre-commit\]",
        PRE_COMMIT_DIRECTIVES,
        re.DOTALL,
    )
    assert restricted == ["gitleaks"], (
        f"hooks restricted to `stages: [pre-commit]`: {restricted}. Only gitleaks may "
        "be, because ci/checks.sh runs the suite with --hook-stage manual and every "
        "other hook must still execute in CI."
    )
    assert "--hook-stage manual" in FUNCTIONS["stage_hooks"], (
        "stage_hooks no longer uses --hook-stage manual, so the staged-diff gitleaks "
        "hook is back to reporting Passed on an empty diff"
    )


def test_stage_hooks_builds_its_file_list_without_xargs() -> None:
    """``xargs -0`` is a GNU/BSD extension and node A's ``/bin/sh`` is dash.

    GNU ``xargs`` without ``-r`` also runs the command once on empty input,
    which handed pre-commit zero files and produced a full page of green
    "no files to check" — the vacuous pass this stage exists to prevent. The
    behavioural consequences are asserted in ``test_checks_sh_behaviour``; the
    portability one cannot be, because the authoring box has GNU xargs.
    """
    body = _strip_comments(FUNCTIONS["stage_hooks"])
    assert "xargs" not in body, (
        "stage_hooks uses xargs again. `-0` is not in POSIX, and without `-r` GNU "
        "xargs invokes the command even when its input is empty."
    )


def test_default_install_hook_types_matches_the_hooks_that_exist() -> None:
    """``commit-msg`` was installed with zero commit-msg-stage hooks defined."""
    match = re.search(
        r"^default_install_hook_types:\s*\[([^\]]*)\]", PRE_COMMIT_DIRECTIVES, re.MULTILINE
    )
    assert match, "default_install_hook_types is missing"
    declared = {item.strip() for item in match.group(1).split(",") if item.strip()}
    if "commit-msg" in declared:
        assert "stages: [commit-msg]" in PRE_COMMIT_DIRECTIVES, (
            "default_install_hook_types installs a commit-msg hook but no hook "
            "declares `stages: [commit-msg]`, so the installed hook does nothing"
        )


# =============================================================================
# MINOR 14 — the workflow must cover every stage ci/checks.sh defines.
# =============================================================================


def test_workflow_matrix_covers_every_stage_label() -> None:
    """Adding a stage to ``stage_all`` must not silently skip CI.

    Matched against the workflow with comments STRIPPED. Against raw source, a
    comment such as ``# reproduce with: sh ci/checks.sh newstage`` satisfied the
    coverage assertion with no job running the stage — the same false-negative
    the sibling assertions strip comments to avoid.
    """
    matrix_match = re.search(r"^\s*stage:\s*\[([^\]]*)\]", WORKFLOW_DIRECTIVES, re.MULTILINE)
    assert matrix_match, "no `stage:` matrix found in .github/workflows/ci.yml"
    matrix = {item.strip() for item in matrix_match.group(1).split(",") if item.strip()}

    direct = set(re.findall(r"sh ci/checks\.sh ([a-z][a-z0-9_-]*)", WORKFLOW_DIRECTIVES))

    covered = matrix | direct | {"all"}
    assert set(DISPATCH) == covered, (
        f"ci/checks.sh dispatches {sorted(DISPATCH)}; the workflow covers "
        f"{sorted(covered)} (matrix {sorted(matrix)}, direct {sorted(direct)}, plus "
        "`all`, which is the local-only aggregate). A stage in one and not the other "
        "either never runs in CI or fails the workflow with 'unknown stage'."
    )


def test_workflow_runs_the_documented_bootstrap_command() -> None:
    """CI installed four packages by name; README documents one command."""
    assert 'pip install -e ".[dev]"' in WORKFLOW_DIRECTIVES, (
        "the workflow no longer runs the bootstrap command README.md documents"
    )
    named_installs = re.findall(
        r'pip install\s+\\?\s*\n?\s*"(ruff|mypy|pytest|pre-commit)==', WORKFLOW_DIRECTIVES
    )
    assert not named_installs, (
        f"the workflow installs {sorted(set(named_installs))} by name again, "
        "diverging from the single documented Principle IV command path"
    )


def test_workflow_pins_pip_itself() -> None:
    """``pip install --upgrade pip`` is an unpinned upgrade to a moving target.

    It ran immediately before the step that installs every other pin, in a
    workflow whose own header argues that a mutable reference with repository
    access is unacceptable.
    """
    assert re.search(r"pip install --upgrade pip\b(?!==)", WORKFLOW_DIRECTIVES) is None, (
        "the workflow upgrades pip to an unpinned version again"
    )
    assert "PIP_VERSION" in VERSIONS, "ci/versions.env does not pin pip"
    assert 'pip install --upgrade "pip==${{ needs.versions.outputs.pip }}"' in WORKFLOW_SRC, (
        "the workflow does not install the pinned pip from ci/versions.env"
    )


def test_every_workflow_action_is_pinned_to_a_commit_sha() -> None:
    """A tag like ``@v4`` is mutable and this workflow has repository read access.

    Added with the caching steps: ``actions/cache`` was the first new action in
    the file since that rule was written down, and the easy way to add it is
    ``uses: actions/cache@v4``.
    """
    uses = re.findall(r"^\s*uses:\s*(\S+)[ \t]*(?:#.*)?$", WORKFLOW_DIRECTIVES, re.MULTILINE)
    assert uses, "no `uses:` steps parsed out of .github/workflows/ci.yml"
    unpinned = [ref for ref in uses if not re.search(r"@[0-9a-f]{40}$", ref)]
    assert not unpinned, (
        f"{unpinned} are not pinned to a 40-character commit SHA. A tag can be "
        "re-pointed at different code, which would then run with this workflow's "
        "repository access."
    )


def test_the_pre_commit_cache_is_keyed_on_the_hook_config() -> None:
    """A cold pre-commit cache clones three repos and builds three virtualenvs.

    Both the ``hooks`` job and the matrix (whose ``unit`` stage runs pre-commit
    to enumerate the merged hook set) need it. Keyed on the config hash so a
    ``rev:`` bump does not silently reuse the old hook environments.
    """
    caches = re.findall(
        r"path: ~/\.cache/pre-commit\n\s*key: (\S.*)$", WORKFLOW_DIRECTIVES, re.MULTILINE
    )
    assert len(caches) == 2, (
        f"expected the pre-commit cache in both the matrix and hooks jobs, found {caches}"
    )
    for key in caches:
        assert "hashFiles('.pre-commit-config.yaml')" in key, (
            f"pre-commit cache key {key!r} is not derived from the hook config, so a "
            "`rev:` bump would reuse stale hook environments"
        )


def test_every_workflow_job_has_a_timeout() -> None:
    """The default is 360 minutes; a hung ``docker pull`` sat there for six hours."""
    jobs_block = WORKFLOW_DIRECTIVES.split("\njobs:\n", 1)[1]
    jobs = re.findall(r"^  ([a-z][a-z0-9-]*):$", jobs_block, re.MULTILINE)
    assert jobs, "no jobs parsed out of .github/workflows/ci.yml"
    timeouts = re.findall(r"^    timeout-minutes: (\d+)$", jobs_block, re.MULTILINE)
    assert len(timeouts) == len(jobs), (
        f"{len(jobs)} jobs ({jobs}) but {len(timeouts)} timeout-minutes keys. Every job "
        "needs one, or a hung step burns the 360-minute default."
    )


def test_the_gitleaks_job_installs_no_python() -> None:
    """The secrets gate must not be able to fail for a PyPI reason.

    ``ci/checks.sh gitleaks`` asserts its own container and needs Docker and git
    only. Installing the toolchain there would reintroduce exactly the coupling
    the per-stage preflight removed, one layer up.
    """
    job = WORKFLOW_DIRECTIVES.split("\n  gitleaks:\n", 1)[1].split("\n  hooks:", 1)[0]
    assert "setup-python" not in job, (
        "the gitleaks job installs Python again. A yanked build backend or a PyPI "
        "outage would then redden a job named `gitleaks`, which is how an operator "
        "learns to skim past a red secrets gate."
    )
    assert "pip install" not in job
    assert "sh ci/checks.sh gitleaks" in job


def test_workflow_has_no_gate_disabling_constructs() -> None:
    """A gate that cannot fail the build is not a gate (Constitution V)."""
    for forbidden in ("continue-on-error", "|| true", "if: always()"):
        assert forbidden not in WORKFLOW_DIRECTIVES, (
            f".github/workflows/ci.yml contains {forbidden!r}"
        )


# =============================================================================
# MINOR 10 (round 3) — `set +e` must be SCOPED, not merely balanced.
# =============================================================================

FORBIDDEN_SUPPRESSORS: Final[tuple[str, ...]] = ("|| true", "|| :", "; true", "; :", "then :")

# How many lines a disarmed window may span. Both current uses need three: the
# command, `rc=$?`, and `set -e`.
ERREXIT_WINDOW: Final = 4


def test_checks_sh_has_no_gate_disabling_constructs() -> None:
    """Counting ``set +e`` against ``set -e`` is a balance check, not a scope check.

    ``set +e`` at the top of a stage and ``set -e`` at the bottom balances
    perfectly while disarming the entire stage. The old assertion also ignored
    ``|| :``, ``; true`` and ``if ! cmd; then :; fi``, each of which turns a gate
    into a no-op just as effectively as ``|| true``.
    """
    for forbidden in FORBIDDEN_SUPPRESSORS:
        assert forbidden not in CHECKS_DIRECTIVES, (
            f"ci/checks.sh contains {forbidden!r}, which suppresses a non-zero exit"
        )

    assert "set +e" not in TOP_LEVEL, (
        "ci/checks.sh disarms errexit outside any function, where nothing re-arms it"
    )

    for name, raw_body in FUNCTIONS.items():
        lines = _strip_comments(raw_body).splitlines()
        disarmed_at: int | None = None
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "set +e":
                assert disarmed_at is None, f"{name}: nested `set +e` at line {index}"
                disarmed_at = index
                window = lines[index + 1 : index + 1 + ERREXIT_WINDOW]
                assert any(re.search(r"=\$\?\s*$", entry) for entry in window), (
                    f"{name}: `set +e` is not immediately followed by a `rc=$?` capture. "
                    "The only sanctioned reason to disarm errexit is to record an exit "
                    "status that is then re-raised."
                )
                assert any(entry.strip() == "set -e" for entry in window), (
                    f"{name}: errexit stays off for more than {ERREXIT_WINDOW} lines after "
                    f"line {index}. A wide disarmed window is a disabled gate."
                )
            elif stripped == "set -e":
                assert disarmed_at is not None, (
                    f"{name}: `set -e` at line {index} with no matching `set +e`"
                )
                disarmed_at = None
        assert disarmed_at is None, (
            f"{name}: returns with errexit still disabled (opened at line {disarmed_at})"
        )


@pytest.mark.parametrize("function_name", ["pytest_suite", "require_pinned_image"])
def test_each_captured_exit_status_is_actually_tested(function_name: str) -> None:
    """Capturing a status and not branching on it is the same as ``|| true``."""
    body = _strip_comments(FUNCTIONS[function_name])
    captures = re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=\$\?\s*$", body, re.MULTILINE)
    assert captures, f"{function_name} disarms errexit but captures no exit status"
    for variable in captures:
        assert re.search(rf'\[ "\$\{{{variable}\}}" -(ne|eq|gt) ', body), (
            f"{function_name} captures {variable}=$? and never tests it"
        )


# =============================================================================
# MINOR 18 — ci/ must never be swallowed by .gitignore.
# =============================================================================


def test_no_file_under_ci_is_swept_up_by_the_env_glob() -> None:
    """``*.env`` ignores every ``ci/*.env``; only versions.env is negated.

    A future ``ci/anything.env`` would be invisible to git AND — because
    ci/checks.sh derives the working-tree scan's exclusions from
    ``git ls-files --others --ignored`` — dropped from the gitleaks walk at the
    same time. Ignored and unscanned is the worst possible pair for a file in a
    public repository.
    """
    env_files = {
        path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "ci").rglob("*.env")
    }
    assert env_files - {"ci/versions.env"} == set(), (
        f"{sorted(env_files - {'ci/versions.env'})} match .gitignore's `*.env` and are "
        "not negated: git will not track them and ci/checks.sh will exclude them from "
        "the gitleaks working-tree walk. Add an anchored `!` line for each, or move "
        "them out of ci/."
    )


def test_versions_env_negation_is_anchored() -> None:
    """``!versions.env`` un-ignores that name anywhere in the tree."""
    lines = {line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()}
    assert "!/ci/versions.env" in lines, (
        ".gitignore must negate the pin file by its exact path (`!/ci/versions.env`), "
        "not by bare name — `!versions.env` un-ignores any versions.env anywhere"
    )
    assert "!versions.env" not in lines, (
        ".gitignore carries the unanchored `!versions.env` negation again"
    )
