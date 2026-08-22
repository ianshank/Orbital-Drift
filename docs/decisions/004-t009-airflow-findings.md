# D-004: Findings made while authoring T009 (Airflow), not covered by D-000 or D-002

**Status:** recorded during T009's own authoring session, 2026-08-16, by `infra-scaffolder`. Not reviewed by `spec-guardian`/`peer-reviewer` yet — the collaboration-protocol pass this line anticipated ran over the integrated T007-T010 *artifact set* (both reviewers APPROVE, `docs/decisions/006-t007-t010-integration.md`), including `airflow.tf` (whose SeaweedFS-credential rewire D-006/03 records supersedes this doc's Finding 4 resolution), but no record shows this document's own content was in either reviewer's scope — queued for review under the RB-007 reconciliation; see PR. Not operator-ratified.
**Why this exists:** CLAUDE.md's working agreement — "Unknowns discovered mid-task: write a short note in `docs/decisions/` and surface to the operator; do not improvise architecture" — plus the infra-scaffolder charter's provenance discipline. Five findings surfaced while implementing `infra/terraform/20-platform/airflow.tf` that neither `docs/decisions/000-phase0-technical-decisions.md` (D-000) nor `docs/decisions/002-infra-layout.md` (D-002) anticipated. None of them are "T009's call to just decide silently" — three are genuine technical facts (verified against source, not guessed) that change how the Airflow Terraform must be written; two are cross-dispatch coordination risks that affect T007/T008/T010 too, not just this file.

---

## Finding 1 — CloudNativePG's auto-created `<cluster>-app` Secret has no `connection` key; `data.metadataSecretName` cannot point at it directly

D-002/D-08 (relayed from CloudNativePG's own docs, not independently verified there) describes the app-user secret only as holding "the user password and database connection details" — vague enough that this dispatch's brief read it as directly compatible with Airflow's `data.metadataSecretName`. It is not.

**Verified directly against CloudNativePG source** (`pkg/specs/secrets.go`, `CreateSecret()`, `cloudnative-pg/cloudnative-pg` `main` branch, fetched 2026-08-16 — same residual-risk caveat D-08 already states: checked against `main`, not byte-pinned to the exact `0.29.0`/app `1.30.0` release, though D-08 also notes this mechanism has been stable across every version range it checked):

```go
StringData: map[string]string{
    "username": username, "user": username, "password": password,
    "dbname": dbname, "host": hostname, "port": ...,
    "pgpass": ..., "uri": namespacedBuilder.buildPostgres(),
    "jdbc-uri": ..., "fqdn-uri": ..., "fqdn-jdbc-uri": ...,
},
```

No key named `connection`. The Airflow chart's own `values.yaml` (fetched at tag `helm-chart/1.22.0`, `chart/values.yaml`) is explicit about what it needs:

```yaml
data:
  # If secret name is provided, secret itself has to be created manually with 'connection' key like:
  #   data:
  #     connection: base64_encoded_connection_string
  # postgresql+psycopg2://airflow:password@postgres/airflow
  metadataSecretName: ~
```

Pointing `data.metadataSecretName` straight at `${var.cnpg_cluster_name}-app`, as a literal reading of this dispatch's brief instructs, would deploy Airflow pods (api-server, scheduler, triggerer, dag-processor) that all fail with `CreateContainerConfigError: key "connection" not found` — the metadata DB connection would never resolve. This is not a cosmetic gap; it breaks the whole deployment.

**Resolution implemented in `airflow.tf`:** a `data "kubernetes_secret_v1" "cnpg_app"` reads the CNPG secret's discrete `user`/`password`/`host`/`port`/`dbname` keys, a `local` assembles `postgresql+psycopg2://user:pass@host:port/dbname` (using `urlencode()` on user/password, matching the chart's own worked example's driver — `postgresql+psycopg2://`, not CNPG's own bare `postgresql://` `uri` key), and a new `kubernetes_secret_v1.airflow_metadata_connection` stores it under a literal `connection` key. `data.metadataSecretName` is wired to *that* secret's name, not CNPG's.

This is the same "transformation step" D-002/D-11 already sketches as a possibility for lakeFS's `databaseConnectionString` (same class of problem — a chart wanting one composed field, CNPG exposing discrete ones) but leaves undecided ("T007's call"). This finding is independent confirmation, from actually reading CNPG's source rather than its docs, that the transformation step is *not* merely a possibility — for Airflow specifically it is required. Recommend T007 check whether the same reasoning applies to lakeFS's `databaseConnectionString` (very likely yes, same discrete-vs-composed mismatch) and to T008/MLflow (probably *not* an issue — the community MLflow chart's `backendStore.postgres.existingSecret.usernameKey`/`.passwordKey` read **discrete** keys, which do match CNPG's `user`/`password` keys directly, per D-002/D-09).

Residual risk carried forward, same shape as D-08's own: this is confirmed against CNPG's `main` branch, not byte-pinned to the exact `0.29.0` release tag. Flagged for whoever operates T012 (`[HUMAN]` platform bring-up) to notice immediately if it turns out wrong — the failure mode (`CreateContainerConfigError`) is loud and immediate, not a silent drift.

## Finding 2 — Same-root-module `provider`/shared-`variable` duplicate declarations across T007/T008/T009/T010's files in `20-platform/`

D-002/D-01 places `lakefs.tf` (T007), `mlflow.tf` (T008), `airflow.tf` (T009, this dispatch), and `argo_workflows.tf` (T010) all in the **same** Terraform root module (`infra/terraform/20-platform/` — one state, per D-01). D-002/D-03 states provider *version* pins go in a shared `versions.tf`, textually identical across stage directories, "first-merged pin wins" — but that file's exact text is fixed by this dispatch's own brief and does not (and per the brief, must not) carry `provider "helm" {}` / `provider "kubernetes" {}` **configuration** blocks, nor the `variable {}` declarations more than one release file needs (`orbital_drift_namespace`, `cnpg_cluster_name`; this file also needs `kubeconfig_path`).

Terraform allows exactly **one** unaliased `provider "helm" {}` / `provider "kubernetes" {}` block, and exactly **one** declaration of any given `variable "name" {}`, **per root module** — not one per file. D-002/D-07's language that `cnpg_cluster_name`/`seaweedfs_s3_secret_name` are "re-declared in 20-platform" is accurate for *cross-root* redeclaration (10-storage vs 20-platform — different states, no collision, genuinely fine) but does not distinguish that from *same-root, same-directory* redeclaration across `lakefs.tf`/`mlflow.tf`/`airflow.tf`/`argo_workflows.tf` — which **is** a hard `terraform validate` error ("Duplicate provider configuration" / "Duplicate variable declaration") the moment two of the four land with their own independent copies.

**This dispatch's own brief explicitly instructs the same-file, self-contained pattern** ("re-declare `var.cnpg_cluster_name` with no in-code default... same pattern T007/T008 use"), so `airflow.tf` as written declares `kubeconfig_path`, `orbital_drift_namespace`, and `cnpg_cluster_name` itself, plus its own `provider` blocks — making it correct and independently `terraform validate`-able **in isolation**. It will very likely collide with T007/T008/T010's own files once all four land in the same directory, unless the orchestrator has already told those three dispatches to *reference* rather than *redeclare* these four things (this session had no visibility into T007/T008/T010's actual dispatch text or output — the sibling worktrees are separate, isolated checkouts).

**Not resolved here** — flagged per CLAUDE.md's "do not improvise architecture" instruction rather than unilaterally inventing a new shared `providers.tf` file that would bind three other in-flight dispatches without their own sign-off. Recommended resolution for `spec-guardian`/the operator to apply at integration time, extending D-002/D-03's own accepted "detect, not prevent" tradeoff for the provider-version race to this same class of problem: whichever of T007/T008/T009/T010 merges first keeps its `provider`/shared-`variable` declarations; every subsequent PR deletes its own duplicate copies and references the survivor's `var.x` instead. `spec-guardian` should treat a duplicate declaration surviving into a later PR as the same class of blocking finding D-03 already assigns to a mismatched version pin.

## Finding 3 — The chart's `createUserJob.defaultUser.password` default is the plaintext literal `"admin"`

Not mentioned anywhere in D-000/D-002. The chart's own `values.yaml`:

```yaml
createUserJob:
  defaultUser:
    role: Admin
    username: admin
    email: admin@example.com
    firstName: admin
    lastName: user
    password: admin
```

Left unset, this dispatch's Airflow deployment would create an initial admin account with a well-known trivial credential — a real Constitution VII-adjacent issue even though the literal itself lives in upstream chart defaults rather than this repo. **Resolution:** a new `var.airflow_admin_password` (sensitive, no default anywhere, operator-supplied via `TF_VAR_airflow_admin_password`), wired via `helm_release`'s `set_sensitive` (redacted from Terraform CLI plan/apply output) rather than a literal in `infra/helm-values/airflow.yaml`.

## Finding 4 — SeaweedFS's `existingSecret`/`seaweedfs_s3_config` mechanism has moved again since D-002/D-10 was written, and this dispatch does not use it

D-002/D-10 already flags real uncertainty about the SeaweedFS chart's exact `existingSecret` shape ("fetched from `master`... not independently confirmed this is genuinely the source that produced the pinned `4.41.0` release"). Re-fetching `seaweedfs/seaweedfs`'s `k8s/charts/seaweedfs/values.yaml` from `master` in this session (2026-08-16, same day as D-10, evidently a different commit) found a **different** shape again: `s3.existingConfigSecret` (not `s3.auth.existingSecret` as D-10 describes) and `s3.credentials.admin.accessKey`/`s3.credentials.read.accessKey` (not `s3.auth.adminAccessKeyId` as D-10 describes) — while the `seaweedfs_s3_config`-inline-JSON mechanism D-10 also describes is *still present* in this newer fetch, at a different values path. This is exactly the instability D-10 already flagged as a residual risk materializing, not a new category of risk — but it means the internal JSON *schema* of whatever secret `var.seaweedfs_s3_secret_name` (T007's variable, D-002/D-07) ends up naming is genuinely unconfirmed at authoring time for T009, and is entirely T007's implementation call, not something this dispatch can respect a specific shape of.

**Resolution implemented in `airflow.tf`:** rather than `jsondecode()`-parsing an unconfirmed nested JSON structure out of `var.seaweedfs_s3_secret_name`'s secret (T007-owned, cross-root, chart-version-dependent), Airflow's S3 remote-logging connection is built from three **dedicated** new variables (`airflow_seaweedfs_s3_endpoint`, `airflow_seaweedfs_s3_access_key_id`, `airflow_seaweedfs_s3_secret_access_key`) — operator-supplied, no default anywhere, same bootstrapping pattern D-000/D-09 already establishes generally. This is a deliberate, disclosed **deviation** from D-002/D-07's shared-variable table, which lists T009 as a consumer of `var.seaweedfs_s3_secret_name` directly.

**Cost, stated plainly:** the operator must enter the *same* underlying SeaweedFS credential material twice — once for whatever T007's `seaweedfs.tf` actually wires, once for these three new Airflow-specific variables — until a follow-up PR (once T007's actual secret shape is confirmed on a real cluster) can point Airflow at T007's secret directly and remove the duplication. Flagged as exactly that: a follow-up, not done here, matching D-002/D-11's own "not resolved here" treatment of an analogous lakeFS question.

## Finding 5 — `hashicorp/helm` provider `3.x` is a breaking rewrite of `helm_release`'s `set`/`set_sensitive` syntax; relevant to every sibling dispatch, not just this file

Pinned to `3.2.0` (this dispatch's exact `versions.tf` text, shared verbatim with T007/T008/T010 per D-002/D-03). The provider's own `CHANGELOG.md` and `docs/guides/v3-upgrade-guide.md` (`hashicorp/terraform-provider-helm`, fetched 2026-08-16) document a `3.0.0` migration from the legacy SDKv2 to the Terraform Plugin Framework, with two changes that will silently produce invalid HCL if authored the "obvious"/pre-3.x way:

- `set`, `set_list`, `set_sensitive` on `helm_release` (and `helm_template`) are now **lists of nested objects**, not repeatable blocks: `set = [{ name = "x", value = "y" }]`, not `set { name = "x"; value = "y" }`.
- `provider "helm" { kubernetes { ... } }`'s `kubernetes` block is now a **single nested object attribute**: `provider "helm" { kubernetes = { config_path = ... } }`, not `kubernetes { config_path = ... }`.

`airflow.tf` in this dispatch is written against the confirmed `3.x` syntax throughout. Flagged here because D-002/D-03 pins the provider version but says nothing about this syntax break, and it is exactly the kind of thing muscle memory (or an LLM trained mostly on `2.x`-era Terraform examples) gets wrong — worth `spec-guardian` specifically checking T007/T008/T010's `helm_release` blocks for the old block-style `set { ... }` syntax, which would fail `terraform validate` (or worse, silently parse as something else) rather than merely producing a style nit.

---

## Sources cited in this document

- `cloudnative-pg/cloudnative-pg`, `pkg/specs/secrets.go`, `main` branch — https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/main/pkg/specs/secrets.go (fetched 2026-08-16)
- `apache/airflow`, `chart/values.yaml` and `chart/templates/**`, tag `helm-chart/1.22.0` — https://raw.githubusercontent.com/apache/airflow/helm-chart/1.22.0/chart/values.yaml (fetched 2026-08-16)
- `seaweedfs/seaweedfs`, `k8s/charts/seaweedfs/values.yaml`, `master` branch — https://raw.githubusercontent.com/seaweedfs/seaweedfs/master/k8s/charts/seaweedfs/values.yaml (fetched 2026-08-16)
- `hashicorp/terraform-provider-helm`, `CHANGELOG.md` and `docs/guides/v3-upgrade-guide.md`, `main` branch — https://raw.githubusercontent.com/hashicorp/terraform-provider-helm/main/CHANGELOG.md (fetched 2026-08-16)
- `hashicorp/terraform-provider-kubernetes`, `CHANGELOG.md`, `main` branch — https://raw.githubusercontent.com/hashicorp/terraform-provider-kubernetes/main/CHANGELOG.md (fetched 2026-08-16)
