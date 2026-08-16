# infra/terraform/10-storage/cnpg_cluster.tf
#
# The CloudNativePG `Cluster` custom resource — the actual Postgres cluster
# (D-000/D-04, D-000/D-06). Requires the `Cluster` CRD installed by
# 00-crds/cnpg_operator.tf to already exist and be reconciled; that is why
# this lives in a SEPARATE Terraform root module/state (D-002/D-01), not a
# later resource appended to the operator's own apply.
#
# Chart pin provenance: `cluster` chart `0.8.1`. Originally
# docs/decisions/versions.md (2026-08-08). Re-confirmed 2026-08-16 against the
# tag-pinned source:
#   https://raw.githubusercontent.com/cloudnative-pg/charts/cluster-v0.8.1/charts/cluster/values.yaml
# Chart default `enableSuperuserAccess: true` is EXPLICITLY overridden to
# `false` below (infra/helm-values/cnpg-cluster.yaml) — the chart's own
# default is the opposite of this project's hardened default, so this is not
# a no-op; nothing in T007-T010 needs superuser access (dispatch charter).
#
# Secret naming (D-002/D-07, D-08): NOT declared as its own variable here.
# `.spec.bootstrap.initdb.secret.name` is deliberately left unset in
# infra/helm-values/cnpg-cluster.yaml (the chart's own default is already
# commented-out/empty) so CNPG auto-creates "<cluster-name>-app" from this
# Cluster's own metadata.name — which equals the Helm release name below,
# which equals var.cnpg_cluster_name.
#
# D-002/D-11 RESOLUTION (see 20-platform/lakefs.tf for the full write-up):
# confirmed 2026-08-16 (relayed search citing CNPG's own applications
# documentation; cloudnative-pg.io itself was blocked by this session's
# egress proxy — residual risk noted in lakefs.tf) that the auto-created
# "-app" secret carries a single-field "uri" key
# (postgresql://user:pass@host:port/dbname) alongside discrete
# username/password/dbname/host/port/jdbc-uri/pgpass fields. lakeFS's
# lakefs.tf reads that "uri" field directly via a `data
# "kubernetes_secret_v1"` lookup rather than needing a hand-typed
# TF_VAR_lakefs_db_connection_string.

variable "orbital_drift_namespace" {
  description = <<-EOT
    Namespace holding the CNPG Cluster CR, SeaweedFS, and lakeFS (D-002/D-06).
    Declared HERE (first file in 10-storage/, which T007 owns exclusively per
    D-002/D-01's reference tree — no other T007-T010 task writes into
    10-storage/) and reused, unqualified, by seaweedfs.tf in this same
    directory. No default — proposed default "orbital-drift" lives only in
    terraform.tfvars.example.
  EOT
  type        = string
}

variable "cnpg_cluster_name" {
  description = <<-EOT
    Name of the CloudNativePG Cluster CR. Also the Helm release name, so the
    resulting Cluster object's metadata.name matches, and CNPG's auto-created
    app secret is named "$${var.cnpg_cluster_name}-app" (D-002/D-07). No
    default — proposed default "orbital-drift-postgres" lives only in
    terraform.tfvars.example.
  EOT
  type        = string
}

resource "helm_release" "cnpg_cluster" {
  name             = var.cnpg_cluster_name
  repository       = "https://cloudnative-pg.github.io/charts"
  chart            = "cluster"
  version          = "0.8.1"
  namespace        = var.orbital_drift_namespace
  create_namespace = true

  values = [
    file("${path.module}/../../helm-values/cnpg-cluster.yaml"),
  ]

  # No set{} blocks: the app-user secret name is intentionally left to CNPG's
  # auto-create behaviour (see header comment) — there is no secret-name
  # literal to inject here. enableSuperuserAccess: false and all other
  # tunables (instance count, storage size, resource requests) live in the
  # static values file per D-002/D-07 (none of them are secret-name-shaped).
}
