# infra/terraform/10-storage/seaweedfs.tf
#
# SeaweedFS object store (D-000/D-05, operator decision). No CRDs (D-000/D-06's
# "No CRDs" list), so this lives alongside the CNPG Cluster CR in 10-storage,
# not 00-crds (D-002/D-01).
#
# Chart pin provenance: chart `4.41.0` / appVersion `4.41`. Originally
# docs/decisions/versions.md (artifacthub.io, 2026-08-08). Re-confirmed
# 2026-08-16 directly against
#   https://raw.githubusercontent.com/seaweedfs/seaweedfs/master/k8s/charts/seaweedfs/Chart.yaml
# whose HEAD literally reads `version: "4.41.0"` as of this session — this
# resolves D-002 Follow-up #5's residual-risk flag more strongly than the
# values.yaml-excerpt-only finding D-002/D-10 relayed (master's CURRENT HEAD
# matches the pin, not just an undated excerpt). RESIDUAL, NOT FABRICATED:
# artifacthub.io and seaweedfs.github.io (the chart's own Helm repo index,
# which would let a fully independent digest/tag cross-check happen) were
# BOTH blocked by this session's egress proxy, so this is "master HEAD
# matches the pin today," not "an immutable tagged ref was fetched." Uses the
# OFFICIAL seaweedfs/seaweedfs chart repo, NOT Bitnami (incompatible key
# schema, D-002/D-10).
#
# Secret-shape CORRECTION to D-002/D-07 and D-002/D-10's paraphrase: the
# field is `s3.existingConfigSecret` (top-level `s3:` component), not
# `s3.auth.existingSecret` as D-002/D-10 summarized it — confirmed against
# k8s/charts/seaweedfs/values.yaml and README.md, 2026-08-16. Also, D-002/D-07
# implicitly assumed ONE secret object with ONE shape would satisfy both
# SeaweedFS's own chart AND every downstream `existingSecret` consumer
# (MLflow T008 `artifactRoot.s3.existingSecret.keyOfAccessKeyId`/
# `.keyOfSecretAccessKey`, Airflow T009's S3 remote-logging connection,
# lakeFS's blockstore credentials in this same PR). That assumption does not
# hold: SeaweedFS's own `s3.existingConfigSecret` expects exactly ONE key,
# `seaweedfs_s3_config`, holding an inline JSON blob of named identities —
# there is no top-level `accessKeyId`/`secretAccessKey` in that shape for a
# downstream chart's `existingSecret` + per-field key names to point at.
# RESOLUTION (T007's call): the ONE secret named var.seaweedfs_s3_secret_name
# carries BOTH shapes, built from the SAME two Terraform variables so they
# cannot drift apart — "seaweedfs_s3_config" (JSON, consumed by SeaweedFS's
# own chart below) plus "access_key_id"/"secret_access_key" (plain top-level
# keys, consumed by every existingSecret-style reference downstream,
# including 20-platform/lakefs.tf in this PR). T008 (mlflow.tf) and T009
# (airflow.tf) should point their own existingSecret.name at this same secret
# and use these same two discrete key names, not invent new ones.
#
# INNER JSON SCHEMA — peer-reviewer finding on the integrated T007-T010 set
# (docs/decisions/006-t007-t010-integration.md): the citations above verify
# the OUTER Helm field path (s3.existingConfigSecret) but never verified the
# seaweedfs_s3_config payload's own inner shape (identities[].name,
# .credentials[].{accessKey,secretKey}, .actions) against anything beyond
# D-002/D-10's own relayed, not-independently-verified values.yaml excerpt —
# a real citation gap, closed here. Confirmed via unmediated `curl` (not an
# LLM-summarized fetch) of the chart's OWN README.md, 2026-08-16:
#   https://raw.githubusercontent.com/seaweedfs/seaweedfs/master/k8s/charts/seaweedfs/README.md
# which documents this exact shape as a complete, byte-exact worked example
# (a full example Secret manifest, not just a field-path mention):
#   seaweedfs_s3_config: '{"identities":[{"name":"...","credentials":
#   [{"accessKey":"...","secretKey":"..."}],"actions":["Admin","Read",
#   "Write"]}, ...]}'
# — matching this resource's jsonencode() output field-for-field
# (identities/name/credentials/accessKey/secretKey/actions). Residual risk,
# stated plainly: this is the chart maintainers' own documented contract,
# not SeaweedFS core's S3 IAM-loader source code directly — genuinely
# undecidable with more certainty than that without a live gateway to
# smoke-test against, which is exactly what T012 is for.

variable "seaweedfs_s3_secret_name" {
  description = <<-EOT
    Name of the pre-created Kubernetes Secret holding SeaweedFS's S3 identity
    config, referenced by SeaweedFS's own chart AND by every downstream
    existingSecret consumer (MLflow T008, Airflow T009, lakeFS in this PR).
    D-002/D-07. No default — proposed default
    "orbital-drift-seaweedfs-s3-credentials" lives only in
    terraform.tfvars.example.
  EOT
  type        = string
}

variable "seaweedfs_s3_admin_access_key_id" {
  description = <<-EOT
    Access key ID for the SeaweedFS S3 gateway's admin identity.
    Operator-supplied via TF_VAR_seaweedfs_s3_admin_access_key_id at apply
    time (D-000/D-09) — a secret VALUE, never a tracked default, never in any
    *.tfvars.example.
  EOT
  type        = string
  sensitive   = true
}

variable "seaweedfs_s3_admin_secret_access_key" {
  description = <<-EOT
    Secret access key for the SeaweedFS S3 gateway's admin identity.
    Operator-supplied via TF_VAR_seaweedfs_s3_admin_secret_access_key at apply
    time (D-000/D-09) — a secret VALUE, never a tracked default, never in any
    *.tfvars.example.
  EOT
  type        = string
  sensitive   = true
}

variable "seaweedfs_s3_admin_identity_name" {
  description = <<-EOT
    Name of the admin identity inside the seaweedfs_s3_config JSON blob
    (SeaweedFS's own s3.existingConfigSecret shape — an identity NAME, not a
    credential VALUE, so unlike the two variables above this one is not
    secret and does get a proposed default). Not schema-constrained to any
    particular string by SeaweedFS's chart — a discretionary, project-chosen
    identifier, the same class of value cnpg_cluster_name/lakefs_secret_name/
    mlflow_s3_bucket/argo_workflows_s3_artifact_bucket/airflow_s3_log_bucket
    are already treated as elsewhere in this artifact set (spec-guardian
    finding on the integrated T007-T010 set,
    docs/decisions/006-t007-t010-integration.md). No in-code default —
    proposed default "orbital-drift-admin" lives only in
    terraform.tfvars.example.
  EOT
  type        = string
}

resource "kubernetes_secret_v1" "seaweedfs_s3" {
  metadata {
    name      = var.seaweedfs_s3_secret_name
    namespace = var.orbital_drift_namespace
  }

  type = "Opaque"

  data = {
    # Consumed by SeaweedFS's own chart (s3.existingConfigSecret) below.
    seaweedfs_s3_config = jsonencode({
      identities = [
        {
          name = var.seaweedfs_s3_admin_identity_name
          credentials = [
            {
              accessKey = var.seaweedfs_s3_admin_access_key_id
              secretKey = var.seaweedfs_s3_admin_secret_access_key
            },
          ]
          actions = ["Admin", "Read", "Write"]
        },
      ]
    })

    # Consumed by every downstream existingSecret reference (lakeFS below;
    # MLflow/Airflow in T008/T009) — same underlying credential, discrete
    # keys, built from the same two variables so the two representations
    # cannot drift apart.
    access_key_id     = var.seaweedfs_s3_admin_access_key_id
    secret_access_key = var.seaweedfs_s3_admin_secret_access_key
  }
}

resource "helm_release" "seaweedfs" {
  name             = "seaweedfs"
  repository       = "https://seaweedfs.github.io/seaweedfs/helm"
  chart            = "seaweedfs"
  version          = "4.41.0"
  namespace        = var.orbital_drift_namespace
  create_namespace = true

  values = [
    file("${path.module}/../../helm-values/seaweedfs.yaml"),
  ]

  set = [
    {
      name  = "s3.existingConfigSecret"
      value = kubernetes_secret_v1.seaweedfs_s3.metadata[0].name
    },
  ]

  depends_on = [kubernetes_secret_v1.seaweedfs_s3]
}
