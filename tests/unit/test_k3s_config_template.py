"""Unit tests for ``infra/k3s/config-v3.toml.tmpl`` (T004a).

The template is the ONLY supported way to persist containerd configuration on
k3s: k3s regenerates ``/var/lib/rancher/k3s/agent/etc/containerd/config.toml``
on every server start and silently discards hand-edits (D-000/D-02b). Runbook
``docs/runbooks/01-k3s-install.md`` Step 2 carries a STOP condition on this
file's existence, and Step 12's escalation path depends on it encoding the
``nvidia`` runtime handler correctly.

What these tests pin down (load-bearing keys, not exact formatting):

* the file exists where the runbook's Step 2 checks for it;
* it targets containerd config **version 3** (k3s v1.35.7+k3s1 bundles
  containerd v2.2.5-k3s2 — pinned with provenance in
  ``docs/decisions/versions.md``; config v3 renames the CRI runtime plugin
  table from v2's ``io.containerd.grpc.v1.cri`` to
  ``io.containerd.cri.v1.runtime``);
* it encodes the ``nvidia`` runtime stanza per D-000/D-02b: handler name
  ``nvidia``, ``runtime_type = "io.containerd.runc.v2"``, ``BinaryName``
  pointing into ``/usr/local/nvidia/toolkit/`` — the path k3s hardcodes at
  highest scan precedence and the GPU Operator deliberately installs to;
* the fallback stanza is fully enclosed in the ``not ... .BinaryName`` guard
  branch — exact guard text and polarity included (D-008/D-02): guard-removed
  and guard-inverted templates both collide with the base's own nvidia table
  and must fail here, not on-node;
* the ``.options`` table header is present — containerd reads ``BinaryName``
  only from the handler's ``.options`` table, so losing the header silently
  degrades the handler to plain runc;
* it never enables/configures NRI (D-000/D-02: the GPU Operator's NRI path
  deletes the ``nvidia`` RuntimeClass that D-000/D-03's UUID pinning depends
  on — NRI must not appear in active, non-comment template content);
* it carries no host-specific literals (Constitution III / D-000/D-10): no
  GPU UUIDs, no IPv4 addresses, no hostname substitutions.

Source of truth for the required stanza:
``docs/decisions/000-phase0-technical-decisions.md`` D-02/D-02b and
``docs/runbooks/01-k3s-install.md`` Steps 2, 11, 12.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "infra" / "k3s" / "config-v3.toml.tmpl"

# containerd config-v3 CRI runtime plugin table id; TOML permits either quote
# style around the dotted key, so accept both.
_V3_CRI_RUNTIME_PLUGIN = r"plugins\.['\"]io\.containerd\.cri\.v1\.runtime['\"]"
# The v2 spelling must NOT appear in active content: a v2 table id inside a
# file k3s renders as config v3 would be a silently-dead stanza.
_V2_CRI_PLUGIN = "io.containerd.grpc.v1.cri"


def _template_text() -> str:
    assert TEMPLATE_PATH.is_file(), (
        "infra/k3s/config-v3.toml.tmpl is missing — runbook 01 Step 2 STOPs on this "
        "file's absence, and Step 12's escalation path depends on it (T004a)"
    )
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _active_lines(text: str) -> list[str]:
    """Template lines with TOML comments stripped (comment-only lines dropped).

    The template documents the NRI rule in comments; assertions about what the
    template DOES must ignore what its comments merely SAY.
    """
    stripped: list[str] = []
    for line in text.splitlines():
        code = line.split("#", 1)[0].rstrip()
        if code.strip():
            stripped.append(code)
    return stripped


def test_template_file_exists() -> None:
    """Runbook 01 Step 2's ``test -f infra/k3s/config-v3.toml.tmpl`` must pass."""
    assert TEMPLATE_PATH.is_file(), (
        "missing infra/k3s/config-v3.toml.tmpl — see runbook 01 Step 2 STOP condition"
    )


def test_extends_base_template_and_targets_v3() -> None:
    """D-008/D-01 is a MADE decision: base-extension form, v3 plugin table ids.

    The template must delegate to k3s's built-in v3 base template via
    ``{{ template "base" . }}``. The base emits ``version = 3`` and every k3s
    default (snapshotter, cgroup driver, pause image, registry handling); the
    static form — a literal ``version = 3`` stub with no base call — was
    explicitly REJECTED in D-008/D-01 (it freezes k3s defaults at authoring
    time, turning every patch release into silent config drift). This test
    therefore pins the base call itself; a static config is the rejected
    alternative, not an equivalent way to "target v3".

    A literal top-level ``version = 3`` must also be ABSENT from active
    content: the base already emits it, and repeating a top-level key makes
    the rendered config a duplicate-key TOML parse error.
    """
    active = "\n".join(_active_lines(_template_text()))
    assert '{{ template "base" . }}' in active, (
        'template does not call {{ template "base" . }} in active content — '
        "D-008/D-01 decided the base-extension form; a static stub here is the "
        "rejected alternative, not an equivalent"
    )
    assert re.search(_V3_CRI_RUNTIME_PLUGIN, active), (
        "template never references the v3 CRI runtime plugin table id "
        "(io.containerd.cri.v1.runtime) — it does not verifiably target config v3"
    )
    assert not re.search(r"^\s*version\s*=\s*3\s*$", active, flags=re.MULTILINE), (
        "active content declares a literal `version = 3` alongside (or instead of) "
        "the base call — the base template already emits that key; a second copy is "
        "a duplicate-key TOML parse error in the rendered config"
    )


def test_v2_cri_plugin_id_absent_from_active_content() -> None:
    """No ``io.containerd.grpc.v1.cri`` table in active (non-comment) content.

    That id is the config-v2 spelling; inside a v3 render it would produce a
    stanza containerd's CRI plugin never reads — the handler would look wired
    in the file while being dead at runtime, the exact failure mode runbook 01
    Step 13 exists to catch.
    """
    active = "\n".join(_active_lines(_template_text()))
    assert _V2_CRI_PLUGIN not in active, (
        f"active template content references the config-v2 plugin id {_V2_CRI_PLUGIN!r}; "
        "config v3 requires io.containerd.cri.v1.runtime"
    )


def test_nvidia_runtime_stanza_load_bearing_keys() -> None:
    """The D-000/D-02b stanza: nvidia runtimes table, runc-v2 shim, toolkit BinaryName."""
    active = "\n".join(_active_lines(_template_text()))

    nvidia_table = re.search(
        _V3_CRI_RUNTIME_PLUGIN + r"\.containerd\.runtimes\.['\"]?nvidia['\"]?\]",
        active,
    )
    assert nvidia_table, (
        "no [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.nvidia] table "
        "found — the template does not encode the nvidia handler (D-000/D-02b)"
    )

    options_table = re.search(
        _V3_CRI_RUNTIME_PLUGIN + r"\.containerd\.runtimes\.['\"]?nvidia['\"]?\.options\]",
        active,
    )
    assert options_table, (
        "no [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.'nvidia'"
        ".options] table header — containerd reads BinaryName only from the "
        "handler's .options table; without the header the keys land in the parent "
        "table and the handler silently degrades to plain runc (D-008/D-01's "
        "byte-verified stanza shape)"
    )
    assert options_table.start() > nvidia_table.start(), (
        "the .options table header must follow the nvidia runtimes table header "
        "(D-008/D-01's byte-verified stanza shape)"
    )

    # Scope the key assertions to the nvidia stanza itself (Copilot review, PR #7):
    # a global search would pass even if these keys sat in some other table, which
    # is exactly the silently-dead-handler failure mode this suite exists to block.
    runtime_region = active[nvidia_table.end() : options_table.start()]
    assert re.search(r"""runtime_type\s*=\s*['"]io\.containerd\.runc\.v2['"]""", runtime_region), (
        'the nvidia runtimes table itself must set runtime_type = "io.containerd.runc.v2" '
        "(between its header and the .options header, not anywhere else in the file)"
    )

    options_region = active[options_table.end() :]
    end_marker = options_region.find("{{- end }}")
    if end_marker != -1:
        options_region = options_region[:end_marker]
    binary_name = re.search(r"""BinaryName\s*=\s*['"]([^'"]+)['"]""", options_region)
    assert binary_name, "the nvidia .options table itself must set BinaryName"
    assert binary_name.group(1).startswith("/usr/local/nvidia/toolkit/"), (
        "BinaryName must point into /usr/local/nvidia/toolkit/ — the path k3s scans at "
        "highest precedence and the GPU Operator installs to (D-000/D-02b); got "
        f"{binary_name.group(1)!r}"
    )
    assert binary_name.group(1) == "/usr/local/nvidia/toolkit/nvidia-container-runtime", (
        "BinaryName must be the toolkit's nvidia-container-runtime binary; got "
        f"{binary_name.group(1)!r}"
    )


def test_fallback_guard_polarity_and_full_enclosure() -> None:
    """D-008/D-02: the fallback stanza sits entirely inside the not-BinaryName branch.

    The guard's exact text — polarity included — is load-bearing, not style:

    * guard REMOVED → the stanza renders unconditionally; when detection
      succeeds the base has already emitted the same nvidia table, so the
      rendered config defines one TOML table twice — a containerd parse
      error, CRI down on the next restart (D-008/D-01, rejected-unconditional);
    * guard INVERTED (``not`` dropped) → the stanza renders exactly when
      detection SUCCEEDED — the same duplicate-table collision, now bricking
      precisely the happy path, while the detection-failed case the fallback
      exists for renders nothing.

    Enclosure matters end to end: the guard must open BEFORE the nvidia table
    header and the closing ``{{- end }}`` must fall AFTER the ``SystemdCgroup``
    line, so both tables and every key of the fallback are inside the branch.
    """
    active = "\n".join(_active_lines(_template_text()))

    guard = '{{- if not (index .ExtraRuntimes "nvidia").BinaryName }}'
    assert guard in active, (
        'fallback guard `{{- if not (index .ExtraRuntimes "nvidia").BinaryName }}` '
        "is missing from active content, or its polarity changed — either way the "
        "stanza collides with the base's own nvidia table on some restart "
        "(D-008/D-02; exact text and the `not` are both load-bearing)"
    )
    guard_idx = active.index(guard)

    nvidia_table = re.search(
        _V3_CRI_RUNTIME_PLUGIN + r"\.containerd\.runtimes\.['\"]?nvidia['\"]?\]",
        active,
    )
    assert nvidia_table, "nvidia runtimes table header missing from active content"
    assert guard_idx < nvidia_table.start(), (
        "the guard must open BEFORE the nvidia table header — a header outside the "
        "guard renders unconditionally and collides with the base's stanza"
    )

    cgroup = re.search(r"^\s*SystemdCgroup\s*=", active, flags=re.MULTILINE)
    assert cgroup, (
        "fallback stanza must set SystemdCgroup (mirrors the base template's "
        "byte-verified shape, D-008/D-01)"
    )
    assert cgroup.start() > guard_idx, "the SystemdCgroup line must sit inside the guarded branch"

    assert active.find("{{- end }}", cgroup.end()) != -1, (
        "no `{{- end }}` after the SystemdCgroup line — the fallback stanza is not "
        "fully enclosed in the not-BinaryName branch; whatever trails the last "
        "closed block renders unconditionally"
    )


def test_nri_never_appears_in_active_content() -> None:
    """D-000/D-02: NRI stays OFF — not configured, not enabled, not present.

    Enabling the GPU Operator's NRI plugin deletes the ``nvidia`` RuntimeClass
    (``clearRuntimeClasses()`` → ``client.Delete``) that D-000/D-03's UUID
    pinning depends on, and k3s's per-start ``runtimes.yaml`` re-staging turns
    that into flapping. The template must not touch NRI in any direction —
    comments explaining the rule are fine; active config lines are not.
    """
    for line in _active_lines(_template_text()):
        assert "nri" not in line.lower(), (
            f"NRI must not appear in active template content (D-000/D-02): {line!r}"
        )


def test_no_hardcoded_gpu_uuids_or_host_literals() -> None:
    """Constitution III / D-000/D-10: no GPU UUIDs, no IPv4 literals, no hostnames.

    GPU UUIDs are gathered at T003 via ``nvidia-smi -L`` and live only in the
    gitignored ``.env`` (D-000/D-10 forbids committing them); the template has
    no legitimate use for them, an IP, or a node hostname. Nothing in this
    template is host-specific — that is asserted, not assumed.
    """
    text = _template_text()
    assert not re.search(r"GPU-[0-9a-fA-F]{8}\b", text), (
        "template contains what looks like a hardcoded GPU UUID — forbidden (D-000/D-10)"
    )
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text), (
        "template contains an IPv4 literal — host-specific values are forbidden "
        "(Constitution III / D-000/D-10)"
    )
    for env_var in ("NODE_A_HOSTNAME", "NODE_A_LAN_IP"):
        assert env_var not in text, (
            f"template references {env_var} — k3s renders this template with its own Go "
            "template context, not shell env; a shell-style substitution here would land "
            "verbatim (and unresolved) in containerd's config"
        )
