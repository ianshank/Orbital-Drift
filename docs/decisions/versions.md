# Verified Version Pins

**Retrieval date: 2026-08-08.** Every version below was confirmed against a live source in-session, per the plan's Step 3 pinning protocol. A pin without a provenance URL is a `spec-guardian` BLOCK. Anything marked UNVERIFIED must not be pinned until confirmed.

Re-verify before use if this file is more than ~30 days old. `kube-prometheus-stack` in particular releases very frequently.

## Host / cluster

| Component | Version | Source |
|---|---|---|
| k3s (recommended) | `v1.35.7+k3s1` | https://update.k3s.io/v1-release/channels · https://docs.k3s.io/release-notes/v1.35.X |
| k3s (latest stable — **not** recommended, see D-07) | `v1.36.3+k3s1` | https://update.k3s.io/v1-release/channels |
| NVIDIA driver (host, **open** modules) | `610.57.04` | https://download.nvidia.com/XFree86/Linux-x86_64/ |
| CUDA | `13.x` | https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-610-57-04/index.html |
| GPU Operator chart | `v26.3.3` | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html |

RTX 5060 Ti (device ID `2D04`) and RTX 5060 (`2D05`) are both explicitly listed as supported by driver 610.57.04: https://download.nvidia.com/XFree86/Linux-x86_64/610.57.04/README/supportedchips.html

## Platform charts

| Component | Chart repo | Chart | App | Source |
|---|---|---|---|---|
| Apache Airflow (official) | `https://airflow.apache.org` | `1.22.0` | `3.2.2` | https://github.com/apache/airflow/releases |
| Argo Workflows | `https://argoproj.github.io/argo-helm` | `1.0.23` | `v4.0.8` | https://raw.githubusercontent.com/argoproj/argo-helm/main/charts/argo-workflows/Chart.yaml |
| **MLflow (CHOSEN — community)** | `https://community-charts.github.io/helm-charts` | `1.11.4` | `3.15.1` | https://artifacthub.io/api/v1/packages/helm/community-charts/mlflow |
| MLflow (official — future migration, D-05b) | `oci://ghcr.io/mlflow/charts/mlflow` | `0.1.0` | `3.15.1` | https://mlflow.org/docs/latest/self-hosting/kubernetes-helm/ |
| lakeFS | `https://charts.lakefs.io` | `1.12.24` | `1.103.0` | https://raw.githubusercontent.com/treeverse/charts/lakefs-1.12.24/charts/lakefs/Chart.yaml |
| **SeaweedFS (CHOSEN — object store)** | `https://seaweedfs.github.io/seaweedfs/helm` | `4.41.0` | `4.41` | https://artifacthub.io/api/v1/packages/helm/seaweedfs/seaweedfs |
| MinIO (REJECTED — upstream archived, D-05) | `https://charts.min.io` | `5.4.0` | `RELEASE.2024-12-18T13-15-44Z` | https://charts.min.io/index.yaml |
| CloudNativePG operator | `https://cloudnative-pg.github.io/charts` | `0.29.0` | `1.30.0` | https://api.github.com/repos/cloudnative-pg/charts/releases |
| CNPG `cluster` chart | same | `0.8.1` | — | same |
| CNPG `plugin-barman-cloud` | same | `0.7.1` | — | same |
| kube-prometheus-stack (Phase 5) | `https://prometheus-community.github.io/helm-charts` | `88.2.0` | `v0.93.0` | https://artifacthub.io/api/v1/packages/helm/prometheus-community/kube-prometheus-stack |
| prometheus-operator-crds (CRD stage) | same | `31.0.0` | `v0.93.0` | https://artifacthub.io/api/v1/packages/helm/prometheus-community/prometheus-operator-crds |

## Rejected / not viable

- **Bitnami PostgreSQL** — chart `18.8.7` is published but its `18.4.0` image is unpullable. The public `docker.io/bitnami` catalog was deleted 2025-09-29; `bitnamilegacy/postgresql` is frozen at `17.6.0` (Aug 2025, no patches); the free tier is `latest`-only with no version pinning, which is incompatible with Principle IV. Source: https://github.com/bitnami/charts/issues/35164 · https://vcf.broadcom.com/vsc/bitnamiAnnouncement.pdf
## Corrections to earlier drafts of this file

- **`CONTAINERD_RUNTIME_CLASS` still exists** in toolkit v1.19.1 (flag `runtime-name`, aliases `runtime-class`; env sources `NVIDIA_RUNTIME_NAME`, `CONTAINERD_RUNTIME_CLASS`, `DOCKER_RUNTIME_NAME`) despite being absent from the v26.3.3 options table. Its default `nvidia` already matches k3s's handler name — **do not override it**, but it is not obsolete as an earlier draft claimed.
- **`CONTAINERD_CONFIG` and `CONTAINERD_SOCKET` are required** and must point at k3s's paths. An earlier draft claimed the NRI path made them unnecessary; that was wrong (see D-02).
- **lakeFS chart pin corrected `1.12.22` → `1.12.24`** (appVersion `1.102.0` → `1.103.0`). T007 (`docs/decisions/002-infra-layout.md` D-11, Follow-up #6) re-verified 2026-08-16 directly against the tag-pinned source (`.../treeverse/charts/lakefs-1.12.24/charts/lakefs/Chart.yaml`, three independent fetches agreeing) rather than `master`, closing the residual risk D-11 flagged when it first found the drift. The three `existingSecret`-key-path findings D-002 Follow-up #6 also references (CloudNativePG, community MLflow, SeaweedFS) are recorded in `docs/decisions/002-infra-layout.md` D-08–D-10 and `docs/decisions/000-phase0-technical-decisions.md` D-09; the UNVERIFIED bullet below is not yet edited for them — left for a follow-up PR per D-002 Follow-up #6's own scope note, since resolving it precisely for kube-prometheus-stack/MinIO too is out of T007's scope.

## UNVERIFIED — do not pin without confirming

- Garage official chart version (chart lives on a Gitea instance that could not be retrieved; the artifacthub `2.3.3` entry is a third-party mirror, not official)
- Zalando postgres-operator chart version
- `existingSecret` key names for kube-prometheus-stack, CloudNativePG, community MLflow, MinIO
- GPU Operator `v26.3.3` exact release date (sources disagreed)
- Whether GPU Operator 26.3.3 functions on driver branch 610 (validated list stops at 595.71.05)
