"""Remote-URL normalizer and allowlist check (charter C-5, design in
adopt-governance-kit spec scenario "Push to non-allowlisted remote refused").

ONE normalizer, shared by the PreToolUse guard (scripts/pretooluse_guard.sh)
and the native pre-push hook (scripts/pre_push_scan.sh) — never duplicated, so
the two layers cannot disagree about what a URL "is".

    python -m orbital_drift.remotes --check-url <url> --allowlist <path>

Exit codes: 0 = allowlisted; 1 = NOT allowlisted; 2 = error (callers treat any
nonzero as a block — fail closed).

The 1-vs-2 split is load-bearing for the CALLER'S MESSAGE, not just its
verdict: a missing editable install also exits 1 from ``python -m``, so
callers must confirm the package is importable (``od_package_importable`` in
scripts/_lib.sh) before rendering exit 1 as "this remote is not allow-listed".
Without that check a broken venv produced a confident C-5 accusation and sent
the operator to edit the allowlist instead of to ``pip install -e ".[dev]"``.

Normalization: scheme/case/trailing-`.git`/trailing-slash insensitive;
`git@host:owner/repo` == `https://host/owner/repo`. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SCP_LIKE = re.compile(r"^(?P<user>[\w.-]+)@(?P<host>[\w.-]+):(?P<path>.*)$")


def normalize(url: str) -> str:
    """Canonical form: ``host/owner/repo`` — lowercase, no scheme, no .git.

    CASE FOLDING APPLIES TO THE WHOLE REFERENCE, path included. On GitHub that
    is correct (repository paths are case-insensitive). On a self-hosted forge
    where ``group/Repo`` and ``group/repo`` are different projects it is
    deliberately conservative: two spellings collapse to one allow-list entry,
    which can only ever ALLOW a spelling the operator already listed, never a
    different repository. Revisit via a DEC entry if such a forge is adopted.
    """
    text = url.strip()
    scp = _SCP_LIKE.match(text)
    if scp is not None and "://" not in text:
        text = f"{scp.group('host')}/{scp.group('path')}"
    else:
        text = re.sub(r"^[a-zA-Z][\w+.-]*://", "", text)
        text = re.sub(r"^[\w.-]+@", "", text)
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    return text.lower()


def is_allowlisted(url: str, allowlist_path: Path) -> bool:
    allowed = {
        normalize(line)
        for line in allowlist_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return normalize(url) in allowed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-url", required=True)
    parser.add_argument("--allowlist", required=True, type=Path)
    args = parser.parse_args(argv)

    # sys.stderr.write, not print(): ruff T20 bans print() in src/.
    if not args.allowlist.is_file():
        sys.stderr.write(f"remotes: allowlist {args.allowlist} is missing — failing closed\n")
        return 2
    try:
        allowed = is_allowlisted(args.check_url, args.allowlist)
    except OSError as error:
        sys.stderr.write(f"remotes: cannot read allowlist: {error} — failing closed\n")
        return 2
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
