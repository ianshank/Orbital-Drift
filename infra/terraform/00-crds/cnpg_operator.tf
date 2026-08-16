# infra/terraform/00-crds/cnpg_operator.tf
#
# CloudNativePG operator (D-000/D-04, D-002/D-01). Installs the operator
# Deployment AND the CRDs the rest of the CNPG chain depends on (Cluster,
# Pooler, ...). Must apply — and reconcile — before 10-storage/cnpg_cluster.tf
# can reference the Cluster CRD (D-000/D-06's staged-apply requirement:
# `kubernetes_manifest`/CRD-backed resources validate against the live API
# server at plan time, so a same-run CR would fail even with `depends_on`).
#
# Chart pin provenance: chart `0.29.0` / operator app `1.30.0`. Originally
# docs/decisions/versions.md (https://api.github.com/repos/cloudnative-pg/charts/releases,
# 2026-08-08). Re-confirmed 2026-08-16 directly against the tag-pinned chart
# source:
#   https://raw.githubusercontent.com/cloudnative-pg/charts/cloudnative-pg-v0.29.0/charts/cloudnative-pg/values.yaml
#   https://raw.githubusercontent.com/cloudnative-pg/charts/cloudnative-pg-v0.29.0/charts/cloudnative-pg/README.md
#
# D-002 Follow-up #9 RESOLVED: the `cnpg-system` namespace convention (D-002/
# D-06) is confirmed directly against this pinned chart's own README install
# example, not merely inferred from general CNPG documentation practice:
#   "helm upgrade --install cnpg --namespace cnpg-system --create-namespace
#    cnpg/cloudnative-pg"
# (fetched 2026-08-16). cnpg_operator_namespace's proposed default below
# matches this exactly.
#
# D-002 Follow-up #1 (plugin-barman-cloud `0.7.1`) DECIDED, NOT IMPLEMENTED
# HERE: confirmed against the chart's own README
# (cloudnative-pg-charts/plugin-barman-cloud-v0.7.1) that it (a) DOES carry
# its own CRDs (e.g. `ObjectStore` — "Uninstalling the chart does not remove
# the plugin's CRDs"), so if it is ever added it WOULD need its own 00-crds
# file/split, per D-002/D-01's CRD-staging rule; and (b) requires a working
# cert-manager installation as a hard prerequisite ("this chart requires a
# working installation of cert-manager ... install it and wait until it is
# ready before installing this chart") — an entirely new, currently-unpinned,
# operator-undecided dependency. T007's task line and D-002/D-01's own file
# table name exactly four files for T007's scope; introducing cert-manager
# (its own version pin, its own CRD stage, its own backup-target/schedule/
# retention decisions) is new scope beyond "SeaweedFS + lakeFS + CloudNativePG
# values and Terraform releases" and needs explicit operator sign-off before
# a future task, not a decision an infra-scaffolder dispatch should make
# unilaterally. NOT implemented in this PR. Backups therefore remain
# unconfigured after T007-T012 — the Cluster CR created in
# 10-storage/cnpg_cluster.tf uses `local-path` PVC storage only, with no
# `.spec.backup` / `.spec.plugins` configuration, consistent with D-000/D-04's
# own framing of backups as a stated cost/consequence of choosing CNPG, not a
# guarantee attached to this PR.

variable "cnpg_operator_namespace" {
  description = <<-EOT
    Namespace the CloudNativePG operator (cluster-scoped controller) installs
    into (D-002/D-06). Confirmed 2026-08-16 against the pinned 0.29.0 chart's
    own README install example (resolves D-002 Follow-up #9). No default —
    proposed default "cnpg-system" lives only in terraform.tfvars.example
    (Constitution III / D-002's no-tracked-default discipline).
  EOT
  type        = string
}

resource "helm_release" "cnpg_operator" {
  name             = "cnpg"
  repository       = "https://cloudnative-pg.github.io/charts"
  chart            = "cloudnative-pg"
  version          = "0.29.0"
  namespace        = var.cnpg_operator_namespace
  create_namespace = true

  values = [
    file("${path.module}/../../helm-values/cnpg-operator.yaml"),
  ]

  # No set{} blocks: nothing this file configures is a secret name. The
  # operator itself has no existingSecret-shaped inputs (D-002/D-07) — it
  # only creates/manages CRDs and its own controller Deployment. Chart
  # defaults already install CRDs (crds.create: true) and run the operator
  # cluster-wide (it must watch every namespace containing a Cluster CR;
  # the Cluster CR itself lives in orbital-drift per D-002/D-06, a different
  # namespace than the operator's own cnpg-system).
}
