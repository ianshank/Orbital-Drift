#!/bin/sh
# =============================================================================
# Orbital-Drift — OpenSpec structural validator (adopt-governance-kit D13).
#
# DETERMINISTIC BY DESIGN: this is the SOLE implementation of the `specs` gate.
# It applies the same checks locally and in CI, with no optional-tool branch —
# gate strength must never depend on what happens to be installed
# (test_ci_contract.py's whole architecture, and the exact green-locally/
# red-in-CI divergence ci/versions.env's header warns about). Adopting the
# OpenSpec Node CLI later is its own decision (design D13) and would replace,
# not conditionally augment, this file.
#
# What it asserts, per openspec/changes/<id>/:
#   0. openspec/changes exists and holds at least one change package.
#   1. proposal.md, design.md, tasks.md all exist.
#   2. Every specs/**/spec.md contains at least one "### Requirement:" heading.
#   3. Every "### Requirement:" block contains at least one "#### Scenario:".
#   4. Every "#### Scenario:" block contains a **WHEN** and a **THEN** line.
# Malformed -> exit 1 naming the file and the defect. No opt-out.
#
# POSIX sh (dash-compatible), same as ci/checks.sh; awk does the block parsing
# because it is already an unconditional dependency of the gate harness.
# =============================================================================
set -u

fail=0

err() {
  printf 'specs: %s\n' "$*" >&2
  fail=1
}

# =============================================================================
# THE CHANGE ROOT IS RESOLVED FROM THIS SCRIPT'S OWN LOCATION, NEVER THE CWD.
#
# This was `changes_root="openspec/changes"` — a relative literal — followed by
# `exit 0` with "nothing to validate" when it was absent. Measured consequence,
# and it is this repository's own recorded one: scripts/_lib.sh:15-17 cites THIS
# FILE as the instance ("ci/validate_specs.sh was measured green-lighting from
# /tmp because it trusted a relative literal"). The reachable path is DIRECT
# invocation — `sh ci/validate_specs.sh` from anywhere but the repo root, which
# is exactly the invocation _lib.sh records: it exited 0 having read no file.
#
# WHAT WAS NOT AFFECTED, stated because overstating it would misdirect the next
# reader: the GATE path was sound. ci/checks.sh does `cd "${REPO_ROOT}"` before
# it dispatches, so `sh ci/checks.sh specs` validated the real packages from any
# cwd — re-measured against the pre-fix file from /tmp and from /home/user, both
# rc 0 with "all change packages structurally valid". checks.sh's own `cd`
# MASKED this defect rather than inheriting it, which is why it survived: the
# caller everybody exercises could not reach it. That is not a reason to leave
# it — a script whose correctness depends on a `cd` in its one caller is one
# refactor away from the fail-open, and the missing/empty branches below were
# reachable from the gate path regardless (a partial checkout has no
# openspec/changes to `cd` into).
#
# Two independent defects, fixed together because either alone still fails open:
# resolving the root correctly (below) and refusing to pass when it is missing
# or empty (both branches now exit 1). Every sibling gate in ci/checks.sh already
# behaves that way — pytest_suite() discriminates "declared but unauthored" from
# "collected nothing because something is broken" rather than passing on the
# ambiguity.
#
# The resolution idiom is copied verbatim from ci/checks.sh's SCRIPT_DIR block
# (same file header, same reasoning): POSIX parameter expansion rather than
# `dirname`, because an external command here would fail before this script can
# print its own diagnostic on a minimal PATH; both separators, because a caller
# on the Windows authoring box can hand this script a native `$0`; and
# `CDPATH='' cd -- ... && pwd` to normalise a relative path without an
# operator's own CDPATH redirecting it. See ci/checks.sh's "SCRIPT_DIR, resolved
# with NO external command" block for the full derivation, including the two
# cases a bare `${0%/*}` gets wrong; it is deliberately not duplicated here.
#
# SYMLINKS ARE NOT RESOLVED, and that is inherited, not overlooked: POSIX
# `cd`+`pwd` is LOGICAL, so invoking this through `/usr/local/bin/vs ->
# /repo/ci/validate_specs.sh` yields repo_root=/usr/local and the script exits 1
# naming /usr/local/openspec/changes. Loud and closed — the safe direction, and
# byte-identical to what ci/checks.sh does under the same invocation, so the two
# cannot disagree about which tree they are gating.
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

script_dir=$(CDPATH='' cd -- "${self_dir}" && pwd)
repo_root=$(CDPATH='' cd -- "${script_dir}/.." && pwd)

changes_root="${repo_root}/openspec/changes"

if [ ! -d "${changes_root}" ]; then
  {
    printf 'specs: %s does not exist.\n' "${changes_root}"
    printf 'specs: refusing to report a green OpenSpec gate over a tree this script\n'
    printf '       never read. If the change packages moved, this validator moved with\n'
    printf '       them or it is being run against the wrong checkout.\n'
  } >&2
  exit 1
fi

found_any=0
for change_dir in "${changes_root}"/*/; do
  [ -d "${change_dir}" ] || continue
  found_any=1
  change_dir="${change_dir%/}"

  for required in proposal.md design.md tasks.md; do
    [ -f "${change_dir}/${required}" ] || err "${change_dir} is missing ${required}"
  done

  # Spec deltas are optional per change, but every one present must be valid.
  # Fixed-depth glob, not find: the OpenSpec layout is exactly
  # specs/<capability>/spec.md, a glob keeps the loop in THIS shell (a
  # `find | while` subshell would silently drop `fail=1`), and shellcheck
  # SC2044 rightly objects to iterating find output.
  for spec in "${change_dir}"/specs/*/spec.md; do
    [ -f "${spec}" ] || continue
      awk -v spec="${spec}" '
        BEGIN { requirements = 0; scenarios = 0; when = 0; then_seen = 0; bad = 0 }
        function close_scenario() {
          if (in_scenario && (!when || !then_seen)) {
            printf "specs: %s: scenario %s lacks a **WHEN** and/or **THEN** line\n", \
              spec, scenario_name > "/dev/stderr"
            bad = 1
          }
          in_scenario = 0; when = 0; then_seen = 0
        }
        function close_requirement() {
          close_scenario()
          if (in_requirement && req_scenarios == 0) {
            printf "specs: %s: requirement %s has no #### Scenario: block\n", \
              spec, req_name > "/dev/stderr"
            bad = 1
          }
          in_requirement = 0; req_scenarios = 0
        }
        /^### Requirement:/ {
          close_requirement()
          in_requirement = 1; requirements += 1; req_name = substr($0, 18)
          next
        }
        /^#### Scenario:/ {
          close_scenario()
          if (!in_requirement) {
            printf "specs: %s: scenario outside any requirement: %s\n", spec, $0 > "/dev/stderr"
            bad = 1
          }
          in_scenario = 1; scenarios += 1; req_scenarios += 1
          scenario_name = substr($0, 15)
          next
        }
        /\*\*WHEN\*\*/ { if (in_scenario) when = 1 }
        /\*\*THEN\*\*/ { if (in_scenario) then_seen = 1 }
        END {
          close_requirement()
          if (requirements == 0) {
            printf "specs: %s contains no ### Requirement: heading\n", spec > "/dev/stderr"
            bad = 1
          }
          exit bad
        }
      ' "${spec}" || err "${spec} failed structural validation (details above)"
  done
done

if [ "${found_any}" = "0" ]; then
  {
    printf 'specs: %s holds no change packages.\n' "${changes_root}"
    printf 'specs: an empty change root is a broken checkout, not a clean bill of\n'
    printf '       health. This branch used to fall through to the success line below,\n'
    printf '       announcing every change package valid over zero of them.\n'
  } >&2
  exit 1
fi

[ "${fail}" = "0" ] || exit 1
printf 'specs: all change packages structurally valid.\n'
