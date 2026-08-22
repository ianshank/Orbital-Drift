#!/usr/bin/env bash
# Shared helpers for the governance shell scripts (adopt-governance-kit).
#
# WHY THIS EXISTS: venv-Python discovery was triplicated across
# pretooluse_guard.sh, pre_push_scan.sh and session_start_check.sh with THREE
# different probe orders and three different PATH fallbacks — so a box with
# only `python` on PATH was blocked from pushing by one script while another
# proceeded happily. One home, three call sites; the differing POLICY on
# failure (block / warn / defer) stays at the call site, because that part is
# legitimately per-script.
#
# Sourced, never executed. Every function is prefixed `od_` to keep the
# caller's namespace clean.

# Absolute path to this repository's root, resolved from this file's location
# (so it is correct regardless of the caller's cwd — ci/validate_specs.sh was
# measured green-lighting from /tmp because it trusted a relative literal).
od_repo_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  (cd "${here}/.." && pwd)
}

# Echo the interpreter to use, or nothing when none is usable.
#
# Probe order is deliberate and identical for every caller: the project venv
# first (both layouts — probing only .venv/bin/python on Windows fell through
# to a CRLF-emitting interpreter and refused every push), then PATH.
od_find_python() {
  local root="$1" candidate
  for candidate in "${root}/.venv/bin/python" "${root}/.venv/Scripts/python.exe"; do
    if [ -x "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return 0
    fi
  done
  return 1
}

# Is orbital_drift importable by this interpreter?
#
# Callers need this to tell "package missing" from a real policy verdict:
# `python -m orbital_drift.remotes` exits 1 for BOTH "not allowlisted" and
# ModuleNotFoundError, which made a missing editable install look like a
# confident C-5 accusation and sent the operator to edit the allowlist.
od_package_importable() {
  "$1" -c 'import orbital_drift' >/dev/null 2>&1
}

# Path to the remote allowlist (charter C-5, DEC-003). One home, not two.
od_allowlist_path() {
  printf '%s\n' "$1/.claude/allowed-remotes.txt"
}
