#!/usr/bin/env sh
# =============================================================================
# Orbital-Drift CI stage runner — the canonical definition of every gate.
#
# WHERE THINGS LIVE (plan.md puts "pipeline defs, gitleaks config" in ci/, and
# the remote is GitHub — this is how the two reconcile):
#
#     ci/                          the gates themselves. Runner-agnostic.
#       versions.env               tool pins
#       checks.sh                  <- you are here; one function per stage
#       gitleaks.toml              secret-scan ruleset
#     .github/workflows/ci.yml     a thin caller. It sets up a runner and
#                                  invokes `ci/checks.sh <stage>`. It contains
#                                  no gate logic of its own.
#
# Consequence: `sh ci/checks.sh all` reproduces CI exactly on a laptop or on
# node A. If you change a gate, change it here — never in the workflow. That
# claim is load-bearing, so anything CI runs runs here too: the gitleaks stage
# performs BOTH the working-tree scan and the full-history scan, and the hooks
# stage runs the pre-commit config that would otherwise be enforced only on
# machines where somebody remembered `pre-commit install`.
#
# NON-NEGOTIABLE: no `|| true`, no `set +e` around a gate, no `continue-on-error`
# in the caller. A gate that cannot fail is not a gate (Constitution V, FR-011).
# (`set +e` appears twice below, both times to CAPTURE a diagnostic before
# re-raising the failure. Neither swallows a non-zero result, and
# tests/unit/test_ci_contract.py enforces that scope per function rather than
# merely counting the directives.)
#
# BOOTSTRAP: Python ${PYTHON_VERSION} + `python -m pip install -e ".[dev]"`.
# See README.md. That is also the exact command .github/workflows/ci.yml runs,
# so a broken build backend fails in CI rather than first on node A.
#
# WHAT THE PREFLIGHT ACTUALLY GUARANTEES — read this before quoting it.
# It asserts the interpreter's major.minor and the exact versions of the four
# PINNED DISTRIBUTIONS a stage executes (ruff, mypy, pytest, pre-commit). It
# says NOTHING about their transitive dependencies: `pip install -e ".[dev]"`
# also pulls identify, nodeenv, virtualenv, cfgv, platformdirs, filelock,
# distlib, pyyaml, mypy_extensions and typing_extensions, none of which is
# pinned anywhere in this repository. "The toolchain is byte-for-byte what
# ci/versions.env claims" would therefore be false; what is true is that the
# version a stage header PRINTS is the version that stage RUNS.
#
# PER-STAGE SCOPE. Each stage asserts only the pins it uses (see
# stage_python_pins). The secrets gate deliberately asserts NONE of them: a
# yanked hatchling, a PyPI outage or a stray mypy on PATH must not be able to
# redden a job named `gitleaks`, and `sh ci/checks.sh gitleaks` has to work on a
# fresh clone that has Docker and git and no Python at all. That is the whole
# point of the stage with the fewest prerequisites in the repository.
#
# SIDE EFFECTS: the `hooks` stage runs pre-commit, and several of those hooks
# REWRITE files (ruff --fix, end-of-file-fixer, trailing-whitespace,
# mixed-line-ending). A failing `all` can therefore leave the tree modified and
# pass on a second run. See README.md, "Running the gates".
#
# Usage:  sh ci/checks.sh <stage>          (an unrecognised value prints the
#         authoritative list, generated from STAGE_LABELS below rather than
#         hand-kept here — a hand-kept copy has already drifted from the real
#         dispatch case once)
#         PYTHON=/path/to/python3.12 sh ci/checks.sh all
#         DEBUG=1 sh ci/checks.sh gitleaks      # dump the generated overlay
# =============================================================================

set -eu

# =============================================================================
# SCRIPT_DIR, resolved with NO external command.
#
# This line used to be:
#
#     SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
#
# `dirname` is an external binary, and this is the FIRST thing the script does:
# before ci/versions.env is sourced, before a single diagnostic function is
# defined, before any guard can speak. On a PATH that does not provide it, the
# operator got
#
#     ci/checks.sh: 63: dirname: not found
#     ci/checks.sh: 70: .: cannot open .../versions.env: No such file
#
# — a coreutils error plus a phantom missing file — instead of this script's own
# message about whatever was actually wrong. (`cd -- ""` is "stay where you
# are", not an error, so SCRIPT_DIR silently became the caller's cwd and the
# second line is a consequence of the first, not a separate fault.)
#
# Measured on the first real GitHub Actions run of this workflow: three tests in
# tests/unit/test_checks_sh_behaviour.py build a deliberately reduced PATH to
# prove the docker/git guards fire, and on ubuntu-24.04 — where /usr/bin
# provides the tool under test AND every coreutil — the script died here rather
# than at the guard being measured. Those tests had a bug of their own and are
# fixed too, but a top-of-file dependency on an external command is a defect in
# THIS file independently of them: the moment diagnostics matter most (a
# hostile, minimal or misordered PATH) is exactly the moment they disappeared.
#
# POSIX parameter expansion replaces it, including the two cases a bare
# `${0%/*}` gets wrong:
#
#   * `$0` containing no slash at all — the script was found via a PATH lookup,
#     so `${0%/*}` yields `$0` UNCHANGED, i.e. the script rather than its
#     directory. Correct answer: `.`, which is what dirname returns.
#   * `$0` = `/checks.sh` — `${0%/*}` yields the empty string, and `cd -- ""`
#     means "stay here". Correct answer: `/`.
#
# Trailing separators are stripped first, matching dirname's own behaviour
# (`dirname a/b/` is `a`), and the `?*` prefix leaves a lone separator alone so
# the root case still lands on the `[ -n ... ]` fallback below.
#
# BOTH SEPARATORS, and this is replicated MEASURED behaviour rather than
# invented behaviour. On the Windows/Git-Bash authoring box a caller can hand
# this script a native `$0` — `sh ci\checks.sh unit` from PowerShell, or any
# Python `subprocess.run([sh, str(path), ...])`, which is what
# tests/unit/shell_harness.py's sibling call sites do. Measured under MSYS:
#
#     dirname -- 'E:\...\ci\checks.sh'   ->  E:\...\ci        (rc 0)
#     CDPATH='' cd -- 'E:\...\ci' && pwd ->  /e/.../ci
#
# i.e. the `dirname` this replaces treated `\` as a separator, and `cd` accepts
# the result. Matching only `/` would therefore have REGRESSED that invocation
# into precisely the "no separator found -> `.` -> versions.env not found"
# failure this block exists to abolish. `*[/\\]*` is POSIX pattern-matching
# notation, verified byte-identical under dash and under bash-as-sh, including
# the mixed-separator case (`a/b\c/d.sh` -> `a/b\c`: the LAST separator of
# either kind wins, which is what MSYS's dirname measurably does too). The cost
# on a real POSIX system is that a `\` inside a path COMPONENT — legal, and
# pathological — would be read as a separator; `cd` then fails, `set -e` aborts
# the assignment below, and nothing proceeds on a wrong SCRIPT_DIR. GNU dirname
# would have returned `.` there; neither answer is usable, and this one fails
# loudly rather than sourcing versions.env from somewhere unrelated.
#
# The `CDPATH='' cd -- ... && pwd` normalisation stays exactly as it was, and
# should: it is what turns a relative `ci` into an absolute path, and the empty
# CDPATH is what stops an operator's own CDPATH from silently redirecting that
# cd. `cd` and `pwd` are shell builtins, so neither needs PATH.
# =============================================================================
self_path=$0
while :; do
  case "${self_path}" in
    ?*[/\\]) self_path=${self_path%[/\\]} ;;
    *) break ;;
  esac
done
case "${self_path}" in
  *[/\\]*) self_dir=${self_path%[/\\]*} ;;
  *) self_dir=. ;;
esac
[ -n "${self_dir}" ] || self_dir=/

SCRIPT_DIR=$(CDPATH='' cd -- "${self_dir}" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)
cd "${REPO_ROOT}"

VERSIONS_ENV="${SCRIPT_DIR}/versions.env"

# shellcheck source=ci/versions.env
. "${VERSIONS_ENV}"

PYTHON="${PYTHON:-python}"

# Every stage label the dispatch `case` at the bottom accepts. Declared once so
# the pin-coverage self-check below can iterate them without re-parsing the
# case, and so tests/unit/test_ci_contract.py can assert this list, the dispatch
# and .github/workflows/ci.yml all describe the same set of stages.
STAGE_LABELS='lint typecheck unit contract smoke coverage gitleaks hooks dead audit specs traceability projections governance all'

log() { printf '\n\033[1m>>> %s\033[0m\n' "$*"; }

# Read one pin straight out of ci/versions.env by key. Used so the preflight can
# look a version up from a DERIVED tool name without `eval`.
pin_value() {
  sed -n "s/^$1=//p" "${VERSIONS_ENV}" | tail -n 1
}

# =============================================================================
# PREFLIGHT — the announced version must be the version that runs.
#
# Every stage header prints a pin from ci/versions.env. Without this block those
# headers are decoration: the script would print "ruff 0.16.2" and then run
# whatever `python -m ruff` happens to resolve to. Measured on the authoring box
# before this existed: announced ruff 0.16.2 / ran 0.15.20, announced mypy 2.3.0
# / ran 2.1.0, announced pytest 9.1.1 / ran 8.4.2, pyproject requires Python
# >=3.12,<3.13 / ran 3.11.9. Only pytest was caught at all, incidentally, by
# pyproject's `minversion` — three stages late, and with a message
# ("'minversion' requires pytest-9.0, actual pytest-8.4.2") that sends the
# operator to `pip install -U pytest` and leaves the other three wrong.
#
# So: fail CLOSED, before any gate runs, naming tool / expected / found / fix.
#
# SCOPED PER STAGE. An earlier revision asserted the whole toolchain for every
# stage, which made the Constitution VII secrets gate hostage to tooling it does
# not execute: a yanked build backend, a PyPI blip or a stray `mypy 2.3.1` on
# PATH reddened a job named `gitleaks` with a message about mypy, and
# `sh ci/checks.sh gitleaks` — the "check before I push" path — could not run at
# all on a machine with Docker and git but no Python. Each stage now asserts
# exactly the pins it runs; stage_python_pins is the table, and the coverage
# self-check below refuses to let a pin fall out of every stage unnoticed.
#
# There is NO supported way to opt out.
#
# ROUNDS 3-6 — a converging arms race that did not converge. Round 3 added a
# memoised "already checked" flag so `sh ci/checks.sh all` asserted each pin
# once per run instead of once per stage, and named the flag
# `ORBITAL_DRIFT_PREFLIGHT_DONE`. Rounds 4 through 6 each found ONE MORE
# spelling of "set that flag from the environment without going near the
# literal name a source-level grep was watching for" and added ONE MORE
# detector for it: a name-agnostic `$VAR`/`${VAR}` reference scan, then a
# bare-identifier POSIX-arithmetic-expansion extension of that same scan
# (`$((OD_FAST))` reads `OD_FAST` with no leading `$` anywhere in the source),
# then an outright ban on `eval` (which can construct a reference whose name is
# nowhere a literal token), then an outright ban on `env` / `printenv` / bare
# `export` / bare `set` (each dumps every variable as `NAME=value` text for a
# runtime comparison to key off of, invisible to all three prior scans). Round
# 6's review then found TWO reviewers independently constructing a live bypass
# that defeats all FOUR of those detectors AT ONCE — piping `/proc/self/environ`
# through `tr '\0' '\n'` BEFORE the value crosses a `$()` boundary, which is the
# exact idiom this file already ships and trusts elsewhere (see
# nul_records_survive_newline_translation() below) — and a further pass in the
# same round found two more independently-sufficient mechanisms beyond that:
# quote-splitting (`ev''al`, `s''et` parse as the literal banned command after
# quote removal but never appear as a contiguous banned substring in source)
# and POSIX awk's ENVIRON array (awk is already an unconditional dependency of
# this script via tool_version()). "Enumerate every POSIX/shell mechanism that
# can read an environment variable" is an open-ended set, not a closable list;
# four rounds of finding one more, each falsifying the previous round's
# closure claim, is the proof of that, not a hypothesis about it.
#
# ROUND 7 — the structural fix, in place of a sixth detector. There is no
# memoised skip-state anywhere in this file any more: no `python_preflight_done`,
# no `pins_coverage_checked`, no per-tool `checked_tools`. require_python_
# interpreter() and require_pinned_tool() below re-run their REAL comparison —
# probe the installed version, compare it byte-for-byte to ci/versions.env —
# unconditionally, on every single call, every stage, every run. This does not
# make "can a bypass read an environment variable?" closed; that remains
# unclosable, as rounds 3-6 demonstrated. It makes the question IRRELEVANT:
# there is no boolean gate left anywhere for a successfully-read variable to
# flip, so it no longer matters how cleverly it was read. The three token-ban
# tests rounds 4-6 added (the `$VAR`/arithmetic scan, the `eval` ban, the
# env/printenv/export/set ban) stay in the suite as defense-in-depth — a
# bypass attempt that also happens to use one of those mechanisms is still
# caught twice — but they are no longer the PRIMARY defense; see
# tests/unit/test_ci_contract.py's `test_no_memoized_flag_gates_pin_verification`
# and tests/unit/test_checks_sh_behaviour.py's re-execution-count test for that.
#
# What IS still memoised below — `python_logged` / `logged_tools` — gates LOG
# OUTPUT ONLY, never verification: once `sh ci/checks.sh all` has printed
# "ruff 0.16.2" for the lint stage, printing the identical line again when a
# later stage also needs ruff is noise, not information. Every read of those
# two variables is inside a `printf` guard that runs AFTER the real comparison
# has already executed and passed; neither ever gates a `return` that would
# skip a comparison. If that boundary is ever blurred — a `return 0` added
# before the comparison, guarded by either of these — the class this round
# closes reopens under a different name. Deliberately NOT exported, same as
# their predecessors: memoisation (log-only now) is per-process.
# =============================================================================

# LOG-ONLY memos — see the paragraph immediately above. Initialised
# unconditionally for the same reason the removed verification memos were:
# even though nothing downstream lets either one skip a comparison, a
# same-named environment variable pre-seeding them would still be a silent
# influence on OUTPUT that this file's own standard (no unexplained behaviour
# change from the environment) does not want to carry, however low the stakes.
python_logged=0
logged_tools=''

BOOTSTRAP_CMD='python -m pip install -e ".[dev]"'

# Keys in ci/versions.env of the form <NAME>_VERSION that are real pins but are
# NOT probed as `python -m <name>`, each with the reason. The coverage check
# iterates the pins it finds in versions.env; a pin that is neither claimed by a
# stage nor listed here is an internal error, so adding a pin and forgetting to
# enforce it cannot pass silently.
#
# (Each name below is prefixed to keep it off the start of the comment: with -x,
# a comment line beginning with the word that names this repo's shell linter is
# parsed as a linter directive and fails with SC1072/SC1073. Same trap as the
# one documented in ci/versions.env.)
#
#   pin python      has its own diagnostic (fail_python) — `pip install
#                   python==` would be worse than useless advice.
#   pin hatchling   PEP 517 build backend; resolved in an isolated build env by
#                   pip and not importable from the target interpreter after.
#   pin pip         bootstrap-level: it is what INSTALLS the pinned toolchain,
#                   so it is upgraded by .github/workflows/ci.yml before that
#                   toolchain exists. Pinned there (Constitution IV) rather than
#                   asserted here, because refusing to run a gate over an
#                   operator's local pip patch version would be a prerequisite
#                   out of all proportion to the risk.
#   pin gitleaks    runs as a pinned container; asserted by
#                   require_pinned_image() from stage_gitleaks.
#   pin shellcheck  same, asserted from stage_hooks.
#   pin terraform   same: runs as a pinned container, asserted from
#                   stage_hooks by require_terraform_image — never a Python
#                   distribution, so tool_version() has no probe for it.
PREFLIGHT_EXEMPT_PINS='python hatchling pip gitleaks shellcheck terraform'

# Which pinned Python distributions each stage actually EXECUTES. This is the
# whole of the per-stage scoping rule; nothing else decides what a stage asserts.
#
# `gitleaks` is deliberately empty. It runs a container and git, both of which it
# asserts itself (require_gitleaks_image, git_or_fail,
# require_git_history_is_scannable). It runs no Python.
# THREE THINGS IN THIS FILE ARE NOW CALLED "coverage" AND THEY ARE UNRELATED:
#   require_pin_coverage()  — "is every pin claimed by some stage?"
#   the `coverage` STAGE    — the FR-011a test-coverage gate
#   the `coverage` TOOL     — the pinned distribution that computes the number
# The arm below claims the tool for the stage. Mechanically unambiguous; named
# here because a reader meeting the third one cold will otherwise assume a typo.
stage_python_pins() {
  case "$1" in
    lint)                printf 'ruff\n' ;;
    typecheck)           printf 'mypy\n' ;;
    unit|contract|smoke) printf 'pytest\n' ;;
    coverage)            printf 'pytest\npytest-cov\ncoverage\n' ;;
    hooks)               printf 'pre-commit\n' ;;
    gitleaks)            ;;
    dead)                printf 'vulture\n' ;;
    audit)               printf 'pip-audit\n' ;;
    specs)               ;;
    traceability)        printf 'pytest\n' ;;
    projections)         ;;
    governance)          printf 'pytest\n' ;;
    # `all` must list the UNION of every arm above. Not cosmetic: preflight()
    # prints its banner when a stage needs a tool not yet logged, so omitting
    # the two coverage pins here makes `all` print a second banner when
    # stage_coverage's preflight meets them for the first time — which
    # test_all_run_prints_the_preflight_banner_once_not_once_per_stage catches.
    all)                 printf 'ruff\nmypy\npytest\npytest-cov\ncoverage\npre-commit\nvulture\npip-audit\n' ;;
    *)
      printf 'internal error: no pin set declared for stage %s\n' "$1" >&2
      return 1
      ;;
  esac
}

fail_pin() {
  fp_tool="$1"
  fp_expected="$2"
  fp_found="$3"
  {
    printf '\n'
    printf 'FAIL: pinned-tool mismatch. Refusing to run a gate that would announce a\n'
    printf '      version it is not executing (Constitution IV).\n\n'
    printf '        tool:         %s\n' "${fp_tool}"
    printf '        expected:     %s        <- ci/versions.env\n' "${fp_expected}"
    printf '        found:        %s\n' "${fp_found}"
    printf '        interpreter:  %s\n' "${PYTHON_RESOLVED:-${PYTHON}}"
    printf '\n'
    printf '        fix (all pins at once, from the repo root):\n'
    printf '            %s\n' "${BOOTSTRAP_CMD}"
    printf '        fix (this tool only):\n'
    printf '            "%s" -m pip install "%s==%s"\n' "${PYTHON}" "${fp_tool}" "${fp_expected}"
    printf '\n'
    printf '        If %s is simply the wrong interpreter, point PYTHON at the right one:\n' "${PYTHON}"
    printf '            PYTHON=/path/to/python%s sh ci/checks.sh <stage>\n' "${PYTHON_VERSION}"
    printf '\n'
    printf '        This stage runs %s. The secrets gate does not:\n' "${fp_tool}"
    printf '            sh ci/checks.sh gitleaks    needs only Docker and git.\n'
    printf '\n'
  } >&2
  return 1
}

# The interpreter is not a pip-installable pin, so it gets its own diagnostic:
# telling an operator to `pip install python==3.12` would be worse than useless.
fail_python() {
  fp_found="$1"
  {
    printf '\n'
    printf 'FAIL: wrong Python interpreter. Refusing to run the gates on it.\n\n'
    printf '        expected:     %s.x      <- ci/versions.env PYTHON_VERSION\n' "${PYTHON_VERSION}"
    printf '                                     (pyproject requires-python agrees)\n'
    printf '        found:        %s\n' "${fp_found}"
    printf '        interpreter:  %s\n\n' "${PYTHON_RESOLVED:-${PYTHON}}"
    printf '        Point PYTHON at a %s interpreter — do not reorder your PATH:\n' "${PYTHON_VERSION}"
    printf '            PYTHON=/path/to/python%s sh ci/checks.sh <stage>\n\n' "${PYTHON_VERSION}"
    printf '        Then install the pinned toolchain into THAT interpreter:\n'
    printf '            /path/to/python%s -m pip install -e ".[dev]"\n\n' "${PYTHON_VERSION}"
    printf '        See README.md, "Bootstrap".\n\n'
  } >&2
  return 1
}

# Version of the code that will actually execute. Every stage invokes its tool
# as `${PYTHON} -m <tool>`, so the probe must go through the same interpreter.
#
# pytest is probed by import, not by `pytest --version`: pytest loads
# pyproject.toml first and `minversion` aborts it before it prints anything,
# so `--version` returns an error string exactly when we most need a number.
#
# Each branch ends in a pipeline so a missing tool yields an empty string and
# exit 0 rather than tripping `set -e` before the diagnostic can be printed.
# The `*)` branch is reachable only if a stage claims a pin that has no probe —
# see the coverage self-check in require_pin_coverage().
tool_version() {
  case "$1" in
    ruff)       "${PYTHON}" -m ruff --version 2>/dev/null | awk 'NR==1{print $2}' | tr -d '\r' ;;
    mypy)       "${PYTHON}" -m mypy --version 2>/dev/null | awk 'NR==1{print $2}' | tr -d '\r' ;;
    pytest)     "${PYTHON}" -c 'import pytest;print(pytest.__version__)' 2>/dev/null | tr -d '\r' ;;
    # pytest-cov and coverage are probed through importlib.metadata rather than a
    # module attribute, for two reasons. It is the canonical way to ask what
    # DISTRIBUTION is installed (a plugin need not expose __version__ at all),
    # and — load-bearing for the test harness — the resulting argv contains no
    # `import pytest` substring, which tests/unit/shell_harness.py's stub matches
    # as a glob. A probe written `import pytest_cov` would be answered with
    # pytest's version by that stub, and the pin mismatch would name a tool that
    # is perfectly fine.
    pytest-cov) "${PYTHON}" -c 'import importlib.metadata as m;print(m.version("pytest-cov"))' 2>/dev/null | tr -d '\r' ;;
    coverage)   "${PYTHON}" -c 'import importlib.metadata as m;print(m.version("coverage"))' 2>/dev/null | tr -d '\r' ;;
    pre-commit) "${PYTHON}" -m pre_commit --version 2>/dev/null | awk 'NR==1{print $NF}' | tr -d '\r' ;;
    vulture)    "${PYTHON}" -m vulture --version 2>/dev/null | awk 'NR==1{print $2}' | tr -d '\r' ;;
    pip-audit)  "${PYTHON}" -m pip_audit --version 2>/dev/null | awk 'NR==1{print $2}' | tr -d '\r' ;;
    *)
      printf 'internal error: no version probe defined for %s\n' "$1" >&2
      return 1
      ;;
  esac
}

require_tool() {
  rt_tool="$1"
  rt_expected="$2"
  rt_found=$(tool_version "${rt_tool}")
  if [ -z "${rt_found}" ]; then
    fail_pin "${rt_tool}" "${rt_expected}" "(not installed, or not importable by this interpreter)"
    return 1
  fi
  if [ "${rt_found}" != "${rt_expected}" ]; then
    fail_pin "${rt_tool}" "${rt_expected}" "${rt_found}"
    return 1
  fi

  # LOG-ONLY suppression, below the comparison above, which has ALREADY run and
  # ALREADY passed by the time this line is reached — require_pinned_tool calls
  # this unconditionally on every stage that needs ${rt_tool}, so the real
  # probe-and-compare happens every time regardless of what this prints.
  if ! tool_already_logged "${rt_tool}"; then
    printf '    %-12s %s\n' "${rt_tool}" "${rt_found}"
    logged_tools="${logged_tools} ${rt_tool}"
  fi
}

# Tool names implied by the <NAME>_VERSION keys in ci/versions.env, lowercased
# with `_` folded to `-` (PRE_COMMIT_VERSION -> pre-commit). Deriving the list
# means the coverage check cannot fall behind the pin file.
versions_env_tools() {
  sed -n 's/^\([A-Za-z0-9_]*\)_VERSION=.*/\1/p' "${VERSIONS_ENV}" | tr 'A-Z_' 'a-z-'
}

# Every pin declared by at least one stage, deduplicated.
declared_stage_pins() {
  for dsp_label in ${STAGE_LABELS}; do
    stage_python_pins "${dsp_label}"
  done | sort -u
}

# A pin that no stage claims and that is not explicitly exempt is a pin nobody
# enforces. That used to be impossible because every stage asserted everything;
# with per-stage scoping it becomes possible, so it is checked instead.
#
# UNCONDITIONAL, every call (round 7): this used to memoise "already checked"
# in ${pins_coverage_checked} the same way require_python_interpreter() and
# require_pinned_tool() did. It is cheap — a handful of sed/shell-loop
# comparisons against this script's OWN static case statement and ci/versions.env,
# no subprocess — so re-running it on every preflight() call costs nothing
# worth trading the memo for. Removed for consistency with the rest of the
# preflight machinery, not because this particular flag was ever shown to be a
# live bypass target the way the other two were.
require_pin_coverage() {
  rpc_declared=$(declared_stage_pins)
  for rpc_tool in $(versions_env_tools); do
    rpc_ok=0
    for rpc_known in ${PREFLIGHT_EXEMPT_PINS} ${rpc_declared}; do
      if [ "${rpc_tool}" = "${rpc_known}" ]; then
        rpc_ok=1
        break
      fi
    done
    if [ "${rpc_ok}" = "0" ]; then
      {
        printf 'internal error: ci/versions.env pins %s but no stage in\n' "${rpc_tool}"
        printf '                stage_python_pins() claims it and it is not listed in\n'
        printf '                PREFLIGHT_EXEMPT_PINS. An unchecked pin is an\n'
        printf '                announcement, not a pin (Constitution IV).\n'
      } >&2
      return 1
    fi
  done
}

# UNCONDITIONAL, every call (round 7 — see the preflight header comment above
# for the round-3-through-6 history this replaces). No memo gates this
# function: the interpreter's path, full version and major.minor are resolved
# and compared to ci/versions.env PYTHON_VERSION fresh, every single time this
# is called, whether that is once (a single-stage invocation) or seven times
# (every stage `sh ci/checks.sh all` runs). The FAILURE paths (`return 1`) are
# the only early returns in this function; there is no early `return 0` a
# bypass could aim for, because there is no condition left that would produce
# one before the comparison below has actually run.
require_python_interpreter() {
  PYTHON_RESOLVED=$(command -v "${PYTHON}" 2>/dev/null | tr -d '\r')
  if [ -z "${PYTHON_RESOLVED}" ]; then
    PYTHON_RESOLVED="${PYTHON} (not on PATH)"
  fi

  py_full=$("${PYTHON}" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null | tr -d '\r')
  if [ -z "${py_full}" ]; then
    fail_python "(PYTHON=${PYTHON} is not a working interpreter)"
    return 1
  fi
  py_minor=$("${PYTHON}" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null | tr -d '\r')
  if [ "${py_minor}" != "${PYTHON_VERSION}" ]; then
    fail_python "${py_full}"
    return 1
  fi

  # LOG-ONLY suppression, below the comparisons above, which have ALREADY run
  # and ALREADY passed by the time this line is reached.
  if [ "${python_logged}" != "1" ]; then
    printf '    %-12s %s  (%s)\n' "python" "${py_full}" "${PYTHON_RESOLVED}"
    python_logged=1
  fi
}

# LOG-ONLY: has this tool's found-version line already been printed in this
# run? Consulted by require_tool() (to decide whether to print again) and by
# preflight() (to decide whether the banner above it is worth printing again).
# NEVER consulted to decide whether to verify — nothing calls this before
# deciding whether to run require_tool/require_pinned_tool at all; see round 7
# in the preflight header comment.
tool_already_logged() {
  for tal_seen in ${logged_tools}; do
    if [ "${tal_seen}" = "$1" ]; then
      return 0
    fi
  done
  return 1
}

# UNCONDITIONAL, every call — no `tool_already_checked`-shaped early return
# survives here. require_tool() below re-runs the genuine probe-and-compare
# against ci/versions.env every time this is called, once per stage per pin,
# for the same round-7 reason as require_python_interpreter() above.
require_pinned_tool() {
  rpt_tool="$1"
  rpt_key=$(printf '%s' "${rpt_tool}" | tr 'a-z-' 'A-Z_')_VERSION
  rpt_expected=$(pin_value "${rpt_key}")
  if [ -z "${rpt_expected}" ]; then
    printf 'internal error: could not read %s from %s\n' "${rpt_key}" "${VERSIONS_ENV}" >&2
    return 1
  fi
  require_tool "${rpt_tool}" "${rpt_expected}"
}

preflight() {
  pf_stage="$1"

  require_pin_coverage

  pf_pins=$(stage_python_pins "${pf_stage}")
  if [ -z "${pf_pins}" ]; then
    return 0
  fi

  # LOG-ONLY: is there anything NEW to announce for this call? `all`
  # front-loads every pin, so the per-stage calls that follow usually have
  # nothing new to print; a banner with no lines under it reads as "the check
  # was skipped", which round 7 makes categorically false — the check below
  # always runs — so it is worth suppressing only the REDUNDANT banner, never
  # the check itself. This decides ONLY whether `log` runs; it does not gate
  # require_python_interpreter or the require_pinned_tool loop two lines below,
  # both of which execute unconditionally regardless of what this decides.
  pf_new=0
  if [ "${python_logged}" != "1" ]; then
    pf_new=1
  fi
  for pf_tool in ${pf_pins}; do
    if ! tool_already_logged "${pf_tool}"; then
      pf_new=1
    fi
  done
  if [ "${pf_new}" = "1" ]; then
    log "preflight — pinned toolchain for stage ${pf_stage} (ci/versions.env)"
  fi

  # VERIFICATION — unconditional, every call, every stage, every pin that stage
  # needs. No memo of any kind gates either line below (round 7).
  require_python_interpreter
  for pf_tool in ${pf_pins}; do
    require_pinned_tool "${pf_tool}"
  done
}

# -----------------------------------------------------------------------------
# Stage: lint  (ruff check + ruff format --check)
# -----------------------------------------------------------------------------
stage_lint() {
  preflight lint
  log "lint — ruff ${RUFF_VERSION}"
  "${PYTHON}" -m ruff check .
  "${PYTHON}" -m ruff format --check .
}

# -----------------------------------------------------------------------------
# Stage: typecheck  (mypy strict)
# -----------------------------------------------------------------------------
stage_typecheck() {
  preflight typecheck
  log "type-check — mypy ${MYPY_VERSION}"
  "${PYTHON}" -m mypy
}

# -----------------------------------------------------------------------------
# pytest_suite <dir> <label>
#
# Runs pytest against one suite directory.
#
# pytest exits 5 when it collects nothing. Three very different situations
# produce that code and they must NOT be conflated:
#
#   (a) the directory holds no test module at all — the suite is DECLARED but
#       not yet authored (contract tests are T013/T014, DAG smoke tests are
#       T020). Reported as DECLARED-EMPTY and passes.
#   (b) the directory holds files pytest WOULD collect, yet it collected
#       nothing — a collection failure (bad conftest, import error, mis-set
#       testpaths) masquerading as an empty suite. FAILS as such.
#   (c) the directory holds only helper modules (fixtures.py, helpers.py) whose
#       names match no `python_files` pattern. Also DECLARED-EMPTY, and it must
#       NOT be reported as a collection error — that diagnosis sends the
#       operator hunting for an import failure that does not exist.
#
# The (a)/(b) split used to be decided by `find -name 'test_*.py'`. That is
# filename-fragile: pytest's default `python_files` is `test_*.py` AND
# `*_test.py`. Measured — `tests/contract/stac_boundary_test.py` containing no
# test functions produced pytest exit 5, find count 0, and a green
# DECLARED-EMPTY. Contract coverage could drop to zero with the gate still
# passing, leaving Constitution V unenforced. So BOTH default patterns are
# counted, and the (c) case is separated out by a second, wider count.
#
# The emptiness question itself is put to pytest (`--collect-only`); the file
# counts only corroborate. This is deliberately not `|| true`: the stage arms
# itself the moment the first test lands, with no edit to this script.
# -----------------------------------------------------------------------------
# Shared walk for pytest_suite()'s two corroborating counts, so a future change
# to what gets pruned has ONE call site instead of two that can silently drift
# apart (measured risk, not hypothetical: this is exactly the shape of bug the
# rest of this file exists to catch). Deliberately a FUNCTION, not a string
# variable holding the prune clause: POSIX sh has no arrays, and interpolating
# an unquoted variable containing `-name '*.egg'` would let word-splitting
# glob-expand `*.egg` against the CWD before find ever saw it. Passing the
# post-prune test as literal call-site arguments through "$@" keeps each token
# exactly as quoted at the call site — no re-parsing, no glob risk.
#
# The prune list covers the entries of pytest's `norecursedirs` default that can
# plausibly occur inside a suite directory here, plus `__pycache__`. It is NOT the
# whole default — `_darcs`, `CVS` and `{arch}` are omitted as implausible in this
# repo, and saying "mirrors norecursedirs" would overclaim. `find` does not honour
# that setting at all, so without this a stray `test_*.py` under a nested virtualenv,
# build tree or egg inside a suite directory counted towards a corroborating count
# while pytest collected nothing from it — producing a FAIL that names a
# "collection error" for a file pytest never looked at. The corroborating count
# has to corroborate what pytest actually does, or it argues with the tool it
# exists to double-check.
_pytest_suite_walk() {
  psw_dir="$1"
  shift
  find "${psw_dir}" \
    \( -name '.*' -o -name '__pycache__' -o -name 'build' -o -name 'dist' \
    -o -name 'node_modules' -o -name 'venv' -o -name '*.egg' \) -prune -o \
    -type f "$@" -print
}

pytest_suite() {
  suite_dir="$1"
  label="$2"

  if [ ! -d "${suite_dir}" ]; then
    printf 'FAIL: suite directory %s is missing (plan.md requires it)\n' "${suite_dir}" >&2
    return 1
  fi

  # FAIL CLOSED ON A BROKEN WALK. These used to be `$(find ... | wc -l | tr ...)`,
  # whose exit status is `tr`'s and therefore always 0 — so `set -e` never saw a
  # find failure and BOTH counts silently degraded to 0. With collect_rc 5 that
  # routes straight to "DECLARED-EMPTY ... arms automatically when one lands" and
  # returns 0: a FALSE DECLARED-EMPTY, the dangerous direction this whole ladder
  # exists to prevent, reachable with no hostile input at all — an unreadable
  # subdirectory, a full disk mid-walk, or a find that rejects the `-prune`
  # expression on node A's toolchain would each do it. The find now runs on its
  # own so its status is visible, and the count is taken from the captured list.
  #
  # Files pytest's default `python_files` would collect from.
  collectable_list=$(_pytest_suite_walk "${suite_dir}" \
    \( -name 'test_*.py' -o -name '*_test.py' \)) || {
    printf 'FAIL: could not walk %s to corroborate the collect probe. Refusing to\n' \
      "${suite_dir}" >&2
    printf '      report DECLARED-EMPTY on a count this script could not take.\n' >&2
    return 1
  }
  collectable_count=$(printf '%s\n' "${collectable_list}" | sed '/^$/d' | wc -l |
    tr -d '[:space:]')

  # Any .py that is not package plumbing — the wider "somebody put code here".
  module_list=$(_pytest_suite_walk "${suite_dir}" \
    -name '*.py' ! -name '__init__.py' ! -name 'conftest.py') || {
    printf 'FAIL: could not walk %s for helper modules (see above).\n' "${suite_dir}" >&2
    return 1
  }
  module_count=$(printf '%s\n' "${module_list}" | sed '/^$/d' | wc -l | tr -d '[:space:]')

  # If a `python_files` override is in force, the two counts above no longer
  # bound what pytest collects and this function cannot tell (b) from (c). Say so
  # rather than guessing.
  #
  # THE GOVERNING FILE, not "every file that mentions python_files". pytest picks
  # exactly ONE ini source and ignores the rest entirely — this is not "check
  # each candidate file, first match wins", it is "find the file pytest would
  # actually read, then check ONLY that one". The two are different, and MEASURED
  # different: an empty `[tool.pytest.ini_options]` table in pyproject.toml (no
  # python_files line at all) makes pytest use ITS defaults and completely
  # ignore a real `python_files` override sitting in tox.ini or setup.cfg —
  # confirmed with a real pytest run, collecting the default-pattern file and
  # never touching the override-pattern one. A "first file with the text, in
  # precedence order" loop would have reported that non-live tox.ini override as
  # in force and failed the stage closed for no reason. Precedence, in the order
  # pytest itself applies it:
  #   pytest.ini    governs unconditionally if the file exists at all.
  #   pyproject.toml  governs if pytest.ini is absent and it carries a
  #                   `[tool.pytest.ini_options]` table — EMPTY counts.
  #   tox.ini       governs if neither above applies and it carries `[pytest]`.
  #   setup.cfg     governs if none above applies and it carries `[tool:pytest]`
  #                 — NOT `[pytest]`: that spelling is a hard pytest error in
  #                 setup.cfg ("no longer supported"), confirmed by running it.
  python_files_overridden=0
  python_files_source=''
  python_files_governing=''
  if [ -f pytest.ini ]; then
    python_files_governing='pytest.ini'
  elif [ -f pyproject.toml ] && grep -Eq '^[[:space:]]*\[tool\.pytest\.ini_options\]' pyproject.toml; then
    python_files_governing='pyproject.toml'
  elif [ -f tox.ini ] && grep -Eq '^[[:space:]]*\[pytest\]' tox.ini; then
    python_files_governing='tox.ini'
  elif [ -f setup.cfg ] && grep -Eq '^[[:space:]]*\[tool:pytest\]' setup.cfg; then
    python_files_governing='setup.cfg'
  fi
  # A bare grep for the KEY within the governing file can still false-positive on
  # a `python_files` line that isn't actually under the pytest section (e.g. a
  # coincidentally-named key elsewhere in a multi-tool setup.cfg). That direction
  # is deliberate: a false positive fails this stage closed with a message
  # telling the operator to teach pytest_suite about the override, whereas a
  # false negative silently unsounds the split. Fail towards the loud one.
  if [ -n "${python_files_governing}" ] &&
    grep -Eq '^[[:space:]]*python_files[[:space:]]*=' "${python_files_governing}"; then
    python_files_overridden=1
    python_files_source="${python_files_governing}"
  fi

  set +e
  collect_output=$("${PYTHON}" -m pytest "${suite_dir}" --collect-only -q 2>&1)
  collect_rc=$?
  set -e

  if [ "${collect_rc}" -eq 5 ]; then
    # CHECKED BEFORE collectable_count. Both counts are built from pytest's
    # DEFAULT python_files patterns, so once an override governs, a file that
    # matches the DEFAULT patterns is not "a module matching pytest python_files"
    # at all — pytest is looking for a different pattern entirely, correctly
    # found nothing, and that is not a collection error. Checking
    # `collectable_count` first would misreport that correct, expected outcome
    # as "FAIL: collection error", sending the operator hunting for an import
    # failure that does not exist — on a suite with nothing wrong with it.
    if [ "${python_files_overridden}" = "1" ]; then
      {
        printf 'FAIL: %s collected nothing and %s overrides python_files, so\n' \
          "${label}" "${python_files_source}"
        printf '      this script cannot tell an unauthored suite from a collection error.\n'
        printf '      Teach pytest_suite in ci/checks.sh about the override, or remove it.\n'
      } >&2
      return 1
    fi
    if [ "${collectable_count}" -gt 0 ]; then
      {
        printf 'FAIL: %s contains %s module(s) matching pytest python_files but pytest\n' \
          "${label}" "${collectable_count}"
        printf '      collected nothing (exit 5). That is a collection error, not an empty\n'
        printf '      suite. pytest said:\n'
        printf '%s\n' "${collect_output}"
      } >&2
      return 1
    fi
    if [ "${module_count}" -gt 0 ]; then
      printf 'DECLARED-EMPTY: %s holds %s helper module(s) but no test module (nothing named\n' \
        "${label}" "${module_count}"
      printf '                test_*.py or *_test.py); stage arms automatically when one lands.\n'
      return 0
    fi
    printf 'DECLARED-EMPTY: %s holds no test module yet; stage arms automatically when one lands.\n' "${label}"
    return 0
  fi

  if [ "${collect_rc}" -ne 0 ]; then
    {
      printf 'FAIL: %s failed collection (pytest --collect-only exit %s).\n' "${label}" "${collect_rc}"
      printf '%s\n' "${collect_output}"
    } >&2
    return 1
  fi

  "${PYTHON}" -m pytest "${suite_dir}"
}

stage_unit() {
  preflight unit
  log "unit — pytest ${PYTEST_VERSION}"

  # tests/unit/test_gitleaks_positive_control.py's positive controls run the
  # PINNED gitleaks container directly, to prove ci/gitleaks.toml — not
  # gitleaks' embedded default ruleset — is what the working-tree and
  # staged-diff scans actually load (see that file's own module docstring for
  # why "a leak was found" alone proves nothing). Without this guard, a
  # Docker-less machine did not fail here: that file's own `_tool()` helper
  # falls back to `pytest.skip()` outside CI, so `sh ci/checks.sh unit`
  # reported GREEN having run zero of the positive controls — false confidence
  # that the gitleaks-config-loading assertions ran at all, on the one stage
  # README.md's own prerequisites table already claims needs Docker. Fail fast
  # here instead, before pytest ever starts, with a message distinct from
  # stage_gitleaks's and stage_hooks's docker_or_fail calls so the operator
  # knows WHICH stage's Docker dependency is unmet and why. (Not a candidate
  # for moving those tests out of tests/unit into a suite whose stage already
  # declares Docker: they assert a structural property of THIS gate runner's
  # own gitleaks integration — same file, same reasoning as
  # tests/unit/test_ci_contract.py and tests/unit/test_checks_sh_behaviour.py
  # sitting in tests/unit rather than tests/contract or tests/smoke.)
  #
  # ROUND 10 — this guard used to stop at "is the docker binary on PATH", and
  # that is the WEAKER of the two questions it has to ask. With Docker Desktop
  # stopped, the CLI is still on PATH, so the guard passed, pytest ran, and this
  # stage produced 8 failures whose text said the SECRETS GATE had let a planted
  # Fernet key and a committed kubeconfig through. docker_or_fail now probes the
  # daemon as well and fails here, before pytest, naming the real cause; the
  # reason string below is what makes that message say WHICH stage and WHY.
  docker_or_fail "tests/unit/test_gitleaks_positive_control.py's positive controls run the pinned gitleaks container directly, to prove ci/gitleaks.toml (not gitleaks' embedded defaults) is what actually loads"

  # ROUND 6 / MAJOR 2 — symmetric with the docker_or_fail call immediately
  # above, for a git-shaped instance of the exact same failure mode. 5 of
  # tests/unit/test_gitleaks_positive_control.py's 8 tests
  # (test_a_custom_rule_only_secret_reddens_the_real_gate,
  # test_a_gitignored_copy_of_the_same_secret_does_not_redden_the_gate,
  # test_a_committed_kubeconfig_is_caught_by_a_rule_with_no_default_equivalent,
  # test_the_gitleaks_hook_entry_actually_parses_against_the_pinned_image,
  # test_the_gitleaks_hook_catches_a_custom_rule_secret_in_the_staged_index)
  # drive git directly — `git init`, `git add -A` — to build the synthetic
  # repositories and staged indices those same positive controls then scan.
  # That file's `_tool()` helper falls back to `pytest.skip()` outside CI for
  # git exactly as it does for docker, so a machine with Docker and the pinned
  # Python toolchain but no git on PATH used to pass this stage having proven
  # nothing about whether ci/gitleaks.toml — not gitleaks' embedded defaults —
  # is what actually loads, on every one of those five.
  git_or_fail "tests/unit/test_gitleaks_positive_control.py's positive controls also drive git directly (git init, git add -A) to build the synthetic repositories and staged indices those same 5 tests scan"

  # tests/unit is never allowed to be empty: the repo-structure meta-test lives
  # here from T001 onward, so a real assertion always runs.
  "${PYTHON}" -m pytest tests/unit
}

stage_contract() {
  preflight contract
  log "contract — pytest ${PYTEST_VERSION} (Constitution V)"
  pytest_suite tests/contract "contract suite"
}

stage_smoke() {
  preflight smoke
  log "smoke — pytest ${PYTEST_VERSION} (DAG import + structural validation)"
  pytest_suite tests/smoke "DAG smoke suite"
}

# -----------------------------------------------------------------------------
# Stage: coverage  (FR-011a — minimum measured statement coverage of src/)
#
# NOT one of FR-011's six gates. It exists because FR-011a asks for it, which is
# the only thing that makes it conformant: the `hooks` stage is NOT a precedent
# here, because Constitution VII already required gitleaks as both a hook and a
# CI gate, so `hooks` enforced an existing requirement. Nothing required coverage
# until FR-011a. See docs/decisions/001-coverage-gate.md D-01.
#
# ONE PROCESS OVER ALL SUITES, deliberately. .github/workflows/ci.yml runs each
# stage as a separate matrix JOB, so per-stage coverage could only be combined
# via artifact upload plus a combine step — orchestration logic in a file whose
# own header forbids gate logic. The cost is that `all` runs tests/unit twice;
# that is accepted and recorded (D-06), not optimised away, because every cheaper
# option weakens a gate. In particular this stage must NOT replace unit/contract/
# smoke: a single `pytest tests/` always collects (tests/unit is never empty), so
# pytest_suite's exit-5 discrimination — the mechanism that stops an empty or
# broken contract suite passing vacuously — would never run again.
#
# THRESHOLD COMES FROM ci/versions.env, never a literal here (Constitution III).
#
# NO SPECIAL CASE FOR "no product code yet", because none is needed: coverage
# reports 100% for zero measurable statements, so the docstring-only __init__.py
# tree clears the threshold today and the gate arms itself the instant the first
# executable line lands. An earlier draft keyed that on "the package holds no
# module other than __init__.py" — a filename heuristic, i.e. the exact class of
# condition pytest_suite's own header records as already measured broken here,
# and one that would disarm the gate permanently and silently the day somebody
# writes real code in an __init__.py. Measured instead; see D-02.
#
# `--cov=src/orbital_drift` is the PATH form, not `--cov=orbital_drift`. The path
# form has no import-resolution ordering dependency under the src layout, and it
# is what makes a module no test imports report as 0% instead of vanishing from
# the report entirely. Verified: planting one uncovered module drops TOTAL to 0%.
#
# A --cov-fail-under breach exits 1, exactly as a test failure does; only the
# "Required test coverage" line distinguishes them, so --cov-report=term-missing
# is not decoration. Nothing here branches on the exit code.
# -----------------------------------------------------------------------------
stage_coverage() {
  preflight coverage
  log "coverage — pytest-cov ${PYTEST_COV_VERSION} / coverage ${COVERAGE_VERSION} (FR-011a, min ${COVERAGE_MIN_PERCENT}%)"

  # This stage runs tests/unit, whose gitleaks positive controls drive real
  # containers and real git, and whose own _tool() helper falls back to
  # pytest.skip() outside CI. Without these two guards the stage is a FAIL-OPEN:
  # on a Docker-less box it would report a green coverage number computed from a
  # run in which eight of those controls silently skipped. Reasons are distinct
  # from stage_unit's and stage_hooks's so the message names which stage's
  # dependency is unmet, not merely that one is.
  docker_or_fail "this stage measures the same tests/unit positive controls under coverage, and a control that skipped instead of running would inflate the measured number"
  git_or_fail "this stage measures tests/unit, 5 of whose positive controls drive git directly to build the synthetic repositories and staged indices they scan"

  # PYTEST_ADDOPTS is pytest's own documented escape hatch and it is NOT
  # available here, for exactly the reason SKIP is not available to stage_hooks.
  # pytest builds argv as [ini addopts] + [PYTEST_ADDOPTS] + [command line], so
  # a `--cov-fail-under=0` injected this way loses to the flag below — but the
  # BOOLEAN switches do not lose, because they are not overridden by a later
  # value:
  #
  #   PYTEST_ADDOPTS='--no-cov'        pytest-cov's documented "disable coverage
  #                                    completely" flag. Warns, does not error;
  #                                    --cov-fail-under is never applied; exit 0.
  #   PYTEST_ADDOPTS='--collect-only'  never runs a test, never reports, exit 0.
  #
  # Either one turns this stage into a green coverage number over a run that
  # measured nothing, which is precisely the vacuous pass stage_hooks has two
  # separate branches dedicated to refusing. Unset rather than merely warned
  # about, and announced when it was set so the operator is not left wondering
  # why their flag had no effect.
  if [ -n "${PYTEST_ADDOPTS:-}" ]; then
    printf 'NOTE: ignoring PYTEST_ADDOPTS=%s — this stage is a gate and has no opt-out.\n' \
      "${PYTEST_ADDOPTS}"
  fi
  # No `local` in POSIX sh: this removes PYTEST_ADDOPTS from the CALLING shell's
  # environment for the remainder of the process, exactly like `unset SKIP` in
  # stage_hooks. Harmless today — no stage after this one in stage_all reads
  # PYTEST_ADDOPTS — but stage_hooks gets away with the same unset because it
  # runs LAST; this stage does not. If a future stage lands after `coverage` in
  # stage_all and wants its own PYTEST_ADDOPTS semantics, it inherits "already
  # unset" silently. Flagged here so that future stage's author finds this note
  # instead of a puzzling always-empty variable.
  unset PYTEST_ADDOPTS

  # Output is captured rather than streamed so the diagnosis below can read it.
  # A --cov-fail-under breach and a test failure BOTH exit 1 and only the
  # "Required test coverage" line separates them, so without this branch a red
  # `coverage` job tells the operator nothing about which of the two happened —
  # and this stage would be the only gate in the file with no FAIL: block.
  # Deliberately one long line: test_checks_sh_has_no_gate_disabling_constructs
  # allows a disarmed-errexit window of ERREXIT_WINDOW lines and requires the
  # `rc=$?` capture inside it. Wrapping this invocation for readability pushed
  # the capture out of that window. Widening the guard to suit one call site
  # would trade a real protection for a cosmetic one.
  set +e
  cov_output=$("${PYTHON}" -m pytest tests --cov=src/orbital_drift --cov-report=term-missing --cov-report=json --cov-fail-under="${COVERAGE_MIN_PERCENT}" 2>&1)
  cov_rc=$?
  set -e

  printf '%s\n' "${cov_output}"
  if [ "${cov_rc}" -eq 0 ]; then
    # Charter C-6's per-file floor. The global average above can pass while a
    # single untested module hides behind a healthy aggregate (a global bar
    # asks "is the average acceptable"; this asks "is anything unwatched").
    # Run ONLY after the global floor has already passed, over the
    # coverage.json --cov-report=json just produced, and propagate its exit
    # code as this stage's own so a per-file breach still reddens `coverage`.
    "${PYTHON}" -m orbital_drift.covcheck
    return $?
  fi

  # THREE structurally distinct causes, not two, and the order they are checked
  # in is load-bearing (D-12).
  #
  # A NAMED TEST FAILURE IS CHECKED FIRST, unconditionally — via `^FAILED `,
  # pytest's own per-test marker, always at the start of a line. This is not
  # equivalent to grepping for the coverage-breach text and falling through:
  # pytest-cov's `_should_report()` only suppresses its "Required test coverage"
  # line on a test failure when --no-cov-on-fail is passed, which this stage
  # does not, so BOTH lines can appear in the SAME run — a real test failure
  # plus a real coverage breach, together. Checking the coverage line first
  # would report "the tests are not the problem" while a test is, in fact, the
  # problem. Checking `^FAILED ` first means a mixed run is correctly reported
  # as a test failure, coverage breach or not.
  #
  # This ALSO closes a self-referential trap the first version of this stage
  # shipped with: tests/unit/test_coverage_positive_control.py asserts on the
  # literal string "Required test coverage of 85% not reached". If that
  # assertion itself ever fails — a future pytest-cov wording change, anything —
  # pytest's rewritten-assertion traceback reprints the literal string being
  # checked for, and an UNANCHORED grep for that text over the whole run's
  # combined output would match the TRACEBACK, not a real coverage report, and
  # report "COVERAGE BREACH. The tests are not the problem" for a run in which
  # a test — the one designed to catch exactly this class of regression —
  # genuinely failed. Verified by reproducing the traceback shape directly:
  # pytest prefixes assertion-introspection lines with `>` or `E`, never with a
  # bare `FAILED ` at column 0, so `^FAILED ` cannot be satisfied by that
  # traceback text — only by pytest's own summary line for a test that actually
  # failed, including this one.
  if printf '%s' "${cov_output}" | grep -q '^FAILED \|^ERROR '; then
    {
      printf '\n'
      printf 'FAIL: tests failed UNDER MEASUREMENT — this is not (only) a coverage breach.\n\n'
      printf '        This stage runs all three suites in ONE pytest process, so its\n'
      printf '        process topology differs from the unit/contract/smoke stages.\n\n'
      printf '        First check whether the ordinary stage(s) for the failing test(s)\n'
      printf '        agree:\n\n'
    } >&2
    # NAME THE STAGE(S) THE FAILURE ACTUALLY IMPLICATES, not a fixed `unit`.
    # This stage runs tests/unit, tests/contract AND tests/smoke together
    # (D-06), and until now this message unconditionally suggested only
    # `sh ci/checks.sh unit` regardless of which suite the failing test lives
    # in — latent while tests/contract and tests/smoke are still empty, but a
    # real test failure in either of those two suites would have sent the
    # operator to check a stage that was GREEN for an unrelated reason (the
    # broken test simply isn't in it), and the message's own next paragraph
    # ("if they are GREEN and this is RED, ... not a broken test") would then
    # have actively argued them away from the real, ordinary bug. Read the
    # implicated suite(s) from the FAILED/ERROR lines themselves rather than assume.
    failed_lines=$(printf '%s' "${cov_output}" | grep '^FAILED \|^ERROR ')
    suite_named=0
    if printf '%s' "${failed_lines}" | grep -q 'tests/unit/'; then
      printf '            sh ci/checks.sh unit\n' >&2
      suite_named=1
    fi
    if printf '%s' "${failed_lines}" | grep -q 'tests/contract/'; then
      printf '            sh ci/checks.sh contract\n' >&2
      suite_named=1
    fi
    if printf '%s' "${failed_lines}" | grep -q 'tests/smoke/'; then
      printf '            sh ci/checks.sh smoke\n' >&2
      suite_named=1
    fi
    if [ "${suite_named}" = "0" ]; then
      # A FAILED line whose path matched none of the three prefixes (an
      # unexpected pytest invocation shape) — fail towards suggesting
      # everything rather than nothing, the same "loud, not silent" bias
      # pytest_suite's own override detection uses.
      printf '            sh ci/checks.sh unit; sh ci/checks.sh contract; sh ci/checks.sh smoke\n' >&2
    fi
    {
      printf '\n'
      printf '        If they are GREEN and this is RED, the failure is an interaction\n'
      printf '        introduced by the single-process run, not a broken test — see\n'
      printf '        docs/decisions/001-coverage-gate.md D-06 for why that run exists.\n\n'
    } >&2
    if printf '%s' "${cov_output}" | grep -q '^FAIL Required test coverage of .* not reached'; then
      printf '        Coverage ALSO breached the threshold in this same run (see the\n' >&2
      printf '        "Required test coverage" line above) — fix the failing test(s)\n' >&2
      printf '        first; the coverage number is not trustworthy until they pass.\n\n' >&2
    fi
    return 1
  fi

  # ANCHORED to line start and to pytest-cov's exact literal prefix ("FAIL ",
  # not merely the prose fragment "Required test coverage"), for the same
  # self-reference reason above: this is pytest-cov's own terminal-summary line
  # (verified against the real plugin output, not assumed), and nothing else
  # this repo's test suite prints reproduces that exact line shape at column 0.
  if printf '%s' "${cov_output}" | grep -q '^FAIL Required test coverage of .* not reached'; then
    {
      printf '\n'
      printf 'FAIL: COVERAGE BREACH (FR-011a). No test failed; this is a coverage-only\n'
      printf '      breach.\n\n'
      printf '        threshold:  %s%%        <- ci/versions.env COVERAGE_MIN_PERCENT\n' \
        "${COVERAGE_MIN_PERCENT}"
      printf '        measured:   see the "Total coverage" line above\n\n'
      printf '        The term-missing table above lists the uncovered lines per file.\n'
      printf '        FIX: write tests for them. Lowering COVERAGE_MIN_PERCENT to make\n'
      printf '        this green is the failure mode docs/decisions/001-coverage-gate.md\n'
      printf '        D-05 names explicitly; change it only deliberately, with a reason\n'
      printf '        recorded there.\n\n'
    } >&2
    return 1
  fi

  # NEITHER signal fired: no named test failure, no coverage-threshold breach,
  # yet pytest exited non-zero. A COLLECTION error — an import failure, a
  # fixture error, or (foreseeable given this stage's own design, D-06)
  # pytest's well-known "import file mismatch" when two suites it runs
  # TOGETHER as one session (tests/unit, tests/contract, tests/smoke) contain
  # same-named modules with no __init__.py to disambiguate them.
  {
    printf '\n'
    printf 'FAIL: pytest exited non-zero with neither a named test failure nor a\n'
    printf '      coverage-threshold breach in its output — a COLLECTION error (import\n'
    printf '      failure, fixture error, or a basename collision between tests/unit,\n'
    printf '      tests/contract and tests/smoke, which this stage runs together as one\n'
    printf '      pytest session — see docs/decisions/001-coverage-gate.md D-06). Full\n'
    printf '      pytest output is above.\n\n'
  } >&2
  return 1
}

# =============================================================================
# Pinned-container plumbing (gitleaks, shellcheck, terraform)
#
# Runs the pinned containers rather than local builds, so these stages behave
# identically on the Windows authoring box, a GitHub runner, and node A.
# =============================================================================

# Git Bash / MSYS on Windows rewrites arguments that look like POSIX paths into
# Windows paths before docker sees them. Observed here: `-w /repo` reaching
# docker as `-w C:/Program Files/Git/repo`, exit 125. Suppressing the conversion
# fixed that case; both variables are inert on a real POSIX shell. No claim is
# made about which other arguments MSYS would rewrite: only `-w` was observed.
#
# SCOPED to this wrapper, not exported at module scope. Exporting them changed
# the environment of every stage — ruff, mypy, pytest and pre-commit all
# inherited MSYS2_ARG_CONV_EXCL='*' — to fix a problem that was only ever
# measured at two `docker run` sites. A prefix assignment on a single command
# applies to that command only, which is the scope the measurement supports.
#
# ROUND 5 — this is no longer only "one historical manual measurement". Every
# real-docker test in tests/unit/test_gitleaks_positive_control.py drives
# `sh ci/checks.sh gitleaks` with these two variables deliberately ABSENT from
# its own environment (`_run(..., msys_passthrough=False)`, precisely so a
# regression in this wrapper's scoping would show up), on this project's own
# Windows/Git-Bash authoring box. Reverting this function to a bare
# `docker run "$@"` and re-running that file reproduces the original failure
# EXACTLY, on 3 of its 8 tests:
#
#     docker: Error response from daemon: the working directory
#     'C:/Program Files/Git/repo' is invalid, it needs to be an absolute path
#
# — i.e. those three tests are not just source-level coverage of this fix, they
# are a live behavioural regression test for its EFFECT, on every run. What
# remains unverified is only the negative claim in the paragraph above: that no
# argument OTHER than `-w` is subject to the same rewriting. That is a
# statement about MSYS's rewriting rules in general, which this project has no
# way to enumerate exhaustively; the claim is deliberately scoped to what was
# observed rather than generalised.
docker_run() {
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker run "$@"
}

# ---------------------------------------------------------------------------
# TWO DIFFERENT QUESTIONS, and for six rounds this function only asked the
# first one.
#
#   1. is the docker CLI installed?        `command -v docker`
#   2. is the DAEMON it talks to alive?    `docker info`
#
# ROUND 10 — measured live on the authoring box, Docker Desktop stopped between
# test runs, nothing planted or simulated:
#
#     command -v docker
#     -> /c/Program Files/Docker/Docker/resources/bin/docker            rc 0
#     docker info
#     -> failed to connect to the docker API at
#        npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is
#        correct and if the daemon is running: open
#        //./pipe/dockerDesktopLinuxEngine: The system cannot find the file
#        specified.                                                     rc 1
#
# Question 1 answered YES, so the guard did not fire, pytest started, and
# `sh ci/checks.sh all` reported
#
#     8 failed, 211 passed
#
# — the gitleaks positive controls and one hook test, with assertion text
# reading "the gate passed on a planted Fernet key" and "a committed kubeconfig
# passed the gate". An operator reading that is told THE SECRETS GATE IS
# BROKEN. The true cause was "Docker Desktop is not running". Same class of
# misdiagnosis docker_failure_report() was written to end in round 4 (an
# infrastructure outage reported as pin drift), one layer earlier in the same
# stage, and reached by a fail-fast guard that was supposed to prevent exactly
# this.
#
# So both questions are asked here, in that order, and the answer to the second
# is routed through the SAME failure-mode table require_pinned_image already
# uses (docker_error_cause below) rather than a second, weaker diagnosis.
# Callers are unchanged: each still supplies its own literal reason string, so
# the message still names WHICH stage's Docker dependency is unmet and why.
#
# COST ON THE HAPPY PATH: one `docker info` round trip per docker_or_fail call
# — four per `sh ci/checks.sh all` (unit, coverage, gitleaks, hooks), one per
# single-stage run — against stages that then run containers and full test
# suites anyway.
#
# NOT CACHED, deliberately and permanently. `docker info` is a pass/fail
# decision, and rounds 3-7 of this file's history are one long demonstration
# that a memo standing in for a real probe is a defect generator (see the
# preflight header comment). There is no "daemon already checked" flag here,
# per-run or per-stage, for the same structural reason there is no
# "pins already checked" flag left anywhere above; the probe re-runs, in full,
# on every call. tests/unit/test_checks_sh_behaviour.py asserts the count
# behaviourally (four probes in one `all` run, not one).
# ---------------------------------------------------------------------------
docker_or_fail() {
  df_reason="$1"
  if ! command -v docker >/dev/null 2>&1; then
    {
      printf '\n'
      printf 'FAIL: docker is not on PATH, and %s.\n\n' "${df_reason}"
      printf '        gitleaks:    %s\n' "${GITLEAKS_IMAGE}"
      printf '        shellcheck:  %s\n\n' "${SHELLCHECK_IMAGE}"
      printf '        Install Docker (README.md, Requirements), or run the equivalent\n'
      printf '        gitleaks %s / shellcheck %s binaries by hand.\n\n' \
        "${GITLEAKS_VERSION}" "${SHELLCHECK_VERSION}"
    } >&2
    return 1
  fi
  docker_daemon_or_fail "${df_reason}"
}

# The daemon half of the guard above. Split out so the binary-missing message
# and the daemon-unreachable message stay two distinct diagnostics with two
# distinct remediations ("install Docker" vs "start Docker Desktop"), which is
# the whole point — conflating them is how the round-4 pin-drift misdiagnosis
# happened one layer down.
#
# `docker info` and not a cheaper-looking probe: it is the exact command every
# FIX line in docker_error_cause() already tells the operator to run, so
# "reproduce it in isolation" is literally true, and it is one API round trip.
#
# `|| dd_rc=$?` rather than the `set +e` / `rc=$?` / `set -e` window used twice
# elsewhere in this file: a command on the LEFT of `||` is already exempt from
# errexit, so the status is captured without ever disarming errexit. Strictly
# narrower than a disarmed window, and it keeps the number of places in this
# file where errexit is off at two.
docker_daemon_or_fail() {
  dd_reason="$1"
  docker_probe_errfile
  dd_rc=0
  docker info >/dev/null 2>"${DOCKER_PROBE_ERR}" || dd_rc=$?
  if [ "${dd_rc}" -ne 0 ]; then
    docker_daemon_report "${dd_reason}" "${dd_rc}"
    return 1
  fi
}

docker_daemon_report() {
  ddr_reason="$1"
  ddr_rc="$2"
  ddr_cli=$(command -v docker 2>/dev/null || printf '(not resolvable)')
  {
    printf '\n'
    printf 'FAIL: the Docker daemon is not reachable (Docker Desktop may not be running).\n'
    printf '      The docker CLI itself IS installed and IS on PATH, which is precisely\n'
    printf '      why a "command -v docker" check cannot see this state.\n\n'
    printf '      This is an INFRASTRUCTURE failure. It is NOT a gate failure, NOT a\n'
    printf '      version mismatch and NOT evidence that any secret slipped through —\n'
    printf '      do not edit ci/versions.env, and do not read it as the secrets gate\n'
    printf '      being broken.\n\n'
    printf '        probe:       docker info\n'
    printf '        docker rc:   %s\n' "${ddr_rc}"
    printf '        docker CLI:  %s\n\n' "${ddr_cli}"
    printf '        this stage needs the daemon because %s.\n\n' "${ddr_reason}"
  } >&2
  docker_error_cause ''
  return 1
}

# git is a hard prerequisite of four stages now — gitleaks, hooks, (round
# 6 / MAJOR 2) unit and (FR-011a) coverage — each for a DIFFERENT reason. Without a named reason, a
# missing git surfaced generically as "not a git repository" from
# require_git_history_is_scannable — a true statement about the wrong problem
# for stage_gitleaks, and no diagnostic at all for the other two before this
# guard existed for them. Mirroring docker_or_fail exactly: every caller
# supplies its OWN literal-string reason, so an operator reading the failure
# knows which stage's git dependency is unmet and why, not just that git is
# missing.
#
# ROUND 6 / MAJOR 2 — SPLIT VERDICT the operator resolved by choosing the
# stricter reading. spec-guardian argued stage_unit needed no guard: git is
# this whole repository's unconditional baseline (needed to even have the
# repo checked out) in a way Docker — needed by exactly three of eight
# stages — is not, so a missing git is implausible in practice.
# peer-reviewer traced a concrete counter-scenario instead: Docker present,
# the pinned Python toolchain present, git ABSENT from PATH (a minimal
# container built to run Docker jobs, or a source snapshot with no .git
# directory) — `sh ci/checks.sh unit` would pass preflight and docker_or_fail,
# pytest would start, and every one of
# tests/unit/test_gitleaks_positive_control.py's 5 git-dependent positive
# controls would hit `shutil.which("git") is None`, fall through to
# `pytest.skip()` (CI env var falsy locally), and the stage would still exit
# 0 — green, having proven nothing about whether ci/gitleaks.toml (not
# gitleaks' embedded defaults) is what actually loads. The operator's
# resolution: "usually present" is a plausibility argument, not a code-level
# guarantee, and this file already holds Docker to that stricter standard —
# add the guard anyway, for consistency.
#
# ROUND 10 — WHY THIS ONE STAYS A `command -v` CHECK while docker_or_fail no
# longer is. The docker guard was wrong because `docker` is a CLIENT: the
# binary on PATH is a different thing from the daemon it talks to over a socket
# or a named pipe, and only the second one can do any work. git has no daemon
# and no server component in anything this repository does: `git init`,
# `git add`, `git ls-files`, `git rev-parse` and `git config` are all local
# process + filesystem operations, so an executable `git` on PATH IS the
# capability, and there is no equivalent "installed but not answering" state
# for a probe to detect. Checked, not assumed, before leaving it alone.
#
# The two git preconditions that are NOT "is git installed" — is this a git
# repository at all, and is its history complete enough to scan — are real, and
# they are asserted separately and specifically by
# require_git_history_is_scannable() for the one stage that walks the commit
# graph. Neither of those is a liveness question either.
git_or_fail() {
  gf_reason="$1"
  if ! command -v git >/dev/null 2>&1; then
    {
      printf '\n'
      printf 'FAIL: git is not on PATH, and %s.\n\n' "${gf_reason}"
      printf '        See README.md, Requirements.\n\n'
    } >&2
    return 1
  fi
}

# stderr from the last docker probe — the daemon-liveness probe
# (docker_daemon_or_fail) or the pinned-container version probe
# (require_pinned_image), whichever ran last. Created lazily, so the stages that
# touch no docker at all (lint, typecheck, contract, smoke) still write nothing
# outside the repo. Round 10 widened WHO calls this — stage_unit now creates the
# file too, at its guard, before pytest — but not WHERE or HOW MANY: one file
# per process, reused by every probe in it, removed by the same trap.
DOCKER_PROBE_ERR=''
docker_probe_errfile() {
  if [ -z "${DOCKER_PROBE_ERR}" ]; then
    DOCKER_PROBE_ERR=$(mktemp "${TMPDIR:-/tmp}/orbital-drift-docker-probe.XXXXXX")
    trap 'rm -f "${DOCKER_PROBE_ERR}"' EXIT HUP INT TERM
  fi
}

# ---------------------------------------------------------------------------
# A failed `docker run` is NOT evidence of version drift.
#
# The previous form was `found=$(docker run ... version 2>/dev/null)`, which
# discarded stderr and dropped the exit status, so a stopped daemon, a missing
# network route, a ghcr.io rate limit, a deleted tag and a user who is not in
# the docker group ALL arrived at the same branch — one that told the operator
# to go and edit ci/versions.env, a file that was correct. Branch on the exit
# code first, and name the failure that actually happened.
# ---------------------------------------------------------------------------
# The failure-mode table itself, factored out in round 10 so the daemon guard
# (docker_daemon_or_fail, above) and the pinned-container probe
# (require_pinned_image, below) share ONE diagnosis of ONE captured stderr
# instead of the container probe having a good one and the fail-fast guard
# having none. Reads ${DOCKER_PROBE_ERR}, which both callers have just written.
#
# $1 is the image reference, or EMPTY when the failure was not about an image
# (the daemon probe runs no container). The three branches that would otherwise
# print `docker pull <nothing>` check for that rather than emitting a
# copy-pasteable command with a hole in it.
#
# The daemon branch's patterns are matched against LOWERCASED stderr and are
# deliberately over-inclusive across platforms. `docker_engine` and
# `dial unix /var/run/docker.sock` covered the old Windows named pipe and Linux
# socket spellings; Docker Desktop's CURRENT Windows client says
#
#     failed to connect to the docker API at npipe:////./pipe/
#     dockerDesktopLinuxEngine; check if the path is correct and if the daemon
#     is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot
#     find the file specified.
#
# which matches NONE of the pre-round-10 patterns — note "if the daemon is
# running", not "is the docker daemon running", and "dockerDesktopLinuxEngine",
# not "docker_engine". Measured: that exact stderr fell through to the
# `unrecognised docker failure` branch, i.e. the one message in this table that
# tells the operator to go and read raw docker stderr, for the single most
# common and most trivially fixable cause there is.
docker_error_cause() {
  dec_image="$1"
  dec_err=$(tr -d '\r' <"${DOCKER_PROBE_ERR}")
  dec_lower=$(printf '%s' "${dec_err}" | tr '[:upper:]' '[:lower:]')

  {
    case "${dec_lower}" in
      *'cannot connect to the docker daemon'* | \
      *'is the docker daemon running'* | \
      *'if the daemon is running'* | \
      *'the docker daemon is not running'* | \
      *'failed to connect to the docker api'* | \
      *'error during connect'* | \
      *'npipe:'* | \
      *'docker_engine'* | \
      *'dial unix /var/run/docker.sock'*)
        printf '        CAUSE: the Docker daemon is not reachable.\n'
        printf '        FIX:   start Docker Desktop, or: sudo systemctl start docker\n'
        printf '               Then re-run. Verify in isolation with: docker info\n'
        ;;
      *'permission denied'*'docker.sock'* | *'permission denied while trying to connect'*)
        printf '        CAUSE: this user may not talk to the Docker socket.\n'
        printf '        FIX:   sudo usermod -aG docker <your-user>\n'
        printf '               then log out and back in — group membership is only\n'
        printf '               picked up on a new login session.\n'
        ;;
      *'toomanyrequests'* | *'rate limit'* | *'429'*)
        printf '        CAUSE: the registry is rate-limiting this IP.\n'
        printf '        FIX:   wait, or authenticate to raise the quota:\n'
        printf '                   docker login ghcr.io      (gitleaks)\n'
        printf '                   docker login              (shellcheck, Docker Hub)\n'
        if [ -n "${dec_image}" ]; then
          printf '               The image is digest-pinned, so a cached copy is safe to\n'
          printf '               reuse: docker pull %s\n' "${dec_image}"
          printf '               once, and the scan then works offline.\n'
        fi
        ;;
      *'no such host'* | *'i/o timeout'* | *'tls handshake timeout'* | \
      *'dial tcp'* | *'network is unreachable'* | *'temporary failure in name resolution'*)
        printf '        CAUSE: no network route to the registry.\n'
        printf '        FIX:   restore connectivity, or pre-pull the image on a connected\n'
        printf '               machine and move it with: docker save / docker load\n'
        ;;
      *'manifest unknown'* | *'not found'*)
        printf '        CAUSE: the registry has no such image. The digest pin in\n'
        printf '               ci/versions.env cannot be resolved.\n'
        printf '        FIX:   this one IS a pin problem, but it is a SUPPLY-CHAIN event,\n'
        printf '               not a routine bump: a digest that existed no longer does.\n'
        printf '               Stop and escalate to the operator before changing the pin.\n'
        ;;
      *'unauthorized'* | *'denied'* | *'authentication required'*)
        printf '        CAUSE: the registry refused this client.\n'
        printf '        FIX:   docker login ghcr.io   (gitleaks)\n'
        printf '               docker login           (shellcheck, Docker Hub)\n'
        printf '               then re-run.\n'
        ;;
      *)
        printf '        CAUSE: unrecognised docker failure.\n'
        if [ -n "${dec_image}" ]; then
          printf '        FIX:   read the stderr below. These two reproduce it in isolation:\n'
          printf '                   docker info\n'
          printf '                   docker pull %s\n' "${dec_image}"
        else
          printf '        FIX:   read the stderr below. This reproduces it in isolation:\n'
          printf '                   docker info\n'
        fi
        ;;
    esac

    printf '\n        docker stderr:\n'
    if [ -n "${dec_err}" ]; then
      printf '%s\n' "${dec_err}" | sed 's/^/            /'
    else
      printf '            (docker printed nothing on stderr)\n'
    fi
    printf '\n'
  } >&2
}

docker_failure_report() {
  dfr_tool="$1"
  dfr_image="$2"
  dfr_rc="$3"

  {
    printf '\n'
    printf 'FAIL: could not run the pinned %s container. This is an INFRASTRUCTURE\n' "${dfr_tool}"
    printf '      failure, not a version mismatch — do not edit ci/versions.env.\n\n'
    printf '        image:      %s\n' "${dfr_image}"
    printf '        docker rc:  %s\n\n' "${dfr_rc}"
  } >&2
  docker_error_cause "${dfr_image}"
  return 1
}

# Version extractors. `gitleaks version` prints `v8.30.1`; shellcheck --version
# prints a four-line banner with `version: 0.11.0` on the second line.
#
# The `v` is OPTIONAL in the gitleaks extractor. `1s/^v//p` prints nothing when
# the substitution does not match, so an upstream that ever dropped the prefix
# produced "reported: (image printed no parseable version)" — a pin-drift
# diagnostic, sending the operator to re-resolve a digest, for a formatting
# change.
gitleaks_reported_version() { sed -n '1s/^v\{0,1\}//p'; }
shellcheck_reported_version() { sed -n 's/^version:[[:space:]]*//p'; }
# terraform version prints a two-line banner, "Terraform v1.15.8" on line 1
# ("on linux_amd64" on line 2) — confirmed against a real pulled-and-run
# 1.15.8 binary, not just the CLI's own source. -version (not the bare
# "version" gitleaks uses, nor the "--version" shellcheck uses) is a
# deliberate choice: tests/unit/shell_harness.py's DOCKER_STUB dispatches on
# the trailing suffix of the whole probe command line, and "-version" (no
# space before the dash) matches neither of the other two tools' stub arms.
terraform_reported_version() { sed -n '1s/^Terraform v//p'; }

# The image reference is a pin like any other: assert the container agrees with
# ci/versions.env before trusting its verdict. Digest-pinning makes docker
# resolve the CONTENT; this assertion catches the case where the digest is
# stale relative to the version the rest of the repo believes it is running.
#
# rp_tag_prefix exists because not every pinned repository's tags share
# gitleaks/shellcheck's "v"-prefixed convention — hashicorp/terraform's
# Docker Hub tags carry no "v" at all (confirmed against the live registry's
# own tag list). Without a caller-supplied prefix, the remediation commands
# printed below would suggest a tag that does not exist on the registry.
require_pinned_image() {
  rp_tool="$1"
  rp_image="$2"
  rp_expected="$3"
  rp_tag_prefix="$4"
  rp_extract="$5"
  shift 5

  docker_probe_errfile
  set +e
  rp_raw=$(docker_run --rm "${rp_image}" "$@" 2>"${DOCKER_PROBE_ERR}")
  rp_rc=$?
  set -e
  if [ "${rp_rc}" -ne 0 ]; then
    docker_failure_report "${rp_tool}" "${rp_image}" "${rp_rc}"
    return 1
  fi

  rp_found=$(printf '%s\n' "${rp_raw}" | tr -d '\r' | "${rp_extract}" | tr -d '\n')
  if [ "${rp_found}" != "${rp_expected}" ]; then
    # Reconstruct the repository from the reference. `${ref%%:*}` alone was
    # wrong twice over: on a digest-only reference (repo@sha256:...) it yields
    # `repo@sha256`, and on a registry with a port (localhost:5000/x) it yields
    # `localhost`. Strip the digest first, then a tag only if the LAST path
    # segment carries one.
    rp_ref=${rp_image%%@*}
    case "${rp_ref##*/}" in
      *:*) rp_repo=${rp_ref%:*} ;;
      *)   rp_repo=${rp_ref} ;;
    esac
    {
      printf '\n'
      printf 'FAIL: pinned-container version mismatch (Constitution IV).\n\n'
      printf '        image:      %s\n' "${rp_image}"
      printf '        expected:   %s      <- ci/versions.env\n' "${rp_expected}"
      printf '        reported:   %s\n\n' "${rp_found:-(image printed no parseable version)}"
      printf '        docker ran the image successfully, so this is genuine pin drift: the\n'
      printf '        digest and the version string in ci/versions.env describe different\n'
      printf '        releases. Re-resolve both together:\n\n'
      printf '            docker pull %s:%s%s\n' "${rp_repo}" "${rp_tag_prefix}" "${rp_expected}"
      printf '            docker inspect --format="{{index .RepoDigests 0}}" %s:%s%s\n\n' \
        "${rp_repo}" "${rp_tag_prefix}" "${rp_expected}"
    } >&2
    return 1
  fi
  printf '    %-12s %s  (%s)\n' "${rp_tool}" "${rp_found}" "${rp_image}"
}

require_gitleaks_image() {
  require_pinned_image gitleaks "${GITLEAKS_IMAGE}" "${GITLEAKS_VERSION}" v \
    gitleaks_reported_version version
}

require_shellcheck_image() {
  require_pinned_image shellcheck "${SHELLCHECK_IMAGE}" "${SHELLCHECK_VERSION}" v \
    shellcheck_reported_version --version
}

require_terraform_image() {
  require_pinned_image terraform "${TERRAFORM_IMAGE}" "${TERRAFORM_VERSION}" "" \
    terraform_reported_version -version
}

# =============================================================================
# Stage: gitleaks  (Constitution VII — a red gitleaks halts everything)
#
# TWO scans, both here, because CI used to inline the second one and a secret in
# an older commit was therefore invisible to every local check:
#   1. working tree  — catches a secret in a file that is not yet committed.
#   2. full history  — catches a secret in any commit ever made.
#
# Every precondition of scan 2 is asserted rather than assumed: git must exist,
# the directory must be a git repository, and the clone must be neither shallow
# nor partial. See require_git_history_is_scannable().
# =============================================================================

# ---------------------------------------------------------------------------
# `gitleaks git .` walks the commit graph it is given. Two kinds of incomplete
# clone make that walk worthless while looking exactly like a clean scan.
#
# SHALLOW (`git clone --depth 1`). One commit is present, so the scan succeeds,
# prints the same banner as a full scan, and exits 0. `git rev-parse --verify
# HEAD` succeeds there, so the NO-HISTORY branch below does NOT catch it.
#
# PARTIAL / BLOBLESS (`git clone --filter=blob:none`). Measured on git 2.52.0:
# `git rev-parse --is-shallow-repository` returns FALSE, so the shallow guard
# does not fire either. The blobs are fetched lazily from the promisor remote,
# which a scan container has no route or credentials to. Measured against the
# pinned gitleaks image, with the promisor unreachable, on a repository whose
# FIRST commit contained a secret that the later commit removed:
#
#     full clone     -> 2 commits scanned
#     blobless clone -> ERR ... could not fetch <oid> from promisor remote
#                       ERR error="stderr is not empty"
#                       INF 0 commits scanned.
#                       INF no leaks found
#                       exit 0
#
# Zero commits scanned, "no leaks found", exit 0. The discriminator is the
# clone's own config: a partial clone carries remote.<name>.promisor=true and
# remote.<name>.partialclonefilter=<filter>. (`git rev-parse
# --is-partial-clone` is NOT a real option on git 2.52 — rev-parse echoes the
# unrecognised argument back and exits 0, which would have made the guard
# always fire. Do not use it.)
#
# NO-HISTORY was also reachable when the directory was not a git repository at
# all, where its "this is a precondition check, not a suppressed failure"
# wording was simply false. Both states are now separated and named.
#
# .github/workflows/ci.yml sets `fetch-depth: 0` and uses no filter, so it is
# fine, but this script claims to reproduce CI exactly on any machine, so the
# guards belong here and not only in the workflow.
# ---------------------------------------------------------------------------
require_git_history_is_scannable() {
  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    {
      printf '\n'
      printf 'FAIL: %s is not a git repository, so the full-history secret scan\n' "${REPO_ROOT}"
      printf '      (Constitution VII) cannot run at all.\n\n'
      printf '        This is not the "no commits yet" case — there is no .git here. An\n'
      printf '        exported tarball or a copied directory cannot be gate-checked;\n'
      printf '        clone the repository instead:\n\n'
      printf '            git clone <remote> && cd <repo> && sh ci/checks.sh gitleaks\n\n'
    } >&2
    return 1
  fi

  if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
    {
      printf '\n'
      printf 'FAIL: shallow clone. The full-history secret scan would examine only the\n'
      printf '      commits present locally and pass vacuously (Constitution VII).\n\n'
      printf '        A shallow repository is indistinguishable from a clean one once the\n'
      printf '        scan has run: same banner, same exit 0, nothing scanned.\n\n'
      printf '        fix:\n'
      printf '            git fetch --unshallow\n\n'
      printf '        In CI, checkout must use fetch-depth: 0 — see\n'
      printf '        .github/workflows/ci.yml, job "gitleaks".\n\n'
    } >&2
    return 1
  fi

  # ---------------------------------------------------------------------------
  # ROUND 5 — does this guard also cover a BARE `git fetch --filter=` run
  # against an already-full clone, never having gone through
  # `git clone --filter=`? Raised in round 4 peer review, hedged as unverified
  # (no shell access that round). Tested directly in round 5 with a real git
  # sandbox: a bare origin with `uploadpack.allowFilter=true` (matching real
  # remotes such as GitHub), a full clone at commit 1, then
  # `git fetch --filter=blob:none origin master` to pull commit 2. Reproduced
  # twice (blob:none and blob:limit=1): the fetch AUTOMATICALLY sets
  # `remote.origin.promisor=true` and `remote.origin.partialclonefilter=<value>`
  # as a side effect of ANY fetch carrying `--filter`, regardless of whether the
  # repository was ever `clone --filter=`'d — this is git's documented
  # promisor-remote upgrade behaviour, not specific to `clone`. The
  # config-regexp check below WOULD fire on that state (grep exit 0, match
  # found) — CONFIRMED covered, not a gap.
  #
  # The narrower residual scenario also raised in round 4 — the operator
  # manually strips `remote.<name>.promisor`/`partialclonefilter` afterward
  # while genuinely-missing objects remain absent — is NOT guarded against, and
  # is accepted as residual risk rather than fixed here: it requires
  # deliberately removing git's own promisor safety markers while the objects
  # they describe are still missing, which contradicts git's own documented
  # recommended workflow (backfill missing objects BEFORE removing promisor
  # config). This comment exists so a future reader does not re-litigate either
  # question from scratch; no new guard logic was added for it.
  # ---------------------------------------------------------------------------
  if rgh_partial=$(git config --get-regexp \
    '^remote\..*\.(partialclonefilter|promisor)$' 2>/dev/null); then
    {
      printf '\n'
      printf 'FAIL: partial (blobless/treeless) clone. The full-history secret scan\n'
      printf '      would read filtered-out blobs from the promisor remote, which is not\n'
      printf '      reachable from a scan container, and pass vacuously.\n\n'
      printf '        Measured: 0 commits scanned, "no leaks found", exit 0 — while a\n'
      printf '        full clone of the same repository scanned every commit.\n'
      printf '        "git rev-parse --is-shallow-repository" returns false here, so the\n'
      printf '        shallow guard above does not catch this.\n\n'
      printf '        clone config that triggered this:\n'
      printf '%s\n' "${rgh_partial}" | sed 's/^/            /'
      printf '\n        fix:\n'
      printf '            re-clone without --filter, then re-run. In CI, do not set\n'
      printf '            actions/checkout "filter:"; keep "fetch-depth: 0".\n\n'
    } >&2
    return 1
  fi
}

# ---------------------------------------------------------------------------
# `gitleaks dir` walks the filesystem and knows nothing about .gitignore, so it
# reports files that are not in the repository and cannot leak through it. The
# measured case: .env.example tells the operator `cp .env.example .env`,
# .gitignore excludes .env (correctly), and the working-tree scan then flagged
# an innocuous `.env` containing only `NODE_A_LAN_IP=10.0.0.5` — training the
# operator to shrug at a red gitleaks, which is the one reflex Constitution VII
# cannot tolerate.
#
# Fix: exclude .gitignore'd paths from the WORKING-TREE walk only. The path
# rules that catch a committed .env / kubeconfig / tfstate keep their full force
# in the history scan and in the pre-commit staged scan, both of which see
# repository content exclusively — which is the only way such a file can leak.
#
# The exclusion list is DERIVED from `git ls-files` on every run, never
# hand-maintained, so it cannot drift out of step with .gitignore.
#
# HOW IT REACHES THE SCANNER, and why not `--config`. The overlay goes in via
# GITLEAKS_CONFIG_TOML and pulls the real ruleset in with `[extend] path`.
# Measured against the pinned image: passing `--config ci/gitleaks.toml` on the
# same command line makes gitleaks IGNORE GITLEAKS_CONFIG_TOML completely — the
# flag wins, the derived allowlist is silently dropped, and the .env false
# positive comes straight back. So the working-tree scan must NOT carry
# `--config`; the history scan, which uses no overlay, does.
#
# The chain is two levels deep (overlay -> ci/gitleaks.toml -> useDefault).
# Measured on the pinned image: both the custom rules AND the ~150 default rules
# survive it. (Collapsing the chain by putting `useDefault = true` in the
# overlay is NOT an option: gitleaks v8.30.1 refuses a config that sets both
# extend.path and extend.useDefault — "unable to load config due to extend.path
# and extend.useDefault being set".) tests/unit/test_gitleaks_positive_control.py
# re-measures both facts against the pinned image.
#
# FIVE THINGS THIS FUNCTION MUST GET RIGHT, all of them previously wrong:
#
#  1. EMPTY LIST. `paths = [` `]` with nothing between them is not a valid
#     gitleaks allowlist: it loads with
#     `FTL Failed to load config error="[[allowlists]] must contain at least one
#     check for: commits, paths, regexes, or stopwords"`, exit 1, naming a
#     config file that exists nowhere on disk. The list is empty exactly when
#     nothing is gitignored — which is the normal state of the CI gitleaks job,
#     since it does a fresh checkout and never installs Python or runs pytest,
#     so no tool caches exist. The gate was therefore dead in CI while passing
#     locally. The whole [[allowlists]] block is now omitted when the list is
#     empty.
#
#  2. TOML INJECTION. Paths used to be emitted inside `'''...'''` literal
#     strings, and the escaping pass covered RE2 metacharacters but not the
#     delimiter itself. A gitignored file named  x''', '''  closed the literal
#     and opened another, yielding two valid patterns `^x` and `$`. Go's regexp
#     matches `$` against every input, so the ENTIRE walk was pruned: 0 leaks,
#     exit 0, silent. Paths are now emitted as TOML BASIC strings with `\` and
#     `"` escaped, which no filename can escape from.
#
#  3. GIT PATH QUOTING. With the default core.quotePath=true, `git ls-files`
#     renders a non-ASCII name as  "notes-caf\303\251.env"  — quotes, octal
#     escapes and all. The escaping pass then escaped those backslashes, the
#     pattern matched nothing, the file stayed in the walk and reddened the
#     scan. Behaviour therefore depended on an unpinned local git setting. `-z`
#     turns quoting off entirely and emits raw bytes.
#
#  4. NEWLINES IN PATHS. `-z` records are split with `tr`, which cannot
#     represent a filename containing a literal newline; that case is detected
#     and fails loudly rather than silently emitting two broken patterns.
#
#  5. CONTROL CHARACTERS AND INVALID UTF-8. Bytes U+0001-U+0008, U+000B-U+001F
#     and U+007F are legal in a POSIX filename and ILLEGAL raw in a TOML basic
#     string (tab is the sole exception TOML allows); a TOML document must also
#     be valid UTF-8. Either one produced `FTL Failed to load config` against a
#     path that exists nowhere on disk — the same unreadable failure as (1), in
#     a narrower form. LC_ALL=C keeps sed byte-oriented, which stops SED from
#     erroring, but says nothing about whether the RESULT is loadable TOML; the
#     comment that used to claim otherwise was wrong. Both are now detected up
#     front and named.
# ---------------------------------------------------------------------------
overlay_ignored_paths() {
  git ls-files -z --others --ignored --exclude-standard --directory
}

# True when every NUL record from <producer> survives translation to one line
# per record — i.e. no path contains a literal newline. Takes the producer's
# NAME because it has to run it twice; `tr` cannot round-trip an embedded
# newline, so counting after translation is the only way to detect one.
nul_records_survive_newline_translation() {
  nr_producer="$1"
  nr_records=$("${nr_producer}" | tr -dc '\000' | wc -c | tr -d '[:space:]')
  nr_lines=$("${nr_producer}" | tr '\000' '\n' | wc -l | tr -d '[:space:]')
  [ "${nr_records}" = "${nr_lines}" ]
}

worktree_overlay_config() {
  if ! nul_records_survive_newline_translation overlay_ignored_paths; then
    {
      printf 'FAIL: a .gitignore-excluded path contains a newline character.\n'
      printf '      ci/checks.sh cannot build a safe working-tree exclusion for it and\n'
      printf '      will not guess. Rename the file, or delete it:\n'
      printf '          git ls-files -z --others --ignored --exclude-standard | tr "\\0" "\\n"\n'
    } >&2
    return 1
  fi

  # Control characters other than tab. Legal in a POSIX filename, illegal raw
  # inside a TOML basic string, and the resulting parse error names a config
  # file that exists nowhere.
  wo_control=$(overlay_ignored_paths | LC_ALL=C tr -dc '\001-\010\013-\037\177' |
    wc -c | tr -d '[:space:]')
  if [ "${wo_control}" != "0" ]; then
    {
      printf 'FAIL: a .gitignore-excluded path contains %s control character(s)\n' "${wo_control}"
      printf '      (U+0001-U+0008, U+000B-U+001F or U+007F). TOML basic strings allow no\n'
      printf '      raw control character except tab, so the generated overlay would fail\n'
      printf '      to parse and gitleaks would report the error against a config file\n'
      printf '      that exists nowhere on disk. Rename the file, or delete it:\n'
      printf '          git ls-files -z --others --ignored --exclude-standard | cat -v\n'
    } >&2
    return 1
  fi

  # A TOML document must be valid UTF-8. iconv is the only POSIX-ish validator
  # available without adding a dependency to the stage that is supposed to have
  # the fewest; when it is absent the check is skipped and says so, because a
  # silently-skipped check is how (1) survived for a release.
  if command -v iconv >/dev/null 2>&1; then
    if ! overlay_ignored_paths | tr '\000' '\n' | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1; then
      {
        printf 'FAIL: a .gitignore-excluded path is not valid UTF-8. A TOML document must\n'
        printf '      be, so the generated overlay would fail to parse and gitleaks would\n'
        printf '      report the error against a config file that exists nowhere on disk.\n'
        printf '      Rename the file, or delete it:\n'
        printf '          git ls-files -z --others --ignored --exclude-standard | cat -v\n'
      } >&2
      return 1
    fi
  else
    printf 'NOTE: iconv is not on PATH, so the overlay is not UTF-8 validated. A\n' >&2
    printf '      non-UTF-8 gitignored filename would surface as an unhelpful gitleaks\n' >&2
    printf '      config parse error rather than as the message above.\n' >&2
  fi

  printf '[extend]\npath = "ci/gitleaks.toml"\n'

  # RE2-escape, then TOML-basic-escape. Order matters: the second pass doubles
  # the backslashes the first pass added, and the TOML parser hands RE2 back
  # exactly one. LC_ALL=C keeps sed byte-oriented so an undecodable filename
  # cannot abort sed itself; whether the RESULT is loadable is the business of
  # the two checks above, not of the locale.
  wo_paths=$(overlay_ignored_paths | tr '\000' '\n' |
    while IFS= read -r wo_path; do
      [ -n "${wo_path}" ] || continue
      wo_escaped=$(printf '%s' "${wo_path}" |
        LC_ALL=C sed -e 's/[][^$.|?*+(){}\\]/\\&/g' -e 's/\\/\\\\/g' -e 's/"/\\"/g')
      case "${wo_path}" in
        */) printf '  "^%s",\n' "${wo_escaped}" ;;
        *) printf '  "^%s$",\n' "${wo_escaped}" ;;
      esac
    done)

  if [ -n "${wo_paths}" ]; then
    printf '\n[[allowlists]]\n'
    printf 'description = "generated by ci/checks.sh: .gitignore-excluded paths are not repository content"\n'
    printf 'paths = [\n%s\n]\n' "${wo_paths}"
  fi
}

stage_gitleaks() {
  preflight gitleaks
  log "gitleaks — ${GITLEAKS_IMAGE}"
  docker_or_fail "the Constitution VII secret gate runs as a pinned container"
  git_or_fail "the Constitution VII secret gate derives the working-tree scan's exclusions from git ls-files and scans the full commit graph"
  require_gitleaks_image
  require_git_history_is_scannable

  # STANDALONE ASSIGNMENT, not an argument. `set -e` does NOT abort on a failing
  # command substitution used as a WORD of a simple command, so the previous
  # form —
  #     docker run ... -e GITLEAKS_CONFIG_TOML="$(worktree_overlay_config)" ...
  # — printed the overlay's FAIL diagnostic to stderr and then ran the scan
  # anyway with an EMPTY config. Empty means gitleaks falls back to its embedded
  # default ruleset, which silently loses all six custom orbital-drift rules;
  # the four path-based ones (terraform state, tfvars, kubeconfig, dotenv) have
  # no default equivalent at all, so a committed kubeconfig or .tfstate passed
  # with exit 0 and a banner identical to a clean scan. Measured: with the
  # config, RuleID orbital-drift-airflow-fernet-key; with an empty config,
  # RuleID generic-api-key.
  #
  # Hoisting it to its own assignment makes `set -e` fire; the emptiness check
  # is the belt to that braces, since the overlay always emits at least the
  # [extend] block.
  gitleaks_overlay=$(worktree_overlay_config)
  if [ -z "${gitleaks_overlay}" ]; then
    {
      printf '\n'
      printf 'FAIL: the generated gitleaks working-tree overlay is empty.\n\n'
      printf '        Running the scan with an empty GITLEAKS_CONFIG_TOML would fall back\n'
      printf '        to the embedded default ruleset and silently drop every rule in\n'
      printf '        ci/gitleaks.toml, including the path rules for terraform state,\n'
      printf '        tfvars, kubeconfig and .env — which have no default equivalent.\n'
      printf '        Refusing to scan (Constitution VII).\n\n'
      printf '        Reproduce with:  DEBUG=1 sh ci/checks.sh gitleaks\n\n'
    } >&2
    return 1
  fi

  if [ -n "${DEBUG:-}" ]; then
    {
      printf '\n--- generated gitleaks overlay (DEBUG=1) ---\n'
      printf '%s\n' "${gitleaks_overlay}"
      printf '%s\n' '--- end overlay ---'
    } >&2
  fi

  printf '\n--- working tree (.gitignore-excluded paths omitted) ---\n'
  # No `--config` here, deliberately: it would override GITLEAKS_CONFIG_TOML and
  # discard the derived exclusions. See worktree_overlay_config's header.
  docker_run --rm \
    -e GITLEAKS_CONFIG_TOML="${gitleaks_overlay}" \
    -v "${REPO_ROOT}:/repo:ro" \
    -w /repo \
    "${GITLEAKS_IMAGE}" \
    dir . --redact --verbose --no-banner

  printf '\n--- full git history ---\n'
  if git rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
    # `safe.directory` via env: the container runs as root and the bind-mounted
    # repo is owned by the host user, which git refuses to read without this.
    docker_run --rm \
      -e GIT_CONFIG_COUNT=1 \
      -e GIT_CONFIG_KEY_0=safe.directory \
      -e GIT_CONFIG_VALUE_0=/repo \
      -v "${REPO_ROOT}:/repo:ro" \
      -w /repo \
      "${GITLEAKS_IMAGE}" \
      git . --config ci/gitleaks.toml --redact --verbose --no-banner
  else
    printf 'NO-HISTORY: this is a git repository (checked), not shallow (checked) and\n'
    printf '            not a partial clone (checked), but it has no commits yet, so\n'
    printf '            there is no history to scan. This branch disappears permanently\n'
    printf '            after the first commit.\n'
  fi
}

# =============================================================================
# Stage: hooks  (pre-commit run --all-files)
#
# NOT one of FR-011's six gates — those are the six above. This stage exists
# because without it .pre-commit-config.yaml has zero CI enforcement:
# detect-private-key, check-added-large-files, mixed-line-ending and the
# check-yaml/toml/json hooks only ever run on a machine where somebody ran
# `pre-commit install`, and a single `git commit --no-verify` bypasses all of
# them with CI still green.
#
# --hook-stage manual, NOT the default pre-commit stage. Measured with
# pre-commit 4.6.1: `pre-commit run --all-files` defaults to hook-stage
# `pre-commit`, so a hook declaring `stages: [pre-commit]` still runs — the
# gitleaks hook therefore executed `gitleaks git --pre-commit --staged` against
# an empty index (nothing is ever staged here: the index is empty before the
# first commit, and CI runs on a clean checkout) and reported
# "gitleaks (pinned container, staged diff).....Passed" having read zero bytes.
# A green gitleaks line that means nothing is precisely the reflex Constitution
# VII cannot afford. Hooks with no `stages:` key run in every stage, so
# `--hook-stage manual` drops exactly that one hook and keeps the other 14.
#
# CONSEQUENCE, stated plainly: the gitleaks HOOK does not run in this stage, so
# this stage does not assert the gitleaks image and does not print its version —
# printing it here implied an enforcement that was not happening. The hook's own
# behaviour is covered by tests/unit/test_gitleaks_positive_control.py, which
# runs its exact entry line against a synthetic staged index and asserts the
# custom rule fires; the image pin it references is asserted against
# ci/versions.env by tests/unit/test_version_pins.py and exercised for real by
# `sh ci/checks.sh gitleaks`.
#
# tests/unit/test_ci_contract.py asserts, by RUNNING pre-commit rather than by
# reading this repo's config, that the set of hooks which actually execute at
# --hook-stage manual is exactly the configured set minus gitleaks. Reading the
# local config is not enough: the stage filter applies to the MERGED definition,
# so a `rev:` bump in which upstream narrows a hook's `stages` would drop it
# from CI with a config-only test still green.
# =============================================================================

hooks_untracked_paths() {
  git ls-files -z --others --exclude-standard
}

stage_hooks() {
  preflight hooks

  # SKIP is pre-commit's documented escape hatch and it is not available here.
  # `SKIP=gitleaks,shellcheck sh ci/checks.sh hooks` printed "Skipped" for both
  # and exited 0: .pre-commit-config.yaml names SKIP=gitleaks as the exact
  # reflex that must never be trained, and shellcheck has no other invocation
  # anywhere, so skipping it removes the only lint on this file — POSIX sh
  # destined for dash on node A. PRE_COMMIT_ALLOW_NO_CONFIG is neutralised for
  # the same reason: it turns a missing config into a pass.
  #
  # tests/unit/test_checks_sh_behaviour.py asserts this BEHAVIOURALLY — it runs
  # the stage with SKIP set and a stubbed pre-commit that records the
  # environment it was handed — because a source-level `unset SKIP` grep is
  # satisfied by `_saved=$SKIP; unset SKIP; export SKIP=$_saved`.
  if [ -n "${SKIP:-}" ]; then
    printf 'NOTE: ignoring SKIP=%s — this stage is a gate and has no opt-out.\n' "${SKIP}"
  fi
  unset SKIP
  unset PRE_COMMIT_ALLOW_NO_CONFIG

  log "hooks — pre-commit ${PRE_COMMIT_VERSION} (pre-commit run --all-files)"

  # Two hooks in the config that run at this stage are `language: docker_image`
  # (shellcheck, terraform-fmt), so the daemon is a hard requirement and both
  # pins are assertable.
  #
  # ROUND 10 — this stage's guard had the SAME binary-only weakness stage_unit's
  # did, but not the same consequence: require_shellcheck_image two lines below
  # runs a container, fails closed, and routes through docker_error_cause, so a
  # stopped daemon already stopped this stage before pre-commit ran. What it got
  # wrong was the DIAGNOSIS — the message blamed the pinned container image
  # itself and (on Windows) classified the named-pipe error as an
  # `unrecognised docker failure`. docker_or_fail now catches it one step
  # earlier and says "the Docker daemon is not reachable" instead.
  #
  # NOTE: do not let a wrapped comment line in this file begin with the token
  # pair `#` + `shellcheck` — shellcheck parses that as a directive and fails
  # with SC1072/SC1073. This exact comment tripped it once.
  docker_or_fail "the shellcheck and terraform-fmt pre-commit hooks are language: docker_image"
  git_or_fail "this stage builds pre-commit's --files argument from git ls-files (tracked) and git ls-files --others --exclude-standard (untracked)"
  require_shellcheck_image
  require_terraform_image

  hk_tracked=$(git ls-files)
  if [ -n "${hk_tracked}" ]; then
    "${PYTHON}" -m pre_commit run --all-files --hook-stage manual
    return 0
  fi

  printf 'NOTE: nothing is tracked yet (pre-first-commit scaffold). --all-files means\n'
  printf '      "git ls-files", which is empty here, so every hook would report "no files\n'
  printf '      to check" — a vacuous pass. Running against the untracked-but-not-ignored\n'
  printf '      set instead; once the scaffold is committed the two sets are identical.\n\n'

  # Built with `set --` rather than `xargs -0`: `-0` is a GNU/BSD extension, not
  # POSIX, and node A's /bin/sh is dash. GNU xargs without `-r` also invokes the
  # command once with zero arguments when its input is empty, which here meant
  # pre-commit running with no files, every hook reporting "no files to check"
  # and the stage exiting 0 having verified nothing — the exact vacuous pass
  # this branch exists to avoid.
  if ! nul_records_survive_newline_translation hooks_untracked_paths; then
    {
      printf 'FAIL: an untracked, non-ignored path contains a newline character, which\n'
      printf '      cannot be passed to pre-commit safely from POSIX sh. Rename it:\n'
      printf '          git ls-files -z --others --exclude-standard | cat -v\n'
    } >&2
    return 1
  fi

  set --
  while IFS= read -r hk_path; do
    [ -n "${hk_path}" ] || continue
    set -- "$@" "${hk_path}"
  done <<EOF
$(hooks_untracked_paths | tr '\000' '\n')
EOF

  if [ "$#" -eq 0 ]; then
    {
      printf 'FAIL: nothing is tracked AND nothing is untracked-but-not-ignored, so this\n'
      printf '      stage has no files to hand pre-commit. Every hook would report "no\n'
      printf '      files to check" and the stage would exit 0 having verified nothing.\n'
      printf '      Refusing to report a vacuous pass (Constitution V).\n'
    } >&2
    return 1
  fi

  "${PYTHON}" -m pre_commit run --hook-stage manual --files "$@"
}

# -----------------------------------------------------------------------------
# Stage: dead  (vulture dead-code scan; adopt-governance-kit design D3)
#
# Scope and confidence floor live in pyproject [tool.vulture] — the config,
# like every other gate bar, has exactly one home.
# -----------------------------------------------------------------------------
stage_dead() {
  preflight dead
  log "dead-code — vulture ${VULTURE_VERSION}"
  "${PYTHON}" -m vulture
}

# -----------------------------------------------------------------------------
# Stage: audit  (pip-audit dependency vulnerability scan; design D3)
#
# Ignoring an advisory requires a named `--ignore-vuln ID` argument added HERE
# with a comment citing the advisory and the reason — never a blanket flag.
# -----------------------------------------------------------------------------
stage_audit() {
  preflight audit
  log "audit — pip-audit ${PIP_AUDIT_VERSION}"
  "${PYTHON}" -m pip_audit
}

# -----------------------------------------------------------------------------
# Stage: specs  (OpenSpec structural validation; design D13)
#
# ci/validate_specs.sh is the SOLE implementation — deterministic, identical
# locally and in CI, no optional-CLI branch. It needs no Python, so this stage
# declares no pins (same rationale as gitleaks).
# -----------------------------------------------------------------------------
stage_specs() {
  preflight specs
  log "specs — OpenSpec structural validation (ci/validate_specs.sh)"
  sh "${SCRIPT_DIR}/validate_specs.sh"
}

# -----------------------------------------------------------------------------
# Stage: governance  (the meta-tests that watch the PROCESS)
#
# stage_coverage's bare `pytest tests` already collects tests/governance/ as
# part of the whole-tree run its coverage number is measured over, but that
# gives a governance regression no dedicated job of its own: an operator
# reading a red `coverage` job reasonably assumes the FR-011a threshold, not
# the PreToolUse guard verdicts, the zero-skip guard or the skill-freshness
# check. This stage runs the same directory again, on its own, so a
# guard/meta-test regression reddens a job actually named `governance`.
# -----------------------------------------------------------------------------
stage_governance() {
  preflight governance
  log "governance — pytest ${PYTEST_VERSION} (tests/governance)"
  git_or_fail "the governance meta-tests enumerate tracked paths via git ls-files"
  pytest_suite tests/governance "governance suite"
}

# -----------------------------------------------------------------------------
# Stage: traceability  (requirement-traceability matrix lint; design D3)
#
# Claims the pytest pin because the linter shells out to
# `pytest --collect-only` to verify Green rows' node ids really collect.
# -----------------------------------------------------------------------------
stage_traceability() {
  preflight traceability
  log "traceability — orbital_drift.traceability (matrix lint)"
  "${PYTHON}" -m orbital_drift.traceability --json
}

# -----------------------------------------------------------------------------
# Stage: projections  (generated-planning drift check; design D3/D9)
#
# planning/roadmap.md and planning/jira-import.csv must byte-match what
# orbital_drift.projections emits from roadmap_data.py. Pure-stdlib module, so
# no pinned tool to claim (same rationale as specs).
# -----------------------------------------------------------------------------
stage_projections() {
  preflight projections
  log "projections — orbital_drift.projections --check (byte-drift vs roadmap_data.py)"
  "${PYTHON}" -m orbital_drift.projections --check --json
}

stage_all() {
  preflight all
  # The six FR-011 gates, in order...
  stage_lint
  stage_typecheck
  stage_unit
  stage_contract
  stage_smoke
  # ...then FR-011a's coverage gate. After the per-suite stages because it must
  # not replace them (see stage_coverage's header), and before gitleaks/hooks so
  # the pytest stages stay contiguous. This is the step that makes `all` run
  # tests/unit a second time — accepted, docs/decisions/001-coverage-gate.md D-06.
  stage_coverage
  stage_gitleaks
  # ...the adopt-governance-kit gates (design D3; not part of FR-011's six,
  # they extend the same contract)...
  stage_dead
  stage_audit
  stage_specs
  stage_traceability
  stage_projections
  stage_governance
  # ...then the hook enforcement stage. Last, because pre-commit hooks may
  # rewrite files (end-of-file-fixer, trailing-whitespace, ruff --fix) and must
  # not be able to influence a gate that already ran. That rewriting is also why
  # a failed `all` can leave the tree dirty — see README.md.
  stage_hooks
  log "all stages passed"
}

# The labels below are a contract with .github/workflows/ci.yml: the matrix
# there plus {gitleaks, hooks, all} must equal this set exactly, or a stage can
# be added to stage_all and never run in CI. They must also equal STAGE_LABELS,
# which stage_python_pins is iterated over.
# tests/unit/test_ci_contract.py asserts both.
case "${1:-all}" in
  lint)      stage_lint ;;
  typecheck) stage_typecheck ;;
  unit)      stage_unit ;;
  contract)  stage_contract ;;
  smoke)     stage_smoke ;;
  coverage)  stage_coverage ;;
  gitleaks)  stage_gitleaks ;;
  hooks)     stage_hooks ;;
  dead)      stage_dead ;;
  audit)     stage_audit ;;
  specs)     stage_specs ;;
  traceability) stage_traceability ;;
  projections)  stage_projections ;;
  governance)   stage_governance ;;
  all)       stage_all ;;
  *)
    printf 'unknown stage: %s\n' "$1" >&2
    printf 'usage: sh ci/checks.sh <%s>\n' "$(printf '%s' "${STAGE_LABELS}" | tr ' ' '|')" >&2
    exit 2
    ;;
esac
