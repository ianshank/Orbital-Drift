"""Pinned verdicts for the PreToolUse guard (charter C-1/C-5).

Every "blocks X" / "allows Y" claim in scripts/pretooluse_guard.sh's header is
probed for real here, and each block asserts the REASON string, not merely the
exit code — a guard test that matches exit codes alone can stay green via an
unrelated fail-closed error path with the actual fix removed (donor-kit
incident; adversarial-reviewer tooling-diff protocol).

REGRESSION CORPUS. Every `LAUNDERING` case below was measured ALLOWED by a
SHIPPED implementation of this guard: the first nine by the original sed + ERE
tokenizer (the reason tokenization moved into orbital_drift.guard), and the
last by the shlex rewrite itself, which fell off its own segment work ceiling
and allowed a denied command outright (RB-009). They must never pass again.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
GUARD: Final = REPO_ROOT / "scripts" / "pretooluse_guard.sh"
ALLOWLIST: Final = REPO_ROOT / ".claude" / "allowed-remotes.txt"
#: Subprocess ceiling: the guard shells out to git and python; anything slower
#: than this is a hang, not a slow box. Every subprocess in this repo sets one.
TIMEOUT: Final = 60.0


def _bash() -> str:
    found = shutil.which("bash")
    assert found, "bash is required to run the guard (Git Bash on Windows)"
    return found


def _env(**overrides: str) -> dict[str, str]:
    """The environment every probe in this module hands the wrapper.

    One home, because the tests, the debug test and the interpreter probe below
    must all present the wrapper with the SAME environment — a probe that
    resolved a different interpreter than the test it vouches for would be
    worse than no probe.
    """
    import os

    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
    env.pop("GUARD_DEBUG", None)
    env.update(overrides)
    return env


def _run(payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        env=_env(),
    )


@pytest.fixture(scope="module", autouse=True)
def _analyzer_under_test_is_this_checkout() -> None:
    """Pin WHICH `orbital_drift` the wrapper's subprocess actually loads.

    The wrapper runs `${REPO_ROOT}/.venv/bin/python -m orbital_drift.guard`, so
    the module it analyses with is whatever that interpreter resolves — not
    necessarily the tree pytest is collecting from. In a git worktree whose
    `.venv` is a symlink to another checkout's venv, the editable install
    resolves to that OTHER checkout: measured 2026-08-22, the whole in-process
    suite went green against a fixed guard while every test in THIS module
    still ran the unfixed one and reported exit 0 on a payload that must block.

    That is a false green in the fail-open direction, and no assertion in this
    file could see it. So the interpreter is asked directly, once per module,
    through the same `_lib.sh` resolution the wrapper uses rather than a second
    copy of that probe order, and the answer must live under REPO_ROOT.
    """
    resolver = f'. "{REPO_ROOT}/scripts/_lib.sh"; od_find_python "{REPO_ROOT}"'
    found = subprocess.run(
        [_bash(), "-c", resolver],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        env=_env(),
    )
    interpreter = found.stdout.strip()
    assert interpreter, f"the wrapper would find no interpreter: {found.stderr}"

    located = subprocess.run(
        [interpreter, "-c", "import orbital_drift.guard as g; print(g.__file__)"],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        env=_env(),
    )
    assert located.returncode == 0, f"{interpreter} cannot import the guard: {located.stderr}"
    module = Path(located.stdout.strip()).resolve()
    assert module.is_relative_to(REPO_ROOT), (
        f"the wrapper's interpreter ({interpreter}) analyses commands with\n"
        f"    {module}\n"
        f"which is OUTSIDE this checkout ({REPO_ROOT}). Every boundary verdict in\n"
        "this module would be a verdict about someone else's code. Fix the\n"
        f"environment (e.g. PYTHONPATH={REPO_ROOT / 'src'}, or install this tree\n"
        "editable into the venv the wrapper resolves) — do not weaken this check."
    )


def _verdict(command: str) -> subprocess.CompletedProcess[str]:
    return _run(json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}))


# --- the regression corpus: every shape the sed+ERE guard let through -------

#: Nesting depth at which the segmenter's work ceiling truncates the split.
#:
#: Each `$( )` level costs TWO iterations of guard.split_segments' queue: the
#: lifted body is queued AND the stripped remainder is re-queued. The queue is
#: LIFO, so the body lifted on the first pass — the one holding the real
#: command — lands at the bottom and is popped LAST, on iteration
#: `2 * depth + 1`. Against the ceiling of 512 that is out of reach from
#: `512 // 2` = 256 levels up, so the split returned an EMPTY list, analyze()
#: looped over nothing, and this wrapper exited 0 (ALLOW).
#:
#: Measured 2026-08-22 through this very wrapper: depth 1 -> exit 2, depth 255
#: -> exit 2, depth 256 -> exit 0 for a command denied in every mode (RB-009).
#: Deliberately a literal here, not `guard._MAX_SEGMENTS // 2` — this suite
#: drives the guard as a black box, through its real entry point, and importing
#: the module under test to compute its own payload would let a change to the
#: ceiling silently re-aim the probe. If the ceiling moves, the reason
#: assertion in test_ceiling_truncation_is_blocked_at_the_boundary fails and
#: says so.
CEILING_NESTING_DEPTH: Final = 256

#: RB-009's payload: a denied command buried under `CEILING_NESTING_DEPTH`
#: command substitutions. Exercised at the boundary because that is the only
#: place the bug was ever visible — in-process the analyzer merely returned a
#: verdict object; here it is an exit code that permits a tool call.
NESTED_PAST_CEILING: Final = (
    "$(" * CEILING_NESTING_DEPTH + "kubectl delete ns prod" + ")" * CEILING_NESTING_DEPTH
)

#: Deepest nesting the segmenter still reads IN FULL — the ALLOW half of
#: RB-009's contract ("the ceiling SHALL NOT become a blanket refusal"), which
#: until now was pinned in-process only.
#:
#: DERIVED from `CEILING_NESTING_DEPTH`, not chosen: that constant is the FIRST
#: depth at which the split truncates, so one level below it is the last that
#: does not. Measured 2026-08-22 at 11af312 against the shipped ceiling of 512
#: (`2 * 255 + 1 = 511 <= 512` reads in full; `2 * 256 + 1 = 513` does not).
#: The MEASURED gap this closes: the rest of the ALLOWED corpus nests at most
#: one level deep, so a guard mutated to refuse anything past ~50 queue
#: iterations — a blanket refusal of exactly the deep band the ceiling governs
#: — leaves this whole boundary suite green. Should the ceiling ever be
#: LOWERED past this depth, this entry reddens as a BLOCK; that failure is the
#: intended loud one, and its fix is here, not in the guard.
DEEP_BUT_READABLE_DEPTH: Final = CEILING_NESTING_DEPTH - 1

#: A BENIGN command at that depth. Benign is the point: `NESTED_PAST_CEILING`
#: proves an unreadable command is refused, and only a readable one can prove
#: the refusal is aimed rather than blanket.
NESTED_BELOW_CEILING: Final = (
    "$(" * DEEP_BUT_READABLE_DEPTH + "echo hi" + ")" * DEEP_BUT_READABLE_DEPTH
)

#: How many `;`-separated commands the benign ALLOW control chains together.
#: Deliberately MORE than the queue budget the block above is about: a chain is
#: split in one pass, so segment count and queue budget are different
#: quantities and the guard must not conflate them.
#:
#: DERIVED, not chosen, so the claim in that name cannot quietly stop being
#: true. `test_ceiling_truncation_is_blocked_at_the_boundary` asserts the
#: nested payload at `CEILING_NESTING_DEPTH` truncates, and a nested payload
#: truncates exactly when `2 * depth + 1` exceeds the ceiling. So for as long
#: as that assertion holds, the ceiling is strictly below the value below —
#: i.e. this chain really does carry more segments than the guard's whole
#: iteration budget. Raise `_MAX_SEGMENTS` and that assertion goes red first,
#: which is the loud failure this constant would otherwise lack: a hard 513
#: would degrade in silence into an ordinary long-chain ALLOW case.
SEGMENTS_PAST_THE_CEILING: Final = 2 * CEILING_NESTING_DEPTH + 1


def _corpus_id(value: object) -> str | None:
    """Readable pytest ids for every corpus entry that is payload-sized:
    the RB-009 nested payload (790 characters — mostly `$(` — at
    ``CEILING_NESTING_DEPTH`` 256: ``2 * 256`` opening, 22 for the command, 256
    closing), its below-ceiling sibling ``NESTED_BELOW_CEILING`` (772, one
    nesting level shallower), and the ``SEGMENTS_PAST_THE_CEILING`` chain. The
    two nested payloads share a long ``$($($(`` prefix, so it is the length in
    the id that tells them apart. pytest would
    otherwise use either verbatim as the test id, including in the
    ``--collect-only -q`` probe ci/checks.sh runs over this suite.

    Returning ``None`` falls back to pytest's own id, so every pre-existing
    case keeps the id it has always had.
    """
    if isinstance(value, str) and len(value) > 72:
        return f"{value[:20]}...<{len(value)} chars>"
    return None


LAUNDERING: Final[tuple[tuple[str, str], ...]] = (
    # Co-located `git` token routed the whole segment into the push branch,
    # which resolved `origin` (allow-listed) and continued.
    ("kubectl delete ns $(git rev-parse --abbrev-ref HEAD)", "C-1"),
    ("helm upgrade rel ./chart --set sha=$(git rev-parse HEAD)", "C-1"),
    ("terraform apply -var git_sha=$(git rev-parse HEAD)", "C-1"),
    # An ALLOWED_READONLY prefix laundered a dangerous token later in the
    # segment, because `$(...)`, backticks and bare `&` were not separators.
    ("helm template . --set x=$(kubectl get nodes -o name)", "C-1"),
    ("helm template . & kubectl apply -f x", "C-1"),
    ("terraform validate `kubectl apply -f x`", "C-1"),
    ("helm lint . $(argocd app sync foo)", "C-1"),
    # A flag value containing `/` hid the push verb from the ERE entirely.
    ("git -C /somewhere push evil main", "C-5"),
    # An unparseable destination fell back to `origin` and was allowed.
    ("git -c user.name=x push evil", "C-5"),
    # Not a tokenizer failure but a WORK-CEILING failure: the segmenter ran out
    # of queue budget before it reached the denied command, returned nothing,
    # and the analyzer read "no segments" as "nothing to object to" (RB-009).
    (NESTED_PAST_CEILING, "C-1"),
)

BLOCKED: Final[tuple[tuple[str, str], ...]] = (
    ("kubectl apply -f x.yaml", "C-1"),
    ("kubectl get pods", "C-1"),  # settings deny-list is stricter; guard matches it
    ("git commit -m ok && kubectl delete pod x", "C-1"),
    ("git commit && argo submit wf.yaml", "C-1"),  # bare argo
    ("argocd app sync x", "C-1"),
    ("terraform plan", "C-1"),
    ("terraform apply -auto-approve", "C-1"),
    ("terraform init", "C-1"),  # without -backend=false it initializes a real backend
    ("helm install release ./chart", "C-1"),
    ("kustomize build overlays/prod", "C-1"),
    ("git push evil main", "C-5"),
    ("sudo kubectl apply -f x.yaml", "C-1"),  # wrapper stripped
    ("env FOO=bar kubectl apply -f x.yaml", "C-1"),
    ("FOO=bar kubectl apply -f x.yaml", "C-1"),  # leading assignment stripped
    ("/usr/bin/kubectl apply -f x.yaml", "C-1"),  # absolute path resolves to basename
    ("echo hi; kubectl apply -f x", "C-1"),
    ("echo hi | kubectl apply -f -", "C-1"),
    ('kubectl apply -f "unterminated', "C-1"),  # unparseable -> fail closed
    # `-c` payloads are CODE: quoting must not launder them. Without the
    # SHELLS branch every rule above is one `sh -c` away from irrelevant.
    ('bash -c "kubectl apply -f x.yaml"', "C-1"),
    ("sh -c 'terraform apply -auto-approve'", "C-1"),
    ("""bash -c "sh -c 'kubectl delete ns prod'" """.strip(), "C-1"),
    ('bash -c "git push evil main"', "C-5"),
    ("bash -c", "C-1"),  # -c with no argument is unanalyzable -> fail closed
)

ALLOWED: Final[tuple[str, ...]] = (
    "pytest -q",
    "git status",
    "git commit -m 'ordinary message'",
    # A quoted mention is DATA, not a command. The regex implementation could
    # not tell the two apart and blocked this as an accepted false positive;
    # the lexer can, so it no longer has to. `bash -c "<same string>"` is
    # code and is in BLOCKED above — that is the distinction that matters.
    'git commit -m "kubectl apply -f x.yaml"',
    'echo "run kubectl apply yourself"',
    "helm template ./chart",
    "helm template . --set image.tag=v1",
    "helm lint ./chart",
    "terraform validate",
    "terraform fmt -check",
    "terraform init -backend=false",
    "sh ci/checks.sh lint",
    "ls -la && echo done",
    "python -m orbital_drift.traceability --json",
    # SUBSTITUTIONS IN ORDINARY USE. The segment ceiling makes an unreadable
    # command fail closed; it must not make a readable one fail closed too.
    # Every other entry above is substitution-free, so without these the whole
    # corpus would stay green if the guard started refusing `$( )` outright.
    "echo $(git rev-parse HEAD)",
    "cd $(git rev-parse --show-toplevel) && pytest -q",
    "make pre-pr 2>&1 | tee /tmp/`date +%s`.log",
    # ...INCLUDING DEEP ONES, right up to the last depth the segmenter reads in
    # full. The three entries above nest ONE level, so they cannot tell an
    # aimed refusal from a blanket one anywhere past that: the spec delta's
    # "the ceiling SHALL NOT become a blanket refusal" is a claim about the
    # DEEP band, and this is the only entry in the corpus standing in it.
    NESTED_BELOW_CEILING,
    # MANY SEGMENTS IS NOT TRUNCATION. A `;`-chain is split in a single pass,
    # so it costs one queue iteration however long it runs; this one yields
    # more segments than the ceiling itself and must still be allowed. It is
    # the control that separates "never read this command" from "read all of
    # this command, at length" — a count-based ceiling would confuse the two.
    "; ".join("echo x" for _ in range(SEGMENTS_PAST_THE_CEILING)),
)


@pytest.mark.parametrize(("command", "constraint"), LAUNDERING, ids=_corpus_id)
def test_laundering_shapes_are_blocked(command: str, constraint: str) -> None:
    """The measured bypass corpus. A regression here is a live fail-open."""
    result = _verdict(command)
    assert result.returncode == 2, (
        f"LAUNDERING REGRESSION — {command!r} was allowed (rc={result.returncode}). "
        f"stderr: {result.stderr}"
    )
    assert f"BLOCKED ({constraint}" in result.stderr, (
        f"block must name its constraint for {command!r}; stderr was: {result.stderr}"
    )


def test_ceiling_truncation_is_blocked_at_the_boundary() -> None:
    """The block for RB-009's payload must come from REFUSING TO ANALYSE it.

    The corpus entry above pins the exit code; this pins the reason, and with
    it the probe's aim. Should `_MAX_SEGMENTS` ever REACH
    `2 * CEILING_NESTING_DEPTH + 1` — the iteration on which the innermost body
    is popped, and iteration i runs while i <= the ceiling — this payload would
    be fully unwrapped and blocked on the denied verb instead: still exit 2, so
    the corpus entry would stay green while no longer testing truncation at
    all. This assertion fails loudly in that case and names the constant to
    update.
    """
    result = _verdict(NESTED_PAST_CEILING)
    assert result.returncode == 2, (
        f"FAIL-OPEN at the enforcement boundary: {CEILING_NESTING_DEPTH} nested "
        f"substitutions were allowed (rc={result.returncode})"
    )
    assert "ceiling" in result.stderr, (
        "expected a refusal-to-analyse block naming the segment ceiling; "
        f"CEILING_NESTING_DEPTH may no longer truncate. stderr was: {result.stderr}"
    )
    # ...and the operator must be shown WHICH command was refused, on the
    # ordinary path, with no GUARD_DEBUG. This block names no segment because
    # there is none, so it quotes a bounded excerpt of the command instead: a
    # refusal that identifies nothing is a refusal nobody can act on.
    assert NESTED_PAST_CEILING[:40] in result.stderr, (
        f"the block quotes no part of the command it refused; stderr was: {result.stderr}"
    )
    assert len(result.stderr) < len(NESTED_PAST_CEILING), (
        f"the block echoed the payload back: {len(result.stderr)} chars of stderr for a "
        f"{len(NESTED_PAST_CEILING)}-char command"
    )


@pytest.mark.parametrize(("command", "constraint"), BLOCKED)
def test_blocks_with_reason(command: str, constraint: str) -> None:
    result = _verdict(command)
    assert result.returncode == 2, (
        f"expected BLOCK (exit 2) for {command!r}, got {result.returncode}: {result.stderr}"
    )
    assert f"BLOCKED ({constraint}" in result.stderr, (
        f"block must name its constraint for {command!r}; stderr was: {result.stderr}"
    )


@pytest.mark.parametrize("command", ALLOWED, ids=_corpus_id)
def test_allows(command: str) -> None:
    result = _verdict(command)
    assert result.returncode == 0, (
        f"expected ALLOW (exit 0) for {command!r}, got {result.returncode}: {result.stderr}"
    )


def test_allowlisted_push_is_allowed() -> None:
    """A push to a remote in the allowlist passes the guard.

    Reads the allowlist rather than hard-coding `origin` so the test survives a
    fork whose origin differs (audit TD-6).
    """
    entries = [
        line.strip()
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert entries, "allowlist is empty — the C-5 gate would have nothing to permit"
    result = _verdict(f"git push {entries[0]} 001-orbital-drift-ct")
    assert result.returncode == 0, result.stderr


def test_bare_push_resolves_the_effective_remote() -> None:
    """`git push` with no destination must resolve, not assume, and this repo's
    effective remote is allow-listed — so it is permitted."""
    result = _verdict("git push")
    assert result.returncode == 0, result.stderr


def test_unanalyzable_dangerous_payload_fails_closed() -> None:
    result = _run("not json at all -- kubectl delete ns prod")
    assert result.returncode == 2, "dangerous-shaped unparsable payload must BLOCK"
    assert "BLOCKED" in result.stderr


def test_unanalyzable_benign_payload_is_allowed() -> None:
    result = _run("not json at all -- echo hello")
    assert result.returncode == 0, result.stderr


def test_empty_command_is_allowed() -> None:
    result = _verdict("")
    assert result.returncode == 0, result.stderr


def _debug_run(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(GUARD)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
        env=_env(GUARD_DEBUG="1"),
    )


def test_debug_mode_traces_segments() -> None:
    """GUARD_DEBUG renders the parsed segments — the affordance whose absence
    let the laundering bugs survive a full review cycle."""
    result = _debug_run("echo a && echo $(echo b)")
    assert result.returncode == 0, result.stderr
    assert "guard: segment=" in result.stderr, result.stderr


def test_debug_mode_says_so_when_the_trace_is_incomplete() -> None:
    """A trace that stops early must SAY it stopped early.

    On a NESTED truncating payload the segment list comes back empty, so the
    trace printed nothing whatsoever and the operator saw a block with no
    evidence — the same silent shortfall as the bug itself.

    The length assertion below is scoped to THAT shape and is not a general
    claim about debug output. This payload traces zero segment lines, so its
    stderr is two fixed lines carrying one bounded excerpt (measured
    2026-08-22: 790-char payload -> 514 chars over 2 lines). A WIDE fan-out
    legitimately exceeds its own payload length, because every lifted segment
    gets a line of its own: `echo $(<denied>) ` plus 600 siblings measured
    6031 -> 12780 chars over 513 lines. That output is bounded by
    `_MAX_SEGMENTS` lines rather than by payload size — identical 12780 for a
    10031-char payload — and it is opt-in behind GUARD_DEBUG, so it is a
    verbosity property, not a hazard.
    """
    result = _debug_run(NESTED_PAST_CEILING)
    assert result.returncode == 2, result.stderr
    assert "guard: TRUNCATED" in result.stderr, result.stderr
    assert len(result.stderr) < len(NESTED_PAST_CEILING), (
        f"the trace echoed the payload back ({len(result.stderr)} chars of stderr for a "
        f"{len(NESTED_PAST_CEILING)}-char command)"
    )
