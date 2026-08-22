"""Unit tests for the guard's analyzer (charter C-1/C-5).

tests/governance/test_pretooluse_guard.py drives the real bash wrapper end to
end — that is the integration proof. These tests exercise the classification
logic directly, so a tokenizer regression is diagnosed in milliseconds and at
the level it occurred, instead of as a rc=0 from a subprocess.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Final

import pytest

from orbital_drift import guard

#: A command the deny-list forbids in EVERY mode. Used as the payload wherever
#: a test has to prove the analyzer actually READ what it was asked to judge —
#: a verdict of "not blocked" means nothing unless the denied verb was in scope.
_DENIED: Final = "kubectl delete ns prod"

#: Deepest `$( )` nesting `split_segments` can still fully unwrap.
#:
#: Each level costs TWO queue iterations: the lifted body is queued AND the
#: stripped remainder is re-queued (guard.py's `continue`). The queue is LIFO,
#: so the body lifted on the FIRST pass — the one holding the real command —
#: lands at the bottom and is popped LAST, on iteration ``2 * depth + 1``. The
#: split is therefore complete only while ``2 * depth + 1 <= _MAX_SEGMENTS``,
#: which is where both constants below come from rather than from a literal.
#:
#: Measured 2026-08-22 at the ceiling of 512, BEFORE the fail-closed fix
#: (RB-009): depth 255 -> 1 segment -> BLOCK (C-1); depth 256 -> 0 segments ->
#: ALLOW, and the wrapper exited 0 for a command denied in every mode.
_LAST_ANALYZABLE_DEPTH: Final = (guard._MAX_SEGMENTS - 1) // 2

#: First depth at which the ceiling stops the split with work still queued.
_TRUNCATING_DEPTH: Final = _LAST_ANALYZABLE_DEPTH + 1

#: Well past the boundary, to show the verdict does not depend on landing
#: exactly on it. Twice the first truncating depth, not an arbitrary offset.
_WELL_PAST_TRUNCATING_DEPTH: Final = _TRUNCATING_DEPTH * 2

#: Most SIBLING (unnested) substitutions one command line can carry and still
#: be split completely. A single pass lifts all N bodies and re-queues the
#: stripped remainder, so the whole split costs ``N + 2`` iterations: one to
#: lift, one for the remainder, one per body. Complete while
#: ``N + 2 <= _MAX_SEGMENTS``. Measured 2026-08-22: N=510 -> 512 iterations,
#: queue drained, 511 segments, NOT truncated; N=511 -> truncated.
_LAST_COMPLETE_SIBLINGS: Final = guard._MAX_SEGMENTS - 2

#: First sibling count that leaves work queued at the ceiling.
_TRUNCATING_SIBLINGS: Final = _LAST_COMPLETE_SIBLINGS + 1


def _nested(command: str, depth: int) -> str:
    """``command`` buried under ``depth`` levels of command substitution."""
    return "$(" * depth + command + ")" * depth


@pytest.fixture
def allowlist(tmp_path: Path) -> Path:
    path = tmp_path / "allowed-remotes.txt"
    path.write_text(
        "# charter C-5 / DEC-003\nhttps://github.com/ianshank/Orbital-Drift.git\n",
        encoding="utf-8",
    )
    return path


def _blocked(command: str, allowlist: Path, *, remote: str = "") -> guard.Verdict:
    return guard.analyze(command, allowlist=allowlist, effective_remote=remote)


# --- segmentation ----------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("a && b", {"a", "b"}),
        ("a || b", {"a", "b"}),
        ("a ; b", {"a", "b"}),
        ("a | b", {"a", "b"}),
        ("a & b", {"a", "b"}),
        ("a\nb", {"a", "b"}),
        # Substitution bodies are lifted into their own segments: this is the
        # class of bypass the regex tokenizer could not see at all.
        ("echo $(kubectl get pods)", {"echo", "kubectl get pods"}),
        ("echo `kubectl get pods`", {"echo", "kubectl get pods"}),
        ("a $(b $(c))", {"a", "b", "c"}),
    ],
)
def test_split_segments(command: str, expected: set[str]) -> None:
    assert set(guard.split_segments(command)) >= expected


def test_parameter_expansion_is_data_not_a_command() -> None:
    """${VAR} expands to a value; it is not a nested command line."""
    assert "VAR" not in " ".join(guard.split_segments("echo ${VAR}"))


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "kubectl get pods",
        "argo list",
        "argocd app list",
        "k3s kubectl get nodes",
        "k9s",
        "/usr/local/bin/kubectl apply -f x",
        "sudo kubectl apply -f x",
        "env KUBECONFIG=/tmp/c kubectl apply -f x",
        "FOO=1 BAR=2 kubectl apply -f x",
        "xargs kubectl delete pod",
    ],
)
def test_always_denied_commands_block(command: str, allowlist: Path) -> None:
    verdict = _blocked(command, allowlist)
    assert verdict.blocked and verdict.constraint == "C-1", verdict


@pytest.mark.parametrize(
    "command",
    [
        "helm install r ./c",
        "helm upgrade r ./c",
        "helm get values r",
        "terraform apply",
        "terraform plan",
        "terraform init",  # no -backend=false: initializes a real backend
        "kustomize build overlays/prod",
    ],
)
def test_conditional_commands_block_outside_readonly_forms(command: str, allowlist: Path) -> None:
    verdict = _blocked(command, allowlist)
    assert verdict.blocked and verdict.constraint == "C-1", verdict


@pytest.mark.parametrize(
    "command",
    [
        "helm template ./chart",
        "helm template . --set a=b",
        "helm lint ./chart",
        "terraform validate",
        "terraform fmt -check",
        "terraform init -backend=false",
        "pytest -q",
        "git status",
        "ls -la",
        # Substitutions in ORDINARY use. Without these the suite could not tell
        # a working analyser from one that refuses every command carrying a
        # `$( )` at all — the fail-closed fix must not become a blanket ban.
        "echo $(git rev-parse HEAD)",
        "cd $(git rev-parse --show-toplevel) && pytest -q",
        "make pre-pr 2>&1 | tee /tmp/`date +%s`.log",
    ],
)
def test_permitted_commands_allow(command: str, allowlist: Path) -> None:
    assert not _blocked(command, allowlist).blocked


def test_unparseable_segment_fails_closed(allowlist: Path) -> None:
    verdict = _blocked('echo "unterminated', allowlist)
    assert verdict.blocked
    assert "tokenize" in verdict.reason


# --- shell -c recursion ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        'bash -c "kubectl apply -f x"',
        "sh -c 'terraform apply'",
        "bash -c \"sh -c 'kubectl delete ns prod'\"",
        'zsh -c "helm install r ./c"',
    ],
)
def test_shell_dash_c_payloads_are_analyzed_as_code(command: str, allowlist: Path) -> None:
    assert _blocked(command, allowlist).blocked


def test_shell_dash_c_with_safe_payload_allows(allowlist: Path) -> None:
    assert not _blocked('bash -c "pytest -q"', allowlist).blocked


def test_deep_nesting_refuses_rather_than_recursing(allowlist: Path) -> None:
    command = "echo hi"
    for _ in range(guard._MAX_DEPTH + 2):
        command = f'bash -c "{command}"'
    verdict = guard.analyze(command, allowlist=allowlist, _depth=guard._MAX_DEPTH + 1)
    assert verdict.blocked


# --- git push destinations -------------------------------------------------


def test_allowlisted_push_allows(allowlist: Path) -> None:
    assert not _blocked(
        "git push https://github.com/ianshank/Orbital-Drift.git main", allowlist
    ).blocked


def test_unlisted_push_blocks(allowlist: Path) -> None:
    verdict = _blocked("git push https://evil.example/x.git main", allowlist)
    assert verdict.blocked and verdict.constraint == "C-5"


@pytest.mark.parametrize(
    "command",
    [
        "git -C /somewhere push evil main",
        "git --exec-path=/usr/lib push evil",
        "git -c user.name=x push evil",
    ],
)
def test_flag_bearing_push_forms_are_still_checked(command: str, allowlist: Path) -> None:
    """Every one of these was measured ALLOWED by the regex implementation."""
    verdict = _blocked(command, allowlist)
    assert verdict.blocked and verdict.constraint == "C-5", verdict


def test_bare_push_uses_the_effective_remote(allowlist: Path) -> None:
    allowed = _blocked(
        "git push", allowlist, remote="https://github.com/ianshank/Orbital-Drift.git"
    )
    assert not allowed.blocked
    denied = _blocked("git push", allowlist, remote="https://evil.example/x.git")
    assert denied.blocked


def test_bare_push_with_no_resolvable_remote_blocks(allowlist: Path) -> None:
    """Never assume `origin`: assuming a default is how a push to an
    off-allowlist remote.pushDefault was measured to pass."""
    verdict = _blocked("git push", allowlist, remote="")
    assert verdict.blocked and verdict.constraint == "C-5"


def test_push_flags_are_skipped_when_finding_the_destination(allowlist: Path) -> None:
    verdict = _blocked("git push --force-with-lease https://evil.example/x.git main", allowlist)
    assert verdict.blocked and verdict.constraint == "C-5"


def test_missing_allowlist_fails_closed(tmp_path: Path) -> None:
    verdict = _blocked(
        "git push https://github.com/ianshank/Orbital-Drift.git", tmp_path / "no.txt"
    )
    assert verdict.blocked and verdict.constraint == "C-5"


def test_non_push_git_commands_allow(allowlist: Path) -> None:
    for command in ("git status", "git commit -m 'x'", "git -C /tmp log --oneline"):
        assert not _blocked(command, allowlist).blocked, command


def test_quoted_mention_is_data(allowlist: Path) -> None:
    """A quoted string reaching `git commit -m` is data; the identical string
    reaching `bash -c` is code and blocks (see the -c tests above)."""
    assert not _blocked('git commit -m "kubectl apply -f x.yaml"', allowlist).blocked


def test_verdict_renders_its_constraint(allowlist: Path) -> None:
    verdict = _blocked("kubectl apply -f x", allowlist)
    assert verdict.render().startswith("BLOCKED (C-1)")
    assert guard.Verdict(False).render() == "ALLOW"


# --- main(): the CLI boundary ----------------------------------------------
#
# The bash wrapper is covered end-to-end by tests/governance, but coverage.py
# cannot see a subprocess-spawned interpreter, so main()'s branches are
# exercised directly here too.


def _payload(command: str) -> str:
    import json

    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def test_main_allows_benign(
    allowlist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload("pytest -q")))
    assert guard.main(["--allowlist", str(allowlist)]) == 0
    assert capsys.readouterr().err == ""


def test_main_blocks_and_names_the_constraint(
    allowlist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload("kubectl apply -f x")))
    assert guard.main(["--allowlist", str(allowlist)]) == 2
    assert "BLOCKED (C-1)" in capsys.readouterr().err


def test_main_empty_command_allows(allowlist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload("")))
    assert guard.main(["--allowlist", str(allowlist)]) == 0


def test_main_unanalyzable_dangerous_payload_fails_closed(
    allowlist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json -- kubectl delete ns prod"))
    assert guard.main(["--allowlist", str(allowlist)]) == 2
    assert "unanalyzable payload" in capsys.readouterr().err


def test_main_unanalyzable_benign_payload_allows(
    allowlist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json -- echo hello"))
    assert guard.main(["--allowlist", str(allowlist)]) == 0


def test_main_debug_traces_segments(
    allowlist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload("echo a && echo b")))
    assert guard.main(["--allowlist", str(allowlist), "--debug"]) == 0
    assert "guard: segment=" in capsys.readouterr().err


def test_main_passes_the_effective_remote_through(
    allowlist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload("git push")))
    blocked = guard.main(["--allowlist", str(allowlist), "--effective-remote", "https://evil/x"])
    assert blocked == 2
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload("git push")))
    allowed = guard.main(
        [
            "--allowlist",
            str(allowlist),
            "--effective-remote",
            "https://github.com/ianshank/Orbital-Drift.git",
        ]
    )
    assert allowed == 0


# --- the segment work ceiling (RB-009) -------------------------------------
#
# `split_segments` stops at _MAX_SEGMENTS. Everything below pins the one thing
# that matters when it does: a command the segmenter could not finish reading
# is UNANALYSED, and an unanalysed command must never read as permission.
# Measured 2026-08-22 before the fix, `analyze` returned ALLOW for a payload
# whose only content was a command denied in every mode.


def test_segment_queue_has_a_work_ceiling(allowlist: Path) -> None:
    """A pathological input must terminate rather than spin — and it must come
    back BLOCKED, because a split that stopped early proves nothing about the
    part it never read.

    The two assertions this replaces (``isinstance(..., list)`` and
    ``is not None``) were unfalsifiable: they held with ``_MAX_SEGMENTS`` at
    10**9, and they held while this very input was being ALLOWED. The payload
    is deliberately BENIGN — refusing to analyse *is* the verdict, so the
    accepted false positive is pinned here rather than met in an incident.
    """
    pathological = _nested("echo hi", _TRUNCATING_DEPTH)
    assert len(guard.split_segments(pathological)) <= guard._MAX_SEGMENTS
    verdict = guard.analyze(pathological, allowlist=allowlist)
    assert verdict.blocked and verdict.constraint == "C-1", verdict


@pytest.mark.parametrize("depth", [_TRUNCATING_DEPTH, _WELL_PAST_TRUNCATING_DEPTH])
def test_truncated_analysis_fails_closed(depth: int, allowlist: Path) -> None:
    """The measured fail-open: the ceiling was reached before the denied verb
    was ever popped off the queue, so `analyze` iterated an empty list, fell
    through every check and allowed a command the deny-list forbids outright.
    """
    verdict = _blocked(_nested(_DENIED, depth), allowlist)
    assert verdict.blocked, f"depth {depth} was ALLOWED — the guard failed open"
    assert verdict.constraint == "C-1", verdict
    assert "ceiling" in verdict.reason, verdict.reason


@pytest.mark.parametrize("depth", [1, _LAST_ANALYZABLE_DEPTH])
def test_below_the_ceiling_the_command_is_still_read(depth: int, allowlist: Path) -> None:
    """No regression, and no false comfort: under the ceiling the BLOCK must
    still come from finding the denied verb, not from refusing to look. Without
    this, a fix that blocked every nested payload outright would look identical
    to a working analyser.
    """
    verdict = _blocked(_nested(_DENIED, depth), allowlist)
    assert verdict.blocked and verdict.constraint == "C-1", verdict
    assert "denied in every mode" in verdict.reason, verdict.reason
    assert "ceiling" not in verdict.reason, verdict.reason


def test_wide_substitution_fan_out_also_fails_closed(allowlist: Path) -> None:
    """Same defect, no nesting at all, and a PARTIAL split rather than an empty
    one — so a fix keyed on "no segments" would not close it.

    One pass lifts every sibling substitution onto the queue, which is LIFO, so
    the leftmost body is popped last. Put the denied command first, follow it
    with enough harmless substitutions to reach `_TRUNCATING_SIBLINGS`, and it
    is never reached. Measured 2026-08-22 pre-fix: 511 segments, verdict ALLOW.
    """
    command = f"echo $({_DENIED}) " + "$(echo x) " * (_TRUNCATING_SIBLINGS - 1)
    verdict = _blocked(command, allowlist)
    assert verdict.blocked, "a partially-read command was ALLOWED"
    assert verdict.constraint == "C-1", verdict
    assert "ceiling" in verdict.reason, verdict.reason


def test_a_benign_command_with_many_segments_is_still_allowed(allowlist: Path) -> None:
    """SEGMENT COUNT IS NOT THE CEILING — the ceiling counts queue iterations.

    A `;`-chain of N commands is split in a single pass, so it costs one
    iteration no matter how long it is. This chain yields MORE segments than
    `_MAX_SEGMENTS` itself and must still be judged on its contents and
    allowed. Without this the fix could tighten into `len(segments) > k` and
    nothing would notice: an unread command and a long-but-fully-read one are
    opposite states, and only one of them is a refusal.
    """
    count = guard._MAX_SEGMENTS + 1
    chain = "; ".join("echo x" for _ in range(count))
    segments, truncated = guard._split_segments(chain)
    assert truncated is False, "a fully-split chain was reported truncated"
    assert len(segments) == count
    assert not guard.analyze(chain, allowlist=allowlist).blocked


def test_split_segments_reports_truncation_to_policy_callers() -> None:
    """The checked form exists to tell "nothing to object to" apart from "never
    looked" — the distinction the public list return shape cannot express, and
    the reason the fail-open survived review.
    """
    segments, truncated = guard._split_segments("echo a && echo b")
    assert truncated is False
    assert set(segments) == {"echo a", "echo b"}

    partial, truncated = guard._split_segments(_nested(_DENIED, _TRUNCATING_DEPTH))
    assert truncated is True
    assert partial == [], f"the denied body should never have been reached: {partial}"

    # THE EXACT-DRAIN CASE, which is the whole reason the flag is the QUEUE and
    # not the iteration count. `_LAST_COMPLETE_SIBLINGS` bodies consume the
    # ceiling to the last iteration and leave nothing queued: the split IS
    # complete, so reporting it truncated would refuse a command the guard read
    # in full. A count-based flag (`guard_rail >= _MAX_SEGMENTS`) passes every
    # other assertion in this file and fails only here.
    drained, truncated = guard._split_segments("echo " + "$(echo x) " * _LAST_COMPLETE_SIBLINGS)
    assert truncated is False, "a complete split that used the last iteration was called truncated"
    assert drained == ["echo"] + ["echo x"] * _LAST_COMPLETE_SIBLINGS


def test_excerpt_is_lossless_below_the_bound_and_says_so_above_it() -> None:
    """A diagnostic must not quietly shorten a command that fits.

    In practice only huge commands reach the truncation block — the shortest
    input that can truncate is ~775 characters — so the short branch is
    defensive. Pinned anyway: the day this helper is reused for a segment or a
    push destination, silently dropping the tail would be a real defect.
    """
    short = "echo hi"
    assert guard._excerpt(short) == short

    long_command = "x" * (guard._EXCERPT_CHARS + 500)
    rendered = guard._excerpt(long_command)
    assert rendered.startswith("x" * guard._EXCERPT_CHARS)
    assert len(rendered) < len(long_command)
    assert "500 more characters" in rendered, rendered


def test_truncation_verdict_shows_the_operator_a_bounded_excerpt(allowlist: Path) -> None:
    """Every other block in this module names the segment it objected to; the
    truncation block has no segment to name, so it quotes the COMMAND instead —
    bounded, because the payloads that reach it are kilobytes long and this
    string is written to an operator's stderr.

    THE RENDER ASSERTION IS THE LOAD-BEARING ONE. `Verdict.render()` formats
    the constraint and the reason and nothing else, and `main()` writes
    `render()`; every other block gets its segment onto stderr by putting it IN
    the reason (`...; segment: {segment}`). So a populated `.segment` that no
    reason interpolates is invisible, and a test asserting only the field would
    pass while the operator saw nothing — a decorative assertion about a
    decorative field.
    """
    command = _nested(_DENIED, _TRUNCATING_DEPTH)
    verdict = _blocked(command, allowlist)
    assert verdict.segment, "the truncation block names nothing at all"
    assert command.startswith(verdict.segment[:40]), verdict.segment
    assert len(verdict.segment) < len(command), "the whole payload was echoed back"
    assert len(verdict.segment) <= guard._EXCERPT_CHARS + 60, len(verdict.segment)

    rendered = verdict.render()
    assert verdict.segment in rendered, (
        "the excerpt never reaches stderr — main() writes render(), which "
        f"formats constraint + reason only. rendered was: {rendered}"
    )
    assert len(rendered) < len(command), "render() echoed the whole payload back"


def test_main_debug_reports_truncation(
    allowlist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """GUARD_DEBUG must not go silent in the one case it is most needed.

    The public `split_segments` returns an EMPTY list for a truncating payload,
    so tracing through it printed the verdict and not one `guard: segment=`
    line — the operator saw a block with no evidence, which is the same
    invisible truncation that caused RB-009 in the first place.
    """
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(_payload(_nested(_DENIED, _WELL_PAST_TRUNCATING_DEPTH)))
    )
    assert guard.main(["--allowlist", str(allowlist), "--debug"]) == 2
    err = capsys.readouterr().err
    assert "guard: TRUNCATED" in err, err[:400]
    assert "BLOCKED (C-1)" in err


# --- arcs the statement counter could not see (RB-008 part 3) --------------
#
# Everything below was written because `--cov-branch` made it visible.
# Statement coverage reported each of these `if` lines as executed the moment
# any test ran the condition; the arc INTO the body was never taken, so the
# body's behaviour was unproved. Measured at 18330d4: guard.py carried five
# such partial arcs, and this section closes four of them — 335->336,
# 319->320, 214->220, 258->259. Each test is paired, in its docstring, with the
# one-line production mutation that reddens it: a test that survives every
# mutation of the line it claims to cover is measuring nothing, and an arc
# closed by such a test is a number, not a proof.


@pytest.mark.parametrize(
    "command",
    [
        "bash -c",  # `-c` as the final token
        "bash -x -c",  # `-c` final AFTER another flag: `index` is not 1
        "sh -c",  # not bash-specific
        "/bin/bash -c",  # path form: `head` comes from `_basename`
    ],
)
def test_a_shell_dash_c_with_nothing_to_analyze_fails_closed(command: str, allowlist: Path) -> None:
    """A shell `-c` with no argument is a BLOCK no test had ever exercised.

    This is the highest-value arc `--cov-branch` exposed (guard.py:335->336),
    and it sits in the module whose OTHER fail-open — an empty segment list
    read as "nothing to object to" — is what RB-009 was raised to close. Same
    defect class: `analyze` meets an interpreter whose `-c` payload it cannot
    see, and the only safe reading of "cannot see" is BLOCK. The module
    docstring already promised that ("Every uncertain path returns a BLOCK");
    until now nothing checked that this path agreed.

    Both reaching shapes are covered because the index arithmetic differs:
    `bash -c` puts `-c` at index 1, `bash -x -c` at index 2, so a bounds check
    written against a fixed position instead of `index + 1 >= len(argv)` would
    pass the first and fail the second.

    THE MUTATION THAT REDDENS THIS: replace the `return Verdict(True, "C-1",
    ...)` at guard.py:336-341 with `continue`. That is precisely the fail-open
    the arc guards — an unreadable interpreter invocation waved through — and
    it turns every assertion below from blocked=True to blocked=False.
    """
    verdict = _blocked(command, allowlist)
    assert verdict.blocked, f"{command!r} was allowed with no payload to analyze: {verdict}"
    assert verdict.constraint == "C-1", verdict
    assert "no argument to analyze" in verdict.reason, verdict.reason
    # The operator has to be able to tell WHICH segment objected: render()
    # formats constraint + reason and nothing else, so a segment that no reason
    # interpolates never reaches stderr.
    assert command in verdict.render(), verdict.render()


# NOTE ON THE MISSING CONTROL. The ALLOW side of the arc above — a `-c` that
# DOES have a payload — is deliberately not re-asserted here. It already has
# two tests, `test_shell_dash_c_with_safe_payload_allows` (`bash -c "pytest
# -q"` allows) and `test_shell_dash_c_payloads_are_analyzed_as_code` (the
# payload is really re-analyzed), both of which take the 335->False arc, so the
# analyzer cannot be passing the test above by having turned blanket-hostile to
# the token `-c`. Adding a third would be a redundant test, which RB-008 part 2
# is in the business of removing.


@pytest.mark.parametrize(
    "segment",
    [
        "# just a comment",  # shlex drops it entirely (comments=True)
        "FOO=bar",  # a bare assignment, stripped as a leading VAR=value
        "FOO=bar BAZ=qux",  # ...and the loop must strip ALL of them
        "builtin",  # a bare WRAPPERS entry with nothing wrapped
        "FOO=bar # trailing",  # both mechanisms in one segment
    ],
)
def test_a_segment_that_tokenizes_to_nothing_is_skipped_not_misread(
    segment: str, allowlist: Path
) -> None:
    """Closes TWO arcs at once: `_argv`'s `while argv:` exhausting (214->220)
    and `analyze`'s `if not argv: continue` (319->320).

    They are one path seen twice. `_argv` strips leading `VAR=value`
    assignments and wrapper commands; a segment made of nothing else strips to
    the empty list, which the loop in `analyze` must SKIP. Skipping is not a
    detail: the next segment of the same command line is where the real verb
    usually sits, so the difference between `continue` and any early exit is
    the difference between reading `FOO=bar && kubectl delete ns prod` and
    stopping at `FOO=bar`.

    THE MUTATIONS THAT REDDEN THIS, one per arc:

    * 214->220 — change `while argv:` (guard.py:214) to `while len(argv) > 1:`.
      `_argv("FOO=bar")` then returns `["FOO=bar"]`, and the first assertion
      fails.
    * 319->320 — change the `continue` (guard.py:320) to
      `return Verdict(False)`. The empty segment then ends the whole analysis,
      and the compound assertion below fails because the denied verb after it
      is never read.
    """
    assert guard._argv(segment) == [], f"{segment!r} did not strip to nothing"
    assert not _blocked(segment, allowlist).blocked

    verdict = _blocked(f"{segment}\n{_DENIED}", allowlist)
    assert verdict.blocked and verdict.constraint == "C-1", (
        f"an empty segment ended the analysis instead of being skipped, so "
        f"{_DENIED!r} after {segment!r} was never classified: {verdict}"
    )


@pytest.mark.parametrize("command", ["terraform", "helm"])
def test_a_conditional_command_with_no_subcommand_matches_no_readonly_form(
    command: str, allowlist: Path
) -> None:
    """Closes guard.py:258->259, the `len(argv) < len(form): continue` arc.

    Every entry in `READONLY_FORMS` is a (command, subcommand) PAIR, so a
    single-token argv is shorter than all five and can match none — which is
    the right answer, because a bare `terraform` is not `terraform validate`
    and must not inherit its permission. Reaching the arc needs no contrivance:
    `terraform` on its own is a command a human types.

    THE MUTATION THAT REDDENS THIS: change the `continue` at guard.py:259 to
    `return form`. `_readonly_prefix(["terraform"])` then answers with the
    first form in the tuple — `("helm", "template")` — instead of None, the
    first assertion fails, and a bare `terraform` is ALLOWED on a permission it
    never matched.
    """
    assert guard._readonly_prefix([command]) is None
    verdict = _blocked(command, allowlist)
    assert verdict.blocked and verdict.constraint == "C-1", verdict


def test_a_single_token_command_outside_the_conditional_set_is_the_control(
    allowlist: Path,
) -> None:
    """The same arc, ALLOW side: a one-token argv is shorter than every form,
    comes back None, and `ls` is then judged on not being a conditional command
    rather than on having failed to match one.

    NOT redundant with `ls -la` in `test_permitted_commands_allow`, which is
    the obvious objection. That argv is two tokens, so `len(argv) < len(form)`
    is FALSE for all five forms and the 258->259 arc is never reached; the
    single-token shape is the only one that gets there. The bare `_readonly_
    prefix` assertion is likewise made nowhere else.
    """
    assert guard._readonly_prefix(["ls"]) is None
    assert not _blocked("ls", allowlist).blocked
