#!/usr/bin/env bash
# Show the PreToolUse guard's verdict for a command, with the reason.
#
# usage: scripts/guard_probe.sh 'kubectl apply -f x.yaml'
#        GUARD_DEBUG=1 scripts/guard_probe.sh 'a && b'      # trace segments
#
# The guard's header calls a probe harness "design rule 6", and the Makefile
# target that provided it needs GNU make — which is not installed on the
# authoring box (design D12 accepted that as the normal case). This script is
# the probe; `make guard-probe` now calls it, so the affordance works in both
# worlds and there is one implementation rather than two.
set -u

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  echo "usage: scripts/guard_probe.sh '<command>'" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(od_repo_root)}"
PY="$(od_find_python "${REPO_ROOT}" || true)"
if [ -z "${PY}" ]; then
  echo "guard_probe: no usable Python interpreter" >&2
  exit 2
fi

PAYLOAD="$(COMMAND="$1" "${PY}" -c '
import json, os, sys
sys.stdout.write(json.dumps({"tool_name": "Bash", "tool_input": {"command": os.environ["COMMAND"]}}))
')"

# `|| RC=$?`, not a `set +e` / `set -e` window. This script's prologue is
# `set -u` ALONE (line 12): errexit was never on, so a trailing `set -e` would
# not restore anything - it would switch errexit ON for the rest of the script,
# the opposite of what a "restore" reads as, in the one place whose entire job
# is to report a nonzero exit code rather than die on it. A command on the LEFT
# of `||` is already exempt from errexit, so this captures the status without
# ever touching errexit state - the strictly narrower form ci/checks.sh's
# docker_daemon_or_fail comment argues for.
RC=0
printf '%s' "${PAYLOAD}" | CLAUDE_PROJECT_DIR="${REPO_ROOT}" bash "${SCRIPT_DIR}/pretooluse_guard.sh" || RC=$?

if [ "${RC}" -eq 2 ]; then
  echo "verdict: BLOCK (rc=2)"
else
  echo "verdict: ALLOW (rc=${RC})"
fi
exit 0
