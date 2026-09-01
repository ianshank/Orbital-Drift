"""Behavioral tests for the Constitution IV dependency-contract build gate.

Two positive controls anchor this file, named exactly for what they prove
(mirrors ``tests/unit/test_hardcode_scan.py``'s own behavioral style: exercise
the public API, not private helpers):

* ``test_a_synthetic_undeclared_import_is_caught`` -- a temp file imports a
  fake, never-declared package; the gate must find it and fail.
* ``test_the_real_repository_declares_every_import_it_makes`` -- the gate runs
  against the REAL ``pyproject.toml`` and REAL ``src/orbital_drift``, and must
  find zero undeclared imports. This is the reality check: it proves the tool
  works against this repository's actual current state, not only synthetic
  fixtures built to make it pass.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest

from orbital_drift.quality.dep_contract import (
    DepContractError,
    DepContractPolicy,
    build_report,
    declared_import_map,
    main,
    scan_imports,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
REAL_PYPROJECT: Final = REPO_ROOT / "pyproject.toml"
REAL_SRC: Final = REPO_ROOT / "src" / "orbital_drift"


# =============================================================================
# DECLARED side: reading pyproject.toml.
# =============================================================================


def test_declared_import_map_reads_core_and_every_extra_and_folds_names(
    tmp_path: Path,
) -> None:
    """Core deps, every optional extra (dev included), aliases, and the
    hyphen->underscore fold for distributions with no configured alias."""
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        """
[project]
name = "example"
dependencies = ["pydantic==2.13.4", "pydantic-settings==2.15.0"]

[project.optional-dependencies]
evaluation = ["scikit-learn==1.9.0"]
serve = ["fastapi==0.141.1"]
dev = ["pydantic==2.13.4", "types-requests==2.32.4.20250913"]
""".lstrip(),
        encoding="utf-8",
    )

    declared = declared_import_map(pyproject_file, DepContractPolicy())

    assert declared["pydantic"] == frozenset({"core", "dev"})
    assert declared["pydantic_settings"] == frozenset({"core"})
    assert declared["sklearn"] == frozenset({"evaluation"})
    assert declared["fastapi"] == frozenset({"serve"})
    # No configured alias for types-requests: falls back to the PyPI
    # hyphen->underscore convention rather than raising or being dropped.
    assert declared["types_requests"] == frozenset({"dev"})


def test_declared_import_map_requires_a_project_table(tmp_path: Path) -> None:
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text("[tool.other]\nvalue = 1\n", encoding="utf-8")

    with pytest.raises(DepContractError, match=r"\[project\]"):
        declared_import_map(pyproject_file, DepContractPolicy())


# =============================================================================
# IMPORTED side: AST-walking src/.
# =============================================================================


def test_scan_imports_finds_third_party_imports_and_excludes_stdlib_and_relative(
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(
        "import os\n"
        "import numpy\n"
        "from typing import Any\n"
        "from . import sibling\n"
        "from .sibling import thing\n"
        "from orbital_drift.domain import geometry\n"
        "import scipy.stats as st\n",
        encoding="utf-8",
    )

    names = {site.import_name for site in scan_imports((package,))}

    assert names == {"numpy", "scipy"}


def test_scan_imports_errors_for_missing_path(tmp_path: Path) -> None:
    with pytest.raises(DepContractError, match="does not exist"):
        scan_imports((tmp_path / "missing",))


def test_scan_imports_ignores_non_python_files(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("import numpy\n", encoding="utf-8")

    assert scan_imports((note,)) == ()


def test_syntax_errors_raise_typed_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("if True print('broken')\n", encoding="utf-8")

    with pytest.raises(DepContractError, match=r"Unable to parse.*broken\.py"):
        scan_imports((broken,))


# =============================================================================
# THE POSITIVE CONTROL: a synthetic undeclared import must be caught.
# =============================================================================


def test_a_synthetic_undeclared_import_is_caught(tmp_path: Path) -> None:
    """The exact PR#16 shape, reproduced synthetically.

    An import with no matching declaration anywhere in pyproject.toml is a
    Blocker-shaped finding, and fails the gate.
    """
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_text(
        "import definitely_never_declared_anywhere_xyz\n", encoding="utf-8"
    )
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        '[project]\nname = "example"\ndependencies = ["pydantic==2.13.4"]\n',
        encoding="utf-8",
    )

    report = build_report(pyproject_file, (package,), DepContractPolicy())

    assert len(report.undeclared) == 1
    finding = report.undeclared[0]
    assert finding.import_name == "definitely_never_declared_anywhere_xyz"
    assert finding.path.endswith("mod.py")

    status = main(
        [str(package), "--pyproject", str(pyproject_file), "--fail-on-undeclared"],
        stream=io.StringIO(),
    )
    assert status == 1


def test_dead_declaration_is_reported_but_does_not_fail(tmp_path: Path) -> None:
    """A declared, never-imported dependency is advisory only (report but don't fail)."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "used.py").write_text("import numpy\n", encoding="utf-8")
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        '[project]\nname = "example"\ndependencies = ["numpy==2.5.2", "requests==2.34.2"]\n',
        encoding="utf-8",
    )

    report = build_report(pyproject_file, (package,), DepContractPolicy())

    assert report.undeclared == ()
    assert any(dead.import_name == "requests" for dead in report.dead)

    status = main(
        [str(package), "--pyproject", str(pyproject_file), "--fail-on-undeclared"],
        stream=io.StringIO(),
    )
    assert status == 0


def test_dead_declarations_are_omitted_when_the_policy_disables_them(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "used.py").write_text("import numpy\n", encoding="utf-8")
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        '[project]\nname = "example"\ndependencies = ["numpy==2.5.2", "requests==2.34.2"]\n',
        encoding="utf-8",
    )

    report = build_report(
        pyproject_file, (package,), DepContractPolicy(report_dead_declarations=False)
    )

    assert report.dead == ()


def test_allowed_undeclared_exempts_a_named_import(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "optional.py").write_text("import totally_optional_plugin\n", encoding="utf-8")
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text('[project]\nname = "example"\n', encoding="utf-8")

    strict_report = build_report(pyproject_file, (package,), DepContractPolicy())
    relaxed_report = build_report(
        pyproject_file,
        (package,),
        DepContractPolicy(allowed_undeclared=("totally_optional_plugin",)),
    )

    assert len(strict_report.undeclared) == 1
    assert relaxed_report.undeclared == ()


def test_findings_are_deterministic_across_multiple_files(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "beta.py").write_text("import beta_only_pkg\n", encoding="utf-8")
    (package / "alpha.py").write_text("import alpha_only_pkg\n", encoding="utf-8")
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text('[project]\nname = "example"\n', encoding="utf-8")

    report = build_report(pyproject_file, (package,), DepContractPolicy())

    assert [finding.path for finding in report.undeclared] == sorted(
        finding.path for finding in report.undeclared
    )
    assert len(report.undeclared) == 2


# =============================================================================
# Policy loading.
# =============================================================================


def test_policy_round_trips_from_pyproject(tmp_path: Path) -> None:
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        """
[tool.orbital_drift.dep_contract]
source_paths = ["package"]
import_aliases = { "beautifulsoup4" = "bs4" }
allowed_undeclared = ["optional_thing"]
report_dead_declarations = false
fail_on_undeclared = false
""".lstrip(),
        encoding="utf-8",
    )

    policy = DepContractPolicy.from_pyproject(pyproject_file)

    assert policy.source_paths == ("package",)
    assert dict(policy.import_aliases) == {"beautifulsoup4": "bs4"}
    assert policy.allowed_undeclared == ("optional_thing",)
    assert policy.report_dead_declarations is False
    assert policy.fail_on_undeclared is False


def test_policy_falls_back_when_the_table_is_absent_or_malformed(tmp_path: Path) -> None:
    absent = tmp_path / "absent.toml"
    absent.write_text("[project]\nname = 'example'\n", encoding="utf-8")
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("[tool]\norbital_drift = 'not a table'\n", encoding="utf-8")
    tool_malformed = tmp_path / "tool-malformed.toml"
    tool_malformed.write_text("tool = 'not a table'\n", encoding="utf-8")
    table_malformed = tmp_path / "table-malformed.toml"
    table_malformed.write_text(
        "[tool.orbital_drift]\ndep_contract = 'not a table'\n", encoding="utf-8"
    )
    aliases_malformed = tmp_path / "aliases-malformed.toml"
    aliases_malformed.write_text(
        "[tool.orbital_drift.dep_contract]\nimport_aliases = 'not a table'\n",
        encoding="utf-8",
    )

    assert DepContractPolicy.from_pyproject(absent) == DepContractPolicy()
    assert DepContractPolicy.from_pyproject(malformed) == DepContractPolicy()
    assert DepContractPolicy.from_pyproject(tool_malformed) == DepContractPolicy()
    assert DepContractPolicy.from_pyproject(table_malformed) == DepContractPolicy()
    assert DepContractPolicy.from_pyproject(aliases_malformed) == DepContractPolicy()


def test_import_aliases_default_is_a_read_only_mapping() -> None:
    """Mirrors the repo's own ``types.MappingProxyType`` precedent (RB-008a):
    a single stray write to the default alias table must not be possible."""
    policy = DepContractPolicy()
    with pytest.raises(TypeError):
        policy.import_aliases["scikit-learn"] = "tampered"  # type: ignore[index]


# =============================================================================
# CLI.
# =============================================================================


def test_cli_reports_json_and_text_with_both_failure_branches(tmp_path: Path) -> None:
    target = tmp_path / "candidate.py"
    target.write_text("import definitely_not_declared_anywhere\n", encoding="utf-8")
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text("[project]\nname = 'example'\ndependencies = []\n", encoding="utf-8")

    json_stream = io.StringIO()
    failing_status = main(
        [
            str(target),
            "--pyproject",
            str(pyproject_file),
            "--format",
            "json",
            "--fail-on-undeclared",
        ],
        stream=json_stream,
    )
    payload = json.loads(json_stream.getvalue())

    text_stream = io.StringIO()
    passing_status = main(
        [
            str(target),
            "--pyproject",
            str(pyproject_file),
            "--format",
            "text",
            "--no-fail-on-undeclared",
        ],
        stream=text_stream,
    )

    assert failing_status == 1
    assert passing_status == 0
    assert len(payload["undeclared"]) == 1
    assert payload["undeclared"][0]["import_name"] == "definitely_not_declared_anywhere"
    assert "UNDECLARED_IMPORT" in text_stream.getvalue()


def test_cli_uses_policy_paths_and_policy_failure_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No positional paths delegates both target selection and failure behavior to TOML."""
    target = tmp_path / "candidate.py"
    target.write_text("import not_declared_either\n", encoding="utf-8")
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        f"""
[project]
name = "example"

[tool.orbital_drift.dep_contract]
source_paths = ["{target.as_posix()}"]
fail_on_undeclared = false
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()

    status = main(["--pyproject", str(pyproject_file)], stream=stream)

    assert status == 0
    assert "UNDECLARED_IMPORT" in stream.getvalue()


# =============================================================================
# THE OTHER POSITIVE CONTROL: reality, not just fixtures.
# =============================================================================


def test_the_real_repository_declares_every_import_it_makes() -> None:
    """The exact PR#16 regression test.

    Runs the gate against the REAL ``pyproject.toml`` and the REAL
    ``src/orbital_drift`` tree. Zero undeclared imports is the claim this
    module exists to keep true mechanically, not by hand-review habit.
    """
    report = build_report(REAL_PYPROJECT, (REAL_SRC,), DepContractPolicy())

    assert report.undeclared == (), (
        f"undeclared imports found against the real repository: {report.undeclared!r}"
    )


def test_the_real_repository_gate_invocation_exits_zero() -> None:
    """The exact command `sh ci/checks.sh deps` runs, end to end, via main()."""
    stream = io.StringIO()

    status = main(
        [str(REAL_SRC), "--pyproject", str(REAL_PYPROJECT), "--fail-on-undeclared"],
        stream=stream,
    )

    assert status == 0, stream.getvalue()


def test_import_aliases_can_be_overridden_directly(tmp_path: Path) -> None:
    """Constructing the policy directly replaces the default table wholesale,
    the same semantics ``from_pyproject`` uses for every other field."""
    policy = DepContractPolicy(import_aliases=MappingProxyType({"beautifulsoup4": "bs4"}))
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        '[project]\nname = "example"\ndependencies = ["beautifulsoup4==4.12.0"]\n',
        encoding="utf-8",
    )

    declared = declared_import_map(pyproject_file, policy)

    assert declared["bs4"] == frozenset({"core"})
    # scikit-learn's alias is gone: the override REPLACED the default table,
    # so the distribution now folds through the generic hyphen->underscore rule.
    assert "sklearn" not in frozenset(policy.import_aliases.values())
