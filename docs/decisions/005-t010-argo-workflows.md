# D-005: T010 (Argo Workflows) findings — CRD-split, cross-namespace secret, and confirmed cross-dispatch collisions in the shared `20-platform` root module

**Status:** authored by the T010 `infra-scaffolder` dispatch, 2026-08-16, alongside `infra/terraform/20-platform/argo_workflows.tf`, `infra/helm-values/argo-workflows.yaml`, `infra/terraform/20-platform/terraform.tfvars.example`, and `infra/terraform/common.tfvars.example`. Not reviewed by `spec-guardian`/`peer-reviewer` yet. Per CLAUDE.md's "unknowns discovered mid-task: write a short note in `docs/decisions/` and surface to the operator; do not improvise architecture" — this is that note.

**Decision-ID namespace:** independent of `plan.md`'s own `D-01…D-05`, of `docs/decisions/000-phase0-technical-decisions.md`'s `D-000/D-nn` series, and of `docs/decisions/002-infra-layout.md`'s `D-002/D-nn` series. Cross-references from other docs should read `D-005/D-nn`.

**Why `004`, not `003`:** this dispatch's own worktree contains only `000-phase0-technical-decisions.md` and `001-coverage-gate.md` under `docs/decisions/`. The shared/main checkout (read via absolute path, not this worktree) additionally has `002-infra-layout.md`. A sibling T008 (MLflow) dispatch, working in its own isolated worktree, independently authored `docs/decisions/003-t008-mlflow-secret-wiring-findings.md`, directly confirmed by reading that worktree. `003` is therefore already claimed. This numbering, like T008's own, is expected to need reconciliation once all four T007-T010 branches (and `main`) are actually merged — flagged, not resolved, here.

**Unusual provenance note:** this environment's harness permits `Read` (though not `Bash`/git operations) against sibling agents' isolated worktrees under `.claude/worktrees/agent-*/`. T007 (SeaweedFS/lakeFS/CloudNativePG) and T008 (MLflow) had already produced real Terraform artifacts in their own worktrees at the time this dispatch ran. Every finding below that cites "T007's actual `seaweedfs.tf`" or "T008's actual `mlflow.tf`" was confirmed by directly reading those files, not inferred from `docs/decisions/002-infra-layout.md` alone — stronger evidence than either T007 or T008 had available to themselves, since each was authored in isolation from the others. This section exists specifically so `spec-guardian` can distinguish "T010 guessed and got lucky" from "T010 checked."

---

## D-005/01 — CRD-split decision: single `helm_release`, no `00-crds/argo_workflows_crds.tf` (diverges from D-002/D-01's stated default, per D-002 Follow-up #8's explicit invitation to do so)

`infra/helm-values/argo-workflows.yaml` sets `crds.install: true` / `crds.full: false`. Confirmed directly against the pinned chart's own `templates/crds.yaml` at the `argo-workflows-1.0.23` tag: the template is gated by `{{- if and .Values.crds.install (not .Values.crds.full) }}`, globs `files/crds/minimal/*.yaml`, and carries no `helm.sh/hook` annotation — i.e. these are ordinary chart templates, applied as part of one `helm upgrade --install`, not the `crds.full: true` default's `templates/crds-install-job.yaml` pre-install/pre-upgrade hook Job (which downloads ~11MB of full-schema CRDs from GitHub at apply time — D-000/D-06's documented risk, compounded by `argoproj.github.io` being blocked by this environment's own egress proxy).

D-000/D-06's "CRD stage must be a separate apply" rule exists specifically because `kubernetes_manifest` validates a custom resource against the live API server's OpenAPI schema **at plan time**, so a same-run CR referencing a not-yet-applied CRD fails even with `depends_on` set correctly — this is the CNPG `Cluster` CR problem T007 genuinely has (hence its real `00-crds` / `10-storage` split). `infra/terraform/20-platform/argo_workflows.tf` declares **no** `kubernetes_manifest` resource for any `Workflow` / `WorkflowTemplate` / `CronWorkflow` CR — those are submitted at runtime (`argo submit`, T027's `workflows/train.yaml`) or via git-sync, never provisioned by Terraform. The specific hazard D-000/D-06 warns about therefore does not apply to this file. Helm's own resource-kind sort order applies `CustomResourceDefinition` objects ahead of `Deployment`/`ServiceAccount`/etc. within one `helm upgrade --install`, and `terraform-provider-helm`'s `helm_release` resource does not perform `kubernetes_manifest`'s per-object, plan-time OpenAPI validation of a chart's individual rendered manifests — it shells out to the Helm SDK and lets Helm sequence the apply itself. A single release is safe.

**Consequence:** `infra/terraform/00-crds/` receives no Argo-related file from this dispatch. If a future task needs a Terraform-managed Workflow/WorkflowTemplate CR (not the current design — T027 submits at runtime), this decision would need revisiting.

## D-005/02 — Cross-namespace secret decision (closes D-002/D-06 Follow-up #7 for Argo): copy the SeaweedFS S3 credential into `orbital-drift-training` via Terraform

Argo's artifact-repository `secretKeySelector` fields are resolved by the workflow pod's own injected `wait`/`init` sidecar container at **pod admission time** — Kubernetes has no cross-namespace `secretKeyRef`. Since GPU training workflow pods run in `orbital-drift-training` (not `orbital-drift`, where T007's `seaweedfs.tf` creates the original Secret), the referenced Secret must exist in the pod's own namespace.

**Chosen:** a `data "kubernetes_secret_v1"` read of T007's secret (name/namespace from Terraform variables) plus a new `kubernetes_secret_v1.seaweedfs_s3_credentials_training` resource that copies the entire `.data` map verbatim into `orbital-drift-training`, referenced by `artifactRepository.s3.accessKeySecret`/`secretKeySecret`.

**Rejected alternatives, with reasons:**
- *Rely on cluster DNS only, no credential.* SeaweedFS's S3 gateway is not anonymous-write by default (T007's `seaweedfs.tf` explicitly configures named identities with `Admin`/`Read`/`Write` actions) — reachability alone does not solve authentication.
- *Fold training pods back into `orbital-drift`.* Would erase the namespace-level GPU-RBAC trust boundary this task exists to create (D-000/D-03) — the same reasoning D-002/D-06 already gives for keeping `orbital-drift-training` separate in the first place.

**Why this doesn't need broader controller RBAC:** the copy is created by Terraform, not read by the controller at runtime with elevated privilege — `controller.rbac.accessAllSecrets` stays `false` and `secretWhitelist` stays empty in `infra/helm-values/argo-workflows.yaml`. The controller's own service account never touches the original Secret in `orbital-drift`; only Terraform's own provider credentials (the operator's kubeconfig, at apply time) read it once via the `data` source.

Because the copied Secret's `.data` is passed through wholesale, this design does **not** need to know T007's exact key names to perform the copy — only the two `secretKeySelector.key` values (D-005/05 below) need the real names.

## D-005/03 — `hashicorp/helm` provider `3.x` syntax: `set` is a list of objects, `kubernetes` provider config is a nested object attribute, not a block (corroborates T008's own independent finding)

Verified directly against `terraform-provider-helm`'s `docs/resources/release.md` and `docs/index.md` at the pinned `v3.2.0` tag (not `main`), via `WebFetch`, before writing `argo_workflows.tf`:

- `set { name = ...; value = ... }` repeated blocks (2.x-era, still what most search results and training-era examples show) are **schema-invalid** against provider `3.2.0`. The correct form is `set = [ { name = ..., value = ... }, ... ]` — a single list-valued attribute.
- `provider "helm" { kubernetes { config_path = ... } } }` (block syntax) is likewise schema-invalid at `3.2.0`. The correct form is `provider "helm" { kubernetes = { config_path = ... } }` — a nested object attribute.

`infra/terraform/20-platform/argo_workflows.tf` uses the `3.x` forms throughout. This independently corroborates a finding T008 already made and documented in its own decision doc (fetched from the provider's `v3-upgrade-guide.md` there) — both dispatches converged on the same correction from different source documents, which is reassuring rather than redundant: `docs/decisions/002-infra-layout.md`'s own D-03 prose example (`provider "helm" { kubernetes { config_path = var.kubeconfig_path } }`) uses the **old**, now-incorrect block syntax and should be corrected there when that document is next reachable from a shared branch — not fixed by this PR, since D-002 is not this dispatch's file to edit.

Both T007's `infra/terraform/20-platform/providers.tf` and T008's `infra/terraform/20-platform/mlflow.tf` (both read directly) already use the corrected `3.x` object-attribute form for the `helm` provider — so this particular correction is not itself a live cross-dispatch mismatch, only a stale example worth fixing in D-002 eventually.

## D-005/04 — CONFIRMED (not hypothetical): `20-platform` is one shared Terraform root module across four dispatches, and Terraform forbids duplicate `provider`/`variable` declarations within one root module. Direct evidence from three separate object classes.

T008's own decision doc (`003-t008-mlflow-secret-wiring-findings.md`, D-003/04) already predicted this class of problem from first principles, before any other dispatch's file was visible to it. This dispatch had the unusual advantage of being able to *read* T007's and T008's actual files directly (see the provenance note above), which upgrades the prediction to a confirmed fact, and surfaces the exact shape of the collision:

1. **Provider blocks, duplicated.** T007's `infra/terraform/20-platform/providers.tf` declares `variable "kubeconfig_path"`, `provider "kubernetes" { config_path = var.kubeconfig_path }`, and `provider "helm" { kubernetes = { config_path = var.kubeconfig_path } }`. T008's `infra/terraform/20-platform/mlflow.tf` **independently declares the identical three items again**, in a different file, because — per its own header comment — "`infra/terraform/` was completely empty before this PR," i.e. T008 could not see T007's `providers.tf` at authoring time. Both copies are individually correct HCL; together, in the same root module, `terraform init -backend=false && terraform validate` will hard-fail with a duplicate-provider-configuration error the moment both land in the same branch.
2. **Cross-cutting variables, duplicated.** Both `providers.tf` (T007, only `kubeconfig_path`) and `mlflow.tf` (T008, `kubeconfig_path` **and** `orbital_drift_namespace`) declare overlapping `variable {}` blocks. `orbital_drift_namespace` and `seaweedfs_s3_secret_name` are furthermore now declared a **third** time by this dispatch's own `argo_workflows.tf` (see D-005/06 below on why this dispatch declared them anyway rather than omitting them).
3. **`terraform.tfvars.example` itself, independently authored three times.** T007's `lakefs.tf`-adjacent tfvars file, T008's `mlflow.tf`-adjacent tfvars file, and this dispatch's own `terraform.tfvars.example` are three separately-authored files at the same path. Reassuringly, the **values** for the genuinely shared keys (`orbital_drift_namespace = "orbital-drift"`, `seaweedfs_s3_secret_name = "orbital-drift-seaweedfs-s3-credentials"`) are byte-identical across all three — the disagreement is structural (three files claiming the same path) rather than a real disagreement about what the values should be, which should make manual reconciliation mechanical once someone with visibility into all branches does it.

**Recommendation (not a unilateral fix — flagged for `spec-guardian`/operator triage):** T007's `providers.tf`-per-stage-directory convention already exists, consistently, across all three of T007's own stage directories (`00-crds/providers.tf`, `10-storage/providers.tf`, `20-platform/providers.tf`) — adopt it as the canonical location for `20-platform`'s shared `provider` blocks **and** shared `variable` declarations (`kubeconfig_path`, `orbital_drift_namespace`, `seaweedfs_s3_secret_name`, and any others D-002's table names as multiply-consumed), and have T008's `mlflow.tf` (and this file) drop their own copies once all branches are visible to each other, keeping only each release's genuinely unique variables and its `helm_release` resource. This is the same "first-merged wins, subsequent copies deleted" resolution T008's own D-003/04 already proposed independently; this note agrees with it and adds the concrete detail that a canonical file (`providers.tf`) already exists to converge on, rather than needing to be invented.

This file (`argo_workflows.tf`) does **not** declare `provider`/`kubeconfig_path` itself, specifically to avoid making an already-confirmed two-way collision into a three-way one for that particular pair — but it does still declare `orbital_drift_namespace`/`seaweedfs_s3_secret_name`, per this dispatch's own explicit instruction to follow the same re-declaration pattern T008 used. That instruction predates this discovery; surfacing the now-concrete collision here rather than silently deviating from the instruction is the CLAUDE.md-compliant path ("do not improvise architecture").

## D-005/05 — CONCRETE cross-dispatch bug, not a hypothetical: T007's actual SeaweedFS secret uses `access_key_id` / `secret_access_key`; T008's `mlflow.tf` assumes `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for the *same secret*

T007's `infra/terraform/10-storage/seaweedfs.tf` (read directly) creates `kubernetes_secret_v1.seaweedfs_s3` with these `data` keys:

```hcl
data = {
  seaweedfs_s3_config = jsonencode({ ... })   # SeaweedFS's own chart consumption
  access_key_id        = var.seaweedfs_s3_admin_access_key_id
  secret_access_key     = var.seaweedfs_s3_admin_secret_access_key
}
```

T007's own comment states this explicitly: *"T008 (mlflow.tf) and T009 (airflow.tf) should point their own existingSecret.name at this same secret and use these same two discrete key names, not invent new ones."*

T008's `infra/terraform/20-platform/mlflow.tf` (read directly, authored before T007's file was visible to it) instead sets:

```hcl
{ name = "artifactRoot.s3.existingSecret.keyOfAccessKeyId",     value = "AWS_ACCESS_KEY_ID" },
{ name = "artifactRoot.s3.existingSecret.keyOfSecretAccessKey", value = "AWS_SECRET_ACCESS_KEY" },
```

**These do not match.** As currently authored, once both branches land, MLflow's `existingSecret` reference would look for keys that do not exist in the Secret T007 actually creates — a real `terraform apply`-time (or MLflow-pod-startup-time) failure, not a style nit. This dispatch's own artifact-repository wiring uses T007's **actual, confirmed** key names (`access_key_id` / `secret_access_key`, via `var.seaweedfs_s3_access_key_id_key` / `var.seaweedfs_s3_secret_access_key_key`, proposed defaults in `terraform.tfvars.example`), so Argo's own wiring is correct as authored — but T008's mismatch is not this dispatch's file to fix. **Recommendation:** either T008's `mlflow.tf` updates its two literal key-name strings to `access_key_id` / `secret_access_key` to match T007's actual secret, or T007 adds `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` as additional aliased keys in the same Secret. Flagged for `spec-guardian`/operator to choose; not resolved here.

## D-005/06 — SeaweedFS S3 endpoint / bucket-provisioning: same open question T007 and T008 already carry, now triply corroborated for the Service-name/port guess

T007's `lakefs.tf`-adjacent tfvars and T008's `mlflow.tf`-adjacent tfvars both independently propose `http://seaweedfs-s3.orbital-drift.svc.cluster.local:8333` as the SeaweedFS S3 gateway endpoint, both flagged **UNCONFIRMED** by their own authors (T008's own words: "a guess at Helm's fullname convention, not a confirmed SeaweedFS chart Service name"). This dispatch's `argo_workflows_s3_endpoint` variable reuses the identical host:port guess (stripped of the `http://` scheme, since Argo's `artifactRepository.s3` schema takes a bare `endpoint` plus a separate `insecure` boolean, unlike MLflow's boto3-style full-URL `MLFLOW_S3_ENDPOINT_URL`). Three independent dispatches converging on the same guess is somewhat stronger evidence than any one dispatch's guess alone, but it is still a guess, not a confirmation — genuinely resolvable only once T007's `seaweedfs.tf` is actually applied and `kubectl get svc -n orbital-drift` can be run (an on-cluster-only check, listed in this task's deliverable below).

Whether SeaweedFS auto-creates the `orbital-drift-argo-artifacts` bucket on first write, or needs an explicit pre-creation step, is the same open, on-cluster-only question T008 already raised for its own bucket — not resolved here either.

## D-005/07 — Residual RBAC gap, stated explicitly rather than implied

Native Kubernetes RBAC has no verb governing "which namespace may set `runtimeClassName: nvidia`" — `RuntimeClass` is cluster-scoped, and D-000/D-03 itself already documents that any pod anywhere in the cluster that can set `runtimeClassName: nvidia` plus an `NVIDIA_VISIBLE_DEVICES` env var can claim either GPU, bypassing scheduler accounting entirely. What this task's design actually provides is the weaker (but real, and the one D-000/D-03 itself names as acceptable) mitigation: least-privilege RBAC over *who may create Pods/Workflows in `orbital-drift-training` at all*, via the chart's own `workflow.rbac.create` + `controller.workflowNamespaces` mechanism. This is a namespace-level organizational and auditability boundary, not a technical gate against `nvidia` RuntimeClass use from an unrelated namespace. Closing that fully would require a `ValidatingAdmissionPolicy` or an OPA-style policy engine — new scope needing explicit operator sign-off, not introduced here, consistent with D-002/D-06's own reasoning for not adding new cross-namespace machinery without approval.

## D-005/08 — Major version drift, NOT adopted: chart `1.0.23`/app `v4.0.8` pinned as instructed; upstream has moved to a major version

`docs/decisions/versions.md` pins Argo Workflows at chart `1.0.23` / app `v4.0.8`. This dispatch's own instructions stated the orchestrator's session-time re-verification found the chart has since moved to **`2.0.0`** / app **`v4.1.0`** upstream — a major-version jump, unlike the minor/patch bumps T007 is adopting directly for lakeFS/SeaweedFS. Per explicit instruction, this PR pins the **old**, already-verified `1.0.23`/`v4.0.8` (re-confirmed directly by this dispatch via `WebFetch` against `Chart.yaml` at the `argo-workflows-1.0.23` tag) and does **not** silently adopt `2.0.0`. **Recorded here as a follow-up requiring explicit operator/decision-doc treatment before any future PR bumps this chart** — a major-version bump can carry breaking CRD schema changes, RBAC value renames, or behavior changes this dispatch has not researched, and Constitution IV's pinning discipline plus D-002's own precedent (treating even lakeFS's minor bump as something to record explicitly) both argue against an agent making that call unilaterally.

## D-005/09 — GPU-UUID ConfigMap: a scoped, minimal contribution to future GPU-workload authoring (T027), not an attempt to author the WorkflowTemplate itself

This task's charter notes "resource requests in your RBAC/workflow-template scaffolding must reflect [the 5060 Ti/5060 split]." T010's actual scope (per `specs/001-orbital-drift-ct/tasks.md`) is the controller/RBAC file, not `workflows/train.yaml` (T027, a separate, later task) — this file therefore does not attempt `runtimeClassName`/`NVIDIA_VISIBLE_DEVICES`/GPU resource-request wiring for training pods themselves. Its one concrete contribution toward the GPU split is `kubernetes_config_map_v1.gpu_uuids` in `orbital-drift-training`, holding both UUIDs under stable keys (`train-gpu-uuid`, `serve-gpu-uuid`), so T027's WorkflowTemplate can reference `valueFrom.configMapKeyRef` instead of a literal UUID committed to `workflows/train.yaml` (D-000/D-10's "GPU UUIDs must not be committed," which otherwise has no obvious enforcement point outside Terraform/`.tfvars`-shaped files).

---

## Not resolved here, flagged for whoever reviews next

1. The `20-platform` provider/variable-declaration collision (D-005/04) — a proposed resolution is stated, but not enacted across T007/T008's own files, which this dispatch cannot edit.
2. The SeaweedFS secret key-name mismatch between T007's actual `seaweedfs.tf` and T008's `mlflow.tf` (D-005/05) — a real bug, not yet fixed in either dispatch's own PR.
3. `argo_workflows_s3_endpoint`'s Service-name/port guess (D-005/06) — on-cluster-only to confirm, same open item T007/T008 already carry.
4. Whether SeaweedFS auto-creates the Argo artifact bucket or needs explicit pre-creation (D-005/06) — on-cluster-only.
5. The major-version drift to Argo Workflows `2.0.0`/`v4.1.0` (D-005/08) — needs an explicit operator/decision-doc call before ever bumping the pin.
6. `docs/decisions/002-infra-layout.md`'s D-03 prose example uses the stale, pre-3.x block syntax for the `helm` provider's `kubernetes` config (D-005/03) — worth a one-line correction there once that document is reachable from a shared branch.
