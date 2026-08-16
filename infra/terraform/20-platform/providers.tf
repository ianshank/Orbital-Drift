# infra/terraform/20-platform/providers.tf
#
# D-002/D-12: 20-platform is one shared Terraform root module across four
# independently-dispatched `infra-scaffolder` runs (T007's lakefs.tf, T008's
# mlflow.tf, T009's airflow.tf, T010's argo_workflows.tf). Terraform allows
# exactly ONE unaliased `provider "helm"`/`provider "kubernetes"` block and
# exactly ONE declaration of any given `variable` name per root module — not
# one per file. Each of the four dispatches independently declared its own
# copies of the provider blocks and every variable more than one release
# needs (each correctly self-contained and independently `terraform
# validate`-able in isolation, since worktree isolation meant none could see
# the others' output at authoring time). This file is the orchestrator's
# integration-time consolidation: every declaration below appeared,
# duplicated, in at least two of the four dispatches' own files; it is kept
# here exactly once, and every component file references it rather than
# redeclaring it.
#
# Provider CONFIGURATION (not just the version constraint in versions.tf,
# D-002/D-03). Reads config_path from a variable, never a literal path, per
# Constitution III and D-000/D-10 ("terraform runs on the Linux node ... the
# repo stays parameterized").

variable "kubeconfig_path" {
  description = "Path to the kubeconfig Terraform's helm/kubernetes providers use. Host-specific (D-000/D-10) — no default, supplied via ../common.tfvars."
  type        = string
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

provider "helm" {
  # helm provider 3.x: `kubernetes` is a single nested object attribute
  # (`kubernetes = { ... }`), not a block — a breaking rewrite from 2.x
  # (terraform-plugin-sdk/v2 -> terraform-plugin-framework). Confirmed
  # independently by three of the four T007-T010 dispatches against the
  # provider's own v3-upgrade-guide.md; see
  # docs/decisions/003-t008-mlflow-secret-wiring-findings.md D-003/05.
  kubernetes = {
    config_path = var.kubeconfig_path
  }
}

# ---------------------------------------------------------------------------
# Shared cross-cutting variables (D-002/D-06, D-002/D-07's shared-variable
# table). Each is consumed by at least three of the four component files in
# this directory. No in-code default anywhere (D-002's explicit rule,
# confirmed by two spec-guardian rounds on D-002 itself) — proposed defaults
# live only in terraform.tfvars.example.
# ---------------------------------------------------------------------------

variable "orbital_drift_namespace" {
  description = "Namespace shared by every orbital-drift platform release (D-002/D-06): CNPG Cluster CR, SeaweedFS, lakeFS, MLflow, Airflow, Argo controller. Consumed by every file in this directory. Proposed default \"orbital-drift\" lives only in terraform.tfvars.example."
  type        = string
}

variable "cnpg_cluster_name" {
  description = "Name of the CloudNativePG Cluster CR created by 10-storage/cnpg_cluster.tf (a separate Terraform root module/state — this is a re-declaration for D-002/D-07's loosely-coupled-roots pattern, not a cross-module reference). CNPG auto-creates \"<this>-app\" holding app-user credentials (discrete user/password/host/port/dbname/uri keys — D-002/D-08, confirmed independently against CNPG's own source by the T009 dispatch, docs/decisions/004-t009-airflow-findings.md Finding 1). Consumed by lakefs.tf, mlflow.tf, airflow.tf. Proposed default \"orbital-drift-postgres\" lives only in terraform.tfvars.example."
  type        = string
}

variable "seaweedfs_s3_secret_name" {
  description = "Name of the Kubernetes Secret 10-storage/seaweedfs.tf creates, holding SeaweedFS's own `seaweedfs_s3_config` JSON blob AND discrete `access_key_id`/`secret_access_key` keys for every downstream existingSecret-style consumer (D-002/D-07, confirmed against T007's actual seaweedfs.tf — the discrete key names are exactly `access_key_id`/`secret_access_key`, snake_case). Consumed by every file in this directory. Proposed default \"orbital-drift-seaweedfs-s3-credentials\" lives only in terraform.tfvars.example."
  type        = string
}

variable "seaweedfs_s3_endpoint" {
  description = "In-cluster S3-compatible endpoint URL for SeaweedFS's S3 gateway (full URL form, e.g. http://<service>.<namespace>.svc.cluster.local:8333), consumed by lakefs.tf (blockstore endpoint override) and mlflow.tf (extraEnvVars.MLFLOW_S3_ENDPOINT_URL) and airflow.tf (the Airflow 'aws' connection's endpoint_url extra) — all three independently arrived at the same best-effort Helm-fullname-convention guess for this value. argo_workflows.tf needs a DIFFERENT shape (bare host:port, no scheme — Argo's artifactRepository.s3 schema takes `endpoint` + a separate `insecure` boolean) and declares its own `var.argo_workflows_s3_endpoint` for that reason; this is not an oversight. UNCONFIRMED against a live cluster — see lakefs.tf's header comment. No in-code default; proposed value (explicitly flagged unconfirmed) lives only in terraform.tfvars.example."
  type        = string
}
