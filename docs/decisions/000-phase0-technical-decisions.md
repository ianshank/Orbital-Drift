# D-000: Phase 0 Technical Decisions

**Status:** decided 2026-08-08.
**Provenance, stated precisely:** the operator *chose between presented options* for D-03 (GPU pinning), D-05 (object store), D-05b (MLflow chart), and D-10 (repo visibility / Terraform host), and approved the Phase 0 reordering on 2026-08-09. **D-04 (CloudNativePG) was a forced substitution, not a choice** — no pinnable Bitnami Postgres image exists on any free registry; it was presented to and not contested by the operator. D-01, D-02, D-02b, D-06 through D-09, and D-11 are agent research conclusions with sources, not operator decisions.
**Decision-ID namespace:** this file's `D-nn` series is independent of `plan.md`'s Decision Log, which also uses `D-01…D-05` for different decisions. Cross-references from spec/plan/tasks to this file are written `D-000/D-nn`.
**Why this exists:** five parallel `infra-scaffolder` dispatches with no shared decisions produce five internally-consistent, mutually-incompatible artifacts. These answers go into every dispatch prompt. Version pins with provenance live in [versions.md](versions.md).

---

## D-01 — `driver.enabled=false`; host-install the NVIDIA driver, open kernel modules

GPU Operator's platform support matrix lists **zero GeForce parts** — its Blackwell entries are all datacenter/pro (DGX B200/B300, HGX, RTX PRO 6000). The operator's driver container ships the *datacenter* driver, whose supported-chips list excludes GeForce RTX 50-series.

Host driver `610.57.04` **does** explicitly list RTX 5060 Ti (`2D04`) and RTX 5060 (`2D05`).

**Open kernel modules are mandatory, not a preference.** From the 610.57.04 README: the proprietary flavor supports "Turing, Ampere, Ada, and Hopper"; "Blackwell and later are only supported by the open kernel modules." Install `nvidia-driver-610-open`. Getting this wrong produces `NVIDIA GPU at PCI:8:0:0 is not supported by the ... driver` — a documented real failure on a 5060 Ti.

*Caveat:* NVIDIA nowhere says "GeForce is unsupported" literally. This is inference from absence-from-matrix plus the datacenter driver's chip list. Strong, not a quotation.

Source: https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html · https://download.nvidia.com/XFree86/Linux-x86_64/610.57.04/README/kernel_open.html

## D-02 — **NRI plugin stays OFF** (supersedes an earlier draft of this document)

⚠ **An earlier revision of this file recommended `cdi.nriPluginEnabled=true`. That was wrong and would have broken D-03.** Recorded here rather than deleted, because the reasoning matters.

NVIDIA's docs recommend NRI for k3s on the grounds that it avoids modifying containerd's `config.toml`. But the NRI plugin is **not a general device-injection path** — it is for the operator's own management containers:

- It never reads `NVIDIA_VISIBLE_DEVICES`. The string does not appear in the plugin source at all; it is a pure pod-annotation consumer keyed on domain `nvidia.cdi.k8s.io`.
- It is namespace-locked — `if p.namespace != pod.Namespace { ... skip }`, where the namespace is the operator's own.
- GPU Operator v26.3.3 writes exactly one hardcoded device: `managementCDIDevice = "management.nvidia.com/gpu=all"`. NVIDIA's own API comment says NRI exists "as a means of injecting CDI devices to gpu **management** containers."
- Enabling it **deletes the `nvidia` RuntimeClass** (`clearRuntimeClasses()` → `client.Delete`, by name) — the exact object D-03 depends on. Since k3s re-stages its own `runtimes.yaml` on every server start, enabling NRI produces **flapping**, not a stable state.

`nriPluginEnabled: false` is already the v26.3.3 default, so this is a non-action — but do not follow NVIDIA's k3s advice here.

Consequence: the `CONTAINERD_*` env vars **are** required after all, pointed at k3s's paths, since the toolkit otherwise targets `/etc/containerd/config.toml`, which does not exist on k3s.

Source: `cmd/nvidia-ctk-installer/container/runtime/nri/plugin.go` @ v1.19.1 · `controllers/object_controls.go` @ v26.3.3 · https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/cdi.html

## D-02b — k3s provides the `nvidia` RuntimeClass natively; restart k3s after toolkit install

k3s hardcodes `/usr/local/nvidia/toolkit` at highest precedence into the PATH it scans for runtime binaries — **exactly** where the GPU Operator installs (`installDir: /usr/local/nvidia`). This coupling is deliberate. k3s then wires the `nvidia` handler into its generated containerd config and staticly ships `RuntimeClass/nvidia` via `manifests/runtimes.yaml`.

Two corrections to the obvious mental model:
- **RuntimeClass creation is not gated on binary presence** — it is a static manifest with no conditional logic. A `nvidia` RuntimeClass existing is *not* evidence the containerd handler is wired. Check both.
- There is **no `nvidia-cdi` RuntimeClass** despite k3s detecting that handler; docs.k3s.io is inaccurate on this point.

**Operational hazard:** k3s scans PATH **once per k3s start**. If the toolkit DaemonSet lands binaries after k3s is already up, the handler is missing from `config.toml` until restart. **The T004/T011 runbooks must include `sudo systemctl restart k3s` after first GPU Operator install**, with the verification `grep -A3 'runtimes.*nvidia' /var/lib/rancher/k3s/agent/etc/containerd/config.toml`.

Never hand-edit that `config.toml` — k3s regenerates it. Persistent changes go in `config-v3.toml.tmpl` (containerd 2.0+).

Source: `pkg/agent/containerd/config_linux.go`, `manifests/runtimes.yaml` @ k3s v1.35.7+k3s1

## D-03 — 2-GPU split: **UUID pinning** (operator decision)

**The approach originally in the plan does not exist.** The device plugin's config schema has a `Resources` field with pattern→name matching, but `startPlugins` calls `spec.DisableResourceNamingInConfig(config)` unconditionally. No flag, env var, or config key re-enables it; there is no `--resource-name` CLI flag. Every guide describing this documents removed code. Two DaemonSets don't help — both advertise `nvidia.com/gpu` and contend for the same kubelet socket. Node labels/taints don't apply either: both cards are in **one** node and GFD labels are node-scoped.

**DRA was considered and rejected** as too immature (driver v0.4.1, nothing documented about GeForce or k3s).

**Chosen: `NVIDIA_VISIBLE_DEVICES` UUID pinning, with `nvidia.com/gpu` requests zeroed cluster-wide.** The operator accepts that the scheduler is unaware of GPU occupancy and owns the bookkeeping.

Mechanics, verified:
- Requires `runtimeClassName: nvidia` — supplied natively by k3s (D-02b).
- **UUIDs only. PCI bus IDs are a trap:** they work in *legacy* mode, but the JIT-CDI path (default since toolkit v1.18.0; we're on v1.19.1) rejects them with `identifier is not a valid UUID or index`. Indices are explicitly not stable across reboots.
- UUID is documented "globally unique **immutable**", tied to the board serial. Format from `nvidia-smi -L`: `GPU 0: NVIDIA GeForce RTX 5060 Ti (UUID: GPU-<8-4-4-4-12 hex>)`. Include the `GPU-` prefix.
- **A CDI-native pod-spec path does not exist.** CDI device names *do* support UUIDs (`nvidia.com/gpu=GPU-<uuid>`), but kubelet populates `CDIDevices` exclusively from device-plugin allocate responses — a `cdi.k8s.io/` annotation in your YAML is inert. `NVIDIA_VISIBLE_DEVICES` via the NVIDIA runtime is the only mechanism.
- `ACCEPT_NVIDIA_VISIBLE_DEVICES_ENVVAR_WHEN_UNPRIVILEGED` **defaults to `true`** in both the library and installer, and GPU Operator v26.3.3 sets nothing — so unprivileged UUID pinning works out of the box, no config needed.

**Security implication, stated precisely:** any pod that can set `runtimeClassName: nvidia` *and* an env var can claim any GPU on the node, bypassing all resource accounting. Blast radius is narrower than "any pod" — pods on the default `runc` handler never reach this code path. Acceptable on a single-node home lab; the real mitigation is RBAC or a validating policy on which namespaces may reference the `nvidia` RuntimeClass. Note NVIDIA's hardened recommendation (`false` + volume-mount device lists) is **mutually exclusive with this design** — it exists to prevent exactly this.

**Keep `devicePlugin.enabled: true`.** Disabling it deterministically crashloops `nvidia-operator-validator`, which has **no `enabled` key** in the chart and so cannot be turned off to compensate (`validateGPUResource()` polls for GPU resources with no skip path; confirmed in v26.3.3 source and issues #486, #2550). Cost of leaving it on: one idle DaemonSet pod. Benefit: validator green, GFD labels intact, `nvidia.com/gpu` CDI specs generated. Just never *request* the resource.

Source: `cmd/nvidia-device-plugin/main.go` · `pkg/nvcdi/lib-nvml.go` · `internal/config/image/cuda_image.go` @ v1.19.1 · https://docs.nvidia.com/deploy/nvidia-smi/index.html

## D-04 — PostgreSQL: CloudNativePG, not Bitnami

Bitnami PostgreSQL is **not viable for a fresh pinned install**. Chart `18.8.7` is published but wants an `18.4.0` image pullable from no free public registry: the public `docker.io/bitnami` catalog was deleted 2025-09-29, `bitnamilegacy/postgresql` is frozen at `17.6.0` (Aug 2025, unpatched), free tier is `latest`-only. `latest`-only directly violates Principle IV.

**CloudNativePG** (operator `0.29.0` / app `1.30.0`, `cluster` chart `0.8.1`): images from `ghcr.io/cloudnative-pg/postgresql`, fully pinnable, active cadence. Backups to object storage via `plugin-barman-cloud` `0.7.1` — which matters because k3s `local-path` volumes are node-local and unbackupable by k8s primitives.

Cost: CNPG is CRD-based, so the Postgres `Cluster` is a CR gating the whole Airflow/MLflow/lakeFS chain. Forces D-06's staging.

Source: https://github.com/bitnami/charts/issues/35164 · https://api.github.com/repos/cloudnative-pg/charts/releases

## D-05 — Object store: **SeaweedFS 4.41.0** (operator decision)

MinIO's upstream is archived — `minio/minio` archived 2026-04-25 ("THIS REPOSITORY IS NO LONGER MAINTAINED"), community edition source-only with pre-compiled binaries discontinued and existing ones frozen; `minio/operator` archived 2026-03-20. Licensing did not change (still AGPLv3) — maintenance and distribution did. Chart `5.4.0` ships a **Dec 2024** image, ~20 months unpatched, no upstream security pipeline.

**Chosen: SeaweedFS `4.41.0`** — actively released, S3-compatible, and adopted by Kubeflow Pipelines as default object storage after MinIO's retreat, which is the strongest available signal for an MLOps stack specifically.

Cost: more setup concepts than MinIO (master / volume / filer / s3 components rather than one binary), and single-node "just works" needs more explicit configuration. Everything downstream (lakeFS blockstore, MLflow artifacts, Airflow remote logging) only needs S3-compatible + path-style, so the interface is unchanged.

⚠ Every reference to "MinIO" in `spec.md`, `plan.md`, and `tasks.md` (T007, T008, FR-003, plan.md Technical Context) is now stale. **This requires a `tasks.md`/`plan.md` PR**, per CLAUDE.md's "when tasks.md and reality disagree, update tasks.md via PR."

Source: https://github.com/minio/minio · https://artifacthub.io/api/v1/packages/helm/seaweedfs/seaweedfs

## D-05b — MLflow chart: community `1.11.4` (operator decision)

No official chart existed historically; one now does (`oci://ghcr.io/mlflow/charts/mlflow`, `0.1.0` / app `3.15.1`) but has exactly one release. The community chart (`1.11.4` / app `3.15.1`) is on the same app version with ~11 minor versions of hardening: Postgres/MySQL backends, S3/GCS/Azure artifact stores, autoscaling, auto schema migration, ServiceMonitor.

Chosen: community `1.11.4`. Risk accepted: bus-factor-1 (single maintainer, MIT, PGP-signed). Official `0.1.0` is the right destination in ~6 months — record as a future migration, not Phase 0 work.

## D-06 — Staged Terraform apply is mandatory

Three charts install CRDs that same-run resources reference. `kubernetes_manifest` validates against the API server **at plan time**, so `depends_on` is insufficient — the CRD stage must be a *separate apply*.

| Chart | CRDs | Risk |
|---|---|---|
| Argo Workflows | 8 (`argoproj.io`) | With `crds.full: true` (default), CRDs come from a **pre-install hook Job that downloads them from GitHub at install time** — network egress required, and they don't exist at render time at all. Full CRDs are ~11MB, too large to inline. Prefer `crds.full: false` (minified, ordinary templates, no hook, no egress). |
| kube-prometheus-stack | 10 (`monitoring.coreos.com`) | Every chart's `serviceMonitor.enabled: true` emits a `ServiceMonitor` → `no matches for kind`. Large enough to hit the 262144-byte annotation limit on client-side apply — use the standalone `prometheus-operator-crds` `31.0.0` in the CRD stage. |
| CloudNativePG | operator CRDs incl. `Cluster` | Gates the entire Postgres dependency chain. |

No CRDs: Airflow, MLflow, lakeFS, SeaweedFS.

**Stages:** `00-crds` → `10-storage` (CNPG `Cluster`, SeaweedFS, secrets) → `20-platform` (Airflow, MLflow, lakeFS, Argo controller) → `30-observability` (kube-prometheus-stack with `crds.enabled: false`, then flip on `serviceMonitor.enabled` upstream).

**Airflow-via-Terraform requires** `createUserJob.useHelmHooks: false` and `migrateDatabaseJob.useHelmHooks: false` (plus `applyCustomEnv: false`) — these tools "need to perform updates while preserving Kubernetes Job manifest immutability." Single most common cause of Airflow-on-Terraform failure.

## D-07 — Pin k3s to `v1.35.7+k3s1`, not latest

GPU Operator `v26.3.3` supports k3s **1.33–1.35** and containerd **1.7–2.2**. k3s `1.36.3` ships containerd `2.3.2` — outside both. k3s `v1.35.7+k3s1` (released 2026-08-04) ships containerd `2.2.5-k3s2`, inside both.

Chasing latest buys nothing here and leaves the tested envelope on the component most likely to fail (R-05). DRA's k8s ≥1.34 requirement is moot now that D-03 rejected DRA.

## D-08 — Storage: RWO is mostly fine; Airflow logs are the exception

k3s `local-path` is node-local and RWO-only. On a single-node cluster this is far less painful than it sounds — all pods land on the same node; failures are logical/plan-time, not runtime.

The one real problem is **Airflow logs**: the chart's docs say `logs.persistence.enabled: true` provisions **ReadWriteMany**, while the values default is `ReadWriteOnce` — prose and default disagree, and local-path cannot serve RWX.

**Resolution: configure S3 remote logging to SeaweedFS and leave `logs.persistence.enabled: false`.** Note the Airflow chart has **no first-class S3 remote-logging block** — only `elasticsearch`/`opensearch`. Wire via core config (`[logging] remote_logging=True`, `remote_base_log_folder`, `remote_log_conn_id`) plus an `extraSecrets` connection. Set `delete_local_logs: True`.

Non-issues: Postgres (RWO is *correct*), lakeFS (blockstore→S3, KV→Postgres), Prometheus/Grafana, the object store itself.

## D-09 — Secrets: `existingSecret` everywhere; Argo is the weak spot

No researched chart *lacks* a secret-injection path. Strong: Airflow (`fernetKeySecretName`, `webserverSecretKeySecretName`/`apiSecretKeySecretName` on 3.x, `data.metadataSecretName`), lakeFS (`secrets.existingSecret` + per-field keys).

**Argo Workflows has no single `existingSecret` value** — artifact-repository S3 credentials use per-field `secretKeySelector` references in the artifact-repository ConfigMap; `envFrom` on controller/argo-server is the alternative.

**Pre-create the Airflow Fernet key** — the chart generates one if unset and **regenerates it on some upgrade paths**, which silently invalidates every stored connection.

Bootstrapping (sealed-secrets is a Principle VII stretch, not Phase 0): operator-supplied `TF_VAR_*` → `kubernetes_secret`, every chart referencing `existingSecret`. No inline passwords in values files.

## D-10 — Terraform runs **on the Linux node**; public repo stays parameterized

Operator decision: `terraform apply` executes on node A, not from the Windows box.

Consequences:
- **No `--tls-san` needed** — the node's own kubeconfig works as-is. This removes the "decide before T005 or reinstall k3s" trap entirely.
- terraform + helm must be installed **on the node**; the T004/T011 runbooks target the node's shell (bash), not PowerShell.
- The repo must reach the node. `ianshank/Orbital-Drift` is **public**, so `git clone` needs no credentials — a genuine simplification, and it doubles as the T009 git-sync source for the same reason.
- The repo is *authored* on Windows and *executed* on Linux, so `.gitattributes` with `* text=auto eol=lf` remains mandatory in T001 — otherwise committed CRLF reaches the node and shell scripts die on `$'\r': command not found`.

Public-repo hygiene (unchanged): host-specific values are parameterized — `${NODE_A_LAN_IP}`, `${NODE_A_HOSTNAME}`, GPU UUIDs — sourced from gitignored `.env` / `*.tfvars`. **GPU UUIDs in particular must not be committed**, since D-03 puts them directly in workload manifests.

## D-11 — Phase 5 monitoring risk, recorded now because it constrains D-03

Two consequences of D-03 that surface in Phase 5 (T047 dashboards):

1. **No `pod`/`namespace` labels on GPU metrics — ever, under this design.** DCGM exporter's `--kubernetes` mapping reads the kubelet pod-resources socket to enrich metrics; with no `nvidia.com/gpu` allocations there is nothing to map. This is a consequence of bypassing allocation, not a misconfiguration. Build dashboards on `UUID`/`gpu`/`Hostname` labels, plus our own mapping (static Grafana variable or a recording rule keyed on UUID) to attribute a GPU to "training" vs "serving".
2. **`DCGM_FI_PROF_*` profiling metrics are datacenter-Volta-and-newer only.** The common `dcp-metrics-included.csv` will partially fail on GeForce. Use a `DCGM_FI_DEV_*`-only counters CSV.

⚠ **UNVERIFIED and worth one minute at T003:** no source names RTX 50-series or consumer GB20x in any DCGM support matrix. Run `dcgmi dmon -e 203,252,150` (GPU_UTIL, FB_USED, GPU_TEMP) on the host — values or "Not Supported" settles whether the Phase 5 monitoring design holds.

⚠ **This is one of four T003/T005 couplings that make T006 authored-provisional** — see the Dependencies section of `tasks.md`. The others: GPU UUIDs, `RUNTIME_CONFIG_SOURCE=file` behaviour, and GPU Operator 26.3.3 against driver branch 610. T006 must be re-reviewed against T003's and T005's verification blocks before T011 may cite it.

`dcgm.enabled` is already `false` by default in v26.3.3 (exporter uses embedded nv-hostengine); no standalone DCGM DaemonSet needed.

---

## Reference: GPU Operator v26.3.3 values for this setup

```yaml
driver:      { enabled: false }        # host driver 610.57.04, open modules (D-01)
toolkit:
  enabled: true
  env:
    - { name: CONTAINERD_CONFIG, value: /var/lib/rancher/k3s/agent/etc/containerd/config.toml }
    - { name: CONTAINERD_SOCKET, value: /run/k3s/containerd/containerd.sock }
    - { name: RUNTIME_CONFIG_SOURCE, value: "file" }
    # CONTAINERD_RUNTIME_CLASS still exists in toolkit v1.19.1 despite being
    # absent from the 26.3.3 options table; its default "nvidia" already
    # matches k3s's handler name. Do not override.
cdi:         { enabled: true, nriPluginEnabled: false }   # D-02
devicePlugin:{ enabled: true }         # D-03 — disabling crashloops the validator
gfd:         { enabled: true }
dcgmExporter:{ enabled: true }         # DCGM_FI_DEV_*-only counters CSV (D-11)
dcgm:        { enabled: false }        # default; embedded nv-hostengine
migManager:  { enabled: false }        # no MIG on consumer cards
vfioManager: { enabled: false }
sandboxDevicePlugin: { enabled: false }
ccManager:   { enabled: false }
```

Post-install once: `sudo systemctl restart k3s` (D-02b). Verify: `kubectl get runtimeclass nvidia`; `grep -A3 'runtimes.*nvidia' /var/lib/rancher/k3s/agent/etc/containerd/config.toml`; `ls -la /var/run/cdi/`; `nvidia-smi -L`.

Workload shape (D-03) — `runtimeClassName: nvidia`, `NVIDIA_VISIBLE_DEVICES: "GPU-<uuid>"`, `NVIDIA_DRIVER_CAPABILITIES: "compute,utility"`, and deliberately **no** `nvidia.com/gpu` limit.

---

## Requires empirical verification on the hardware (T003/T005)

1. **DCGM field support on consumer Blackwell** — `dcgmi dmon -e 203,252,150`. Could invalidate the Phase 5 monitoring design. Do this first.
2. **GPU UUIDs** — `nvidia-smi -L`; record which UUID is the 16GB card. Feeds every workload manifest. Do not commit these.
3. **`RUNTIME_CONFIG_SOURCE=file` on k3s + containerd 2.2.5-k3s2** — the default `command,file` runs `containerd config dump`, and k3s ships containerd as a subcommand; unverified whether it fails gracefully. `file` is the safe setting.
4. **Handler survives `systemctl restart k3s`** — operator-written and k3s-written `nvidia` handlers should be idempotent (same name, same binary), verified from source but not on a running node.
5. **GPU Operator 26.3.3 on driver branch 610** — validated driver list stops at 595.71.05; 610 appears nowhere.
6. **UUID survival across driver reinstall / PCI slot reordering** — documented "immutable" and serial-tied, but no sentence explicitly covers these. Cheap test: `nvidia-smi -L`, swap slots, repeat.
7. **`GPU-` UUID form in the rendered CDI spec** — check `/var/run/cdi/nvidia.com-gpu.yaml` on the node.

## Follow-up amendments against the spec — LANDED 2026-08-09

All applied directly to the working tree (nothing is committed yet, so these are part of the initial commit rather than separate PRs):

- ✅ **`spec.md` FR-003, `plan.md` Technical Context, `tasks.md` T007/T008** — MinIO → SeaweedFS, Bitnami Postgres → CloudNativePG (D-05, D-04). Each edit records the original text and the reason.
- ✅ **`tasks.md` Dependencies** — Phase 0 ordering relaxed so T004 and T006–T010 author in parallel with the T003/T005 hardware gates. T006 is marked authored-provisional (four couplings to T003/T005 — see the coupling table in `tasks.md`). GPU UUIDs come from `nvidia-smi -L` at T003; T006/T010 must parameterise `${ORBITAL_DRIFT_TRAIN_GPU_UUID}` / `${ORBITAL_DRIFT_SERVE_GPU_UUID}` — the names already defined in `.env.example` — rather than embed literals or invent new names.
- ✅ **`tasks.md` T001** — four CI stages → six, per FR-011 and Constitution V. Ruled conformance (not scope creep) by spec-guardian in both review rounds.
- ✅ **`CLAUDE.md` line 6, `.claude/agents/infra-scaffolder.md` line 9** — `terraform plan` struck from the allowed list in both, with the reason stated inline. This matters because the prose is what a subagent actually reads; `.claude/settings.json` already denied it, so enforcement and instruction now agree.
- ✅ **`.claude/settings.json`** — deny-list extended with `helm delete`, `terraform refresh|taint|untaint|force-unlock`, and `argo`/`argocd`/`k3s`/`k9s`/`kustomize build` ahead of T010. Note this file is defence-in-depth: it takes effect at session start, and `sh -c "…"` wrappers evade pattern matching. The agent instructions remain the primary control.
- ✅ **`plan.md` Project Structure** — added `docs/decisions/`, `.github/workflows/`, `README.md`, and the root config files that T001 legitimately created but the tree block never listed.
