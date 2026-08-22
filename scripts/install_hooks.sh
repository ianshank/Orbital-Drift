#!/usr/bin/env bash
# Orbital-Drift — installs the native pre-push hook (charter C-5 layer 2).
# Rules (donor kit, kept):
# - Fixture tests MUST install through this script — never copies of hook logic.
# - Run from `make install` and on every fresh checkout; a clone without the
#   hook silently lacks layer 2 (the PreToolUse guard and CI still stand).
# - Handle Windows drive-letter paths from `git rev-parse --git-path hooks`:
#   an absolute-path check written as `case $p in /*)` treats `C:/...` as
#   relative and installs to a wrong nested path (donor kit RB-023).
# usage: install_hooks.sh [--repo <path>]   (default: repo containing cwd)
set -euo pipefail

usage() { echo "usage: install_hooks.sh [--repo <path>]" >&2; exit 2; }

REPO=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) shift; [ "$#" -gt 0 ] || usage; REPO="$1" ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/pre_push_scan.sh"
[ -f "$SRC" ] || { echo "install_hooks: hook source $SRC is missing" >&2; exit 1; }

if [ -z "$REPO" ]; then
  REPO="$(git rev-parse --show-toplevel)" || {
    echo "install_hooks: not inside a git repo and no --repo given" >&2; exit 1
  }
fi

HOOKS_DIR="$(git -C "$REPO" rev-parse --git-path hooks)" || {
  echo "install_hooks: $REPO is not a git repository" >&2; exit 1
}
# Absolute iff it starts with / (POSIX) or a drive letter (Windows).
case "$HOOKS_DIR" in
  /*|[A-Za-z]:/*|[A-Za-z]:\\*) : ;;
  *) HOOKS_DIR="$REPO/$HOOKS_DIR" ;;
esac

mkdir -p "$HOOKS_DIR"
cp "$SRC" "$HOOKS_DIR/pre-push"
chmod +x "$HOOKS_DIR/pre-push"
echo "install_hooks: installed $SRC -> $HOOKS_DIR/pre-push"
