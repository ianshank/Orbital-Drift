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

changes_root="openspec/changes"

if [ ! -d "${changes_root}" ]; then
  printf 'specs: %s does not exist — nothing to validate.\n' "${changes_root}"
  exit 0
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
  printf 'specs: %s holds no change packages — nothing to validate.\n' "${changes_root}"
fi

[ "${fail}" = "0" ] || exit 1
printf 'specs: all change packages structurally valid.\n'
