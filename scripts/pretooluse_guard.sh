#!/usr/bin/env bash
# Orbital-Drift — PreToolUse guard (charter C-1 / C-5; Constitution I).
# Interface: stdin = PreToolUse JSON; exit 2 BLOCKS the tool call; exit 0 allows.
#
# WHAT THIS IS: a FIRST-PASS FILTER. `.claude/settings.json`'s deny-list is the
# authoritative C-1 layer (it blocks kubectl/argo/k3s/k9s/kustomize-build in
# every mode); scripts/pre_push_scan.sh is the authoritative C-5 layer; CI is
# the third. A guard header that claims to be the last word is a defect.
#
# THIS FILE IS DELIBERATELY THIN. All classification lives in
# src/orbital_drift/guard.py, because the first implementation tokenized shell
# argv with `sed` + an ERE and four bypasses followed — every one a tokenizer
# failure, not a pattern failure (see that module's header for the measured
# list). Shell syntax needs a shell lexer; this wrapper's only jobs are to
# locate an interpreter, resolve the effective push remote from git config
# (which Python cannot see without shelling out anyway), and translate the
# verdict into an exit code.
#
# FAIL-CLOSED POLICY:
# - No usable interpreter, or the package is not importable: block iff the raw
#   payload is dangerous-shaped, else allow (safe for ordinary work, closed for
#   anything that looks like cluster mutation or a push).
# - Any segment the lexer cannot parse: BLOCK.
# - A command the segmenter could not finish splitting — it exhausts its work
#   ceiling with substitutions still queued: BLOCK, naming the ceiling. A
#   command that was never read is not a command that was found safe; 256
#   nested `$( )` levels used to buy an ALLOW here (RB-009).
# - A push whose destination cannot be resolved: BLOCK. Never assume `origin`.
#
# ACCEPTED FALSE POSITIVES (design choice — changing these needs a DEC entry):
# - Quoted mentions block: `git commit -m "kubectl apply -f x.yaml"` -> BLOCK.
#   The lexer cannot distinguish mention from use, so the guard errs closed.
#   Workaround: rephrase without the literal token.
# - kubectl/argo read-only forms block, because the settings deny-list blocks
#   them in every mode and this guard must never be looser (CLAUDE.md prime
#   constraint 1: hand those to the operator).
# - A BENIGN command carrying more command substitutions than the segmenter's
#   work ceiling allows blocks too — refusing to analyze IS the verdict, so it
#   cannot be conditioned on what the unread part turns out to say, and no
#   reordering evades it. Read the thresholds off `_MAX_SEGMENTS` in
#   src/orbital_drift/guard.py rather than trusting a number here: truncation
#   starts at `_MAX_SEGMENTS - 1` sibling substitutions, or `_MAX_SEGMENTS // 2`
#   levels of nesting (511 and 256 at the ceiling of 512 in force when this was
#   written, 2026-08-22). Segment COUNT is not the ceiling — a `;`-chain of any
#   length is split in one pass and stays allowed. Workaround: express the
#   command with fewer substitutions.
#
# NOT-MODELLED FAMILY (the pre-push hook and the operator layer are
# authoritative for these): environment-supplied git config
# (GIT_CONFIG_COUNT=... does not travel with argv), state mutation before the
# action (`git remote set-url`), shell functions/aliases defined elsewhere, and
# anything invoked indirectly through a file (`bash deploy.sh`). Measured
# 2026-08-21 at the tokenizer rewrite; re-measure when this file changes.
# tests/governance/test_pretooluse_guard.py pins every documented verdict and
# asserts the block REASON, not merely a nonzero exit.
#
# Debugging: GUARD_DEBUG=1 traces each parsed segment to stderr, and
# scripts/guard_probe.sh CMD prints the verdict without needing GNU make.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(od_repo_root)}"
ALLOWLIST="$(od_allowlist_path "${REPO_ROOT}")"

PAYLOAD="$(cat)"

# Raw-text danger check, used only on the paths where the analyzer is
# unavailable. Deliberately broad: it decides fail-closed vs fail-safe, never
# allow-vs-block for an analyzable payload.
raw_is_dangerous() {
  printf '%s' "$1" | grep -Eqi 'kubectl|k3s|k9s|argocd?|kustomize|helm|terraform|git[[:space:]]+push'
}

PY="$(od_find_python "${REPO_ROOT}" || true)"
if [ -z "${PY}" ] || ! od_package_importable "${PY}"; then
  if raw_is_dangerous "${PAYLOAD}"; then
    echo "BLOCKED (C-1): no usable analyzer (interpreter or orbital_drift package missing)" >&2
    echo "Remediation: python -m pip install -e \".[dev]\" in ${REPO_ROOT}" >&2
    exit 2
  fi
  exit 0
fi

# Resolve what a bare `git push` would actually target: branch pushRemote,
# then remote.pushDefault, then origin. Hard-coding `origin` here was a
# measured bypass in the donor kit (a push delivered to an off-allowlist
# remote.pushDefault was allowed), so the value is resolved, never assumed.
EFFECTIVE_REMOTE=""
if command -v git >/dev/null 2>&1; then
  BRANCH="$(git -C "${REPO_ROOT}" symbolic-ref --short HEAD 2>/dev/null || true)"
  if [ -n "${BRANCH}" ]; then
    EFFECTIVE_REMOTE="$(git -C "${REPO_ROOT}" config --get "branch.${BRANCH}.pushRemote" 2>/dev/null || true)"
  fi
  [ -n "${EFFECTIVE_REMOTE}" ] || EFFECTIVE_REMOTE="$(git -C "${REPO_ROOT}" config --get remote.pushDefault 2>/dev/null || true)"
  [ -n "${EFFECTIVE_REMOTE}" ] || EFFECTIVE_REMOTE="origin"
  # Translate a remote NAME to its URL; the allowlist holds URLs.
  RESOLVED_URL="$(git -C "${REPO_ROOT}" remote get-url "${EFFECTIVE_REMOTE}" 2>/dev/null || true)"
  [ -n "${RESOLVED_URL}" ] && EFFECTIVE_REMOTE="${RESOLVED_URL}"
fi

DEBUG_FLAG=""
[ -n "${GUARD_DEBUG:-}" ] && DEBUG_FLAG="--debug"

printf '%s' "${PAYLOAD}" | "${PY}" -m orbital_drift.guard \
  --allowlist "${ALLOWLIST}" \
  --effective-remote "${EFFECTIVE_REMOTE}" \
  ${DEBUG_FLAG}
RC=$?

# Any exit status other than a clean allow/block is an analyzer failure, not a
# verdict — fail closed rather than letting a crash read as permission.
if [ "${RC}" -ne 0 ] && [ "${RC}" -ne 2 ]; then
  echo "BLOCKED (C-1): guard analyzer exited ${RC} — failing closed" >&2
  exit 2
fi
exit "${RC}"
