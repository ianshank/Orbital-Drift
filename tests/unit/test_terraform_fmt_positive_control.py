"""Positive/negative controls for the terraform-fmt hook, run against the pinned image.

WHY A POSITIVE CONTROL IS THE ONLY THING THAT COUNTS HERE
-----------------------------------------------------------
``tests/unit/test_ci_contract.py`` and ``tests/unit/test_version_pins.py`` assert
that the hook is *wired up*: right image, right digest, right flags, called from
``stage_hooks``. None of that proves the flags actually gate anything — a hook
whose ``entry:`` silently no-ops (wrong subcommand, a typo'd flag cobra ignores,
an image that doesn't ship a ``terraform`` binary at the expected path) would
pass every one of those tests while checking nothing. ``adversarial-reviewer.md``'s
own bar (ported from the deleted ``peer-reviewer.md`` per RB-006): "a stub-only
gate is a BLOCK."

So: run the real, digest-pinned container against real HCL fixtures and observe
the exit code ``terraform fmt -check`` actually produces — not a paraphrase of
what the flag is documented to do.

These tests need Docker. They do not need the network after the first run: the
image is digest-pinned, so a cached copy is byte-identical.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from shell_harness import PINS

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PRE_COMMIT_CONFIG: Final = REPO_ROOT / ".pre-commit-config.yaml"

TERRAFORM_IMAGE: Final[str] = PINS["TERRAFORM_IMAGE"]


def _tool(name: str) -> str:
    """Absolute path to a required external tool.

    Skips locally when it is genuinely absent, but never in CI: a positive
    control that quietly turns itself off is worth less than no control, because
    it is counted as coverage. GitHub-hosted runners set ``CI=true`` and always
    provide Docker.
    """
    found = shutil.which(name)
    if found is not None:
        return found
    if os.environ.get("CI"):
        raise RuntimeError(
            f"{name} is not on PATH. These are the only tests that prove the "
            "terraform-fmt hook's flags actually gate real HCL against the pinned "
            "image; they may not be skipped in CI."
        )
    pytest.skip(f"{name} is not on PATH; the pinned-container positive controls need it")


def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        errors="replace",
        cwd=None if cwd is None else str(cwd),
        timeout=120,
        check=False,
    )


def _posix(path: Path) -> str:
    """A bind-mount source docker accepts on both Windows and Linux."""
    text = path.resolve().as_posix()
    if len(text) > 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _folded_entry(hook_id: str) -> list[str]:
    """The ``entry:`` of a local hook, as the argv pre-commit will execute.

    Read out of ``.pre-commit-config.yaml`` rather than duplicated here, so this
    test cannot pass against a command line the hook does not use. A YAML folded
    scalar (``>-``) joins its lines with single spaces. Identical to
    ``test_gitleaks_positive_control.py``'s helper of the same name — duplicated
    rather than shared: this is the second file that wants it, and a shared
    module is worth it starting at the third (rule of three).
    """
    lines = PRE_COMMIT_CONFIG.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == f"- id: {hook_id}"]
    assert len(starts) == 1, f"expected exactly one `- id: {hook_id}` block, found {len(starts)}"

    entry_lines = [i for i in range(starts[0], len(lines)) if lines[i].strip() == "entry: >-"]
    assert entry_lines, f"hook {hook_id} has no folded `entry: >-`"
    start = entry_lines[0]
    indent = len(lines[start]) - len(lines[start].lstrip())

    parts: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        if len(line) - len(line.lstrip()) <= indent:
            break
        parts.append(line.strip())
    assert parts, f"hook {hook_id}'s entry block is empty"
    return " ".join(parts).split()


def _run_fmt_check(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """The real hook entry, run against a bind-mounted fixture directory.

    Mounted to ``/src`` and run with ``-w /src``, matching pre-commit's own
    ``docker_image`` language implementation (``pre_commit/languages/docker.py``'s
    ``docker_cmd()``: ``-v <path>:/src:rw`` + ``--workdir /src``) — the same
    convention ``test_gitleaks_positive_control.py``'s
    ``test_the_gitleaks_hook_entry_actually_parses_against_the_pinned_image``
    already established for a hook-entry positive control. ``-recursive`` walks
    the mount directly, so no filename needs to be appended for this to exercise
    the flags for real; pre-commit's own ``types: [terraform]`` file selection is
    a separate concern already covered in ``test_ci_contract.py``.
    """
    docker = _tool("docker")
    return _run(
        [
            docker,
            "run",
            "--rm",
            "-v",
            f"{_posix(tmp_path)}:/src:rw",
            "-w",
            "/src",
            *_folded_entry("terraform-fmt"),
        ]
    )


def test_the_terraform_fmt_hook_entry_uses_the_pinned_image() -> None:
    """Guard the parser: everything below asserts against this argv."""
    entry = _folded_entry("terraform-fmt")
    assert entry[0] == TERRAFORM_IMAGE, (
        f"the terraform-fmt hook runs {entry[0]!r}; ci/versions.env pins {TERRAFORM_IMAGE!r}"
    )
    assert "fmt" in entry and "-check" in entry, (
        f"the hook does not run fmt -check, so it would reformat files in place "
        f"instead of gating on their formatting: {entry!r}"
    )


# =============================================================================
# The gate, end to end, on the real container.
# =============================================================================


def test_a_misformatted_tf_file_reddens_the_hook(tmp_path: Path) -> None:
    """Valid HCL, wrong indentation, must fail ``-check``.

    Measured directly against the pinned terraform (via the equivalent static
    binary — Docker is unavailable in the environment that first wrote this
    test; CI runs the real container): a diff-needed file exits ``3``, distinct
    from the parse-error case below.
    """
    (tmp_path / "main.tf").write_text(
        'resource "null_resource" "example" {\ntriggers = {\n  key = "value"\n}\n}\n',
        encoding="utf-8",
    )

    result = _run_fmt_check(tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode == 3, (
        f"a misformatted .tf file did not fail fmt -check with exit 3:\n{output}"
    )
    assert "main.tf" in output, f"the hook did not name the misformatted file:\n{output}"


def test_a_canonically_formatted_tf_file_passes_the_hook(tmp_path: Path) -> None:
    """The negative control: canonical HCL must pass with exit 0 and no output.

    Without this, a hook that always fails (e.g. a bad flag combination cobra
    rejects) would look identical to the positive control above — both would
    just be "the hook exited non-zero on a .tf file".
    """
    (tmp_path / "main.tf").write_text(
        'resource "null_resource" "example" {\n  triggers = {\n    key = "value"\n  }\n}\n',
        encoding="utf-8",
    )

    result = _run_fmt_check(tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"a canonically-formatted .tf file failed fmt -check:\n{output}"
    assert output.strip() == "", f"fmt -check on a clean file printed a diff:\n{output}"


def test_a_syntax_error_is_distinguishable_from_a_formatting_diff(tmp_path: Path) -> None:
    """Guard the guard: a parse error must not be reported as "needs formatting".

    ``internal/command/fmt.go`` at the pinned v1.15.8 tag exits ``2`` on a
    genuine HCL parse error and ``3`` when the input parses but is not
    canonically formatted — two different failure classes an operator must be
    able to tell apart. If both collapsed to the same exit code, a genuinely
    broken ``.tf`` file (this hook's read-only ``-check`` cannot fix it) would
    look identical to one ``terraform fmt`` could fix automatically.
    """
    (tmp_path / "main.tf").write_text(
        'resource "null_resource" "example" {\n'
        "  triggers = {\n"
        '    key = "value"\n'
        "  \n",  # unbalanced braces -- never closed
        encoding="utf-8",
    )

    result = _run_fmt_check(tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode == 2, (
        f"a syntactically invalid .tf file did not exit 2 (parse error):\n{output}"
    )
