"""Independent AST and subprocess enforcement of the architecture boundary."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "orbital_drift"
CONFIGURATION_PATH = REPOSITORY_ROOT / "pyproject.toml"


def _architecture_settings() -> tuple[set[str], set[str]]:
    """Read the single source of truth for protected packages and forbidden modules."""
    with CONFIGURATION_PATH.open("rb") as configuration_file:
        configuration = tomllib.load(configuration_file)
    architecture = configuration["tool"]["orbital_drift"]["architecture"]
    packages = set(architecture["domain_packages"])
    forbidden = set(architecture["forbidden_third_party"])
    return packages, forbidden


def _top_level_imports(path: Path) -> set[str]:
    """Return the top-level names imported by one Python module, without importing it."""
    tree = ast.parse(path.read_text(), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[0])
    return imported


def test_domain_and_ports_import_only_stdlib_or_orbital_drift() -> None:
    packages, forbidden = _architecture_settings()
    allowed = set(sys.stdlib_module_names) | {"orbital_drift"}

    for package in packages:
        package_directory = SOURCE_ROOT / package.rsplit(".", maxsplit=1)[1]
        for module_path in sorted(package_directory.rglob("*.py")):
            imports = _top_level_imports(module_path)
            assert imports <= allowed, (
                f"{module_path.relative_to(REPOSITORY_ROOT)} imports {imports - allowed}"
            )
            assert not imports & forbidden, (
                f"{module_path.relative_to(REPOSITORY_ROOT)} imports forbidden modules "
                f"{imports & forbidden}"
            )


def test_import_linter_contracts_pass_and_missing_binary_is_an_explicit_failure() -> None:
    executable = shutil.which("lint-imports")
    if executable is None:
        pytest.fail("lint-imports is required for architecture enforcement but is absent from PATH")
    completed = subprocess.run(
        [executable, "--config", ".importlinter"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, (
        f"import-linter contracts failed:\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_ports_isolation_contract_would_catch_violation() -> None:
    """T058 positive control: verify ports_isolation contract is not decorative.

    The ports_isolation contract was added to close the gap where a port could
    import its own concrete counterpart (e.g., ports/dataversion → data/lakefs_ops)
    without failing. This test verifies the contract's forbidden_modules list is
    comprehensive and non-empty - if it were empty or misspelled, the contract
    would pass even with violations.

    We verify:
    1. The forbidden_modules list contains all application-layer packages
    2. No ports module currently imports from forbidden packages (sanity check)
    3. The contract config is not misconfigured (e.g., empty source_modules)
    """
    # Read the .importlinter config
    import configparser

    config = configparser.ConfigParser()
    config.read(REPOSITORY_ROOT / ".importlinter")

    # Verify the ports_isolation contract exists and is properly configured
    contract_section = "importlinter:contract:ports_isolation"
    assert config.has_section(contract_section), (
        "ports_isolation contract is missing from .importlinter"
    )

    # Verify source_modules is not empty
    source_modules = config.get(contract_section, "source_modules").strip()
    assert source_modules, "source_modules is empty - contract is decorative"
    assert "orbital_drift.ports" in source_modules, (
        "source_modules must include orbital_drift.ports"
    )

    # Verify forbidden_modules contains the expected application-layer packages
    forbidden_modules = config.get(contract_section, "forbidden_modules").strip()
    assert forbidden_modules, "forbidden_modules is empty - contract is decorative"

    expected_forbidden = {
        "orbital_drift.data",
        "orbital_drift.drift",
        "orbital_drift.eval",
        "orbital_drift.ingest",
        "orbital_drift.observability",
        "orbital_drift.planning",
        "orbital_drift.quality",
        "orbital_drift.registry",
        "orbital_drift.serve",
        "orbital_drift.train",
    }
    actual_forbidden = {line.strip() for line in forbidden_modules.split("\n") if line.strip()}
    missing = expected_forbidden - actual_forbidden
    assert not missing, f"forbidden_modules is missing application packages: {missing}"


def test_ports_isolation_contract_catches_planted_violation() -> None:
    """T058 true positive control: prove the contract fails on a real violation.

    This test plants an actual import violation in ports/, runs lint-imports,
    and verifies it returns non-zero. Without this, we only prove the contract
    *exists* syntactically, not that it *works* at runtime.
    """
    executable = shutil.which("lint-imports")
    if executable is None:
        pytest.fail(
            "lint-imports is required for the planted-violation control but is absent from PATH"
        )

    violation_file = SOURCE_ROOT / "ports" / "_test_violation_t058.py"
    try:
        violation_file.write_text(
            "# T058 positive control: planted violation - DO NOT COMMIT\n"
            "from orbital_drift.data import lakefs_ops  # noqa: F401\n"
        )

        result = subprocess.run(
            [executable, "--config", ".importlinter"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0, (
            "import-linter should reject ports → data import, but it passed. "
            "The ports_isolation contract may be misconfigured.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        output = f"{result.stdout}\n{result.stderr}"
        assert "Ports must not import application-layer adapters" in output, (
            "Expected the ports_isolation contract title in lint-imports output, "
            f"not the ini section id.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    finally:
        violation_file.unlink(missing_ok=True)


def test_no_ports_currently_import_application_modules() -> None:
    """T058 sanity check: verify ports don't currently violate the new contract.

    This test independently verifies (via AST, not import-linter) that no ports
    module imports from application-layer packages. If this test fails, the
    ports_isolation contract should also fail - if it doesn't, the contract is
    misconfigured.
    """
    application_packages = {
        "data",
        "drift",
        "eval",
        "ingest",
        "observability",
        "planning",
        "quality",
        "registry",
        "serve",
        "train",
    }

    ports_directory = SOURCE_ROOT / "ports"
    for module_path in sorted(ports_directory.rglob("*.py")):
        imports = _top_level_imports(module_path)
        # Check both top-level module names and orbital_drift subpackages
        for imp in imports:
            assert imp not in application_packages, (
                f"{module_path.relative_to(REPOSITORY_ROOT)} imports forbidden "
                f"application package '{imp}'"
            )
        # Also check for orbital_drift.* imports
        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("orbital_drift.")
            ):
                subpackage = node.module.split(".")[1]
                assert subpackage not in application_packages, (
                    f"{module_path.relative_to(REPOSITORY_ROOT)} imports forbidden "
                    f"orbital_drift.{subpackage}"
                )
