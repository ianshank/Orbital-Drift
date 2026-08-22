#!/usr/bin/env bash
# Orbital-Drift - SessionStart hook body (adopt-governance-kit design D2).
#
# A CHECK, never an install: it compares the venv's installed gate tools
# against ci/versions.env and warns with the one documented bootstrap command
# on mismatch. It must NEVER be session-blocking (the caller appends `exit 0`;
# this script also never exits nonzero) and never mutates the environment - a
# session-start `pip install` is slow and silently mutating (review F10).
#
# OUTPUT GOES TO STDOUT. A SessionStart hook's stdout is what reaches the
# session; stderr on a zero exit is not surfaced. The first version wrote every
# message to stderr, so this check ran on every session and told nobody
# anything - a mechanism whose output goes nowhere is not a mechanism.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(od_repo_root)}"
cd "${REPO_ROOT}" 2>/dev/null || exit 0

PY="$(od_find_python "${REPO_ROOT}" || true)"
if [ -z "${PY}" ]; then
  echo 'SessionStart: no venv yet - bootstrap: python -m pip install -e ".[dev]"'
  exit 0
fi

# Layer-2 C-5 presence check: a pre-push hook nobody installed is a paper gate,
# and the one automatic install path (`make install`) needs a binary that is
# not present on the authoring box.
HOOKS_DIR="$(git -C "${REPO_ROOT}" rev-parse --git-path hooks 2>/dev/null || true)"
case "${HOOKS_DIR}" in
  /*|[A-Za-z]:/*|[A-Za-z]:\\*) : ;;
  "") HOOKS_DIR="" ;;
  *) HOOKS_DIR="${REPO_ROOT}/${HOOKS_DIR}" ;;
esac
if [ -n "${HOOKS_DIR}" ] && [ ! -f "${HOOKS_DIR}/pre-push" ]; then
  echo 'SessionStart: the C-5 pre-push hook is NOT installed - run: bash scripts/install_hooks.sh'
fi

"${PY}" - <<'PYEOF' 2>/dev/null || echo 'SessionStart: pin check errored - continuing'
import pathlib
import re
import sys
from importlib import metadata

# Derived by EXCLUSION, mirroring ci/checks.sh PREFLIGHT_EXEMPT_PINS: a pin
# added to versions.env is checked BY DEFAULT. The first version listed the
# checked tools instead and was already stale the day it shipped - pytest-cov
# was pinned by the same change and never checked.
#
# LOCKSTEP, mechanically enforced. This set and ci/checks.sh's
# PREFLIGHT_EXEMPT_PINS must contain the same names; tests/governance/
# test_session_start_check.py reads BOTH literals out of their files and fails
# if they disagree. Claiming to mirror was not enough: terraform was added
# there and not here, so every session start printed a false
# "terraform: not installed" whose pip-install remedy could never fix it (a
# digest-pinned container image is not a Python distribution). Add a name here
# ONLY together with the one in ci/checks.sh - see .claude/skills/pin-a-tool.
EXEMPT = {"python", "hatchling", "pip", "gitleaks", "shellcheck", "terraform"}
pins = {
    key.lower().removesuffix("_version").replace("_", "-"): value
    for key, value in re.findall(
        r"^([A-Z0-9_]+_VERSION)=(\S+)$",
        pathlib.Path("ci/versions.env").read_text(encoding="utf-8"),
        re.M,
    )
}
drift = []
for dist, pinned in sorted(pins.items()):
    if dist in EXEMPT:
        continue
    try:
        installed = metadata.version(dist)
    except metadata.PackageNotFoundError:
        drift.append(f"  {dist}: not installed (pinned {pinned})")
        continue
    if installed != pinned:
        drift.append(f"  {dist}: installed {installed} != pinned {pinned}")
if drift:
    sys.stdout.write(
        'SessionStart: pin drift vs ci/versions.env - run: python -m pip install -e ".[dev]"\n'
        + "\n".join(drift)
        + "\n"
    )
PYEOF
exit 0
