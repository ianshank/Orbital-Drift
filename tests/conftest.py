"""Zero-skip guard (adopt-governance-kit, design D10; charter C-6 / trigger R-4).

Charter R-4: "coverage drops below the standing bar, or a test is skipped
rather than fixed" is a review trigger. This guard makes the skip half
MECHANICAL: any skipped, xfailed, or xpassed test escalates an otherwise-green
run to a nonzero exit, so a suite cannot quietly shed coverage while staying
green.

Design rules (kept from the donor template):

1. ESCALATE-ONLY. An already-failing run's exit status passes through
   unchanged — the guard never masks a real failure with its own code.

2. The bar lives ONLY here — never as a config key, never env-overridable.
   (Same principle as the coverage floor living only in pyproject.toml.)

3. The ONE allowance (design D10): a skip whose reason begins with the literal
   ``capability-guard:`` names an environment capability probe (docker/git/sh
   absent on a LOCAL box; such skips never fire in CI, and their tests assert
   that). The allowance is hard-coded here, and
   tests/governance/test_zero_skip_guard.py pins the exact set of call sites —
   it cannot grow silently. xfail/xpass have no allowance at all.

4. The guard itself is meta-tested (tests/governance/test_zero_skip_guard.py)
   by running pytest in a subprocess on tmp mini-suites with a COPY of this
   conftest: it must escalate a skip, stay quiet on a clean suite, and pass an
   already-failing suite's exit status through unchanged. A guard nobody has
   watched fire is a decoration.
"""

from __future__ import annotations

import re

import pytest

_GUARD_BANNER = "ZERO-SKIP GUARD"
_CAPABILITY_PREFIX = "capability-guard:"


#: pytest renders a skip reason as ``Skipped: <reason>``; the allowance is a
#: PREFIX of <reason>, anchored here. A substring test would let any parked
#: skip launder itself by mentioning the token mid-sentence — measured, and the
#: enumeration test cannot see it because it only checks WHICH files contain
#: the literal, not where in the string it lands.
_ALLOWED_REASON = re.compile(r"^(?:Skipped:\s*)?" + re.escape(_CAPABILITY_PREFIX))


def _skip_reason(report: object) -> str:
    """Best-effort reason extraction from a pytest skip report entry."""
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        # (path, lineno, "Skipped: <reason>")
        return str(longrepr[2])
    return str(longrepr) if longrepr is not None else ""


def _is_capability_guard(report: object) -> bool:
    """True only when the reason BEGINS with the allowance prefix."""
    return bool(_ALLOWED_REASON.match(_skip_reason(report).strip()))


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # Escalate-only: never rewrite an already-failing exit status.
    if exitstatus != 0:
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:  # e.g. --collect-only; nothing ran
        return

    stats = reporter.stats
    disallowed_skips = [
        report for report in stats.get("skipped", []) if not _is_capability_guard(report)
    ]
    xfailed = stats.get("xfailed", [])
    xpassed = stats.get("xpassed", [])

    offenders = len(disallowed_skips) + len(xfailed) + len(xpassed)
    if offenders:
        names = ", ".join(
            getattr(report, "nodeid", "<unknown>")
            for report in (*disallowed_skips, *xfailed, *xpassed)
        )
        reporter.write_line(
            f"{_GUARD_BANNER}: {len(disallowed_skips)} skipped, "
            f"{len(xfailed)} xfailed, {len(xpassed)} xpassed — a skipped test "
            f"is a silently shed requirement. Fix or delete it; never park it. "
            f"Offenders: {names}",
            red=True,
        )
        session.exitstatus = 1
