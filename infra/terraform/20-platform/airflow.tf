# T009 — Apache Airflow (official chart, KubernetesExecutor), DAG deployment via git-sync.
#
# Chart: https://airflow.apache.org, chart 1.22.0, app 3.2.2.
# Provenance: docs/decisions/versions.md ("Apache Airflow (official)" row) and
# https://github.com/apache/airflow/releases (verified in this session against
# the chart's own values.yaml at tag helm-chart/1.22.0, see
# docs/decisions/004-t009-airflow-findings.md for the exact fetch commands).
#
# Reads: .specify/memory/constitution.md, docs/decisions/000-phase0-technical-decisions.md
# (D-06 staged apply / Helm-hooks gotcha, D-08 storage / RWX trap, D-09 secrets /
# Fernet-key hazard, D-10 Terraform-on-node-A), docs/decisions/002-infra-layout.md
# (D-01..D-12 — shared T007-T010 conventions), docs/decisions/004-t009-airflow-findings.md
# (findings made while authoring this file that D-000/D-002 do not cover: the
# CNPG app-secret key-shape mismatch, and the same-root-module provider/variable
# duplicate-declaration risk across T007/T008/T009/T010's files in 20-platform/).
#
# NOTE (orchestrator integration, D-002/D-12): this file originally declared
# its own `provider "helm"/"kubernetes"` blocks and `kubeconfig_path` /
# `orbital_drift_namespace` / `cnpg_cluster_name` variables (self-contained,
# per its own dispatch's explicit "re-declare, same pattern T007/T008 use"
# instruction, flagged by this file's own D-003/Finding-2 as a likely
# collision). Consolidated into ./providers.tf once all four T007-T010
# dispatches landed and were reconciled together; see that file's header and
# docs/decisions/006-t007-t010-integration.md.
#
# ALSO CHANGED (orchestrator integration): this file originally sourced
# SeaweedFS S3 credentials for its own log bucket from THREE dedicated,
# operator-supplied variables (airflow_seaweedfs_s3_endpoint,
# _access_key_id, _secret_access_key) — a deliberate, disclosed deviation
# from D-002/D-07's shared-variable table (docs/decisions/004-t009-airflow-findings.md
# Finding 4), justified at authoring time because T007's actual SeaweedFS
# secret shape was unconfirmed and this dispatch could not "jsondecode() an
# unconfirmed nested JSON structure." T007 has since landed with a CONFIRMED
# shape (10-storage/seaweedfs.tf: discrete access_key_id/secret_access_key
# keys, specifically designed for this kind of downstream reference — see
# that file's own header). The three dedicated variables and the operator
# cost Finding 4 flagged ("the operator must enter the same underlying
# SeaweedFS credential material twice") are eliminated: this file now reads
# var.seaweedfs_s3_secret_name (providers.tf, shared) directly via its own
# `data "kubernetes_secret_v1"`, same pattern lakefs.tf and argo_workflows.tf
# already use for the same secret.

# =============================================================================
# AIRFLOW-SPECIFIC SECRET *NAME* VARIABLES — non-secret identifiers naming
# which Kubernetes Secret object holds each credential. Same no-tracked-default
# discipline the dispatch brief asks for ("The Airflow-specific secret name
# variables you invent ... should follow the same no-tracked-default pattern
# for consistency"). Proposed defaults live only in terraform.tfvars.example.
# =============================================================================

variable "airflow_fernet_key_secret_name" {
  description = "Name of the kubernetes_secret_v1 this file creates holding the 'fernet-key' key, wired to the chart's fernetKeySecretName (D-000/D-09 Fernet-key hazard: the chart auto-generates/regenerates one if unset, silently invalidating every stored connection — must be pre-created)."
  type        = string
}

variable "airflow_webserver_secret_name" {
  description = "Name of the kubernetes_secret_v1 this file creates holding both 'webserver-secret-key' (2.x-compat, deprecated) and 'api-secret-key' (3.x) keys, wired to webserverSecretKeySecretName and apiSecretKeySecretName respectively — same secret object, two keys, since both point at the same underlying Flask session/API signing material for this single-webserver-mode deployment."
  type        = string
}

variable "airflow_metadata_connection_secret_name" {
  description = "Name of the kubernetes_secret_v1 this file creates holding a 'connection' key (the exact key name the chart's data.metadataSecretName expects), wired from CNPG's '<cnpg_cluster_name>-app' secret via a data source + local transform. See docs/decisions/004-t009-airflow-findings.md Finding 1 for why this indirection is required instead of pointing data.metadataSecretName at CNPG's secret directly."
  type        = string
}

variable "airflow_seaweedfs_s3_connection_secret_name" {
  description = "Name of the kubernetes_secret_v1 this file creates holding a 'connection-uri' key (an Airflow 'aws' connection URI), consumed via extraEnv as AIRFLOW_CONN_SEAWEEDFS_S3 for S3 remote logging to SeaweedFS (D-000/D-08 RWX-trap resolution). The raw access key / secret key it is built from now come from var.seaweedfs_s3_secret_name (providers.tf, T007-owned) rather than dedicated Airflow-only credential variables — see file header."
  type        = string
}

variable "airflow_s3_log_bucket" {
  description = "SeaweedFS bucket for Airflow's remote logs (config.logging.remote_base_log_folder, an 's3://<bucket>/airflow-logs' URI). A bucket name is explicitly enumerated in Constitution III's no-hardcoded-values list — it was originally a literal in infra/helm-values/airflow.yaml, moved here as a spec-guardian finding on the integrated artifact set (docs/decisions/006-t007-t010-integration.md), matching the treatment T008's mlflow_s3_bucket and T010's argo_workflows_s3_artifact_bucket already get for the identical value shape. Distinct from both — Airflow's own logs bucket, not shared. No in-code default; proposed default \"orbital-drift-airflow-logs\" lives only in terraform.tfvars.example. Whether SeaweedFS auto-creates the bucket on first write or needs explicit pre-creation is on-cluster-only to verify, same open question T008/T010 already raised for their own buckets."
  type        = string
}

# =============================================================================
# AIRFLOW-SPECIFIC SECRET *VALUE* VARIABLES — real credential material.
# NO DEFAULT ANYWHERE, INCLUDING terraform.tfvars.example (dispatch brief,
# echoing D-000/D-09's Fernet-key instruction and Constitution VII). Every one
# is `sensitive = true` so Terraform redacts it from plan/apply CLI output;
# state-file exposure is the accepted, already-documented residual risk
# (.gitignore's own header: "Ignoring the [state] file is the real control").
# Supplied only via operator-exported TF_VAR_* environment variables at
# `terraform apply` time. Exact generation commands are in this file's
# deliverable notes / the future T011 runbook.
# =============================================================================

variable "airflow_fernet_key" {
  description = "Real Fernet key value. Generate with: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\". No default anywhere (D-000/D-09)."
  type        = string
  sensitive   = true
}

variable "airflow_webserver_secret_key" {
  description = "Flask/Airflow webserver session-signing secret (2.x-compat key 'webserver-secret-key'). Generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\". No default anywhere."
  type        = string
  sensitive   = true
}

variable "airflow_api_secret_key" {
  description = "Airflow 3.x API server signing secret (key 'api-secret-key'). Generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\". No default anywhere. May reuse the same generation command as airflow_webserver_secret_key but MUST be an independently generated value, not the same literal, per standard key-separation practice."
  type        = string
  sensitive   = true
}

variable "airflow_admin_password" {
  description = "Password for the chart's createUserJob.defaultUser (username 'admin'). The chart's own values.yaml default for this field is the plaintext literal 'admin' — left unset, Airflow would deploy with a well-known trivial credential (see docs/decisions/004-t009-airflow-findings.md Finding 3). Generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(24))\" (URL-safe alphabet — safe for Helm's --set/strvals parsing, which is sensitive to commas/braces/backslashes). No default anywhere."
  type        = string
  sensitive   = true
}

# =============================================================================
# CNPG METADATA-DB SECRET TRANSFORM (docs/decisions/004-t009-airflow-findings.md
# Finding 1). CNPG's auto-created '<cnpg_cluster_name>-app' Secret exposes
# discrete keys (user, password, host, port, dbname, uri, jdbc-uri, pgpass —
# confirmed against cloudnative-pg/cloudnative-pg's pkg/specs/secrets.go
# CreateSecret(), main branch, fetched 2026-08-16) but NO key literally named
# 'connection'. The Airflow chart's data.metadataSecretName requires the named
# secret to contain exactly a 'connection' key holding a base64-encoded
# SQLAlchemy URI (chart values.yaml comment, same fetch). Pointing
# data.metadataSecretName straight at CNPG's secret, as a literal reading of
# this dispatch's brief might suggest, would deploy an Airflow whose scheduler
# / api-server / dag-processor pods crash-loop on a missing secret key. This
# data source + local + kubernetes_secret_v1 triad closes that gap.
# =============================================================================

data "kubernetes_secret_v1" "cnpg_app_for_airflow" {
  metadata {
    name      = "${var.cnpg_cluster_name}-app"
    namespace = var.orbital_drift_namespace
  }
}

# SeaweedFS S3 credentials for Airflow's own log bucket — reads T007's actual
# secret (var.seaweedfs_s3_secret_name, providers.tf) rather than a dedicated
# Airflow-only credential pair (see file header for why this changed from the
# original dispatch). A separate `data` block from lakefs.tf's/
# argo_workflows.tf's own reads of the same underlying secret — Terraform
# data sources are side-effect-free reads, so three independent reads of the
# same secret cost nothing beyond one extra API call each and avoid coupling
# this file to another file's resource address.
data "kubernetes_secret_v1" "seaweedfs_s3_for_airflow" {
  metadata {
    name      = var.seaweedfs_s3_secret_name
    namespace = var.orbital_drift_namespace
  }
}

locals {
  # postgresql+psycopg2:// (not CNPG's own bare postgresql:// 'uri' key) to
  # match the SQLAlchemy driver Airflow's chart values.yaml comment shows in
  # its own worked example for this exact field.
  airflow_metadata_connection_uri = "postgresql+psycopg2://${urlencode(data.kubernetes_secret_v1.cnpg_app_for_airflow.data["user"])}:${urlencode(data.kubernetes_secret_v1.cnpg_app_for_airflow.data["password"])}@${data.kubernetes_secret_v1.cnpg_app_for_airflow.data["host"]}:${data.kubernetes_secret_v1.cnpg_app_for_airflow.data["port"]}/${data.kubernetes_secret_v1.cnpg_app_for_airflow.data["dbname"]}"

  # Airflow 'aws' connection URI for S3-compatible remote logging against
  # SeaweedFS (D-000/D-08 RWX-trap resolution). Airflow's amazon provider reads
  # an 'endpoint_url' extra for non-AWS S3-compatible endpoints. Credentials
  # and endpoint now come from T007's actual secret/shared variable, not
  # dedicated Airflow-only ones (see file header).
  airflow_seaweedfs_s3_connection_uri = "aws://${urlencode(data.kubernetes_secret_v1.seaweedfs_s3_for_airflow.data["access_key_id"])}:${urlencode(data.kubernetes_secret_v1.seaweedfs_s3_for_airflow.data["secret_access_key"])}@/?endpoint_url=${urlencode(var.seaweedfs_s3_endpoint)}&region_name=us-east-1"
}

resource "kubernetes_secret_v1" "airflow_fernet_key" {
  metadata {
    name      = var.airflow_fernet_key_secret_name
    namespace = var.orbital_drift_namespace
  }

  # kubernetes_secret_v1's `data` attribute takes plain (not pre-base64'd)
  # strings and the provider base64-encodes on the wire — confirmed against
  # the resource's registry docs. var.airflow_fernet_key is itself already a
  # url-safe-base64 Fernet key string; that value is stored as-is under this
  # key, matching the chart's own "must contain a 'fernet-key' key with a
  # base64-encoded key value" expectation (every k8s Secret .data value is
  # base64 at the API layer regardless of source).
  data = {
    "fernet-key" = var.airflow_fernet_key
  }

  type = "Opaque"
}

resource "kubernetes_secret_v1" "airflow_webserver_secret" {
  metadata {
    name      = var.airflow_webserver_secret_name
    namespace = var.orbital_drift_namespace
  }

  data = {
    "webserver-secret-key" = var.airflow_webserver_secret_key
    "api-secret-key"       = var.airflow_api_secret_key
  }

  type = "Opaque"
}

resource "kubernetes_secret_v1" "airflow_metadata_connection" {
  metadata {
    name      = var.airflow_metadata_connection_secret_name
    namespace = var.orbital_drift_namespace
  }

  data = {
    connection = local.airflow_metadata_connection_uri
  }

  type = "Opaque"

  # Explicit even though the local already references the data source (and
  # Terraform would infer this edge anyway) — states the real-world ordering
  # requirement out loud: CNPG's Cluster (10-storage, a DIFFERENT Terraform
  # root/state per D-01) must already exist and have reconciled its app
  # secret before this resource's data source read can succeed. That is the
  # staged-apply order D-000/D-06 already mandates (10-storage before
  # 20-platform); this resource has no way to enforce it itself.
  depends_on = [data.kubernetes_secret_v1.cnpg_app_for_airflow]
}

resource "kubernetes_secret_v1" "airflow_seaweedfs_s3_connection" {
  metadata {
    name      = var.airflow_seaweedfs_s3_connection_secret_name
    namespace = var.orbital_drift_namespace
  }

  data = {
    "connection-uri" = local.airflow_seaweedfs_s3_connection_uri
  }

  type = "Opaque"

  # Same staged-apply ordering note as airflow_metadata_connection above:
  # 10-storage's seaweedfs.tf must have applied and created this secret
  # before the data source read can succeed.
  depends_on = [data.kubernetes_secret_v1.seaweedfs_s3_for_airflow]
}

# =============================================================================
# THE RELEASE ITSELF
# =============================================================================

resource "helm_release" "airflow" {
  name             = "airflow"
  namespace        = var.orbital_drift_namespace
  create_namespace = true # idempotent at the Helm level; safe even if
  # 10-storage's releases already created this namespace (D-002/D-06 — every
  # platform release shares one namespace).

  repository = "https://airflow.apache.org"
  chart      = "airflow"
  version    = "1.22.0" # app 3.2.2 — docs/decisions/versions.md, provenance
  # https://github.com/apache/airflow/releases; chart's own values.yaml
  # defaultAirflowTag/airflowVersion already default to "3.2.2", pinned
  # explicitly below anyway (Constitution IV: explicit over implicit).

  values = [
    file("${path.module}/../../helm-values/airflow.yaml"),
    # extraEnv is injected here rather than via a `set` entry: the chart's own
    # templates/_helpers.yaml ("custom_airflow_environment") does
    # `{{- with .Values.extraEnv }}{{- tpl . $Global | nindent 2 }}{{- end }}`
    # — extraEnv must be a STRING (rendered by `tpl`, then nindent'd), not a
    # native YAML list, so it cannot be supplied as an ordinary HCL list value.
    # Building it via yamlencode() (inner call produces that string; outer
    # call safely YAML-quotes it as the value of the extraEnv key) avoids
    # hand-indented, whitespace-sensitive YAML-inside-a-heredoc: an earlier
    # draft of this file used a `<<-EOT ... EOT` heredoc here and local
    # parsing with python-hcl2 (see this dispatch's deliverable notes) showed
    # heredocs nested inside a list literal are exactly the kind of construct
    # third-party HCL2 parsers mishandle — switched to yamlencode(), which
    # Terraform itself guarantees produces valid YAML, to remove that risk
    # entirely rather than merely hope a hand-written heredoc happens to work.
    yamlencode({
      extraEnv = yamlencode([
        {
          name = "AIRFLOW_CONN_SEAWEEDFS_S3"
          valueFrom = {
            secretKeyRef = {
              name = kubernetes_secret_v1.airflow_seaweedfs_s3_connection.metadata[0].name
              key  = "connection-uri"
            }
          }
        }
      ])
    }),
  ]

  # Secret NAMES only — never values — via `set`, per D-002/D-07. Simple
  # scalar dotted-path overrides; safe for Helm's --set/strvals parsing.
  set = [
    {
      name  = "fernetKeySecretName"
      value = kubernetes_secret_v1.airflow_fernet_key.metadata[0].name
    },
    {
      name  = "webserverSecretKeySecretName"
      value = kubernetes_secret_v1.airflow_webserver_secret.metadata[0].name
    },
    {
      name  = "apiSecretKeySecretName"
      value = kubernetes_secret_v1.airflow_webserver_secret.metadata[0].name
    },
    {
      name  = "data.metadataSecretName"
      value = kubernetes_secret_v1.airflow_metadata_connection.metadata[0].name
    },
    # config.logging.remote_base_log_folder — a bucket name is Constitution
    # III-relevant (D-002/D-07), so it is injected from var.airflow_s3_log_bucket
    # here rather than written as a literal in infra/helm-values/airflow.yaml
    # (spec-guardian finding, docs/decisions/006-t007-t010-integration.md).
    {
      name  = "config.logging.remote_base_log_folder"
      value = "s3://${var.airflow_s3_log_bucket}/airflow-logs"
    },
  ]

  # Real value (not a name) — set_sensitive so it is redacted from Terraform
  # CLI plan/apply output (D-003 Finding 3: the chart's own default for this
  # field is the plaintext literal "admin").
  set_sensitive = [
    {
      name  = "createUserJob.defaultUser.password"
      value = var.airflow_admin_password
    },
  ]

  depends_on = [
    kubernetes_secret_v1.airflow_fernet_key,
    kubernetes_secret_v1.airflow_webserver_secret,
    kubernetes_secret_v1.airflow_metadata_connection,
    kubernetes_secret_v1.airflow_seaweedfs_s3_connection,
  ]
}
