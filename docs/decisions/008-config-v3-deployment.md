# D-008: `config-v3.toml.tmpl` — form of the template, and a PROPOSED deployment mechanism for node A

**Status:** Proposed — awaiting operator ratification (required before T005).
**Provenance, stated precisely:** D-01 and D-02 below are **agent research conclusions with sources** (the form and content of the authored template, verified against the pinned k3s tag's source — they are subject to the normal spec-guardian/adversarial-reviewer cycle, not operator ratification). D-03 is a **proposal the operator has not seen or ratified**: runbook `docs/runbooks/01-k3s-install.md` Step 12 states the deployment mechanism "is not yet specified anywhere in this repo — do not invent one", and RB-007 dispatched T004a to *propose* it, not decide it. **Nothing is deployed to node A under this doc. The operator ratifies (or replaces) D-03 before executing T005.** Until then, runbook 01's Step 2 existence check passes (the artifact exists in the repo clone) but the on-node copy step must not be improvised.
**Decision-ID namespace:** this file's `D-008/D-nn` series is independent of `docs/decisions/000-phase0-technical-decisions.md` (`D-000/D-nn`), `001-coverage-gate.md`, `002-infra-layout.md`, and plan.md's own Decision Log. Cross-references are written `D-008/D-nn`.
**Why this exists:** without it, the first time anyone needs `infra/k3s/config-v3.toml.tmpl` on node A's filesystem is mid-incident (runbook 01 Step 12, handler-not-wired-after-restart), with no ratified way to get it there — the exact "invent architecture under pressure" failure the runbook forbids.

Retrieval date for every source URL below: **2026-08-22.**

---

## D-01 — Template form: base-extension (`{{ template "base" . }}`) with a guarded fallback stanza, not a full static config

**Conclusion:** `infra/k3s/config-v3.toml.tmpl` delegates its first line to k3s's built-in v3 base template and appends exactly one conditional block — a `nvidia` runtime stanza rendered only when k3s's own per-start runtime detection did **not** register the handler.

Verified against the pinned tag `v1.35.7+k3s1` (the pin and its own provenance: `docs/decisions/versions.md`, "Host / cluster"; D-000/D-07):

- **The passthrough form works on the pinned k3s.** `pkg/agent/templates/templates.go` (`https://raw.githubusercontent.com/k3s-io/k3s/v1.35.7%2Bk3s1/pkg/agent/templates/templates.go`): `ParseTemplateFromConfig` parses the user template, then `template.Must(t.New("base").Parse(baseTemplate))` — the user template gets a `"base"` associated template. D-000/D-02b mandated the file but did not establish this form; it is now established at the exact pin, not assumed from docs.
- **The v3 template is selected first.** `pkg/agent/containerd/config.go` (same tag): `templateGenerations` is walked in order, `config-v3.toml.tmpl` before `config.toml.tmpl`; the first file found at `filepath.Join(cfg.Containerd.Template, tg.filename)` sets `ConfigVersion` and is parsed with the *matching* base template. So this file, once deployed, wins over any stray v2 template.
- **The stanza the fallback must emit, byte-verified from the base:** `ContainerdConfigTemplateV3` emits `version = 3`, the CRI runtime plugin table id `io.containerd.cri.v1.runtime` (the v3 rename of v2's `io.containerd.grpc.v1.cri`), and renders each `.ExtraRuntimes` entry as:

  ```
  [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.'{{ $k }}']
    runtime_type = "{{$v.RuntimeType}}"
  {{ with $v.BinaryName}}
  [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.'{{ $k }}'.options]
    BinaryName = {{ printf "%q" . }}
    SystemdCgroup = {{ $.SystemdCgroup }}
  {{ end }}
  ```

  The authored fallback mirrors this shape exactly (same table id, same keys, `SystemdCgroup` taken from the same context field), with `BinaryName = "/usr/local/nvidia/toolkit/nvidia-container-runtime"` — the path k3s PATH-prepends before `findContainerRuntimes()` (`pkg/agent/containerd/config_linux.go`, `runtimesPath = "/usr/local/nvidia/toolkit:/opt/kwasm/bin"`, same tag), which is the D-000/D-02b coupling to the GPU Operator's `installDir`.

**Rejected — full static config file:** it would freeze k3s's defaults (snapshotter, cgroup driver, pause image, registry handling) at authoring time; every k3s patch release becomes silent config drift, and the file would inevitably accrete host-specifics. The base-extension form is behavior-identical to *no template* whenever k3s's own detection works.

**Rejected — unconditional nvidia stanza appended after the base:** when detection succeeds, the base already emits the `nvidia` table from `.ExtraRuntimes`; emitting it again defines the same TOML table twice, a parse error, taking down the node's CRI on the next restart. The conditional guard is load-bearing, not style.

**Residual risk, stated:** the rendered output of the *fallback branch* cannot be executed locally (no k3s binary in the agent environment — denied by settings, correctly). Structural correctness is pinned by `tests/unit/test_k3s_config_template.py`; end-to-end render correctness is an on-node verification item (D-03's post-deploy check).

## D-02 — The guard tests `.BinaryName`, not the map entry: Go template struct-truthiness would make the naive guard permanently false

**Conclusion:** the fallback is guarded by `{{ if not (index .ExtraRuntimes "nvidia").BinaryName }}`, **not** `{{ if not (index .ExtraRuntimes "nvidia") }}`.

- `ExtraRuntimes` is `map[string]ContainerdRuntimeConfig` with `ContainerdRuntimeConfig{RuntimeType, BinaryName string}` — a struct **value** type (verified in `pkg/agent/templates/templates.go` at the pinned tag).
- `index` on a missing map key returns the zero value — here a zero *struct* — and Go `text/template`'s truth rules treat every struct as truthy (`text/template` `IsTrue`: structs have no falsy state). The naive guard would therefore never fire: the fallback would render on **every** start, colliding with the base's own stanza exactly when detection works (the D-01 duplicate-table parse error), i.e. the guard would be inverted from its purpose.
- `.BinaryName` on the zero struct is `""` (falsy) and on a detection-populated entry is the non-empty binary path (truthy) — the base template itself branches on the same field (`{{ with $v.BinaryName }}`), which is the strongest available evidence that k3s populates it whenever detection registers a runtime.

**Residual risk — a present entry with an empty `BinaryName`:** the evidence above supports only "detection-populated entries carry a non-empty `BinaryName`"; it does not prove an `ExtraRuntimes` entry can never exist with an empty one. If such an entry ever occurred, the failure is a *duplicate table on detection success*, not a missing fallback: the base emits the `[…runtimes.'nvidia']` table header for **every** entry unconditionally (only the `.options` sub-table sits behind `{{ with $v.BinaryName }}`), and the fallback guard fires too (empty string is falsy) — the same TOML table is defined twice, a containerd parse error, CRI down on the next restart. This is why the post-deploy "stanza appears exactly once" check (Follow-up 3) applies to every post-deploy restart, not only detection-failure scenarios.

**Pointer-type contingency (informational):** if `ExtraRuntimes`' value type were a pointer (contra the value-type reading verified above), `index` on the missing key would yield nil and `.BinaryName` on it would be a template **execution** error in exactly the detection-failed branch — Option A's post-deploy `REGENERATED` check is also the empirical closure for that case.

**Honesty note on verification depth:** the struct-truthiness behavior is from Go stdlib semantics (language-level, stable across Go versions), not from executing this template through k3s. The on-node checkpoint in D-03 closes the gap empirically. Found by the authoring agent while writing the guard, by checking `text/template`'s truth rules against the verified value type rather than assuming map-`index` semantics.

## D-03 — PROPOSED deployment mechanism: documented manual copy from the node's repo clone during runbook 01's Step 2 window, with checksum verification — operator ratifies BEFORE T005

**This is a proposal, not a decision.** Alternatives are presented in full because the choice is the operator's (RB-007; runbook 01 Step 12).

**Context that shrinks the problem:** per D-000/D-10, the repo is public and is cloned onto node A itself (Terraform and all runbooks execute there). "Deployment" is therefore a copy from an already-present local clone to k3s's containerd config directory — no transport, credentials, or new tooling needed.

### Option A (recommended) — proactive manual copy during Phase A, checksum-verified

At runbook 01's Step 2 window (where the template's existence is already checked), the operator additionally installs it on-node. Proposed command sequence, to be carried into the runbook by the T011-era amendment (this doc deliberately does **not** edit runbook 01 — that amendment rides T011):

```
cd <repo clone root on node A>
sha256sum infra/k3s/config-v3.toml.tmpl            # record the hash in the runbook verification block
sudo install -o root -g root -m 0644 -D infra/k3s/config-v3.toml.tmpl \
  /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl
sudo sha256sum /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl   # must equal the repo hash
```

**On sha256 mismatch:** delete the on-node copy (`sudo rm /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl`), re-run the `install` step, and compare again. Do **not** restart k3s — and do not proceed to the restart block below — until the on-node hash equals the repo hash: a restart with an unverified copy renders an unknown template straight into containerd's config.

Then, **only if k3s is already installed and running** (i.e. the copy happens after Step 4 rather than before it):

```
sudo systemctl restart k3s
sudo systemctl is-active k3s        # expect: active
sudo test -f /var/lib/rancher/k3s/agent/etc/containerd/config.toml && echo REGENERATED
```

- If the copy happens at Step 2 (before k3s is installed — the natural point), `install -D` creates the directory and k3s consumes the template on its very first start; no restart step needed.
- **Why proactive is safe:** per D-01, the template is behavior-identical to no template whenever detection works; the only behavioral delta is in exactly the failure mode Step 12 escalates on. The cost of a defective template (CRI breakage on restart) is bounded by the post-deploy `is-active` check landing at a moment when the cluster is empty (Phase A), not mid-Phase-B with a populated platform.
- **Rollback:** `sudo rm /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl && sudo systemctl restart k3s` — k3s falls back to its built-in template; nothing else to undo.
- **Re-sync rule:** any future change to the repo copy requires re-running the copy + checksum + restart; the checksum row in the verification block is what makes on-node drift detectable.

### Option B — escalation-only copy (deploy nothing unless runbook 01 Step 12 fires)

Same commands as A, executed only if the handler fails to wire after one restart. Pro: zero new on-node state in the happy path. Con: re-introduces a mid-incident deployment (with a restart of a by-then-populated cluster) — the precise situation the Step 2 STOP condition exists to avoid; and the happy path never exercises the template at all, so a defect stays latent until the worst moment.

### Option C — drop-in TOML fragment instead of the template

Discovered during source verification: the pinned base template emits `imports = ["<template-dir>/config-v3.toml.d/*.toml"]` (`ContainerdConfigTemplateV3`, same tag/URL as D-01), so a plain TOML fragment in `config-v3.toml.d/` could carry the nvidia stanza with no Go templating at all. **Not proposed**, for three reasons: (1) containerd's `imports` merge semantics for a table that detection *also* emits are unverified here — the duplicate-vs-merge behavior is exactly the class of question D-01's guard eliminates; (2) D-000/D-02b, runbook 01, and tasks.md T004a all name `config-v3.toml.tmpl` as the artifact — switching surfaces is a spec change, not a deployment detail; (3) a fragment is unconditional by nature, so it cannot express "only when detection failed". Recorded because a future operator seeing `config-v3.toml.d` in the rendered config should know it was considered.

### Option D — automated provisioning (Ansible / cloud-init / Terraform local-exec)

Rejected: no provisioning tooling exists anywhere in this repo; inventing it silently is forbidden (CLAUDE.md "do not improvise architecture"), Terraform's remit here is cluster resources via helm/kubernetes providers (D-000/D-06, D-000/D-10), and a `local-exec` that writes root-owned files under `/var/lib/rancher` is an agent-authored cluster-node mutation in all but name — against the spirit of Constitution I even though the operator would run the apply.

**Ratification checklist for the operator (before T005):**
1. Choose A or B (or direct otherwise). A is recommended.
2. Confirm the target directory: `/var/lib/rancher/k3s/agent/etc/containerd/` — see Follow-up 2; the first on-node action should be verifying that directory is where k3s writes `config.toml`, per D-000/D-02b's already-stated path.
3. On ratification, log a `DEC-`/`RB-` line in `docs/decision-log.md` citing D-008/D-03, and rewrite this doc's Status header to "decided" with the date — the T011-era runbook amendment then encodes the chosen commands.

---

## Follow-ups found during this review, NOT fixed here

**Each is unscheduled and needs operator triage before it becomes a task** — listing here is not agreement to do them. (Exception: item 1 already has a home, noted inline.)

| # | Finding | Evidence | Where it should land |
|---|---|---|---|
| 1 | **Runbook 01 Steps 11/13's grep misses k3s's v3 spelling of the nvidia table.** The base template renders extra runtimes *quoted* — `[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.'nvidia']` — but the runbook greps `'runtimes\.nvidia\]'`, which requires the unquoted `runtimes.nvidia]`. Measured 2026-08-22 by the T004a authoring agent: piping the v3-rendered line into the runbook's exact grep exits `1` (no match); the runbook's illustrative v2-style line matches. Consequence if unfixed: after any restart the operator sees "not wired" on a correctly wired handler, loops through Step 12's restart, then escalates a non-incident. Fix is one pattern (e.g. `runtimes\.'\{0,1\}nvidia'\{0,1\}\]` or simply grepping for `nvidia`) — but runbook 01 edits are explicitly out of T004a's scope (the amendment rides T011). | `ContainerdConfigTemplateV3` ExtraRuntimes block at tag `v1.35.7+k3s1`; local grep measurement (exit codes 1 vs 0) | T011 runbook amendment |
| 2 | **The default value of `cfg.Containerd.Template` (the directory k3s reads the template from) was not read from source** — `/var/lib/rancher/k3s/agent/etc/containerd/` is inferred from D-000/D-02b's generated-config path (`config.toml` lives there) plus `config.go` joining the template filename onto the same config's directory field. Near-certain, but the honest close-out is one on-node command before the D-03 copy: confirm `config.toml` appears in that directory after k3s first starts. | `pkg/agent/containerd/config.go` at the pinned tag (field seen; default assignment not retrieved) | D-03 ratification checklist item 2 / runbook verification block |
| 3 | **End-to-end render of the fallback branch is unverifiable off-node** (no k3s binary permitted in the agent environment, correctly). On **every** post-deploy restart — not only detection-failure scenarios — the operator should confirm `config.toml` regenerated and that the `nvidia` stanza appears **exactly once**: the duplicate-table hazard (D-02 residual risk) fires precisely when detection *succeeds* while the fallback also renders, so scoping the exactly-once check to detection failures would skip it in the one case it exists to catch. | D-01 residual-risk note; D-02 residual-risk note | Post-deploy verification in the T011-era amendment |
| 4 | **`config-v3.toml.d` drop-in merge semantics unverified** — only relevant if the operator prefers Option C over A/B. | Option C, reason (1) | Only if C is chosen |

## Verified correct — no action

- **The `{{ template "base" . }}` passthrough works for v3 templates on the pinned k3s** — this was the open question the T004a dispatch flagged (repo decision docs mandated the file without establishing the form). Verified at tag `v1.35.7+k3s1`: `ParseTemplateFromConfig` binds `"base"` to the version-matched base template. Not an assumption carried forward.
- **A stray v2 `config.toml.tmpl` cannot shadow this file** — `templateGenerations` tries `config-v3.toml.tmpl` first (`pkg/agent/containerd/config.go`, same tag).
- **`infra/k3s/.gitkeep` stays** — `tests/unit/test_repo_structure.py::test_empty_directory_is_preserved_by_gitkeep` accepts either real content or a `.gitkeep` (`has_tracked_content or (target / ".gitkeep").is_file()`), so removing it is optional; leaving it is the smaller diff and was checked, not assumed.
- **The hardcoded `/usr/local/nvidia/toolkit/nvidia-container-runtime` path is not a Constitution III violation** — it is a platform constant that must simultaneously byte-match k3s's `runtimesPath` scan constant and the GPU Operator's `installDir` (D-000/D-02b calls the coupling deliberate); a variable here would *permit* drift from a value with exactly one correct spelling. The template carries no host-specific values at all (enforced by `tests/unit/test_k3s_config_template.py::test_no_hardcoded_gpu_uuids_or_host_literals`).
