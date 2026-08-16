# infra/terraform/20-platform/lakefs.tf
#
# lakeFS (D-000/D-04's D-08 non-issue note: "lakeFS depends on S3
# (blockstore) + Postgres (KV)"; D-002/D-01, D-002/D-11). No CRDs (D-000/D-06's
# "No CRDs" list), so 20-platform, not 00-crds.
#
# Chart pin provenance, CORRECTED from docs/decisions/versions.md's stale
# `1.12.22` (fixed in this PR — see that file's diff): chart `1.12.24` /
# appVersion `1.103.0`. Confirmed 2026-08-16, three independent fetches, all
# agreeing, directly against the TAG-PINNED source (not `master`, closing
# D-002/D-11's residual risk on the pin itself):
#   https://raw.githubusercontent.com/treeverse/charts/lakefs-1.12.24/charts/lakefs/Chart.yaml
#
# NOTE (orchestrator integration, D-002/D-12): `provider`/`kubeconfig_path`
# and the `orbital_drift_namespace`/`cnpg_cluster_name`/`seaweedfs_s3_secret_name`/
# `seaweedfs_s3_endpoint` variables this file originally declared here (per
# a "first-merged-wins" convention its own authoring session established)
# were consolidated into ./providers.tf once all four T007-T010 dispatches
# landed and were reconciled together — three of the four independently
# declared the same names, which is a hard `terraform validate` duplicate-
# declaration error the moment more than one coexists in this directory. See
# providers.tf's header comment and docs/decisions/006-t007-t010-integration.md.
#
# =============================================================================
# D-002/D-11's two open questions — both resolved here
# =============================================================================
#
# (1) Does CNPG's auto-created "<cluster-name>-app" secret expose a
#     single-field connection URI lakeFS's secretKeys.databaseConnectionString
#     can use DIRECTLY (existingSecret pointed straight at CNPG's secret)?
#
#     Answer: YES, confirmed twice independently. This file's own research
#     (a relayed web search citing CNPG's own applications documentation,
#     2026-08-16 — cloudnative-pg.io itself returned EGRESS_BLOCKED from this
#     session's proxy) found the "uri" field exists. The T009 (Airflow)
#     dispatch independently confirmed the SAME field set by reading CNPG's
#     actual source (`pkg/specs/secrets.go`, `CreateSecret()`,
#     cloudnative-pg/cloudnative-pg main branch) directly — a materially
#     stronger source than a relayed search — and found exactly the same
#     "uri" key among CNPG's auto-created secret's discrete fields (see
#     docs/decisions/004-t009-airflow-findings.md Finding 1). The residual
#     risk this file originally carried ("relayed, not a direct fetch") is
#     now substantially reduced by that independent, source-level
#     corroboration, though still not byte-pinned to the exact `0.29.0`
#     release tag (D-08's own standing caveat).
#
#     But lakeFS's chart uses exactly ONE `existingSecret` name for EVERY
#     `secretKeys.*` entry (confirmed directly against
#     .../lakefs-1.12.24/charts/lakefs/values.yaml and
#     templates/_shared_env.tpl, 2026-08-16): `secretKeys.authEncryptSecretKey`
#     (lakeFS-internal, no CNPG equivalent) and `secretKeys.
#     databaseConnectionString` MUST live in the SAME secret object. CNPG's
#     "-app" secret cannot be pointed at directly, because it has no
#     `auth_encrypt_secret_key`-shaped field and this file has no way to add
#     one to an operator-owned, auto-created secret without fighting CNPG's
#     own reconciliation.
#
#     CHOSEN (the "transformation step" D-11 flagged as one of two options,
#     over a hand-typed TF_VAR_lakefs_db_connection_string): a
#     `data "kubernetes_secret_v1"` lookup of CNPG's live "-app" secret,
#     copying its "uri" field into a NEW, lakeFS-owned secret alongside the
#     operator-supplied auth-encryption key. This ties lakeFS's DB credential
#     to CNPG's actual value automatically (no operator hand-copy step, no
#     second place a rotated CNPG password could go stale) at the cost of one
#     more resource in this state. Confirmed via the exact env var lakeFS's
#     chart populates from these keys:
#     `LAKEFS_DATABASE_POSTGRES_CONNECTION_STRING` (NOT
#     `LAKEFS_DATABASE_CONNECTION_STRING` — templates/_shared_env.tpl,
#     2026-08-16) and `LAKEFS_AUTH_ENCRYPT_SECRET_KEY`.
#
#     RESIDUAL RISK, stated plainly: this file's `data.kubernetes_secret_v1.
#     cnpg_app.data["uri"]` reference will only resolve once 10-storage has
#     actually been applied and CNPG has reconciled the Cluster into a
#     healthy state (staged-apply is a hard prerequisite here, not just a
#     nicety) — and the "uri" key's existence needs re-confirming against the
#     REAL rendered secret at T012, before T011's runbook treats this as
#     load-bearing (same treatment D-08 already requires for the underlying
#     auto-create mechanism).
#
# (2) How are lakeFS's SeaweedFS (S3) blockstore credentials wired?
#
#     Answer: the chart has NO existingSecret-shaped mechanism for the
#     blockstore at all (confirmed against values.yaml and
#     templates/secret.yaml, 2026-08-16 — neither mentions S3/AWS
#     credentials). The documented mechanism is the generic `extraEnvVars`
#     passthrough (raw env var definitions; values.yaml's own comment shows a
#     `valueFrom.secretKeyRef` example). lakeFS's S3 blockstore driver reads
#     the standard AWS SDK credential env vars, so this file injects
#     AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY via extraEnvVars,
#     secretKeyRef'd against 10-storage/seaweedfs.tf's secret
#     (var.seaweedfs_s3_secret_name, discrete access_key_id/secret_access_key
#     keys — see that file's header for why the secret carries both a JSON
#     blob and discrete keys). The blockstore ENDPOINT is injected the same
#     way, via lakeFS's own documented config-override env var
#     (`LAKEFS_BLOCKSTORE_S3_ENDPOINT`, not an AWS_* var — more directly tied
#     to what lakeFS itself defines than assuming AWS SDK endpoint-override
#     env var support). extraEnvVars is injected via set (not the static
#     helm-values file) because every entry here carries either a secret NAME
#     or a cluster-topology-dependent endpoint — both fail D-002/D-07's
#     "static, literal, fully reviewable" bar for the values file.
#
# =============================================================================
# SeaweedFS S3 endpoint: UNCONFIRMED, flagged explicitly
# =============================================================================
# var.seaweedfs_s3_endpoint's proposed value in terraform.tfvars.example is a
# best-effort reconstruction of the chart's standard Helm fullname-template
# Service naming (helm_release name "seaweedfs" already contains the chart
# name "seaweedfs", so the standard `{{ include "chart.fullname" . }}`
# collapses to the release name; S3 Service = "<fullname>-s3" going by the
# common SeaweedFS chart naming convention). NOT rendered or cluster-confirmed
# — this environment has no helm binary (see deliverable notes). Operator MUST
# confirm with `kubectl get svc -n orbital-drift` after 10-storage applies,
# before applying 20-platform (flagged as a T011 runbook item). mlflow.tf and
# airflow.tf reuse this exact same guess via the shared var.seaweedfs_s3_endpoint
# (providers.tf) rather than each re-guessing independently.

variable "lakefs_secret_name" {
  description = <<-EOT
    Name of the lakeFS-owned Secret this file creates, holding
    auth_encrypt_secret_key and database_connection_string (D-002/D-11). No
    default — proposed default "orbital-drift-lakefs-secrets" lives only in
    terraform.tfvars.example.
  EOT
  type        = string
}

variable "lakefs_auth_encrypt_secret_key" {
  description = <<-EOT
    lakeFS's own auth encryption key (lakeFS-internal — not a connection
    credential, not shared with CNPG or SeaweedFS). Operator-supplied via
    TF_VAR_lakefs_auth_encrypt_secret_key at apply time (D-000/D-09) — a
    secret VALUE, never a tracked default, never in any *.tfvars.example. The
    exact generation command (e.g. `openssl rand -base64 32`) belongs in the
    T011 bring-up runbook, not this file.
  EOT
  type        = string
  sensitive   = true
}

# Reads CNPG's live, auto-created app secret — see resolution (1) above. Only
# resolves once 10-storage has been applied (staged apply, D-000/D-06); a
# `terraform plan` in this directory run before that will fail with a clear
# "secret not found" error, which is expected and correct, not a bug in this
# configuration.
data "kubernetes_secret_v1" "cnpg_app" {
  metadata {
    name      = "${var.cnpg_cluster_name}-app"
    namespace = var.orbital_drift_namespace
  }
}

resource "kubernetes_secret_v1" "lakefs" {
  metadata {
    name      = var.lakefs_secret_name
    namespace = var.orbital_drift_namespace
  }

  type = "Opaque"

  data = {
    auth_encrypt_secret_key    = var.lakefs_auth_encrypt_secret_key
    database_connection_string = data.kubernetes_secret_v1.cnpg_app.data["uri"]
  }
}

resource "helm_release" "lakefs" {
  name             = "lakefs"
  repository       = "https://charts.lakefs.io"
  chart            = "lakefs"
  version          = "1.12.24"
  namespace        = var.orbital_drift_namespace
  create_namespace = true

  values = [
    file("${path.module}/../../helm-values/lakefs.yaml"),
  ]

  set = [
    {
      name  = "existingSecret"
      value = kubernetes_secret_v1.lakefs.metadata[0].name
    },
    {
      name  = "secretKeys.authEncryptSecretKey"
      value = "auth_encrypt_secret_key"
    },
    {
      name  = "secretKeys.databaseConnectionString"
      value = "database_connection_string"
    },
    {
      name  = "extraEnvVars[0].name"
      value = "AWS_ACCESS_KEY_ID"
    },
    {
      name  = "extraEnvVars[0].valueFrom.secretKeyRef.name"
      value = var.seaweedfs_s3_secret_name
    },
    {
      name  = "extraEnvVars[0].valueFrom.secretKeyRef.key"
      value = "access_key_id"
    },
    {
      name  = "extraEnvVars[1].name"
      value = "AWS_SECRET_ACCESS_KEY"
    },
    {
      name  = "extraEnvVars[1].valueFrom.secretKeyRef.name"
      value = var.seaweedfs_s3_secret_name
    },
    {
      name  = "extraEnvVars[1].valueFrom.secretKeyRef.key"
      value = "secret_access_key"
    },
    {
      name  = "extraEnvVars[2].name"
      value = "LAKEFS_BLOCKSTORE_S3_ENDPOINT"
    },
    {
      name  = "extraEnvVars[2].value"
      value = var.seaweedfs_s3_endpoint
    },
  ]

  depends_on = [kubernetes_secret_v1.lakefs]
}
