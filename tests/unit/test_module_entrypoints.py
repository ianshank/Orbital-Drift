"""Every ``python -m orbital_drift.<module>`` entry point is really entered.

RB-008 part 3. Five modules end in::

    if __name__ == "__main__":
        raise SystemExit(main())

and until ``--cov-branch`` was turned on, all five of those arcs were missed —
covcheck.py:153->154, guard.py:452->453, projections.py:206->207,
remotes.py:84->85, traceability.py:228->229. The reason is structural, not
accidental: pytest IMPORTS these modules, so ``__name__`` is never
``"__main__"`` and the two lines never run.

WHY runpy AND NOT ``# pragma: no cover``. The pragma is the one-line fix and it
is the wrong one. It raises the measured number by deleting the question, which
is cosmetically indistinguishable from the vacuous-pass class this whole
program exists to retire: the arc would report as covered while nothing had
ever executed it. ``runpy.run_module(name, run_name="__main__")`` runs the real
module source in-process, under the same tracer, with ``__name__`` set to
``"__main__"`` — so the arc is covered because it was TAKEN.

WHAT THAT BUYS BEYOND A NUMBER. These two lines carry a real invariant: the
exit code of ``main()`` has to reach the process. ``main()`` (no ``raise``) is
a one-word edit that leaves every unit test in this repo green while every gate
that shells out to these modules — ``ci/checks.sh`` stages ``coverage``,
``traceability``, ``projections``; ``scripts/pre_push_scan.sh``;
``scripts/pretooluse_guard.sh`` — starts exiting 0 on failure. A gate that
cannot fail is not a gate: charter C-5 and C-6 both name enforcement mechanisms
that are exactly these entry points' exit codes. (An earlier draft of this
paragraph attributed that sentence to Constitution VII as a quotation; it is
not one — VII is Secrets Hygiene and contains no such text — and an invented
citation in a file arguing for rigor is the defect it argues against.) Three of
the eight cases below therefore drive a FAILING run and assert the nonzero code
survives; ``_Case``'s fourth field, ``expected_code``, is where that is pinned.

A CONSTRAINT THIS FILE OBEYS, WORTH STATING: ``run_module`` re-executes the
module source in a fresh namespace, so module-level constants are recomputed
and monkeypatching the already-imported module has NO effect on the run. Every
case is therefore steered through ``sys.argv`` and ``sys.stdin`` only — the
same two channels a real shell invocation has.

Audience: implementers and reviewers of the gate scripts.
"""

from __future__ import annotations

import io
import json
import runpy
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

#: The remote DEC-003 allows. Written into a tmp allowlist rather than read
#: from `.claude/allowed-remotes.txt`, so this file tests the entry point and
#: not the repository's current allow-list contents.
_ALLOWED_REMOTE = "https://github.com/ianshank/Orbital-Drift.git"


@dataclass(frozen=True)
class _Invocation:
    """One real command line for one entry point."""

    #: Arguments AFTER argv[0], exactly as a shell would pass them.
    args: list[str]
    #: Text to present on stdin, for the one module that reads it.
    stdin: str | None = None


@dataclass(frozen=True)
class _Case:
    case_id: str
    module: str
    build: Callable[[Path], _Invocation]
    expected_code: int


def _allowlist(tmp_path: Path) -> Path:
    path = tmp_path / "allowed-remotes.txt"
    path.write_text(f"# charter C-5 / DEC-003\n{_ALLOWED_REMOTE}\n", encoding="utf-8")
    return path


def _coverage_report(tmp_path: Path, percent: float) -> Path:
    """A minimal coverage.json in the shape `orbital_drift.covcheck` reads."""
    path = tmp_path / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "files": {
                    "src/orbital_drift/a.py": {
                        "summary": {"num_statements": 10, "percent_covered": percent}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _guard_payload(command: str) -> str:
    """A PreToolUse payload in the shape `scripts/pretooluse_guard.sh` pipes."""
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def _hardcode_scan_invocation(tmp_path: Path) -> _Invocation:
    """Build a failing hardcode-scan CLI run over only synthetic source."""
    target = tmp_path / "candidate.py"
    target.write_text("timeout = 30.0\n", encoding="utf-8")
    policy = tmp_path / "pyproject.toml"
    policy.write_text("[project]\nname = 'entrypoint-case'\n", encoding="utf-8")
    return _Invocation(
        [
            str(target),
            "--policy-file",
            str(policy),
            "--format",
            "json",
            "--fail-on-findings",
        ]
    )


#: (id, module, invocation builder, expected process exit code).
#:
#: Each module gets its real gate invocation. Where a FAILING run is cheap to
#: arrange from argv alone, that is the case chosen, because a failing run is
#: what proves the exit code propagates — a clean run cannot tell
#: `raise SystemExit(main())` apart from a bare `main()`.
#:
#: projections and traceability have no argv that makes them fail without
#: touching the repository, so they are pinned on their clean-exit codes only;
#: their nonzero paths are covered by direct `main()` tests in
#: tests/unit/test_projections.py and tests/unit/test_traceability_lint.py.
_CASES: tuple[_Case, ...] = (
    _Case(
        "covcheck-clean",
        "orbital_drift.covcheck",
        lambda tmp: _Invocation(
            ["--json-report", str(_coverage_report(tmp, 99.0)), "--floor", "90"]
        ),
        0,
    ),
    _Case(
        "covcheck-below-floor",
        "orbital_drift.covcheck",
        lambda tmp: _Invocation(
            ["--json-report", str(_coverage_report(tmp, 42.0)), "--floor", "90"]
        ),
        1,
    ),
    _Case(
        "remotes-allowlisted",
        "orbital_drift.remotes",
        lambda tmp: _Invocation(
            ["--check-url", _ALLOWED_REMOTE, "--allowlist", str(_allowlist(tmp))]
        ),
        0,
    ),
    _Case(
        "remotes-unlisted",
        "orbital_drift.remotes",
        lambda tmp: _Invocation(
            [
                "--check-url",
                "https://example.invalid/rogue.git",
                "--allowlist",
                str(_allowlist(tmp)),
            ]
        ),
        1,
    ),
    _Case(
        "guard-allows",
        "orbital_drift.guard",
        lambda tmp: _Invocation(
            ["--allowlist", str(_allowlist(tmp))], stdin=_guard_payload("pytest -q")
        ),
        0,
    ),
    _Case(
        "guard-blocks",
        "orbital_drift.guard",
        lambda tmp: _Invocation(
            ["--allowlist", str(_allowlist(tmp))],
            stdin=_guard_payload("kubectl delete ns prod"),
        ),
        2,
    ),
    _Case(
        "projections-check",
        "orbital_drift.projections",
        lambda _tmp: _Invocation(["--check", "--json"]),
        0,
    ),
    _Case(
        "traceability-check",
        "orbital_drift.traceability",
        lambda _tmp: _Invocation(["--json"]),
        0,
    ),
    _Case(
        "hardcode-scan-finds-literal",
        "orbital_drift.quality.hardcode_scan",
        _hardcode_scan_invocation,
        1,
    ),
)


def _run_as_main(module: str, invocation: _Invocation, monkeypatch: pytest.MonkeyPatch) -> int:
    """Execute ``module`` exactly as ``python -m module`` would; return its code.

    ``run_module`` does not touch ``sys.argv`` (``alter_sys`` defaults to
    False), so argv is set here — argparse in each ``main()`` reads
    ``sys.argv[1:]`` when called with no arguments, which is the code path a
    shell invocation takes and the one the direct ``main([...])`` unit tests
    never reach.
    """
    monkeypatch.setattr(sys, "argv", [module.rsplit(".", 1)[-1], *invocation.args])
    if invocation.stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(invocation.stdin))

    with warnings.catch_warnings():
        # runpy warns when the module it is about to execute is ALREADY in
        # sys.modules — which it always is here, because pytest imported it to
        # run the other tests. That is an artefact of measuring an entry point
        # from inside the same process, not a defect in the module, and the
        # scoped filter keeps it from being mistaken for one.
        warnings.filterwarnings(
            "ignore",
            message=r".*found in sys\.modules after import of package.*",
            category=RuntimeWarning,
        )
        try:
            runpy.run_module(module, run_name="__main__")
        except SystemExit as exit_signal:
            code = exit_signal.code
            assert isinstance(code, int), f"{module} exited with a non-integer code {code!r}"
            return code

    raise AssertionError(
        f"{module} ran to completion without raising SystemExit: its "
        f"`if __name__ == '__main__':` block does not propagate main()'s exit "
        f"code, so every gate that shells out to it now exits 0 on failure"
    )


@pytest.mark.parametrize("case", _CASES, ids=[case.case_id for case in _CASES])
def test_the_module_entry_point_propagates_the_exit_code(
    case: _Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`python -m <module>` exits with exactly what `main()` returned.

    THE MUTATION THAT REDDENS THIS: in any of the five modules, change
    `raise SystemExit(main())` to `main()`. The module then runs to completion
    with no SystemExit, `_run_as_main` raises its AssertionError, and that
    module's cases fail. On the three modules with a nonzero case, changing the
    line to `raise SystemExit(0)` reddens them too — which is the subtler and
    more dangerous shape, since it leaves the entry point looking correct.

    Verified by making both edits: measured 2026-08-22 at 18330d4.
    """
    # Built ONCE. `build` is not a pure accessor — it writes an allowlist or a
    # coverage report under tmp_path — so calling it again to render the failure
    # message would re-run a file-writing builder on the failure path, where the
    # message is supposed to describe the run that just happened.
    invocation = case.build(tmp_path)
    code = _run_as_main(case.module, invocation, monkeypatch)
    # Read and discard whatever the entry point wrote, so a failing case's
    # stderr does not leak into the report of an unrelated test.
    capsys.readouterr()

    assert code == case.expected_code, (
        f"`python -m {case.module} {' '.join(invocation.args)}` exited "
        f"{code}, expected {case.expected_code}"
    )


def test_every_module_with_a_main_has_an_entry_point_case() -> None:
    """The list above cannot silently fall behind the package.

    A sixth module gaining a `main()` and an `if __name__` block would
    otherwise reintroduce exactly the uncovered arc this file was written to
    close, and nothing would say so — the coverage floor has 11 points of
    global headroom to absorb it.
    """
    package_root = Path(__file__).resolve().parents[2] / "src" / "orbital_drift"

    def _dotted(path: Path) -> str:
        stem = path.relative_to(package_root).with_suffix("").as_posix()
        return f"orbital_drift.{stem.replace('/', '.')}"

    with_entry_points = {
        _dotted(path)
        for path in sorted(package_root.rglob("*.py"))
        if 'if __name__ == "__main__":' in path.read_text(encoding="utf-8")
    }
    covered = {case.module for case in _CASES}

    assert with_entry_points == covered, (
        f"entry points with no case here: {sorted(with_entry_points - covered)}; "
        f"cases naming no entry point: {sorted(covered - with_entry_points)}"
    )
