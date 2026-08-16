# infra/terraform/20-platform/argo_workflows.tf
#
# T010 — Argo Workflows controller/server (single Helm release, CRDs
# included) + GPU-training namespace scaffolding.
#
# Scope: specs/001-orbital-drift-ct/tasks.md T010; conventions from
# docs/decisions/002-infra-layout.md (D-002). Chart `argo-workflows` pinned
# `1.0.23` / app `v4.0.8` per docs/decisions/versions.md (Constitution IV) —
# see docs/decisions/005-t010-argo-workflows.md for the major-version-drift
# follow-up (chart has since moved to 2.0.0 / app v4.1.0 upstream; NOT
# adopted here, by explicit instruction).
#
# ---------------------------------------------------------------------------
# CRD-split decision (D-002/D-01 Follow-up #8) — DIVERGES from D-002's
# stated default. No infra/terraform/00-crds/argo_workflows_crds.tf exists.
# ---------------------------------------------------------------------------
# This file installs Argo's CRDs and its controller/server in ONE
# helm_release, not two. infra/helm-values/argo-workflows.yaml sets
# `crds.install: true` / `crds.full: false`, which — confirmed directly
# against the pinned chart's own templates/crds.yaml at the
# argo-workflows-1.0.23 tag — renders Argo's CRDs as ORDINARY chart
# templates (gated by `if and .Values.crds.install (not .Values.crds.full)`,
# globbing files/crds/minimal/*.yaml, no helm.sh/hook annotation) rather
# than the crds.full: true default's pre-install/pre-upgrade hook Job that
# downloads ~11MB of full-schema CRDs from GitHub at apply time
# (D-000/D-06's documented egress/size risk; argoproj.github.io is also
# blocked by this environment's own egress proxy).
#
# D-000/D-06's "CRD stage must be a separate apply" rule exists because
# `kubernetes_manifest` validates a CR against the live API server's OpenAPI
# schema AT PLAN TIME, so a same-run CR referencing a not-yet-applied CRD
# fails even with `depends_on` correctly set. This file declares NO
# `kubernetes_manifest` resource for any Workflow/WorkflowTemplate/
# CronWorkflow CR — those are submitted at runtime (`argo submit`, T027's
# workflow YAML) or via git-sync, never provisioned by Terraform — so the
# specific hazard D-000/D-06 warns about does not apply here. Helm's own
# resource-kind sort order (CustomResourceDefinition sorts ahead of
# Deployment/ServiceAccount/etc.) applies CRDs before other kinds WITHIN one
# `helm upgrade --install`, and terraform-provider-helm's `helm_release`
# resource does not perform kubernetes_manifest's per-object, plan-time
# OpenAPI validation of a chart's individual rendered manifests — it shells
# out to the Helm SDK and lets Helm itself sequence the apply. A single
# release is therefore safe for this specific case.
#
# Flagged per D-002 Follow-up #8 for spec-guardian review since it diverges
# from D-002/D-01's stated default (00-crds/argo_workflows_crds.tf +
# 20-platform/argo_workflows.tf); full reasoning and the residual RBAC gap
# this design does NOT close are in docs/decisions/005-t010-argo-workflows.md.
#
# ---------------------------------------------------------------------------
# Cross-namespace secret decision (D-002/D-06 Follow-up #7)
# ---------------------------------------------------------------------------
# Argo's artifact-repository secretKeySelector fields are resolved by the
# workflow pod's own injected wait/init container at POD ADMISSION time —
# Kubernetes has no cross-namespace secretKeyRef, so the referenced Secret
# must exist IN THE WORKFLOW POD'S OWN NAMESPACE (orbital-drift-training),
# not in the controller's namespace (orbital-drift) where T007's
# seaweedfs.tf creates the original. Chosen: copy the SeaweedFS S3
# credential Secret into orbital-drift-training via a Terraform `data` read
# + a new `kubernetes_secret_v1`, rather than (a) omitting credentials
# entirely and relying on cluster DNS alone — SeaweedFS's S3 gateway is not
# anonymous-write by default, so the artifact repository needs a real
# access/secret key regardless of network reachability — or (b) folding
# training pods back into orbital-drift, which would erase the
# namespace-level GPU-RBAC trust boundary this task exists to create
# (D-000/D-03). The copy is explicit and auditable in `terraform plan`
# output, and needs no broadening of the controller's own RBAC
# (controller.rbac.accessAllSecrets stays false, secretWhitelist stays
# empty — infra/helm-values/argo-workflows.yaml).
#
# ---------------------------------------------------------------------------
# Shared-root-module variable/provider ownership (D-002/D-12)
# ---------------------------------------------------------------------------
# 20-platform is one shared Terraform root module across four dispatches
# (T007's lakefs.tf, T008's mlflow.tf, T009's airflow.tf, this file).
# Terraform allows each `variable` and each `provider` configuration exactly
# ONCE per root module, not once per file. This file always declared NO
# `provider "kubernetes"` / `provider "helm"` block and NO
# `variable "kubeconfig_path"` — a sibling T007 dispatch had already created
# infra/terraform/20-platform/providers.tf owning those, confirmed by this
# file's own authoring session directly reading that file. It originally
# ALSO declared `orbital_drift_namespace` and `seaweedfs_s3_secret_name`
# itself, per this task's own dispatch instruction to "re-declare, same
# pattern T008/T009 use" — a real, confirmed collision with T007's/T008's
# own files, exactly as this file's own decision doc predicted
# (docs/decisions/005-t010-argo-workflows.md). Both were consolidated into
# ./providers.tf at orchestrator integration time, alongside cnpg_cluster_name
# and seaweedfs_s3_endpoint; see providers.tf's header and
# docs/decisions/006-t007-t010-integration.md.

# ---------------------------------------------------------------------------
# Variables — Argo-specific. None carry an in-code default (D-002's
# no-tracked-default rule, extended to every variable this task introduces
# for the same Constitution III reason T008's own findings already
# established for its three new variables). Proposed defaults live only in
# terraform.tfvars.example / common.tfvars.example.
# ---------------------------------------------------------------------------

variable "orbital_drift_training_namespace" {
  description = <<-EOT
    Namespace for Argo Workflow pods that reference the nvidia RuntimeClass
    for GPU training (D-002/D-06) — distinct from orbital_drift_namespace so
    GPU access is a namespace-level trust boundary rather than requiring
    per-pod review inside a namespace shared with the Airflow webserver,
    MLflow UI, etc. T010-only per D-002's shared-variable table; no known
    collision risk. No in-code default; proposed default
    "orbital-drift-training" lives only in terraform.tfvars.example.
  EOT
  type        = string
}

variable "orbital_drift_train_gpu_uuid" {
  description = <<-EOT
    UUID (GPU-<uuid> form) of the RTX 5060 Ti 16GB training card, from
    `nvidia-smi -L` on node A (D-000/D-03). T010-only per D-002/D-04.
    Sourced from infra/terraform/common.tfvars, never a literal here or in
    any tracked file (D-000/D-10 — GPU UUIDs must not be committed).
  EOT
  type        = string
}

variable "orbital_drift_serve_gpu_uuid" {
  description = <<-EOT
    UUID (GPU-<uuid> form) of the RTX 5060 8GB serving card, from
    `nvidia-smi -L` on node A (D-000/D-03). Not used to schedule anything in
    THIS file (only the training card is; T043's serving deployment is the
    real consumer) — recorded here so the GPU-UUID ConfigMap below carries
    both cards symmetrically for any future workflow-side consumer. Sourced
    from infra/terraform/common.tfvars, never a literal (D-000/D-10).
  EOT
  type        = string
}

variable "gpu_uuids_configmap_name" {
  description = <<-EOT
    Name of the ConfigMap this file creates holding both GPU UUIDs
    (train-gpu-uuid/serve-gpu-uuid keys) for future workflow-side consumers.
    Not constrained by any chart schema — a discretionary, project-chosen
    identifier, same class of value as orbital_drift_training_namespace and
    every other name variable in this file (spec-guardian finding on the
    integrated T007-T010 set, same treatment
    seaweedfs_s3_admin_identity_name just got in 10-storage/seaweedfs.tf —
    docs/decisions/006-t007-t010-integration.md). No in-code default;
    proposed default "orbital-drift-gpu-uuids" lives only in
    terraform.tfvars.example.
  EOT
  type        = string
}

variable "seaweedfs_s3_access_key_id_key" {
  description = <<-EOT
    Key, within seaweedfs_s3_secret_name's data, holding the S3 access key
    ID. Confirmed against T007's actual 10-storage/seaweedfs.tf: the key is
    literally named "access_key_id". No in-code default; proposed default
    "access_key_id" lives only in terraform.tfvars.example.
  EOT
  type        = string
}

variable "seaweedfs_s3_secret_access_key_key" {
  description = <<-EOT
    Key, within seaweedfs_s3_secret_name's data, holding the S3 secret
    access key. Confirmed against T007's actual seaweedfs.tf: literally
    named "secret_access_key". No in-code default; proposed default
    "secret_access_key" lives only in terraform.tfvars.example.
  EOT
  type        = string
}

variable "argo_workflows_s3_endpoint" {
  description = <<-EOT
    SeaweedFS S3 gateway endpoint as a bare `host:port` (no URL scheme —
    Argo's artifactRepository.s3 schema takes `endpoint` plus a separate
    `insecure` boolean, unlike MLflow's/Airflow's full-URL forms, which use
    the shared var.seaweedfs_s3_endpoint from providers.tf instead). Same
    underlying service, stripped of the `http://` prefix neither of those
    two need to strip. UNCONFIRMED against T007's actual rendered Service
    name, same residual risk the shared variable already carries — not
    independently resolved here. No in-code default; proposed default lives
    only in terraform.tfvars.example.
  EOT
  type        = string
}

variable "argo_workflows_s3_artifact_bucket" {
  description = <<-EOT
    SeaweedFS bucket used for Argo workflow artifacts. Distinct from
    MLflow's mlflow_s3_bucket (T008) — Argo's own bucket, not shared.
    Whether SeaweedFS auto-creates the bucket on first write or needs
    explicit pre-creation is on-cluster-only to verify (the same open
    question T008 already raised for its own bucket). No in-code default;
    proposed default "orbital-drift-argo-artifacts" lives only in
    terraform.tfvars.example.
  EOT
  type        = string
}

variable "argo_workflows_s3_insecure" {
  description = <<-EOT
    Whether the artifact-repository S3 client skips TLS verification
    against the in-cluster SeaweedFS endpoint (no certificate is issued for
    the internal Service DNS name in this design). No in-code default;
    proposed default `true` lives only in terraform.tfvars.example.
  EOT
  type        = bool
}

# ---------------------------------------------------------------------------
# Training namespace + GPU-UUID scaffolding
# ---------------------------------------------------------------------------
#
# RESIDUAL RBAC GAP, stated plainly rather than implied: native Kubernetes
# RBAC has no verb that gates "which namespace may set
# runtimeClassName: nvidia" — RuntimeClass is cluster-scoped and, absent a
# ValidatingAdmissionPolicy or OPA-style policy engine (out of scope here;
# introducing one is new scope needing operator sign-off, the same reasoning
# D-002/D-06 already applies to not introducing new cross-namespace
# machinery lightly), any pod anywhere in the cluster could still set
# runtimeClassName: nvidia and an NVIDIA_VISIBLE_DEVICES env var
# (D-000/D-03's own security note). What this file actually provides is the
# WEAKER, but real, mitigation D-000/D-03 names as acceptable:
# least-privilege RBAC scoping WHO can create Pods/Workflows in
# orbital-drift-training at all (via the chart's own workflow.rbac.create +
# controller.workflowNamespaces mechanism below), so GPU training workloads
# have a distinct, auditable namespace boundary rather than sharing
# orbital-drift with the Airflow webserver, MLflow UI, etc. — not a hard
# technical gate against RuntimeClass use elsewhere.

resource "kubernetes_namespace_v1" "orbital_drift_training" {
  metadata {
    name = var.orbital_drift_training_namespace
    labels = {
      "app.kubernetes.io/part-of"  = "orbital-drift"
      "orbital-drift.io/gpu-scope" = "training"
    }
  }
}

# GPU UUIDs, addressable by T027's training WorkflowTemplate via
# valueFrom.configMapKeyRef rather than a literal committed to
# workflows/train.yaml (D-000/D-10's "GPU UUIDs must not be committed",
# applied here to a non-Terraform, non-tfvars artifact this file does not
# own). This is the only GPU-scheduling artifact this file creates — actual
# runtimeClassName / NVIDIA_VISIBLE_DEVICES / resource-request wiring for
# training PODS belongs to T027's workflows/train.yaml, not this
# controller/RBAC file.
resource "kubernetes_config_map_v1" "gpu_uuids" {
  metadata {
    name      = var.gpu_uuids_configmap_name
    namespace = kubernetes_namespace_v1.orbital_drift_training.metadata[0].name
  }

  data = {
    train-gpu-uuid = var.orbital_drift_train_gpu_uuid
    serve-gpu-uuid = var.orbital_drift_serve_gpu_uuid
  }
}

# ---------------------------------------------------------------------------
# Cross-namespace SeaweedFS S3 credential copy (see decision note above)
# ---------------------------------------------------------------------------

data "kubernetes_secret_v1" "seaweedfs_s3_credentials" {
  metadata {
    name      = var.seaweedfs_s3_secret_name
    namespace = var.orbital_drift_namespace
  }
}

resource "kubernetes_secret_v1" "seaweedfs_s3_credentials_training" {
  metadata {
    name      = var.seaweedfs_s3_secret_name
    namespace = kubernetes_namespace_v1.orbital_drift_training.metadata[0].name
  }

  # Copies the full data map verbatim (all of T007's keys, whatever they
  # turn out to be) rather than naming individual keys here, so this
  # resource does not itself need to assume T007's exact key names — only
  # the artifact-repository secretKeySelector wiring below does, via the two
  # seaweedfs_s3_*_key variables.
  data = data.kubernetes_secret_v1.seaweedfs_s3_credentials.data
  type = "Opaque"
}

# ---------------------------------------------------------------------------
# Argo Workflows controller + server + CRDs (single release; see CRD-split
# decision above)
# ---------------------------------------------------------------------------

resource "helm_release" "argo_workflows" {
  name       = "argo-workflows"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-workflows"
  # Pinned per docs/decisions/versions.md (Constitution IV). Chart has since
  # moved to a MAJOR version (2.0.0 / app v4.1.0) upstream, re-verified
  # session-time 2026-08-16 — deliberately NOT adopted here; see
  # docs/decisions/005-t010-argo-workflows.md.
  version          = "1.0.23"
  namespace        = var.orbital_drift_namespace
  create_namespace = true

  values = [
    file("${path.module}/../../helm-values/argo-workflows.yaml"),
  ]

  # helm provider 3.x: `set` is a list of objects, not repeated blocks —
  # confirmed against terraform-provider-helm's docs/resources/release.md at
  # the pinned v3.2.0 tag (see docs/decisions/005-t010-argo-workflows.md).
  set = [
    # Namespace(s) this controller instance manages Workflow CRs in — host
    # topology, not chart behavior, so it cannot be a static value in
    # infra/helm-values/argo-workflows.yaml (D-002/D-07).
    {
      name  = "controller.workflowNamespaces[0]"
      value = kubernetes_namespace_v1.orbital_drift_training.metadata[0].name
    },
    # Artifact repository (SeaweedFS S3), wired via per-field
    # secretKeySelector references — Argo has no single existingSecret value
    # (D-000/D-09). The referenced secret is the COPY created above, in the
    # training namespace, not the original in orbital_drift_namespace (see
    # the cross-namespace secret decision above).
    {
      name  = "artifactRepository.s3.bucket"
      value = var.argo_workflows_s3_artifact_bucket
    },
    {
      name  = "artifactRepository.s3.endpoint"
      value = var.argo_workflows_s3_endpoint
    },
    {
      name  = "artifactRepository.s3.insecure"
      value = tostring(var.argo_workflows_s3_insecure)
    },
    {
      name  = "artifactRepository.s3.accessKeySecret.name"
      value = kubernetes_secret_v1.seaweedfs_s3_credentials_training.metadata[0].name
    },
    {
      name  = "artifactRepository.s3.accessKeySecret.key"
      value = var.seaweedfs_s3_access_key_id_key
    },
    {
      name  = "artifactRepository.s3.secretKeySecret.name"
      value = kubernetes_secret_v1.seaweedfs_s3_credentials_training.metadata[0].name
    },
    {
      name  = "artifactRepository.s3.secretKeySecret.key"
      value = var.seaweedfs_s3_secret_access_key_key
    },
  ]

  depends_on = [
    kubernetes_namespace_v1.orbital_drift_training,
    kubernetes_secret_v1.seaweedfs_s3_credentials_training,
  ]
}
