"""Run ``ci/checks.sh`` for real, with ``git``/``docker``/``python`` stubbed.

WHY THIS EXISTS
---------------
``tests/unit/test_ci_contract.py`` greps shell source. Grepping caught real
regressions, but three of its assertions were shown to be defeatable by
refactors that preserve the defect:

* ``"'''" not in worktree_overlay_config`` — move the emission into a helper
  function and the body no longer contains the delimiter, while the TOML
  injection is fully restored;
* pinning the NAME ``ORBITAL_DRIFT_PREFLIGHT_DONE`` — any other spelling
  (``|| [ "${OD_FAST:-0}" = "1" ]``) disables every pin check and passes;
* asserting the substring ``unset SKIP`` with no ordering constraint —
  ``_saved="${SKIP:-}"; unset SKIP; export SKIP="${_saved}"`` passes and SKIP
  works again.

Worse, the grep suite was green through a genuine fail-open: the working-tree
scan passed its config as ``-e GITLEAKS_CONFIG_TOML="$(worktree_overlay_config)"``,
and ``set -e`` does not abort on a failing command substitution used as a WORD
of a simple command, so a failing overlay printed FAIL to stderr and the scan ran
anyway with an empty config — silently losing every custom rule.

So: run the script. ``ci/checks.sh`` is a POSIX ``sh`` program whose entire
interface is argv, the environment, and the three external commands it drives.
All three are replaceable on ``PATH``, which makes its behaviour directly
observable without a Docker daemon, a network, or a populated git repository.

WHAT THE STUBS RECORD
---------------------
Every stub writes one directory per invocation under ``calls/``, containing one
file per argv element (exact bytes — the generated TOML overlay contains
newlines and quotes, so no delimiter is safe) plus the values of the environment
variables the assertions care about. :class:`Recording` reads them back.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CHECKS_SH: Final = REPO_ROOT / "ci" / "checks.sh"

# Environment variables the stubs snapshot on every call. `SKIP` and
# `PRE_COMMIT_ALLOW_NO_CONFIG` are the two pre-commit escape hatches
# `stage_hooks` claims to neutralise.
# `PYTEST_ADDOPTS` is the third: pytest splices it into argv, so `--no-cov` or
# `--collect-only` set there would turn `stage_coverage` into a green number over
# a run that measured nothing. Recorded so a test can assert the stage unset it,
# the same way the two above are asserted for `stage_hooks`.
RECORDED_ENV: Final[tuple[str, ...]] = ("SKIP", "PRE_COMMIT_ALLOW_NO_CONFIG", "PYTEST_ADDOPTS")


def posix_shell() -> str:
    """Absolute path to a POSIX shell, or raise.

    Deliberately not a ``pytest.skip``: on every platform this project supports
    — the Linux CI runner, node A, and the Windows authoring box with Git Bash —
    ``sh`` exists. A skip here would silently retire the only non-grep coverage
    of the gate runner, which is the failure mode this whole module was written
    to end.
    """
    found = shutil.which("sh")
    if found is None:
        raise RuntimeError(
            "no POSIX `sh` on PATH. ci/checks.sh is POSIX sh and these tests run it; "
            "on Windows install Git for Windows (Git Bash provides sh.exe)."
        )
    return found


@dataclass(frozen=True)
class Call:
    """One recorded invocation of a stubbed command."""

    command: str
    argv: tuple[str, ...]
    env: dict[str, str | None]

    @property
    def joined(self) -> str:
        return " ".join(self.argv)

    def flag_value(self, flag: str, prefix: str) -> str | None:
        """Value of ``flag <prefix>=VALUE`` in argv, e.g. ``-e FOO=``.

        Returns the part after ``prefix``. ``None`` when the flag/prefix pair is
        absent. Used to read the generated gitleaks overlay back out of the
        ``docker run -e GITLEAKS_CONFIG_TOML=...`` the stage built.
        """
        for index, item in enumerate(self.argv[:-1]):
            if item == flag and self.argv[index + 1].startswith(prefix):
                return self.argv[index + 1][len(prefix) :]
        return None


@dataclass(frozen=True)
class Recording:
    """Result of one ``ci/checks.sh`` run under stubs."""

    returncode: int
    stdout: str
    stderr: str
    calls: tuple[Call, ...]

    def of(self, command: str) -> tuple[Call, ...]:
        return tuple(call for call in self.calls if call.command == command)

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


_RECORD_PRELUDE = r"""
_od_seq_file="@CALLDIR@/.seq"
_od_n=$(cat "${_od_seq_file}")
_od_n=$((_od_n + 1))
printf '%s' "${_od_n}" > "${_od_seq_file}"
_od_dir="@CALLDIR@/$(printf '%06d' "${_od_n}")-@COMMAND@"
mkdir -p "${_od_dir}"
printf '@COMMAND@' > "${_od_dir}/.command"
_od_i=0
for _od_arg in "$@"; do
  _od_i=$((_od_i + 1))
  printf '%s' "${_od_arg}" > "${_od_dir}/arg-$(printf '%04d' "${_od_i}")"
done
for _od_name in @RECORDED_ENV@; do
  eval "_od_set=\${${_od_name}+set}"
  if [ "${_od_set}" = "set" ]; then
    eval "_od_val=\${${_od_name}}"
    printf '%s' "${_od_val}" > "${_od_dir}/env-${_od_name}"
  fi
done
"""


def _stub(body: str, call_dir: Path, replacements: dict[str, str]) -> str:
    prelude = _RECORD_PRELUDE.replace("@CALLDIR@", call_dir.as_posix()).replace(
        "@RECORDED_ENV@", " ".join(RECORDED_ENV)
    )
    text = "#!/usr/bin/env sh\n" + prelude + body
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


GIT_STUB = r"""
case "$*" in
  "rev-parse --git-dir")
    printf '.git\n' ;;
  "rev-parse --is-shallow-repository")
    printf '@IS_SHALLOW@\n' ;;
  "rev-parse --verify --quiet HEAD")
    exit @HEAD_RC@ ;;
  "config --get-regexp "*)
    if [ "@PARTIAL_RC@" = "0" ]; then
      printf 'remote.origin.promisor true\nremote.origin.partialclonefilter blob:none\n'
    fi
    exit @PARTIAL_RC@ ;;
  "ls-files -z --others --ignored --exclude-standard --directory")
    cat "@IGNORED_FILE@" ;;
  "ls-files -z --others --exclude-standard")
    cat "@UNTRACKED_FILE@" ;;
  "ls-files")
    cat "@TRACKED_FILE@" ;;
  *)
    printf 'git stub: unhandled argv: %s\n' "$*" >&2
    exit 97 ;;
esac
exit 0
"""

# ROUND 10 — the `info` branch is the daemon-liveness probe `docker_or_fail`
# runs, and it is knobbed SEPARATELY from `@DOCKER_RC@` on purpose. The real
# defect this models is "the docker CLI is present and answers `command -v`,
# but the DAEMON behind it is not running", which is a different state from
# "the daemon is up and a `docker run` of a pinned image failed" (rate limit,
# missing digest, bad credentials). Every pre-round-10 test that sets
# ``docker_rc`` means the second one and must keep reaching
# ``require_pinned_image``; ``docker_info_rc`` is the first one.
DOCKER_STUB = r"""
case "$*" in
  "info" | "info "*)
    if [ "@DOCKER_INFO_RC@" != "0" ]; then
      printf '%s\n' "@DOCKER_INFO_STDERR@" >&2
    fi
    exit @DOCKER_INFO_RC@ ;;
  *" version")
    printf 'v@GITLEAKS_REPORTS@\n' ;;
  *" --version")
    printf 'ShellCheck - shell script analysis tool\n'
    printf 'version: @SHELLCHECK_REPORTS@\n'
    printf 'license: GNU General Public License, version 3\n'
    printf 'website: https://www.shellcheck.net\n' ;;
  *" -version")
    printf 'Terraform v@TERRAFORM_REPORTS@\n'
    printf 'on linux_amd64\n' ;;
esac
exit @DOCKER_RC@
"""

# The stderr a stopped Docker Desktop actually prints on the Windows authoring
# box, byte for byte, as captured by the orchestrator while reproducing the
# round-10 defect. Kept here as a named constant so more than one test can
# assert against the REAL text rather than a paraphrase of it: none of
# ci/checks.sh's pre-round-10 daemon patterns matched this string, which is why
# it was classified as an "unrecognised docker failure".
WINDOWS_NPIPE_DAEMON_DOWN_STDERR: Final[str] = (
    "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; "
    "check if the path is correct and if the daemon is running: "
    "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified."
)

# The Linux/socket spelling of the same state, which ci/checks.sh has always
# recognised. Default for ``Stubs.docker_info_stderr`` so a test that only sets
# ``docker_info_rc`` gets a realistic message rather than an empty one.
UNIX_SOCKET_DAEMON_DOWN_STDERR: Final[str] = (
    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
    "Is the docker daemon running?"
)

# ``@DOCKER_INFO_STDERR@`` is interpolated into a double-quoted `printf`
# ARGUMENT inside the generated stub, so a value carrying any of these would
# either break the stub's syntax or be re-expanded by the shell. Both real
# messages above are clean; this refuses anything that is not, loudly, rather
# than producing a stub that fails in a way no assertion explains.
_UNSAFE_IN_STUB_STRING: Final[str] = '"\\$`\n\r'

# ROUND 8 — optional "the stubbed value changes on the Nth call within this
# SAME run" mechanism. Everything above is a fixed value baked into the stub
# script at generation time, which is enough to prove "wrong pin -> stage
# fails"; it says nothing about whether a SECOND call to the same probe, later
# in the SAME `sh ci/checks.sh <stage>` process, is what actually decided the
# outcome — the exact question a memoised/cached-success bypass (of any shape,
# any variable name) turns on. `_od_seq_next NAME fallback` is consulted by
# every probe branch below: with no queue file for NAME it returns `fallback`
# unchanged (byte-for-byte the old behaviour, so every pre-round-8 test that
# never asks for a sequence is unaffected); with one, it returns the next
# queued value on each call — advancing a counter FILE, so the state survives
# across the separate `python` subprocess invocations a real `sh ci/checks.sh
# all` run makes — and repeats the last queued value once exhausted. See
# `run_checks`'s `python_sequences` parameter and
# `test_checks_sh_behaviour.py`'s "ROUND 8" section for what this drives.
_SEQ_HELPER = r"""
_od_seq_next() {
  _od_qfile="@SEQDIR@/$1.txt"
  if [ ! -f "${_od_qfile}" ]; then
    printf '%s' "$2"
    return 0
  fi
  _od_cfile="@SEQDIR@/$1.count"
  [ -f "${_od_cfile}" ] || printf '0' > "${_od_cfile}"
  _od_idx=$(cat "${_od_cfile}")
  _od_idx=$((_od_idx + 1))
  printf '%s' "${_od_idx}" > "${_od_cfile}"
  _od_total=$(wc -l < "${_od_qfile}" | tr -d '[:space:]')
  if [ "${_od_idx}" -gt "${_od_total}" ]; then
    _od_idx="${_od_total}"
  fi
  sed -n "${_od_idx}p" "${_od_qfile}"
}
"""

# ORDERING IS LOAD-BEARING, AND THE TRAP IS SUBTLE. `case` patterns are globs
# matched top-down against the WHOLE argv (`$*`), and several of these are
# substring patterns, so an arm added in the wrong place silently shadows a
# later one. Two live instances:
#
#   * `*'import pytest'*` matches `import pytest_cov` too. Any pytest-cov
#     version probe written as `python -c 'import pytest_cov;...'` would be
#     answered with PYTEST's version, and the resulting pin mismatch names a
#     tool that is fine. ci/checks.sh's pytest-cov probe therefore uses
#     `importlib.metadata.version("pytest-cov")`, whose argv contains no
#     `import pytest` substring at all — the ordering dependency is designed
#     out rather than commented around.
#   * the `--collect-only` arm MUST precede the bare `-m pytest ` arm: a
#     collect-only argv matches both, and `case` takes the first.
#
# The two pytest arms are round 11. Before them, EVERY `python -m pytest ...`
# invocation fell through this `case` untouched and exited `@PYTHON_RC@` (0),
# so `pytest_suite()`'s `collect_rc` was always 0 and its entire exit-5 ladder
# — DECLARED-EMPTY vs collection-error vs helper-modules-only, the mechanism
# two of FR-011's six gates rest on — was unreachable by any test that could
# be written through this harness. Its only coverage was three source-level
# greps in test_ci_contract.py, and the file's own docstring concedes those are
# secondary. That is precisely the gap `test_checks_sh_behaviour.py` exists to
# close for every other claim in the script.
PYTHON_STUB = r"""
case "$*" in
  *'version_info[:3]'*)   printf '%s\n' "$(_od_seq_next PY_FULL '@PY_FULL@')" ;;
  *'version_info[:2]'*)   printf '%s\n' "$(_od_seq_next PY_MINOR '@PY_MINOR@')" ;;
  "-m ruff --version")    printf 'ruff %s\n' "$(_od_seq_next RUFF '@RUFF@')" ;;
  "-m mypy --version")    printf 'mypy %s (compiled: yes)\n' "$(_od_seq_next MYPY '@MYPY@')" ;;
  *'import pytest'*)      printf '%s\n' "$(_od_seq_next PYTEST '@PYTEST@')" ;;
  *'pytest-cov'*)         printf '%s\n' "$(_od_seq_next PYTEST_COV '@PYTEST_COV@')" ;;
  *'"coverage"'*)         printf '%s\n' "$(_od_seq_next COVERAGE '@COVERAGE@')" ;;
  "-m pre_commit --version") printf 'pre-commit %s\n' "$(_od_seq_next PRE_COMMIT '@PRE_COMMIT@')" ;;
  "-m pytest "*"--collect-only"*)
    if [ -n "@PYTEST_COLLECT_STDOUT@" ]; then
      printf '%s\n' "@PYTEST_COLLECT_STDOUT@"
    fi
    exit "$(_od_seq_next PYTEST_COLLECT_RC '@PYTEST_COLLECT_RC@')" ;;
  "-m pytest "*)
    # File-based, unlike PYTEST_COLLECT_STDOUT above: stage_coverage's real
    # invocation is a multi-line combined-suite report (a `^FAILED ...` line, a
    # `^FAIL Required test coverage...` line, or both), and newlines are in
    # _UNSAFE_IN_STUB_STRING — they cannot be interpolated into a double-quoted
    # printf argument the way a single-line collect message can. `cat`ing a file
    # written outside the generated script sidesteps quoting entirely, mirroring
    # how GIT_STUB's ignored/untracked/tracked content is supplied.
    if [ -s "@PYTEST_RUN_STDOUT_FILE@" ]; then
      cat "@PYTEST_RUN_STDOUT_FILE@"
    fi
    exit "$(_od_seq_next PYTEST_RUN_RC '@PYTEST_RUN_RC@')" ;;
esac
exit @PYTHON_RC@
"""


@dataclass
class Stubs:
    """Behaviour knobs for the three stubbed commands.

    Defaults describe the repository as it is right now: a real git repository
    with no commits, nothing tracked, a couple of gitignored tool caches, a
    reachable Docker daemon serving the pinned images, and a correctly pinned
    Python toolchain. Each test changes exactly the one thing it is about.

    THE EIGHT VERSION-KNOB FIELDS ARE ``str | None``, NOT ``str``, AND THE
    DIFFERENCE IS LOAD-BEARING (round 5 / MAJOR 2).

    ``docker_rc`` AND ``docker_info_rc`` ARE TWO DIFFERENT FAILURES (round 10).
    ``docker_info_rc`` is the exit status of the daemon-liveness probe
    ``docker_or_fail`` runs (``docker info``): non-zero means "the CLI is
    installed and on PATH, the daemon is not answering" — the state that made
    ``sh ci/checks.sh all`` report 8 secrets-gate failures on a box where the
    only thing wrong was that Docker Desktop was not started. ``docker_rc`` is
    the exit status of everything else the stub is asked to do, i.e. of a
    ``docker run`` against a pinned image with a healthy daemon. Setting one
    does not set the other.

    ``None`` (the default) means "not overridden — resolve to the pin from
    ci/versions.env", which is what every existing test wants for the seven
    knobs it is not specifically testing. An explicit ``""`` means something
    completely different: "the tool produced genuinely empty output" — the
    shape a missing/not-importable tool or a broken interpreter takes in
    ci/checks.sh's own ``tool_version()`` / ``require_tool()`` /
    ``require_python_interpreter()``.

    Before this, every one of these fields defaulted to ``""`` and
    ``_defaults`` resolved a blank with ``stubs.X or PINS[...]``. Python's `or`
    treats `""` as falsy exactly the same as it treats an unset default, so
    ``Stubs(ruff="")`` was indistinguishable from not passing ``ruff`` at all —
    it silently reverted to the correct pinned version. There was consequently
    NO way to construct a ``Stubs`` that drove ci/checks.sh's
    "(not installed, or not importable by this interpreter)" message or its
    "(PYTHON=... is not a working interpreter)" message through this harness,
    and grepping ``tests/`` for either string found zero tests exercising them.

    ``pytest_collect_rc`` AND ``pytest_run_rc`` DEFAULT TO ``None`` MEANING
    "USE ``python_rc``" (round 11), not to ``0``. That is what makes adding the
    two pytest arms to ``PYTHON_STUB`` a no-op for every test written before
    them: previously a ``python -m pytest ...`` call fell through the ``case``
    and exited ``python_rc``, so resolving these two to ``python_rc`` when
    unset reproduces that byte for byte. Defaulting them to ``0`` instead would
    have silently changed the meaning of ``Stubs(python_rc=1)`` for any stage
    that runs pytest.
    """

    ignored: bytes = b".mypy_cache/\x00.pytest_cache/\x00"
    untracked: bytes = b"README.md\x00pyproject.toml\x00"
    tracked: bytes = b""
    is_shallow: str = "false"
    head_rc: int = 1
    partial_rc: int = 1
    docker_rc: int = 0
    docker_info_rc: int = 0
    docker_info_stderr: str = UNIX_SOCKET_DAEMON_DOWN_STDERR
    python_rc: int = 0
    pytest_collect_rc: int | None = None
    pytest_run_rc: int | None = None
    pytest_collect_stdout: str = ""
    # File-based (see PYTHON_STUB), so this one MAY contain newlines — a real
    # combined-suite pytest report is multi-line, and stage_coverage's diagnosis
    # logic distinguishes cases by which lines are present.
    pytest_run_stdout: str = ""
    gitleaks_reports: str | None = None
    shellcheck_reports: str | None = None
    terraform_reports: str | None = None
    py_full: str | None = None
    py_minor: str | None = None
    ruff: str | None = None
    mypy: str | None = None
    pytest: str | None = None
    pytest_cov: str | None = None
    coverage: str | None = None
    pre_commit: str | None = None


def _pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in (REPO_ROOT / "ci" / "versions.env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pins[key.strip()] = value.strip()
    return pins


PINS: Final[dict[str, str]] = _pins()


@dataclass(frozen=True)
class _ResolvedStubs:
    """``Stubs`` with every ``None`` resolved to a concrete pin.

    A separate, non-Optional type rather than reusing ``Stubs`` for the
    resolved form: after resolution every version-knob field IS a plain
    ``str`` (possibly the empty string, deliberately), and giving that state
    its own type means every call site downstream gets that guarantee from
    mypy rather than having to re-narrow an ``str | None`` it already knows
    cannot be ``None``.
    """

    ignored: bytes
    untracked: bytes
    tracked: bytes
    is_shallow: str
    head_rc: int
    partial_rc: int
    docker_rc: int
    docker_info_rc: int
    docker_info_stderr: str
    python_rc: int
    pytest_collect_rc: int
    pytest_run_rc: int
    pytest_collect_stdout: str
    pytest_run_stdout: str
    gitleaks_reports: str
    shellcheck_reports: str
    terraform_reports: str
    py_full: str
    py_minor: str
    ruff: str
    mypy: str
    pytest: str
    pytest_cov: str
    coverage: str
    pre_commit: str


def _defaults(stubs: Stubs) -> _ResolvedStubs:
    """Resolve every ``None`` version knob to the real pin.

    ``None`` means "not overridden"; an explicit ``""`` is a deliberate "the
    tool produced empty output" and MUST survive untouched, or
    ci/checks.sh's not-installed / not-a-working-interpreter branches remain
    undrivable through this harness — see the ``Stubs`` docstring.
    """
    return _ResolvedStubs(
        ignored=stubs.ignored,
        untracked=stubs.untracked,
        tracked=stubs.tracked,
        is_shallow=stubs.is_shallow,
        head_rc=stubs.head_rc,
        partial_rc=stubs.partial_rc,
        docker_rc=stubs.docker_rc,
        docker_info_rc=stubs.docker_info_rc,
        docker_info_stderr=stubs.docker_info_stderr,
        python_rc=stubs.python_rc,
        # `None` -> `python_rc`, NOT -> 0. See the Stubs docstring: this is what
        # makes the two pytest arms in PYTHON_STUB a no-op for every test
        # written before they existed, when a `-m pytest` call fell through the
        # `case` and exited `python_rc`.
        pytest_collect_rc=(
            stubs.python_rc if stubs.pytest_collect_rc is None else stubs.pytest_collect_rc
        ),
        pytest_run_rc=(stubs.python_rc if stubs.pytest_run_rc is None else stubs.pytest_run_rc),
        pytest_collect_stdout=stubs.pytest_collect_stdout,
        pytest_run_stdout=stubs.pytest_run_stdout,
        gitleaks_reports=(
            PINS["GITLEAKS_VERSION"] if stubs.gitleaks_reports is None else stubs.gitleaks_reports
        ),
        shellcheck_reports=(
            PINS["SHELLCHECK_VERSION"]
            if stubs.shellcheck_reports is None
            else stubs.shellcheck_reports
        ),
        terraform_reports=(
            PINS["TERRAFORM_VERSION"]
            if stubs.terraform_reports is None
            else stubs.terraform_reports
        ),
        py_full=(f"{PINS['PYTHON_VERSION']}.10" if stubs.py_full is None else stubs.py_full),
        py_minor=(PINS["PYTHON_VERSION"] if stubs.py_minor is None else stubs.py_minor),
        ruff=(PINS["RUFF_VERSION"] if stubs.ruff is None else stubs.ruff),
        mypy=(PINS["MYPY_VERSION"] if stubs.mypy is None else stubs.mypy),
        pytest=(PINS["PYTEST_VERSION"] if stubs.pytest is None else stubs.pytest),
        # `.get`, not `[...]`: these two pins land with the `coverage` stage. Until
        # then the knobs resolve to "" and the corresponding stub arms answer with
        # an empty string, which is exactly ci/checks.sh's "(not installed, or not
        # importable by this interpreter)" shape — the honest answer for a tool
        # that genuinely is not pinned yet.
        pytest_cov=(
            PINS.get("PYTEST_COV_VERSION", "") if stubs.pytest_cov is None else stubs.pytest_cov
        ),
        coverage=(PINS.get("COVERAGE_VERSION", "") if stubs.coverage is None else stubs.coverage),
        pre_commit=(PINS["PRE_COMMIT_VERSION"] if stubs.pre_commit is None else stubs.pre_commit),
    )


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def run_checks(
    stage: str,
    workspace: Path,
    *,
    stubs: Stubs | None = None,
    extra_env: dict[str, str] | None = None,
    checks_sh: Path | None = None,
    timeout: float = 120.0,
    python_sequences: dict[str, tuple[str, ...]] | None = None,
) -> Recording:
    """Run ``sh <checks_sh> <stage>`` with stubbed git/docker/python.

    ``workspace`` is a scratch directory (a pytest ``tmp_path``) that holds the
    stubs and the call log. The script itself still runs from the real
    repository unless ``checks_sh`` says otherwise, because ``ci/checks.sh``
    derives its repo root from its own location.

    ``python_sequences`` (round 8) is the mechanism-agnostic, black-box
    no-memoization mechanism: a mapping from probe name (``PY_FULL``,
    ``PY_MINOR``, ``RUFF``, ``MYPY``, ``PYTEST``, ``PYTEST_COV``, ``COVERAGE``,
    ``PRE_COMMIT``, and — round 11 — ``PYTEST_COLLECT_RC`` / ``PYTEST_RUN_RC``,
    whose queued values are exit statuses rather than version strings) to a
    tuple of values to return on the 1st, 2nd, ... call to that probe WITHIN
    THIS ONE run — the Nth-and-beyond call repeats the last value in the tuple. A
    probe not present in this mapping behaves exactly as before (the single
    value from ``stubs``/the real pin, for every call). This is what lets a
    test observe "call 1 says the pin is correct, call 2 — same process, same
    whatever-internal-state exists — says it is wrong" and assert the SECOND
    call's answer, not the first, decided the outcome.
    """
    resolved = _defaults(stubs or Stubs())

    bin_dir = workspace / "bin"
    call_dir = workspace / "calls"
    data_dir = workspace / "data"
    seq_dir = workspace / "seq"
    for directory in (bin_dir, call_dir, data_dir, seq_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (call_dir / ".seq").write_text("0", encoding="utf-8")

    for seq_name, seq_values in (python_sequences or {}).items():
        assert seq_values, f"python_sequences[{seq_name!r}] is empty"
        (seq_dir / f"{seq_name}.txt").write_text(
            "\n".join(seq_values) + "\n", encoding="utf-8", newline="\n"
        )

    ignored_file = data_dir / "ignored"
    untracked_file = data_dir / "untracked"
    tracked_file = data_dir / "tracked"
    pytest_run_stdout_file = data_dir / "pytest_run_stdout"
    ignored_file.write_bytes(resolved.ignored)
    untracked_file.write_bytes(resolved.untracked)
    tracked_file.write_bytes(resolved.tracked)
    pytest_run_stdout_file.write_text(resolved.pytest_run_stdout, encoding="utf-8", newline="\n")

    _write_executable(
        bin_dir / "git",
        _stub(
            GIT_STUB,
            call_dir,
            {
                "@COMMAND@": "git",
                "@IS_SHALLOW@": resolved.is_shallow,
                "@HEAD_RC@": str(resolved.head_rc),
                "@PARTIAL_RC@": str(resolved.partial_rc),
                "@IGNORED_FILE@": ignored_file.as_posix(),
                "@UNTRACKED_FILE@": untracked_file.as_posix(),
                "@TRACKED_FILE@": tracked_file.as_posix(),
            },
        ),
    )
    unsafe = sorted(set(resolved.docker_info_stderr) & set(_UNSAFE_IN_STUB_STRING))
    if unsafe:
        raise ValueError(
            f"docker_info_stderr contains {unsafe!r}, which cannot be interpolated into "
            "the generated docker stub's double-quoted printf argument without either "
            "breaking its syntax or being re-expanded by the shell. Use a message that "
            "avoids them, or teach _stub a quoting pass."
        )

    _write_executable(
        bin_dir / "docker",
        _stub(
            DOCKER_STUB,
            call_dir,
            {
                "@COMMAND@": "docker",
                "@GITLEAKS_REPORTS@": resolved.gitleaks_reports,
                "@SHELLCHECK_REPORTS@": resolved.shellcheck_reports,
                "@TERRAFORM_REPORTS@": resolved.terraform_reports,
                "@DOCKER_RC@": str(resolved.docker_rc),
                "@DOCKER_INFO_RC@": str(resolved.docker_info_rc),
                "@DOCKER_INFO_STDERR@": resolved.docker_info_stderr,
            },
        ),
    )
    # Same guard, same reason, as docker_info_stderr above: this value is
    # interpolated into a double-quoted `printf` argument in the generated stub,
    # so a quote/backslash/backtick/dollar would either break its syntax or be
    # re-expanded by the shell. Real pytest collection output is multi-line, so
    # the newline in _UNSAFE_IN_STUB_STRING bites here in practice rather than
    # theoretically — pass a single representative line.
    unsafe_collect = sorted(set(resolved.pytest_collect_stdout) & set(_UNSAFE_IN_STUB_STRING))
    if unsafe_collect:
        raise ValueError(
            f"pytest_collect_stdout contains {unsafe_collect!r}, which cannot be interpolated "
            "into the generated python stub's double-quoted printf argument without either "
            "breaking its syntax or being re-expanded by the shell. Use a single-line message "
            "that avoids them, or teach _stub a quoting pass."
        )

    _write_executable(
        bin_dir / "python",
        _stub(
            _SEQ_HELPER + PYTHON_STUB,
            call_dir,
            {
                "@COMMAND@": "python",
                "@SEQDIR@": seq_dir.as_posix(),
                "@PY_FULL@": resolved.py_full,
                "@PY_MINOR@": resolved.py_minor,
                "@RUFF@": resolved.ruff,
                "@MYPY@": resolved.mypy,
                "@PYTEST@": resolved.pytest,
                "@PYTEST_COV@": resolved.pytest_cov,
                "@COVERAGE@": resolved.coverage,
                "@PRE_COMMIT@": resolved.pre_commit,
                "@PYTHON_RC@": str(resolved.python_rc),
                "@PYTEST_COLLECT_RC@": str(resolved.pytest_collect_rc),
                "@PYTEST_RUN_RC@": str(resolved.pytest_run_rc),
                "@PYTEST_COLLECT_STDOUT@": resolved.pytest_collect_stdout,
                "@PYTEST_RUN_STDOUT_FILE@": pytest_run_stdout_file.as_posix(),
            },
        ),
    )

    env = dict(os.environ)
    env.pop("SKIP", None)
    env.pop("PRE_COMMIT_ALLOW_NO_CONFIG", None)
    env.pop("DEBUG", None)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHON"] = (bin_dir / "python").as_posix()
    if extra_env:
        env.update(extra_env)

    script = checks_sh or CHECKS_SH
    completed = subprocess.run(
        [posix_shell(), script.as_posix(), stage],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
        cwd=script.parent.parent,
    )

    return Recording(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        calls=_read_calls(call_dir),
    )


def _read_calls(call_dir: Path) -> tuple[Call, ...]:
    calls: list[Call] = []
    for entry in sorted(call_dir.iterdir()):
        if not entry.is_dir():
            continue
        command = (entry / ".command").read_text(encoding="utf-8")
        argv = tuple(
            arg.read_text(encoding="utf-8", errors="surrogateescape")
            for arg in sorted(entry.glob("arg-*"))
        )
        env: dict[str, str | None] = dict.fromkeys(RECORDED_ENV)
        for name in RECORDED_ENV:
            captured = entry / f"env-{name}"
            if captured.exists():
                env[name] = captured.read_text(encoding="utf-8")
        calls.append(Call(command=command, argv=argv, env=env))
    return tuple(calls)
