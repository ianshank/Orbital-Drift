# D-006: Integrating T007-T010's four independent `infra-scaffolder` dispatches — reconciling a shared Terraform root module, four secret-shape corrections, and one decision-doc renumbering

**Status:** performed by the orchestrator, 2026-08-16, immediately after all four T007-T010 `infra-scaffolder` dispatches completed in isolated worktrees. Not an agent dispatch — a mechanical-plus-judgment integration pass over four already-authored, already-reviewed-by-nobody artifact sets. Not yet reviewed by `spec-guardian`/`peer-reviewer` — that review is the next step, covering the integrated result (this doc plus the merged `infra/terraform/20-platform/` and updated `docs/decisions/versions.md`), not the four dispatches' original, now-superseded per-file states. **Not operator-ratified.**

**Why this doc exists:** `docs/decisions/002-infra-layout.md` (D-002) was designed, reviewed (three `spec-guardian` rounds, final verdict APPROVE), and handed to all four dispatches specifically to prevent "four internally-consistent, mutually-incompatible artifacts" (D-000's own stated failure mode). It worked for chart choices, namespaces, and secret-*naming* conventions — every dispatch converged on byte-identical values for `orbital_drift_namespace`, `cnpg_cluster_name`, `seaweedfs_s3_secret_name`, and even the unconfirmed SeaweedFS Service-endpoint guess, independently, in isolated worktrees. It did **not** anticipate two narrower problems, both discovered mid-authoring by the dispatches themselves (T008 first, corroborated and extended by T009 and T010): (1) `20-platform` receiving four separate `.tf` files from four separate dispatches sharing **one Terraform root module**, where `provider`/`variable` declarations must appear exactly once, not once per file; and (2) two dispatches (T008, T009) independently *guessing* at a downstream chart's exact secret-key-name contract before the secret's actual creator (T007) had landed, producing one confirmed, concrete mismatch (T008) and one costlier-than-necessary workaround (T009). This doc records exactly how both were resolved during integration, so a reviewer of the merged `20-platform/` files has one place that explains why they look different from any single dispatch's own deliverable.

---

## D-006/01 — Shared Terraform root module: consolidated into `infra/terraform/20-platform/providers.tf`

Per D-002/D-12 (added to D-002 after T008's own finding, before T009/T010 completed): a fifth file, `providers.tf`, holds every declaration more than one of the four dispatches' files needed. Concretely, after inspecting all four dispatches' actual output:

| Declaration | Needed by | Resolution |
|---|---|---|
| `provider "kubernetes"`, `provider "helm"`, `variable "kubeconfig_path"` | lakefs.tf, mlflow.tf, airflow.tf, argo_workflows.tf (all four) | Kept in `providers.tf` (T007's original copy — T010 had already detected and deferred to it without being told to) |
| `variable "orbital_drift_namespace"` | all four | Moved to `providers.tf`; T007/T008/T009/T010's own copies deleted |
| `variable "cnpg_cluster_name"` | lakefs.tf, mlflow.tf, airflow.tf (not argo_workflows.tf) | Moved to `providers.tf` anyway — an unused declared variable is not a Terraform error, and three-of-four is a strong enough shared-ness signal to centralize rather than leave in whichever file happens to load first |
| `variable "seaweedfs_s3_secret_name"` | all four | Moved to `providers.tf` |
| `variable "seaweedfs_s3_endpoint"` (full-URL form) | lakefs.tf, mlflow.tf (as `seaweedfs_s3_endpoint_url`, renamed — see D-006/02), airflow.tf (after D-006/03's rewire) | Moved to `providers.tf` under lakefs.tf's/T007's original name |

`argo_workflows.tf`'s own `argo_workflows_s3_endpoint` (bare `host:port`, no scheme — Argo's `artifactRepository.s3` schema needs a different shape than the other three's full-URL form) stayed file-local; it is genuinely a different variable, not a naming inconsistency to fix.

All four dispatches had independently anticipated this exact problem class in their own decision notes (T008's `docs/decisions/003-t008-mlflow-secret-wiring-findings.md` D-003/04, T009's `docs/decisions/004-t009-airflow-findings.md` Finding 2, T010's `docs/decisions/005-t010-argo-workflows.md` D-005/04) and each recommended the same detect-then-consolidate resolution D-002/D-03 already accepts for the provider-*version* pin race — this doc enacts that recommendation, it does not invent a new one.

## D-006/02 — Confirmed bug: T008's `mlflow.tf` used the wrong SeaweedFS secret key names

T007's actual `infra/terraform/10-storage/seaweedfs.tf` creates the shared S3 identity secret with discrete keys literally named `access_key_id` / `secret_access_key` (snake_case — chosen, per that file's own header comment, specifically so every downstream `existingSecret`-style consumer could use flat, predictable names). T008's `mlflow.tf`, authored in an isolated worktree before T007 landed, guessed `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (the AWS-SDK-convention names) for the same secret — a real mismatch, independently confirmed by both the T007 and T010 dispatches reading T007's actual file directly, and by the orchestrator re-reading it during integration. **Fixed** in the merged `mlflow.tf`: `artifactRoot.s3.existingSecret.keyOfAccessKeyId`/`.keyOfSecretAccessKey` now read `"access_key_id"`/`"secret_access_key"`. Left unfixed, this would have deployed an MLflow pod unable to authenticate to its own artifact store — a loud, immediate failure at first artifact write, not a silent drift, but a real one nonetheless.

Also renamed during the same fix: T008's `var.seaweedfs_s3_endpoint_url` → the shared `var.seaweedfs_s3_endpoint` (D-006/01). The underlying *value* T008 had guessed was already byte-identical to T007's own guess for the same thing — this was a naming consolidation, not a value correction.

## D-006/03 — T009's Airflow SeaweedFS credentials rewired to reference T007's actual secret, eliminating a disclosed operator-toil cost

T009's original `airflow.tf` (see `docs/decisions/004-t009-airflow-findings.md` Finding 4) deliberately did **not** reference `var.seaweedfs_s3_secret_name` for its own S3 remote-logging credentials — at authoring time, T007 had not landed, and D-002/D-10's relayed SeaweedFS secret-shape research had already proven unstable (three different re-fetches across three dispatches found three different values.yaml shapes on `master` the same day). T009 chose three dedicated, operator-supplied variables instead (`airflow_seaweedfs_s3_endpoint`, `_access_key_id`, `_secret_access_key`), explicitly flagging the cost: "the operator must enter the *same* underlying SeaweedFS credential material twice."

T007 has since landed with a **confirmed** shape (`access_key_id`/`secret_access_key` discrete keys, D-006/02 above) — the condition T009's own Finding 4 named for closing the gap ("once T007's actual secret shape is confirmed on a real cluster... point Airflow at T007's secret directly and remove the duplication," though T009 expected that confirmation to come from a live cluster rather than a sibling dispatch landing first). **Rewired** in the merged `airflow.tf`: a new `data "kubernetes_secret_v1" "seaweedfs_s3_for_airflow"` reads `var.seaweedfs_s3_secret_name` directly (a separate `data` block from `argo_workflows.tf`'s own read of the same secret — Terraform data sources are side-effect-free, so a second read costs one extra API call and avoids coupling `airflow.tf` to another file's resource address); the three dedicated variables are removed. The operator now enters SeaweedFS credentials exactly once, at `10-storage/terraform.tfvars`/`TF_VAR_seaweedfs_s3_admin_*`, not twice.

## D-006/04 — CNPG app-secret `uri` field: independently corroborated by two dispatches, not contradicted

T007's `lakefs.tf` claimed CNPG's auto-created `<cluster>-app` secret exposes a single-field `uri` key, sourced from a relayed web search (cloudnative-pg.io itself was proxy-blocked). T009's `airflow.tf` independently fetched CNPG's actual source (`cloudnative-pg/cloudnative-pg`, `pkg/specs/secrets.go`, `CreateSecret()`) while researching a *different* problem (Airflow needs a `connection`-keyed secret CNPG's doesn't provide) and confirmed the full field set includes `uri` among `username`, `user`, `password`, `dbname`, `host`, `port`, `pgpass`, `jdbc-uri`, `fqdn-uri`, `fqdn-jdbc-uri`. This is independent, source-level corroboration of T007's relayed finding, not a conflict — T009 needed a *different* field (`connection`, which doesn't exist, hence its own transform) while confirming the `uri` field T007 needed *does*. No fix required here; noted in `lakefs.tf`'s own header comment for a future reader who might otherwise wonder whether T009's Finding 1 casts doubt on T007's D-11 resolution — it doesn't, it strengthens it.

## D-006/05 — Decision-doc renumbering

Both T009 and T010 authored their mid-task findings docs without visibility into the others' choices (isolated worktrees, D-000's own predicted failure mode, operating here on filenames rather than Terraform declarations):

- T008 numbered its doc `003` — kept as `docs/decisions/003-t008-mlflow-secret-wiring-findings.md`, since `docs/decisions/002-infra-layout.md` D-09/D-12 already cite it by that exact path and number.
- T009 also numbered its doc `003` (`docs/decisions/003-t009-airflow-findings.md` in its own worktree) — a real filename collision with T008's. Renumbered to `docs/decisions/004-t009-airflow-findings.md`; its title (`# D-003:` → `# D-004:`) updated to match. The file's own body uses `## Finding N` headers rather than `D-003/NN` cross-references, so no other internal renumbering was needed.
- T010 numbered its doc `004` (`docs/decisions/004-t010-argo-workflows.md`) — a collision with T009's *post-renumbering* target. Renumbered to `docs/decisions/005-t010-argo-workflows.md`; all 19 internal `D-004/NN` cross-references mechanically updated to `D-005/NN` (verified via a scripted substitution plus a grep confirming zero remaining `D-004` references in the file).

No cross-references existed between T009's and T010's own docs (neither had visibility into the other), so no further reconciliation was needed there.

---

## What was NOT changed during integration

Everything else in all four dispatches' output was kept as authored: `00-crds/`, `10-storage/` (exclusively T007's, no cross-dispatch conflicts existed there at all — D-002/D-01's own reference tree already scoped them to T007 alone), every `helm-values/*.yaml` file, every chart pin, every namespace choice, T010's CRD-split divergence from D-002/D-01's stated default (already flagged for `spec-guardian` per D-002 Follow-up #8, not this doc's call to second-guess), and every dispatch's own residual-risk/follow-up notes (still open, still listed in their respective decision docs — this integration pass resolved cross-dispatch *mechanical* conflicts, not the underlying on-cluster-only unknowns like the SeaweedFS Service DNS name or bucket auto-creation behavior).

## Follow-ups carried forward, not resolved by this integration

1. Every "UNCONFIRMED against a live cluster" flag any of the four dispatches raised (SeaweedFS Service name/port, bucket auto-creation, CNPG secret field shape at the exact pinned version) — genuinely on-cluster-only, per each dispatch's own deliverable notes.
2. `docs/decisions/002-infra-layout.md`'s D-03 prose still shows the pre-3.x `helm` provider `kubernetes { }` block syntax in one illustrative example (T010's own Follow-up #6, `docs/decisions/005-t010-argo-workflows.md`) — a one-line correction, not done here since it is D-002's own text, not this integration's artifact set.
3. `plan.md`'s Project Structure comment for `infra/terraform/` is stale against D-01's stage-subdirectory design (D-002's own Follow-up #10, still open, now doubly true with real files in every stage directory).
4. The `[HUMAN]` `.terraform.lock.hcl` bootstrap (D-002/D-05) — none of T007-T010's Terraform has one; that remains an explicit T011/T012 operator step, unaffected by this integration.
