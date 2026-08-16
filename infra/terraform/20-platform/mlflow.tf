# T008 — MLflow tracking server + model registry.
#
# Chart: community `mlflow` 1.11.4 (app 3.15.1), per D-000/D-05b. Postgres
# backend store on CloudNativePG (T007, infra/terraform/10-storage), S3
# artifact store on SeaweedFS (T007). No CRDs (D-000/D-06's "No CRDs" list
# names MLflow explicitly) — this is the only Terraform file this task
# authors, per D-002/D-01's "one .tf file per Helm release" rule.
#
# Secret NAMES and any value derived from a shared/cross-cutting Terraform
# variable are injected here via `helm_release.mlflow`'s `set` list, never
# typed as a literal into infra/helm-values/mlflow.yaml (D-002/D-07). That
# file stays static and literal for genuinely static chart configuration
# (image pin, resource requests, feature toggles).
#
# NOTE (orchestrator integration, D-002/D-12): this file originally declared
# its own `provider "helm"/"kubernetes"` blocks and `kubeconfig_path` /
# `orbital_drift_namespace` / `cnpg_cluster_name` / `seaweedfs_s3_secret_name`
# variables (self-contained, per its own dispatch's "you may be the
# first-lander" instruction). Once all four T007-T010 dispatches landed,
# three of the four had independently declared the same names — a hard
# `terraform validate` duplicate-declaration error the moment more than one
# coexists in this directory. Consolidated into ./providers.tf; see that
# file's header and docs/decisions/006-t007-t010-integration.md.
#
# CORRECTION (orchestrator integration): this file originally used
# "AWS_ACCESS_KEY_ID"/"AWS_SECRET_ACCESS_KEY" as the key names inside
# var.seaweedfs_s3_secret_name's data, guessed independently since T007 had
# not landed at authoring time. T007's actual 10-storage/seaweedfs.tf creates
# that secret with discrete keys literally named "access_key_id"/
# "secret_access_key" (snake_case, not the AWS-SDK-convention names this file
# guessed) — confirmed directly by both the T007 and T010 dispatches
# independently reading T007's real file. Fixed below to match. Also renamed
# `seaweedfs_s3_endpoint_url` to the shared `var.seaweedfs_s3_endpoint`
# (providers.tf) — T007's lakefs.tf and this file's own original guess for
# the underlying value were byte-identical, so this is a naming
# consolidation, not a value change.

# ---------------------------------------------------------------------------
# Variables — MLflow-specific only (shared/cross-cutting ones live in
# providers.tf as of the integration above). None of the string-valued
# variables below carry an in-code default: D-002's rule ("none of the
# proposed-default values may appear as `default = "..."` inside a tracked
# .tf variable {} block") is extended here, for the same Constitution III
# reason, to the three new variables this task's chart research surfaced
# (docs/decisions/003-t008-mlflow-secret-wiring-findings.md D-003/03).
# Proposed defaults live only in terraform.tfvars.example.
# ---------------------------------------------------------------------------

variable "cnpg_app_database_name" {
  description = "Name of the Postgres database MLflow's tracking store connects to, inside the CloudNativePG cluster named by var.cnpg_cluster_name. The chart's backendStore.postgres.database field is `required` and always a plain (non-secret) value (docs/decisions/003-t008-mlflow-secret-wiring-findings.md D-003/02) — it cannot be sourced from existingDatabaseSecret. Proposed default \"app\" is CloudNativePG's own default database name when Cluster.spec.bootstrap.initdb.database is left unset; T007's actual Cluster CR leaves initdb.database unset (confirmed against 10-storage/cnpg_cluster.tf), so this default is correct as-is, not merely proposed. Lives only in terraform.tfvars.example."
  type        = string
}

variable "mlflow_s3_bucket" {
  description = "S3 bucket name for MLflow's artifact store (artifactRoot.s3.bucket, `# required` in the chart's own values.yaml). A bucket name is explicitly enumerated in Constitution III's no-hardcoded-values list. Not shared with any other dispatch. Proposed default \"orbital-drift-mlflow-artifacts\" lives only in terraform.tfvars.example."
  type        = string
}

# ---------------------------------------------------------------------------
# The release.
# ---------------------------------------------------------------------------

resource "helm_release" "mlflow" {
  name             = "mlflow"
  repository       = "https://community-charts.github.io/helm-charts"
  chart            = "mlflow"
  version          = "1.11.4"
  namespace        = var.orbital_drift_namespace
  create_namespace = true

  # Static, literal, fully-reviewable chart configuration (image pin,
  # resource requests, feature toggles). D-002/D-02/D-07.
  values = [
    file("${path.module}/../../helm-values/mlflow.yaml")
  ]

  # helm provider 3.x: `set` is a list of objects, not repeated blocks
  # (D-003/05). Every value here is either a secret NAME/key-name (never a
  # secret VALUE — those live only inside the referenced K8s Secret objects,
  # Constitution VII) or a plain value derived from a shared/cross-cutting
  # Terraform variable that does not belong hardcoded in the static YAML.
  set = [
    # --- Postgres backend store (D-003/01, D-003/02) -----------------------
    # backendStore.postgres.host/.database are `required`, always-plain chart
    # fields — never secret-sourced, confirmed against the chart's own
    # configmap template, not just its values.yaml comments.
    #
    # "${var.cnpg_cluster_name}-rw" naming: D-003/02 flagged this as needing
    # confirmation once T007's Cluster CR landed, since it depends on the
    # Cluster's real metadata.name equaling var.cnpg_cluster_name exactly.
    # That equality was NOT guaranteed by the chart's own default naming
    # (a peer-reviewer finding on the integrated set — the chart's
    # "cluster.fullname" helper does not return the bare release name unless
    # the release name happens to contain the chart's own name, "cluster",
    # which "orbital-drift-postgres" does not). Fixed at the source:
    # 10-storage/cnpg_cluster.tf now sets `fullnameOverride` explicitly, so
    # the Cluster's metadata.name — and every CNPG-operator-derived object
    # name built from it, including this "-rw" read/write Service — is
    # guaranteed to equal var.cnpg_cluster_name. This reference is correct.
    {
      name  = "backendStore.postgres.host"
      value = "${var.cnpg_cluster_name}-rw.${var.orbital_drift_namespace}.svc.cluster.local"
    },
    {
      name  = "backendStore.postgres.database"
      value = var.cnpg_app_database_name
    },
    # backendStore.existingDatabaseSecret — NOT backendStore.postgres.existingSecret
    # (D-002/D-09's key path was wrong; corrected here, see D-003/01). CNPG's
    # auto-created "<cluster>-app" secret is a basic-auth-shaped Secret with
    # username/password keys (D-002/D-08).
    {
      name  = "backendStore.existingDatabaseSecret.name"
      value = "${var.cnpg_cluster_name}-app"
    },
    {
      name  = "backendStore.existingDatabaseSecret.usernameKey"
      value = "username"
    },
    {
      name  = "backendStore.existingDatabaseSecret.passwordKey"
      value = "password"
    },

    # --- S3 artifact store on SeaweedFS -------------------------------------
    {
      name  = "artifactRoot.s3.bucket"
      value = var.mlflow_s3_bucket
    },
    {
      name  = "artifactRoot.s3.existingSecret.name"
      value = var.seaweedfs_s3_secret_name
    },
    # Key names inside that secret: CORRECTED to match T007's actual
    # 10-storage/seaweedfs.tf output (snake_case access_key_id/
    # secret_access_key), not the AWS-SDK-convention names this file
    # originally guessed independently — see file header.
    {
      name  = "artifactRoot.s3.existingSecret.keyOfAccessKeyId"
      value = "access_key_id"
    },
    {
      name  = "artifactRoot.s3.existingSecret.keyOfSecretAccessKey"
      value = "secret_access_key"
    },
    # SeaweedFS is not AWS; the chart has no first-class non-AWS-endpoint
    # field. extraEnvVars.MLFLOW_S3_ENDPOINT_URL is the chart's own
    # documented mechanism (D-003/03).
    {
      name  = "extraEnvVars.MLFLOW_S3_ENDPOINT_URL"
      value = var.seaweedfs_s3_endpoint
    },
  ]
}
