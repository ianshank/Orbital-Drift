# Orbital-Drift Roadmap

<!-- GENERATED FILE - DO NOT HAND-EDIT. Source of truth: src/orbital_drift/planning/roadmap_data.py. Regenerate: python -m orbital_drift.projections --write -->

Scope is owned by `specs/001-orbital-drift-ct/tasks.md`; this file is a
projection of `roadmap_data.py` and cannot disagree with it (the
`projections` CI stage byte-checks both projections).

## E0 Substrate (Highest)

Phase 0: repo+CI scaffold, host prep, k3s, GPU operator, platform charts, bring-up. Exit: plan.md Phase-0 gate.

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S0.2 Host-prep runbook (driver+CUDA, Blackwell) | 3 | Highest | AC: docs/runbooks/00-host-prep.md merged with pinned driver/operator versions and verification block. Trace: T002. |
| S0.1a Coverage gate plus CI defect fixes | 5 | High | AC: the coverage stage enforces FR-011a as one combined statement+branch rate, threshold pinned in ci/versions.env, with positive controls proving it fails a run whose tests all pass. Trace: T001a. |
| S0.1b terraform fmt pre-commit hook | 3 | Medium | AC: FR-011b canonical Terraform formatting runs as a pre-commit hook and in the hooks CI stage against a digest-pinned image, with positive, negative and guard-the-guard controls. Trace: T001b. |
| S0.4 k3s install runbook | 3 | High | AC: docs/runbooks/01-k3s-install.md merged (single node, GPU labels, nvidia runtime). Trace: T004. |
| S0.4a containerd config-v3 template | 3 | High | AC: infra/k3s/config-v3.toml.tmpl encodes the nvidia containerd runtime stanza and keeps the NRI plugin off, per D-000/D-02b. Trace: T004a. |
| S0.6 GPU Operator values + Terraform | 5 | High | AC: pinned infra/helm-values/gpu-operator.yaml + infra/terraform/gpu_operator.tf; AUTHORED-PROVISIONAL until re-reviewed against T003/T005 verification blocks. Trace: T006. |
| S0.7 SeaweedFS + lakeFS + CloudNativePG infra | 5 | High | AC: pinned values + Terraform releases under infra/ per D-000/D-04+D-05. Trace: T007. |
| S0.8 MLflow infra | 3 | High | AC: pinned values + Terraform; community chart 1.11.4 per D-000/D-05b; S3 artifact store on SeaweedFS. Trace: T008. |
| S0.9 Airflow infra | 3 | High | AC: official chart, KubernetesExecutor, git-sync DAG deployment; pinned values + Terraform. Trace: T009. |
| S0.10 Argo Workflows infra | 3 | High | AC: pinned values + Terraform; GPU RBAC + training-namespace service account. Trace: T010. |
| S0.11 Platform bring-up runbook | 3 | High | AC: docs/runbooks/02-platform-bringup.md with per-component validation; requires T006-T010 review-APPROVED. Trace: T011. |

## E1 Ingestion and Data Lifecycle (High)

Phase 1 (US1, US2): STAC client, tile store, catalog, lakeFS flow, ingest DAG. Exit: plan.md Phase-1 gate.

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S1.1 STAC client contract tests (failing) | 3 | High | AC: AOI query, pagination, band resolution tests exist against recorded fixtures and fail before implementation. Trace: T013. |
| S1.2 Tile store + lakeFS contract tests (failing) | 3 | High | AC: tile store I/O + lakeFS commit flow tests exist and fail before implementation. Trace: T014. |
| S1.3 config.py via pydantic-settings | 3 | High | AC: AOI, bands, thresholds, cadence, endpoints all config-sourced (Constitution III); no magic numbers. Trace: T015. |
| S1.4 STAC client implementation | 5 | High | AC: Earth Search sentinel-2-l2a queries with retry/backoff budget; contract tests green. Trace: T016. |
| S1.5 Tile store + cloud mask | 5 | High | AC: SCL mask, per-scene cloud fraction, windowed COG reads, read-throughput micro-benchmark logged. Trace: T017. |
| S1.6 Local STAC catalog | 3 | Medium | AC: catalog writer + query API green against contract tests. Trace: T018. |
| S1.7 lakeFS ops module | 3 | High | AC: commit-per-ingest, branch-per-experiment, snapshot pinning; contract tests green. Trace: T019. |
| S1.8 Ingest DAG | 5 | High | AC: scheduled, idempotent, bounded backfill; smoke test in tests/smoke/. Trace: T020. |
| S1.9 Ingest operations runbook | 2 | Medium | AC: docs/runbooks/03-ingest-ops.md incl. STAC outage response. Trace: T021. |

## E2 Training and Registry (High)

Phase 2 (US3): labels, dataset, baseline+finetune training, MLflow registry. Exit: plan.md Phase-2 gate.

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S2.1 Training contract tests (failing) | 3 | High | AC: entrypoint interface, MLflow logging contract, registry transitions tested and failing first. Trace: T023. |
| S2.2 Label bootstrap | 3 | Medium | AC: public land-cover weak labels for AOI with documented caveats (D-04). Trace: T024. |
| S2.3 Dataset assembly | 3 | High | AC: pinned lakeFS snapshot to torchgeo patches. Trace: T025. |
| S2.4 Baseline training entrypoint | 5 | High | AC: U-Net/ResNet50 with AMP + grad-accum, IoU/F1 eval, MLflow logs {lakeFS commit, git SHA, config hash}. Trace: T026. |
| S2.5 Argo training workflow | 3 | High | AC: preprocess-train-eval-register(Staging) with GPU requests for 5060 Ti. Trace: T027. |
| S2.6 Registry ops | 3 | High | AC: promote/archive/rollback as MLflow stage transitions; unit tests green. Trace: T028. |
| S2.8 Foundation-model spike (doc only) | 2 | Medium | AC: docs/decisions/fm-selection.md recommends Clay vs Prithvi-EO with fine-tune config for 16GB. Trace: T030. |
| S2.9 Fine-tune entrypoint | 5 | Medium | AC: per T030 recommendation; baseline-beats gate encoded in eval. Trace: T031. |

## E3 CT Loop (High)

Phase 3 (US4, US5): drift metrics, trigger, drift/retrain DAGs, shadow eval, promotion. Exit: plan.md Phase-3 gate.

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S3.1 Drift contract tests (failing) | 3 | High | AC: drift API, trigger idempotency, hysteresis on synthetic sequences tested and failing first. Trace: T033. |
| S3.2 Reference-stats builder | 3 | High | AC: reference stats from training snapshot. Trace: T034. |
| S3.3 Drift metrics via standard libs | 3 | High | AC: PSI/KS per band + prediction-class shift via off-the-shelf libs only (Constitution II); Prometheus export. Trace: T035. |
| S3.4 Trigger emitter | 3 | High | AC: hysteresis window + cooldown + queue-depth-1 coalescing. Trace: T036. |
| S3.5 Drift DAG | 3 | High | AC: post-ingest sensor to compute/export/maybe-trigger; starvation vs shift distinguished. Trace: T037. |
| S3.6 Retrain DAG + shadow eval | 5 | High | AC: trigger-snapshot-train-shadow-eval-gated-promotion; auto vs operator-approve from config. Trace: T038. |
| S3.7 CT ops + rollback runbooks | 2 | Medium | AC: docs/runbooks/04-ct-ops.md + 05-rollback.md. Trace: T039. |

## E4 Serving and Canary (Medium)

Phase 4 (US6): FastAPI stage-loader, canary split, per-version metrics. Exit: plan.md Phase-4 gate.

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S4.1 Serving contract tests (failing) | 3 | Medium | AC: serving API, stage-loader, canary split tested and failing first. Trace: T041. |
| S4.2 FastAPI serving app | 5 | Medium | AC: loads by registry stage; canary ratio from config; per-version Prometheus metrics. Trace: T042. |
| S4.3 Serving deployment manifests | 3 | Medium | AC: 8GB-GPU deployment with readiness/liveness and single-config revert. Trace: T043. |
| S4.4 Canary operations runbook | 2 | Medium | AC: docs/runbooks/06-canary.md incl. regression response. Trace: T044. |

## E5 Observability and Soak (High)

Phase 5 (US7, US8): dashboards, alerts, runbooks, rebuild drill, 6-week soak. Exit: plan.md Phase-5 gate, operator sign-off only.

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S5.1 kube-prometheus-stack + alert routes | 3 | High | AC: pinned values + Terraform; DAG-failure, drift-trigger, canary-regression routes. Trace: T046. |
| S5.2 Grafana dashboards as code | 3 | High | AC: DAG health, Argo states, GPU util/mem/temp, drift series, serving per-version under dashboards/. Trace: T047. |
| S5.3 Remaining runbooks + templates | 3 | High | AC: rebuild, GPU-operator recovery, scheduler failure, storage recovery runbooks; postmortem + soak-log templates. Trace: T048. |

## E6 Reconciliation and Integration Hardening (High)

Phase 6 (RB-012): defects found in already-remediated code, RB-010 findings assigned to no part, adapter convergence, gate integrity. Exit: T057 complete and roadmap Tracks A-E closed.

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S6.1 Serving startup wiring and a healthy container | 5 | High | AC: a production model is loaded outside tests, /healthz reports ok in the shipped image, and the Dockerfile port env names match the config fields they claim to set. Trace: T053. |
| S6.2 Structured-logging rollout and message redaction | 5 | High | AC: configure_logging runs at every production entrypoint and credential redaction covers the message path, not only extra= fields. Trace: T054. |
| S6.3 Real request-body size limit | 3 | Medium | AC: an oversized /predict body is rejected before it is read and parsed, proven by a test that measures the allocation rather than the comparison operator. Trace: T055. |
| S6.4 Honest lakeFS simulation | 3 | High | AC: commit ids are deterministic for a given scene, and every log line naming a lakeFS object says SIMULATED until a real client exists. Trace: T056. |
| S6.5 Retroactive review of T013-T052 | 8 | Highest | AC: every T013-T052 task carries a recorded spec-guardian and adversarial-reviewer outcome, and the tasks that pass are checked off. Required by RB-010 and owned by no part until RB-012. Trace: T057. |
| S6.6 Close the import-linter contract hole | 3 | High | AC: a port importing its own concrete counterpart breaks a contract, and a positive control proves lint-imports exits non-zero on a planted violation. Trace: T058. |
| S6.7 Real MLflow adapter behind ModelRegistryPort | 5 | Medium | AC: gated on the operator adapter-disposition decision; one port-conformance suite pins both the in-memory fake and the adapter. Trace: T059. |
| S6.8 Real lakeFS adapter behind DataVersionPort | 5 | Medium | AC: gated on the same operator decision; depends on T056 and on a composition root existing. Trace: T060. |
| S6.9 Close the D-012 config-wiring gaps | 3 | Medium | AC: F1 through F5 of docs/decisions/012 are wired, drift/trigger.py first since its config fields already exist and name that file in their own descriptions. Trace: T061. |
| S6.10 Lock the registry rollback path | 2 | Medium | AC: rollback_production either takes the lock or transition_stage stops promising an invariant it cannot hold. Trace: T062. |
| S6.11 Fix the ECE weight shape mismatch | 3 | High | AC: calibration_error asserts its weights and deviations are the same shape instead of letting numpy broadcast, expected calibration error is bounded in zero to one for every input, and the Hypothesis property test stops reddening CI intermittently. Trace: T063. |

## E7 Operator and Decision Gates (Highest)

Operator-side critical path: HUMAN tasks, phase gates G-x, DEC sign-offs (Constitution I/VI).

| Story | Points | Priority | Acceptance |
|---|---|---|---|
| S0.3 Execute host prep on node A | - | Highest | AC: runbook verification block filled with measured driver/CUDA versions; G-1 logged. Owner story. Trace: T003. |
| S0.5 Install k3s on node A | - | Highest | AC: k3s up per runbook; kubeconfig location note committed; G-2 logged. Owner story. Trace: T005. |
| S0.12 Apply platform on cluster | - | Highest | AC: plan.md Phase-0 gate demonstrated (nvidia-smi in pod, Airflow UI, Argo GPU hello-world, lakeFS repo, MLflow UI); G-3 logged. Owner story. Trace: T012. |
| S1.10 Observe two unattended ingests | - | High | AC: plan.md Phase-1 gate (two scheduled real-scene ingests) observed; G-4 logged. Owner story. Trace: T022. |
| S2.7 Baseline workflow run + reproducibility | - | High | AC: US2 reproducibility acceptance verified; wall-clock + GPU util recorded. Owner story. Trace: T029. |
| S2.10 Fine-tune run + gate demo | - | High | AC: plan.md Phase-2 gate; baseline-beats gate shown in both directions. Owner story. Trace: T032. |
| S3.8 Forced-drift E2E + rollback drill | - | High | AC: plan.md Phase-3 gate; rollback drill under 10 min (SC-004). Owner story. Trace: T040. |
| S4.5 Canary regression demo | - | Medium | AC: plan.md Phase-4 gate (canary regression alert + revert). Owner story. Trace: T045. |
| S5.4 Deploy observability + alert drills | - | High | AC: all three alert classes fire in drills. Owner story. Trace: T049. |
| S5.5 P40 node join (optional lesson) | - | Low | AC: training job scheduled to node B; heterogeneous-GPU pain documented as incident (R-05). Owner story. Trace: T050. |
| S5.6 Rebuild-runbook verification | - | High | AC: platform torn down and rebuilt once from docs (SC-006). Owner story. Trace: T051. |
| S5.7 Six-week soak | - | Highest | AC: Constitution VI definition of done - 6 weeks operated, 1 organic drift retrain, 3 incident postmortems, 1 rollback drill; only the operator marks done. Owner story. Trace: T052. |
