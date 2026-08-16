terraform {
  required_version = ">= 1.9.0"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.2.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.2.1"
    }
  }
}

# Provenance (D-002/D-03, "first-merged pin wins, subsequent copies verbatim"):
# helm 3.2.0 and kubernetes 3.2.1 are each the topmost/most recent entry in
#   https://raw.githubusercontent.com/hashicorp/terraform-provider-helm/main/CHANGELOG.md
#   https://raw.githubusercontent.com/hashicorp/terraform-provider-kubernetes/main/CHANGELOG.md
# fetched by the orchestrator 2026-08-16. This exact text is handed identically
# to the T007/T009/T010 dispatches (00-crds, 10-storage, 20-platform all get a
# copy) — a differing provider-version string in a sibling stage directory's
# versions.tf is a spec-guardian blocking finding, not a style difference.
#
# helm provider 3.x is a breaking rewrite from 2.x (terraform-plugin-sdk/v2 ->
# terraform-plugin-framework): `set`/`set_list`/`set_sensitive` on
# helm_release become list-of-object attributes instead of repeated blocks,
# and the provider's own `kubernetes {}` config becomes a single nested
# object (`kubernetes = { ... }`, not `kubernetes { ... }`). See
# docs/decisions/003-t008-mlflow-secret-wiring-findings.md D-003/05 — both
# old forms are syntactically valid HCL but schema-wrong against 3.2.0, so
# this is easy to get wrong silently. mlflow.tf in this directory uses the
# 3.x forms throughout; any sibling .tf file added later in this same stage
# directory must too.
