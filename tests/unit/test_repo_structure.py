"""Meta-test: the working tree matches plan.md's Project Structure block.

At T001 there is no source code, so a `pytest` run would exit 5 ("no tests
collected") and redden the unit gate. Rather than paper over that with
``|| true`` (forbidden — it disarms the gate permanently), the unit suite
carries one real assertion from the start: that the scaffold this task is
responsible for actually exists and survives a clone.

It is not a placeholder. It fails for three genuine reasons:

* a directory required by plan.md was deleted or renamed;
* an empty directory lost its ``.gitkeep`` and therefore vanished from git
  (git does not track directories — this is the classic way a scaffold decays);
* a Python package under ``src/orbital_drift`` lost its ``__init__.py`` and
  stopped being importable.

Source of truth: ``specs/001-orbital-drift-ct/plan.md`` -> Project Structure.
When that block changes, change this file in the same PR.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories named in plan.md's Project Structure block.
REQUIRED_DIRECTORIES: tuple[str, ...] = (
    ".specify/memory",
    ".claude/agents",
    ".claude/skills/orbital-drift-governance",
    "charter",
    "ci",
    "dags",
    "dashboards",
    "docs/decisions",
    "docs/incidents",
    "docs/runbooks",
    "docs/soak-log",
    "infra/helm-values",
    "infra/k3s",
    "infra/terraform",
    "openspec/changes/adopt-governance-kit",
    "specs/001-orbital-drift-ct",
    "tests/governance",
    "planning",
    "scripts",
    "src/orbital_drift",
    "traceability",
    "docs/architecture",
    "tests/contract",
    "tests/smoke",
    "tests/unit",
    "workflows",
)

# Directories that are legitimately empty at T001. Git does not track empty
# directories, so each needs a .gitkeep or it silently disappears on clone and
# the CI stage that scans it starts passing for the wrong reason.
DIRECTORIES_NEEDING_GITKEEP: tuple[str, ...] = (
    "dags",
    "dashboards",
    "docs/incidents",
    "docs/runbooks",
    "docs/soak-log",
    "infra/helm-values",
    "infra/k3s",
    "infra/terraform",
    "tests/contract",
    "tests/smoke",
    "workflows",
)

# Importable subpackages of orbital_drift, one per pipeline stage in plan.md.
REQUIRED_SUBPACKAGES: tuple[str, ...] = (
    "data",
    "drift",
    "ingest",
    "registry",
    "serve",
    "train",
)

# Repo-root files this task is accountable for. Each is load-bearing:
#   .gitattributes  authored on Windows, executed on Linux (D-10) — committed
#                   CRLF kills shell scripts on node A.
#   .gitignore      the repo is public; this is the primary secrets control
#                   (Constitution VII), gitleaks is the backstop.
#   .env.example    the only documented description of the host-specific values
#                   the repo deliberately does not carry (D-10). Losing it turns
#                   `cp .env.example .env` in README.md into a dead instruction.
#   README.md       the one documented bootstrap command path (Principle IV).
REQUIRED_FILES: tuple[str, ...] = (
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
    "CLAUDE.md",
    "README.md",
    "ci/checks.sh",
    "ci/gitleaks.toml",
    "ci/versions.env",
    "pyproject.toml",
    ".specify/memory/constitution.md",
    # adopt-governance-kit control plane (change design D6-D8); each later import
    # phase appends its own paths here in the PR that creates them.
    ".claude/skills/orbital-drift-governance/SKILL.md",
    ".claude/skills/run-the-gate/SKILL.md",
    ".claude/skills/log-decision/SKILL.md",
    "CHANGELOG.md",
    "docs/architecture/ARCHITECTURE.md",
    "tests/governance/test_agents_and_skills.py",
    "tests/governance/test_session_start_check.py",
    "charter/PROJECT-CHARTER.md",
    "docs/decision-log.md",
    "openspec/changes/adopt-governance-kit/proposal.md",
    "openspec/changes/adopt-governance-kit/design.md",
    "openspec/changes/adopt-governance-kit/tasks.md",
    "openspec/changes/adopt-governance-kit/specs/governance-harness/spec.md",
    "Makefile",
    "ci/validate_specs.sh",
    "tests/conftest.py",
    "tests/governance/test_zero_skip_guard.py",
    "tests/governance/test_governance_meta.py",
    "tests/governance/test_pretooluse_guard.py",
    "traceability/REQUIREMENT-TRACEABILITY.md",
    "src/orbital_drift/traceability.py",
    ".claude/allowed-remotes.txt",
    "scripts/_lib.sh",
    "scripts/guard_probe.sh",
    "scripts/pretooluse_guard.sh",
    "scripts/pre_push_scan.sh",
    "scripts/install_hooks.sh",
    "scripts/session_start_check.sh",
    "src/orbital_drift/remotes.py",
    "src/orbital_drift/projections.py",
    "src/orbital_drift/guard.py",
    "src/orbital_drift/covcheck.py",
    "src/orbital_drift/planning/roadmap_data.py",
    "planning/roadmap.md",
    "planning/jira-import.csv",
)


@pytest.mark.parametrize("relative_path", REQUIRED_DIRECTORIES)
def test_required_directory_exists(relative_path: str) -> None:
    """Every directory in plan.md's Project Structure block is present."""
    target = REPO_ROOT / relative_path
    assert target.is_dir(), f"missing directory required by plan.md: {relative_path}"


@pytest.mark.parametrize("relative_path", DIRECTORIES_NEEDING_GITKEEP)
def test_empty_directory_is_preserved_by_gitkeep(relative_path: str) -> None:
    """An intentionally-empty directory keeps a .gitkeep so git preserves it.

    Once the directory gains real content the .gitkeep becomes optional — a
    single always-asserting statement, NOT a skip (the zero-skip guard bans
    parked skips) and NOT a bare early return (the harness's own doctrine calls
    a conditional body-``return`` "the dangerous one: reports as an ordinary
    PASS while executing zero assertions" — see test_checks_sh_behaviour's
    round-9b notes).
    """
    target = REPO_ROOT / relative_path
    has_tracked_content = any(
        child.name != ".gitkeep" and not child.name.startswith(".") for child in target.iterdir()
    )
    assert has_tracked_content or (target / ".gitkeep").is_file(), (
        f"{relative_path} is empty and has no .gitkeep — it will not survive a clone"
    )


@pytest.mark.parametrize("subpackage", REQUIRED_SUBPACKAGES)
def test_subpackage_is_importable(subpackage: str) -> None:
    """Each pipeline-stage subpackage has an __init__.py and imports cleanly."""
    package_dir = REPO_ROOT / "src" / "orbital_drift" / subpackage
    assert (package_dir / "__init__.py").is_file(), (
        f"src/orbital_drift/{subpackage}/__init__.py is missing"
    )
    module = importlib.import_module(f"orbital_drift.{subpackage}")
    assert module.__doc__, f"orbital_drift.{subpackage} has no module docstring"


@pytest.mark.parametrize("relative_path", REQUIRED_FILES)
def test_required_file_exists(relative_path: str) -> None:
    """Scaffold files that gates and runbooks depend on are present."""
    target = REPO_ROOT / relative_path
    assert target.is_file(), f"missing required file: {relative_path}"


def test_gitignore_covers_the_high_value_leaks() -> None:
    """The public repo ignores terraform state, tfvars, kubeconfigs and .env.

    These are the four file classes that reliably carry plaintext credentials in
    this stack (Postgres password, S3 keys, Airflow Fernet key, cluster admin
    creds). gitleaks' default rules do not reliably flag an unprefixed 32-char
    base64 blob inside a JSON state file, so .gitignore is the real control.
    """
    patterns = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    entries = {line.strip() for line in patterns if line.strip() and not line.startswith("#")}
    for required in ("*.tfstate", "*.tfstate.*", ".terraform/", "*.tfvars", ".env"):
        assert required in entries, f".gitignore must ignore {required} (public repo)"


def test_terraform_lock_file_is_not_ignored() -> None:
    """`.terraform.lock.hcl` is a pin artifact and MUST stay committed.

    Constitution IV requires provider versions pinned; the lock file is how that
    pin is enforced. It is routinely mistaken for a build artifact and ignored.
    """
    patterns = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    entries = {line.strip() for line in patterns if line.strip() and not line.startswith("#")}
    forbidden = {".terraform.lock.hcl", "*.lock.hcl", "**/.terraform.lock.hcl"}
    assert not (entries & forbidden), (
        ".terraform.lock.hcl must NOT be gitignored — it is a Constitution IV pin artifact"
    )


# --- .gitignore behaviour, asked of git itself -------------------------------
# The virtualenv patterns are the one place where reading the file tells you
# almost nothing: `.venv/` and `.venv` look interchangeable and are not. A
# trailing slash restricts a pattern to DIRECTORIES, so `.venv/` silently fails
# to match a `.venv` SYMLINK — which is how a `.venv` symlink got committed in
# d664d88 and removed in 6bfb44f. Only `git check-ignore` settles it, so these
# ask git, in a throwaway repo carrying this repo's real .gitignore.


def _check_ignore(repo: Path, candidates: tuple[str, ...]) -> set[str]:
    """The subset of ``candidates`` git reports as ignored, asked of git itself."""
    git = shutil.which("git")
    assert git, "git is required to evaluate .gitignore semantics"
    proc = subprocess.run(
        [git, "-C", str(repo), "check-ignore", "--no-index", *candidates],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    # 0 = at least one ignored, 1 = none ignored; anything else is a real error.
    assert proc.returncode in (0, 1), f"git check-ignore failed: {proc.stderr}"
    return {line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()}


@pytest.fixture
def gitignore_repo(tmp_path: Path) -> Path:
    """A fresh repo carrying THIS repo's .gitignore and nothing else.

    Hermetic on purpose: run against the real working tree, an answer could come
    from ``.git/info/exclude`` or a developer's global excludes file rather than
    from the committed .gitignore these tests are about.
    """
    git = shutil.which("git")
    assert git, "git is required to build the fixture repository"
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True, timeout=60, capture_output=True)
    (tmp_path / ".gitignore").write_text(
        (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def test_virtualenv_patterns_ignore_directories_and_symlinks_alike(gitignore_repo: Path) -> None:
    """Every virtualenv spelling, in both the directory and the symlink form.

    ``--no-index`` makes this a pure pattern question, so nothing has to exist on
    disk and the directory-vs-symlink distinction cannot leak in through the
    filesystem: what is asserted is that the PATTERN is not directory-restricted.
    """
    must_ignore = (
        ".venv",
        "venv",
        "ENV",
        "env",
        ".venv/lib/python3.12/site-packages/x.py",
        "src/.venv",
    )
    ignored = _check_ignore(gitignore_repo, must_ignore)
    missing = sorted(set(must_ignore) - ignored)
    assert missing == [], (
        f"{missing} are NOT ignored. A trailing slash on a virtualenv pattern makes it "
        "directory-only, so a symlinked venv slips through and gets committed by a wide "
        "`git add` (measured: d664d88)."
    )


def test_virtualenv_patterns_do_not_swallow_real_source(gitignore_repo: Path) -> None:
    """The other half: slashless must not become a prefix match.

    A slashless, separator-free pattern matches the whole basename, so `venv`
    must not eat `venv.py`, `venvs` or `myvenv`, and `env` must not eat
    `.env.example` — the one documented description of the host-specific values
    this repo deliberately does not carry. Without this, "just drop the slash"
    could silently untrack real source: a worse failure than the one it fixes.
    """
    must_not_ignore = (
        "venv.py",
        "src/venv.py",
        "venvs",
        "myvenv",
        "myvenv/lib/x.py",
        "realvenv",
        "environment.yml",
        ".env.example",
    )
    ignored = _check_ignore(gitignore_repo, must_not_ignore)
    assert ignored == set(), (
        f"{sorted(ignored)} are ignored but must not be — a virtualenv pattern is "
        "matching more than a whole basename"
    )
