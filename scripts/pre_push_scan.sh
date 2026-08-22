#!/usr/bin/env bash
# Orbital-Drift — native git pre-push hook: the AUTHORITATIVE destination gate
# for charter C-5. Installed by scripts/install_hooks.sh. git invokes this as:
#   pre-push <remote-name> <remote-url>
# with one line per pushed ref on stdin: <local-ref> <local-sha> <remote-ref> <remote-sha>
#
# GATE vs REPORT (donor-kit rule, kept):
# - GATE (allowlist check): unconditional; EVERY error path fails CLOSED with a
#   BLOCKED message naming the reason AND a remediation.
# - REPORT (which governed paths ride this push): non-gating; errors warn
#   LOUDLY on stderr and continue — a silent no-op scan rots while looking
#   installed.
# One normalizer: the URL check delegates to orbital_drift.remotes — the same
# module the PreToolUse guard uses; never a second implementation.
# Limits: `git push --no-verify` skips this hook, and a clone the installer
# never touched doesn't have it. CI and the PreToolUse guard are the layers
# that catch those.
set -u

# Source the shared helpers. When this file is INSTALLED as .git/hooks/pre-push
# the sibling lib is not alongside it, so fall back to the work tree; fail
# closed if neither is reachable (an interpreter we cannot resolve is a gate we
# cannot run).
_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$_lib_dir/_lib.sh" ]; then
  # shellcheck source=scripts/_lib.sh
  . "$_lib_dir/_lib.sh"
else
  _worktree="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$_worktree" ] && [ -f "$_worktree/scripts/_lib.sh" ]; then
    # shellcheck source=scripts/_lib.sh
    . "$_worktree/scripts/_lib.sh"
  else
    echo "BLOCKED (charter C-5): scripts/_lib.sh is missing; cannot resolve an interpreter" >&2
    exit 1
  fi
fi

block() {
  echo "BLOCKED (charter C-5): $1" >&2
  echo "Remediation: $2" >&2
  exit 1
}

[ "$#" -ge 2 ] || block \
  "pre-push hook invoked with $# argument(s); expected <remote-name> <remote-url>" \
  "invoke via a normal git push so git supplies the remote name and URL (fail closed)"

REMOTE_NAME="$1"; REMOTE_URL="$2"
REFS="$(cat 2>/dev/null || true)"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || ROOT=""
[ -n "$ROOT" ] || block "cannot resolve the repository root" "run the push from inside a git work tree"

ALLOWLIST="$(od_allowlist_path "$ROOT")"
[ -f "$ALLOWLIST" ] || block \
  "allowlist $ALLOWLIST is missing (the allowlist is the gate's ground truth)" \
  "restore it from version control before pushing (DEC-003)"

# Interpreter resolution — fail closed, never fail open; BOTH venv layouts
# (probing only .venv/bin/python fell through to a CRLF-emitting interpreter on
# Windows and refused every push — donor kit RB-023).
# One shared probe (scripts/_lib.sh) so this hook, the PreToolUse guard and the
# SessionStart check cannot disagree about which interpreter exists; three
# divergent copies previously meant a box with only `python` on PATH was
# blocked from pushing here while the guard proceeded happily.
PY="$(od_find_python "$ROOT" || true)"
[ -n "$PY" ] || block "no Python interpreter available for the allowlist check" \
      "create the environment (python -m pip install -e \".[dev]\") and retry"

# Distinguish "package not installed" from a real C-5 verdict BEFORE reading
# exit 1 as "not allowlisted" -- `python -m` exits 1 for both, so a broken venv
# produced a confident allowlist accusation and sent the operator to edit the
# allowlist instead of to `pip install -e ".[dev]"`.
od_package_importable "$PY" || block \
  "the orbital_drift package is not importable by $PY, so the allowlist check cannot run" \
  "python -m pip install -e \".[dev]\" and retry (this is NOT an allowlist verdict)"

# ── GATE: allowlist check via the shared normalizer ────────────────────────
"$PY" -m orbital_drift.remotes --check-url "$REMOTE_URL" --allowlist "$ALLOWLIST" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 1 ]; then
  block "push remote '$REMOTE_NAME' ($REMOTE_URL) is not in $ALLOWLIST — denied unconditionally" \
        "if legitimate, add its exact URL to the allowlist via a reviewed commit + decision-log entry (DEC-003)"
elif [ "$RC" -ne 0 ]; then
  block "allowlist check errored (exit $RC) — failing closed" \
        "verify the venv exists and $ALLOWLIST is readable, then retry"
fi

# ── REPORT (non-gating): which governed paths ride this push? ──────────────
report_governed_paths() {
  local zero="0000000000000000000000000000000000000000"
  local globs lref lsha rref rsha paths p g
  globs="$("$PY" - <<'PYEOF' 2>/dev/null
import sys, tomllib
from pathlib import Path
config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
for glob in config["tool"]["orbital_drift"]["governance"]["governed_path_globs"]:
    sys.stdout.write(glob + "\n")
PYEOF
)" || {
    echo "pre-push scan: glob fetch FAILED — reporting disabled this push; fix before trusting the scan" >&2
    return 0
  }
  [ -n "$globs" ] || { echo "pre-push scan: EMPTY glob set — reporting disabled this push" >&2; return 0; }
  while read -r lref lsha rref rsha; do
    : "$lref" "$rref"
    [ -n "${lsha:-}" ] || continue
    [ "$lsha" = "$zero" ] && continue    # ref deletion pushes no content
    if [ "${rsha:-$zero}" = "$zero" ]; then
      paths="$(git ls-tree -r --name-only "$lsha" 2>/dev/null)"
    else
      paths="$(git diff --name-only "$rsha" "$lsha" 2>/dev/null || git ls-tree -r --name-only "$lsha" 2>/dev/null)"
    fi
    while read -r p; do
      [ -n "$p" ] || continue
      while read -r g; do
        g="${g%$'\r'}"                    # CRLF defense
        [ -n "$g" ] || continue
        # Unquoted expansion ON PURPOSE: it IS the glob matcher (** folded to
        # *); quoting would compare literally and break governed-path
        # matching. The governance meta-test replicates exactly these
        # semantics (fnmatchcase after the same rewrite) so test and hook
        # cannot disagree.
        # shellcheck disable=SC2254
        case "$p" in
          ${g//\*\*/\*})
            echo "pre-push scan: governed path in push: $p (matches $g)" >&2
            break
            ;;
        esac
      done <<< "$globs"
    done <<< "$paths"
  done <<< "$REFS"
  return 0
}
cd "$ROOT" && report_governed_paths || true

echo "pre-push scan: remote '$REMOTE_NAME' ($REMOTE_URL) is allowlisted — proceeding" >&2
exit 0
