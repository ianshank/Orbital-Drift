"""Behavioral tests for the AST-based Constitution Principle III build gate."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from orbital_drift.quality.hardcode_scan import (
    HardcodeScanError,
    LiteralCategory,
    ScanPolicy,
    main,
    scan_paths,
    scan_source,
)


def test_positive_controls_report_configuration_literals() -> None:
    """URLs, thresholds, endpoints, buckets, and device selectors must all trip the gate."""
    cases = (
        ('endpoint = "https://service.example/v1"\n', LiteralCategory.URL_LITERAL),
        ("threshold = 0.25\n", LiteralCategory.NUMERIC_LITERAL),
        ("connect(timeout=30.0)\n", LiteralCategory.NUMERIC_LITERAL),
        ('bucket = "s3://bucket/tiles"\n', LiteralCategory.URL_LITERAL),
        ('device = "cuda:0"\n', LiteralCategory.HOST_LITERAL),
    )

    for source, category in cases:
        findings = scan_source(source, path="positive.py", policy=ScanPolicy())
        assert len(findings) == 1
        assert findings[0].category is category


def test_negative_controls_exclude_structural_and_syntactic_literals() -> None:
    """AST context avoids flagging values that are not runtime configuration choices."""
    source = '''"""Documentation may name https://example.com without becoming config."""
from typing import Literal
values = [0, 1, 2]
last = values[-1]
annotation: Literal[3]
__all__ = ["https://export.example"]
__all__: list[str] = ["s3://annotated-export"]
threshold = 30.0  # pin: verified in the documented calibration procedure
'''

    assert scan_source(source, path="negative.py", policy=ScanPolicy()) == ()


def test_bare_pin_marker_does_not_exempt_a_literal() -> None:
    """A marker without a reason cannot launder an unexplained hardcoded value."""
    findings = scan_source("threshold = 30.0  # pin:\n", path="bare_pin.py", policy=ScanPolicy())

    assert len(findings) == 1
    assert findings[0].value_repr == "30.0"


def test_module_constants_are_strict_by_default_and_configurable() -> None:
    """Naming a value is not a configuration source unless the policy explicitly permits it."""
    source = "RETRY_TIMEOUT = 30.0\n"

    strict_findings = scan_source(source, path="module_constant.py", policy=ScanPolicy())
    relaxed_findings = scan_source(
        source,
        path="module_constant.py",
        policy=ScanPolicy(allow_module_constants=True),
    )

    assert len(strict_findings) == 1
    assert relaxed_findings == ()


def test_exempt_paths_are_not_parsed_as_production_code() -> None:
    """Policy-owned config and projection files stay usable as literal declarations."""
    policy = ScanPolicy()

    assert scan_source("value = 30.0\n", path="src/orbital_drift/config.py", policy=policy) == ()
    assert scan_source("value = 30.0\n", path="tests/unit/example.py", policy=policy) == ()


def test_policy_round_trips_from_pyproject(tmp_path: Path) -> None:
    """Every policy knob is TOML-backed, proving policy is configuration rather than code."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[tool.orbital_drift.hardcode_scan]
allowed_numbers = [0, 4.5]
string_patterns = ["redis://"]
exempt_paths = ["generated/**"]
pin_marker = "# approved:"
max_string_length = 12
truncation_suffix = "~"
allow_module_constants = true
paths = ["package"]
fail_on_findings = false
absolute_path_prefix = "root/"
url_patterns = ["redis://"]
host_patterns = ["gpu:"]
""".lstrip(),
        encoding="utf-8",
    )

    expected = ScanPolicy(
        allowed_numbers=frozenset({0.0, 4.5}),
        string_patterns=("redis://",),
        exempt_paths=("generated/**",),
        pin_marker="# approved:",
        max_string_length=12,
        truncation_suffix="~",
        allow_module_constants=True,
        paths=("package",),
        fail_on_findings=False,
        absolute_path_prefix="root/",
        url_patterns=("redis://",),
        host_patterns=("gpu:",),
    )

    assert ScanPolicy.from_pyproject(pyproject) == expected


def test_policy_falls_back_when_the_table_is_absent_or_malformed(tmp_path: Path) -> None:
    """A normal project TOML without scanner policy retains the strict defaults."""
    absent = tmp_path / "absent.toml"
    absent.write_text("[project]\nname = 'example'\n", encoding="utf-8")
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("[tool]\norbital_drift = 'not a table'\n", encoding="utf-8")
    tool_malformed = tmp_path / "tool-malformed.toml"
    tool_malformed.write_text("tool = 'not a table'\n", encoding="utf-8")
    table_malformed = tmp_path / "table-malformed.toml"
    table_malformed.write_text(
        "[tool.orbital_drift]\nhardcode_scan = 'not a table'\n",
        encoding="utf-8",
    )

    assert ScanPolicy.from_pyproject(absent) == ScanPolicy()
    assert ScanPolicy.from_pyproject(malformed) == ScanPolicy()
    assert ScanPolicy.from_pyproject(tool_malformed) == ScanPolicy()
    assert ScanPolicy.from_pyproject(table_malformed) == ScanPolicy()


def test_findings_are_deterministic_across_multiple_files(tmp_path: Path) -> None:
    """Directory expansion and final sorting make output byte-stable across runs."""
    alpha = tmp_path / "alpha.py"
    beta = tmp_path / "beta.py"
    alpha.write_text('url = "https://alpha.example"\n', encoding="utf-8")
    beta.write_text("timeout = 30.0\n", encoding="utf-8")

    first = scan_paths((beta, alpha), ScanPolicy())
    second = scan_paths((tmp_path,), ScanPolicy())

    assert first == second
    assert first == tuple(sorted(first, key=lambda item: (item.path, item.line, item.column)))


def test_scan_paths_ignores_non_python_files_and_errors_for_missing_path(tmp_path: Path) -> None:
    """Only executable Python is a scanner input, while bad path selection stops the gate."""
    note = tmp_path / "note.txt"
    note.write_text("timeout = 30.0\n", encoding="utf-8")

    assert scan_paths((note,), ScanPolicy()) == ()
    with pytest.raises(HardcodeScanError, match="does not exist"):
        scan_paths((tmp_path / "missing.py",), ScanPolicy())


def test_cli_reports_json_and_text_with_both_failure_branches(tmp_path: Path) -> None:
    """The CLI is a usable gate in JSON automation and human-readable terminal modes."""
    target = tmp_path / "candidate.py"
    target.write_text("timeout = 30.0\n", encoding="utf-8")
    policy_file = tmp_path / "pyproject.toml"
    policy_file.write_text("[project]\nname = 'example'\n", encoding="utf-8")

    json_stream = io.StringIO()
    failing_status = main(
        [str(target), "--policy-file", str(policy_file), "--format", "json", "--fail-on-findings"],
        stream=json_stream,
    )
    payload = json.loads(json_stream.getvalue())

    text_stream = io.StringIO()
    passing_status = main(
        [
            str(target),
            "--policy-file",
            str(policy_file),
            "--format",
            "text",
            "--no-fail-on-findings",
        ],
        stream=text_stream,
    )

    assert failing_status == 1
    assert passing_status == 0
    assert len(payload) == 1
    assert set(payload[0]) == {"path", "line", "column", "value_repr", "category", "source_line"}
    assert "NUMERIC_LITERAL 30.0" in text_stream.getvalue()


def test_cli_uses_policy_paths_and_policy_failure_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No positional paths delegates both target selection and failure behavior to TOML."""
    target = tmp_path / "candidate.py"
    target.write_text('url = "https://candidate.example"\n', encoding="utf-8")
    policy_file = tmp_path / "pyproject.toml"
    policy_file.write_text(
        f"""
[tool.orbital_drift.hardcode_scan]
paths = ["{target.as_posix()}"]
fail_on_findings = false
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()

    status = main(["--policy-file", str(policy_file)], stream=stream)

    assert status == 0
    assert "URL_LITERAL" in stream.getvalue()


def test_syntax_errors_raise_typed_error() -> None:
    """Malformed source is a failed quality gate, never an unreported scan hole."""
    with pytest.raises(HardcodeScanError, match=r"Unable to parse broken\.py"):
        scan_source("if True print('broken')\n", path="broken.py", policy=ScanPolicy())


def test_long_rendered_value_uses_the_policy_limit() -> None:
    """Report rendering obeys the policy rather than embedding a display threshold in code."""
    policy = ScanPolicy(max_string_length=10, truncation_suffix="~")
    findings = scan_source(
        'url = "https://example.com/very/long/path"\n', path="long.py", policy=policy
    )

    assert findings[0].value_repr.endswith("~")
    assert len(findings[0].value_repr) == 11


def test_annotations_and_string_categories_cover_ast_context_rules() -> None:
    """Keep annotation syntax exempt while deployment strings remain findings."""
    annotations = """\
from typing import Literal
type Alias = Literal[3]
def convert(
    value: Literal[4],
    /,
    *args: Literal[5],
    option: Literal[6],
    **kwargs: Literal[7],
) -> Literal[8]:
    return 0
"""
    custom_policy = ScanPolicy(
        string_patterns=("deployment",),
        url_patterns=(),
        host_patterns=(),
        absolute_path_prefix="/",
    )

    assert scan_source(annotations, path="annotations.py", policy=ScanPolicy()) == ()
    path_finding = scan_source('root = "/var/lib/orbital"\n', path="path.py", policy=custom_policy)
    generic_finding = scan_source(
        'target = "deployment-zone"\n',
        path="generic.py",
        policy=custom_policy,
    )
    ordinary = scan_source(
        'label = "ordinary prose"\nflag = True\n', path="ordinary.py", policy=custom_policy
    )

    assert path_finding[0].category is LiteralCategory.PATH_LITERAL
    assert generic_finding[0].category is LiteralCategory.URL_LITERAL
    assert ordinary == ()
