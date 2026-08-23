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
