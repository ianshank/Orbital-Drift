# D-003: T008 (MLflow) findings — corrected `existingSecret` key path, and three new variables D-002 did not anticipate

**Status:** authored by the T008 `infra-scaffolder` dispatch, 2026-08-16, alongside `infra/terraform/20-platform/mlflow.tf` and `infra/helm-values/mlflow.yaml`. Not reviewed by `spec-guardian`/`peer-reviewer` yet — queued for review under the RB-007 reconciliation; see PR. (The integrated T007-T010 *artifact set* this doc accompanies — including `mlflow.tf`, whose wiring enacts D-003/01-03 and whose SeaweedFS key-name fix D-006/02 records — was APPROVED by both reviewers, `docs/decisions/006-t007-t010-integration.md`, but no record shows this document's own content was in either reviewer's scope.) Per CLAUDE.md's "unknowns discovered mid-task: write a short note in `docs/decisions/` and surface to the operator; do not improvise architecture" — this is that note, not a unilateral architecture change.

**Decision-ID namespace:** independent of `plan.md`'s own `D-01…D-05` and of `docs/decisions/000-phase0-technical-decisions.md`'s `D-000/D-nn` series. Cross-references from other docs should read `D-003/D-nn`. Numbered `003` (not `002`) because `docs/decisions/002-infra-layout.md` was authored by a parallel `runbook-writer` dispatch and had not landed in this worktree at authoring time (confirmed absent: `ls docs/decisions/` showed only `000-phase0-technical-decisions.md`, `001-coverage-gate.md`, `versions.md`) — `002` was already claimed and I could not safely verify its final content by number alone, only by the pasted excerpt in my own dispatch prompt.

---

## D-003/01 — D-002/D-09's `backendStore.postgres.existingSecret.*` key path is WRONG for the pinned `1.11.4` tag; the correct path is `backendStore.existingDatabaseSecret.*` (sibling of `postgres`, not nested inside it)

My dispatch prompt's explicit follow-up instruction was: re-fetch `.../community-charts/helm-charts/mlflow-1.11.4/charts/mlflow/values.yaml` (the tagged ref, not `main`) and confirm byte-identical.

**Result: NOT byte-identical. D-002/D-09's claimed key path does not exist in the chart.**

Two different fetch methods gave two different answers, so I do not report this from a single source:

1. An LLM-mediated `WebFetch` summary of the tagged URL *initially* echoed back the same (wrong) nested shape D-002/D-09 already claimed — this is a trap: asking a summarizing model "does X exist" tends to produce confirmatory answers even when X does not exist verbatim in the source.
2. A second, more literal `WebFetch` request for verbatim YAML on the same URL returned a *different*, self-contradicting answer — `backendStore.postgres` with no nested `existingSecret` at all, and a new top-level `backendStore.existingDatabaseSecret` sibling instead.

Given the disagreement between two summarized fetches of the *same* URL, I did not trust either and pulled the raw bytes directly (`curl` through the environment's proxy, unmediated by any summarizing model) — `https://raw.githubusercontent.com/community-charts/helm-charts/mlflow-1.11.4/charts/mlflow/values.yaml`, 789 lines, saved and read directly with the `Read` tool. I then cross-checked the raw values.yaml finding against the actual Helm template that consumes it (`templates/deployment.yaml`, `templates/configmap.yaml`, same tag ref) rather than trusting the values.yaml comments alone, since a values.yaml comment can itself be stale relative to what the template actually reads.

**Ground truth, confirmed at both the values.yaml and the consuming-template level:**

```yaml
backendStore:
  postgres:
    enabled: false
    host: ""      # required — plain value, NOT secret-sourced
    port: 5432    # required
    database: ""  # required — plain value, NOT secret-sourced
    user: ""
    password: ""
    driver: ""
  # ... mysql, mssql siblings, each with their own plain host/port/database/user/password ...
  existingDatabaseSecret:      # <-- SIBLING of postgres/mysql/mssql, not nested in any of them
    name: ""
    usernameKey: "username"
    passwordKey: "password"
```

`templates/configmap.yaml:9-11` builds `PGHOST`/`PGPORT`/`PGDATABASE` from the **plain** `backendStore.postgres.host/port/database` values (each `required`, i.e. the chart hard-fails at render time if unset) — never from any secret. `templates/deployment.yaml:129-140` (and three more identical blocks at lines 214-249, 533-568, guarding the dbchecker init container, the db-migration init container, and the main container respectively) sources `PGUSER`/`PGPASSWORD` **only** from `backendStore.existingDatabaseSecret.name/.usernameKey/.passwordKey`, gated on `.Values.backendStore.postgres.enabled` — a completely separate, non-nested top-level key.

**`artifactRoot.s3.existingSecret.name/.keyOfAccessKeyId/.keyOfSecretAccessKey` and `auth.existingAdminSecret.name/.usernameKey/.passwordKey` ARE confirmed byte-identical to D-002/D-09's claim** — verified against the same raw fetch and the same templates (`deployment.yaml:578-591` for the S3 keys). Only the Postgres path was wrong.

**Consequence for `infra/terraform/20-platform/mlflow.tf` (this PR):** wires `backendStore.existingDatabaseSecret.name = "${var.cnpg_cluster_name}-app"` (not `backendStore.postgres.existingSecret.name`), and separately sets `backendStore.postgres.host`/`backendStore.postgres.database` as **plain** Terraform-injected values (see D-003/02) — because the chart's own `required` guard on those two fields means there is no secret-sourced path for them at all; they must be plain strings regardless.

**Action requested:** `docs/decisions/002-infra-layout.md` D-09 (and its Follow-up #3, which flagged exactly the re-fetch this note performs) should be corrected to this finding when that PR is reachable from this branch. I cannot edit it directly — it does not exist in this worktree at authoring time.

## D-003/02 — Two of D-09's key paths are genuinely secret-shaped; two things MLflow also needs (`postgres.database`, `postgres.host`) are not, and were never secret-shaped to begin with

Re-reading `templates/configmap.yaml`'s `required` guard (D-003/01 above): `backendStore.postgres.host` and `backendStore.postgres.database` cannot be sourced from `existingDatabaseSecret` under any chart configuration — they are always plain, always-required values. This means Constitution III's "no literals buried in templates" applies to them the same way it applies to a bucket name: they must come from a Terraform variable, not a literal in `infra/helm-values/mlflow.yaml`, because both are derived from cross-cutting identifiers (`var.cnpg_cluster_name`, `var.orbital_drift_namespace`) that D-002 already treats as variables everywhere else.

`mlflow.tf` therefore injects, via `helm_release.mlflow`'s `set` list:

- `backendStore.postgres.host` = `"${var.cnpg_cluster_name}-rw.${var.orbital_drift_namespace}.svc.cluster.local"` — CloudNativePG's own automatically-created read-write Service, named `<cluster-name>-rw` in the Cluster's own namespace. This is standard, extensively-documented CNPG behavior (`rw`/`ro`/`r` Services, `<CLUSTER_NAME>-<SERVICE_NAME>` naming, `rw` "essential and cannot be disabled") — confirmed via a targeted web search returning consistent results across CNPG's own v1.18, v1.24, v1.25, v1.26, and v1.28 documentation pages. Not independently confirmed against the exact pinned operator `0.29.0`/cluster chart `0.8.1` (same residual-risk caveat D-002/D-08 already carries for CNPG facts generally) — flagged for T007 to confirm once it authors the actual `Cluster` CR, since T007 owns that resource and could in principle disable the default service names.
- `backendStore.postgres.database` = `var.cnpg_app_database_name` (new variable, see D-003/03).

## D-003/03 — Three new Terraform variables this task needed that were not in D-002's shared-variable table, added with the same "no in-code default" discipline

My dispatch prompt scoped me to exactly two shared variables (`cnpg_cluster_name`, `seaweedfs_s3_secret_name`). Implementing the corrected wiring above surfaced three more required, non-optional chart fields with no existing shared-variable coverage. None of these are secrets; all three are Constitution-III-relevant literals (a database name, a bucket name, a service endpoint URL) that must not be hardcoded, and all three depend on choices genuinely owned by other dispatches (T007), not by me. I did not invent silent literals for them — each is a `variable {}` block with **no in-code default** (same rule D-002 states for its own six), a **proposed** default in `infra/terraform/20-platform/terraform.tfvars.example` only, and an explicit flag below for whoever reviews D-002 next.

| Variable | Proposed default | Why it exists | Owned by / needs confirmation from |
|---|---|---|---|
| `cnpg_app_database_name` | `"app"` | `backendStore.postgres.database` is `required` by the chart template (D-003/02); `"app"` is CloudNativePG's own documented default database (and matching owner-user) name when `Cluster.spec.bootstrap.initdb.database` is left unset — confirmed via web search across multiple CNPG doc versions, not the pinned version specifically. | **T007** — if `cnpg_cluster.tf`'s `Cluster` CR sets `initdb.database` to anything other than CNPG's own default (e.g. because lakeFS, per D-002/D-11's still-open question, also needs a database in the same cluster and T007 picks a non-default name), this default must change to match. This is a real cross-dispatch dependency, not a decorative placeholder. |
| `seaweedfs_s3_endpoint_url` | `"http://seaweedfs-s3.orbital-drift.svc.cluster.local:8333"` | The chart has no first-class "S3 endpoint" field for a non-AWS S3-compatible store; the chart's own `extraEnvVars` block documents `MLFLOW_S3_ENDPOINT_URL` in a commented example as the mechanism (`templates/deployment.yaml:606-607` wires this exact env var when `minio.enabled`, confirming the mechanism works; I use `extraEnvVars` directly instead of `minio.enabled` since we reject the chart's bundled MinIO subchart per D-000/D-05). Port `8333` is the SeaweedFS S3 gateway's documented default. **PROVISIONAL** — I could not confirm the SeaweedFS chart's actual rendered Service name (GitHub's tree/contents API was unavailable in this session; targeted fetches for template filenames 404'd; a web search confirmed port `8333` but not the Service name pattern). | **T007** — must confirm or correct the Service name once `seaweedfs.tf` picks its actual Helm release name (Helm's `{{ .Release.Name }}-{{ .Chart.Name }}-s3`-style fullname convention would produce something close to this, but I have not verified it against the pinned `4.41.0` chart's actual template). |
| `mlflow_s3_bucket` | `"orbital-drift-mlflow-artifacts"` | `artifactRoot.s3.bucket` is commented `# required` in the chart's own values.yaml. A bucket name is explicitly named in Constitution III's own enumerated list ("bucket/repo names... all sourced from Helm values, environment") — a hardcoded literal here is a defect class, not a style preference. | Not owned by another dispatch — this is MLflow's own artifact bucket, distinct from whatever T007/T009 name their own buckets. Whether SeaweedFS auto-creates the bucket on first write or needs an explicit pre-creation step is genuinely on-cluster-only to verify (see the parent PR's deliverable). |

**None of these three appear as `default = "..."` inside any tracked `.tf` `variable {}` block** — same discipline D-002 states for its own six, extended here for the same Constitution III reason.

## D-003/04 — A structural gap D-002 does not resolve: `20-platform` is one shared Terraform root module across four dispatches (T007's `lakefs.tf`, T008's `mlflow.tf`, T009's `airflow.tf`, T010's `argo_workflows.tf`), but Terraform forbids duplicate `provider` and `variable` declarations within one root module

D-002/D-03 says every stage directory's `versions.tf` is "textually identical across all four [stage directories]... first-merged pin wins." That rule is about the four **stage-directory-level** `versions.tf` files being identical to each other — it does not address the fact that `20-platform` alone will eventually contain **four separate `.tf` files from four separate dispatches sharing one root module and one Terraform state.** Terraform's actual constraint: a `provider "helm" {}` block, a `provider "kubernetes" {}` block, and each `variable "x" {}` block may be declared **exactly once** per root module, in any one of that module's files — not once per file. If T007's `lakefs.tf`, my `mlflow.tf`, T009's `airflow.tf`, and T010's `argo_workflows.tf` each independently declare their own `provider "helm" {}` / `provider "kubernetes" {}` / `variable "kubeconfig_path"` / `variable "orbital_drift_namespace"` (all four genuinely need all four), `terraform init -backend=false && terraform validate` will hard-fail with a duplicate-configuration error the moment more than one of these files coexists in `20-platform` — a defect invisible to any single dispatch working in isolation, since each dispatch's own local validation only ever sees its own file.

D-01's own file table for `20-platform` lists only `versions.tf`, `terraform.tfvars.example`, and one file per release — no `providers.tf`/`variables.tf` is scoped to any of T007-T010. I did not invent a new file outside that list (which would itself be a divergence risk two other dispatches might not expect). Instead:

**What I did:** `mlflow.tf` (this PR) declares `provider "kubernetes" {}`, `provider "helm" {}`, and the four cross-cutting variables (`kubeconfig_path`, `orbital_drift_namespace`, `cnpg_cluster_name`, `seaweedfs_s3_secret_name`) that every `20-platform` release needs, plus the three new MLflow-specific ones from D-003/03. This worktree's `infra/terraform/` was completely empty before this PR (verified: only `.gitkeep` files existed under `infra/terraform`, `infra/helm-values`, `infra/k3s` in any stage), so this PR is the de facto first-lander for `20-platform`.

**What this means for T007/T009/T010, stated so the next reviewer catches it before a merge conflict becomes a build break:** whichever of `lakefs.tf`/`airflow.tf`/`argo_workflows.tf` lands **after** this PR must **not** redeclare `provider "kubernetes" {}`, `provider "helm" {}`, `variable "kubeconfig_path"`, or `variable "orbital_drift_namespace"` in their own file — they should reference `var.kubeconfig_path`/`var.orbital_drift_namespace` as already-declared. If one of those three PRs is authored and merged **before** this one reaches main, the reverse applies: this PR's copies must be deleted in favor of theirs, keeping only the MLflow-specific variables (`cnpg_cluster_name`/`seaweedfs_s3_secret_name` re-declarations plus the three D-003/03 additions) and the `helm_release.mlflow` resource. This is exactly the same "first-merged wins, subsequent copies verbatim-or-deleted" pattern D-002/D-03 already established for `versions.tf`, extended to cover the two additional object classes (`provider` blocks, cross-cutting `variable` blocks) D-002/D-03 did not name.

**Action requested:** `spec-guardian` should treat a *second* `provider "helm" {}` (or `kubernetes`, or either cross-cutting variable) landing in `20-platform` as the same class of blocking finding D-002/D-03 already defines for a mismatched provider-version pin — a structural duplicate, not a style question. `docs/decisions/002-infra-layout.md` should get a D-nn entry recording this resolution once it is reachable from a shared branch; I could not add it there directly (see header).

## D-003/05 — `hashicorp/helm` provider `3.x` is a breaking rewrite from `2.x`; `set`/`set_list`/`set_sensitive` are now list-of-object attributes, and `kubernetes`/`registry`/`experiments` provider config is now a single nested object, not a block

Confirmed directly from `hashicorp/terraform-provider-helm`'s own `docs/guides/v3-upgrade-guide.md` (fetched at the `main` ref, same source used for the `versions.tf` pin's own provenance). `3.0.0` migrated the provider from `terraform-plugin-sdk/v2` to `terraform-plugin-framework`, which changes wire-level schema shape, not just cosmetics:

- `provider "helm" { kubernetes { config_path = ... } }` (old, block syntax) → `provider "helm" { kubernetes = { config_path = ... } }` (new, single nested object, `=` required).
- `set { name = ...; value = ... }` repeated blocks (old) → `set = [{ name = ...; value = ... }, ...]` a list of objects (new) — same for `set_list`, `set_sensitive`.

Both old forms are syntactically valid HCL (blocks parse fine) but semantically wrong against the `3.2.0` schema — `terraform validate` would reject them with a schema mismatch, not a syntax error, meaning a superficial read of `mlflow.tf` would not obviously look broken to a reviewer unfamiliar with this change. `mlflow.tf` (this PR) uses the `3.x` list-of-objects/nested-object forms throughout. Flagged explicitly since **the same mistake is easy for T007/T009/T010 to make independently** if any of them pattern-match against `2.x`-era Helm/Terraform examples (which dominate search results and training data, since `3.0.0` shipped 2025-06-18) — worth a one-line callout in whichever review pass looks at the other three dispatches' `.tf` files.

---

## Not resolved here, flagged for whoever reviews next

1. `seaweedfs_s3_endpoint_url`'s proposed default is a guess at Helm's fullname convention, not a confirmed SeaweedFS chart Service name (D-003/03) — T007 must confirm once `seaweedfs.tf` exists.
2. Whether SeaweedFS auto-creates an S3 bucket on first write, or needs an explicit pre-creation step for `var.mlflow_s3_bucket` — on-cluster-only, cannot be determined from static chart research (see parent PR's deliverable, "what can only be verified on-cluster").
3. `cnpg_app_database_name`'s `"app"` default assumes T007's `Cluster` CR leaves `initdb.database` at CNPG's own default — genuinely contingent on a T007 decision not yet made (D-003/03).
4. The `20-platform` provider/variable ownership resolution in D-003/04 is a proposal enacted unilaterally by necessity (I am first-lander), not yet confirmed by `spec-guardian` or by T007/T009/T010's own authors.
