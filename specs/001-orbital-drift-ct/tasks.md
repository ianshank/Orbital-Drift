# Tasks: Orbital-Drift CT Pipeline

**Input**: plan.md, spec.md | **Prerequisite**: constitution.md read by all agents

Tag legend:
- `[P]` — parallelizable with adjacent `[P]` tasks (different files, no dependency)
- `[HUMAN]` — operator-only. Agents STOP, hand off the paired runbook, and wait. Never executed by an agent (Constitution I).
- `[A:name]` — owning subagent. Every agent artifact passes `spec-guardian` then `peer-reviewer` before its task is checked off (see CLAUDE.md).

## Phase 0 — Substrate
- [x] T001 [A:infra-scaffolder] Scaffold repo per plan.md structure; pre-commit (ruff, mypy, gitleaks, shellcheck); CI skeleton in `ci/` running **lint, type-check, unit, contract, DAG smoke, gitleaks** — all six of FR-011, not four. (Amended: the original four-stage text was a lossy paraphrase of FR-011, which names six verbatim; Constitution V requires the same. A task cannot narrow a functional requirement it exists to implement. Contract and smoke suites are declared now and arm automatically when T013/T014/T020 land a test module.) Plus a non-FR-011 `hooks` stage that runs the pre-commit config in CI, so those hooks are enforced beyond machines where `pre-commit install` was run — Constitution VII requires gitleaks as pre-commit hook **and** CI gate, which a config nobody executes does not satisfy.
- [ ] T002 [A:runbook-writer] Runbook: host prep — NVIDIA driver + CUDA validation for RTX 50-series (Blackwell), `docs/runbooks/00-host-prep.md`. Include known-good driver/operator version pins (research current; R-05).
- [ ] T003 [HUMAN] Execute host prep on node A; record driver/CUDA versions in the runbook's verification block.
- [ ] T004 [A:runbook-writer] Runbook: k3s install (single node, GPU node labels, containerd nvidia runtime), `docs/runbooks/01-k3s-install.md`.
- [ ] T005 [HUMAN] Install k3s on node A per runbook; commit kubeconfig location note (not the kubeconfig) to docs.
- [ ] T006 [P] [A:infra-scaffolder] GPU Operator Helm values (pinned) `infra/helm-values/gpu-operator.yaml` + Terraform release `infra/terraform/gpu_operator.tf`.
- [ ] T007 [P] [A:infra-scaffolder] SeaweedFS + lakeFS + CloudNativePG values and Terraform releases (pinned) under `infra/`. (Was MinIO + Postgres — see D-000/D-05, D-000/D-04.)
- [ ] T008 [P] [A:infra-scaffolder] MLflow (tracking+registry, S3 artifact store on SeaweedFS) values + Terraform. Chart: community `1.11.4` per D-000/D-05b.
- [ ] T009 [P] [A:infra-scaffolder] Airflow (official chart, KubernetesExecutor) values + Terraform; DAG deployment via git-sync.
- [ ] T010 [P] [A:infra-scaffolder] Argo Workflows values + Terraform; GPU RBAC + service account for training namespace.
- [ ] T011 [A:runbook-writer] Runbook: platform bring-up order + validation checks per component, `docs/runbooks/02-platform-bringup.md`.
- [ ] T012 [HUMAN] `terraform apply` platform per runbook; validate: `nvidia-smi` in a test pod, Airflow UI reachable, Argo hello-world GPU job succeeds, lakeFS repo `orbital-drift` created, MLflow UI up. Log any deviations as incident #0 practice entry.

## Phase 1 — Ingestion & Data Lifecycle (US1, US2)
- [ ] T013 [A:pipeline-engineer] Contract tests (failing) for STAC client against recorded fixtures: AOI query, pagination, band asset resolution — `tests/contract/test_stac_client.py`.
- [ ] T014 [P] [A:pipeline-engineer] Contract tests (failing) for tile store I/O + lakeFS commit flow — `tests/contract/test_tile_store.py`.
- [ ] T015 [A:ml-engineer] `config.py` with pydantic-settings: AOI, bands, cloud threshold, cadence, endpoints, thresholds (Constitution III).
- [ ] T016 [A:pipeline-engineer] STAC client (`src/orbital_drift/ingest/stac_client.py`): Earth Search `sentinel-2-l2a`, retry/backoff budget.
- [ ] T017 [A:pipeline-engineer] Tile store + SCL cloud mask + per-scene cloud fraction (`ingest/tile_store.py`, `ingest/cloud.py`); windowed COG reads; write a micro-benchmark harness logging read throughput (JD talking point).
- [ ] T018 [A:pipeline-engineer] Local STAC catalog writer + query API (`ingest/catalog.py`).
- [ ] T019 [A:pipeline-engineer] lakeFS ops module (`data/lakefs_ops.py`): commit-per-ingest, branch-per-experiment, snapshot pinning.
- [ ] T020 [A:pipeline-engineer] Ingest DAG `dags/ingest.py`: schedule on cadence → query → fetch bands+SCL → mask/flag → store → catalog → lakeFS commit; idempotent; bounded backfill. DAG smoke test in `tests/smoke/`.
- [ ] T021 [A:runbook-writer] Runbook: ingest operations + STAC outage response, `docs/runbooks/03-ingest-ops.md`.
- [ ] T022 [HUMAN] Deploy DAG (git-sync merge), observe two scheduled unattended ingests of real scenes. Phase gate.

## Phase 2 — Training & Registry (US3)
- [ ] T023 [A:ml-engineer] Contract tests (failing): training entrypoint interface, MLflow logging contract, registry stage transitions — `tests/contract/test_training.py`.
- [ ] T024 [A:ml-engineer] Label bootstrap from public land-cover raster for AOI (`data/labels.py`), documented weak-label caveats (D-04).
- [ ] T025 [A:ml-engineer] Dataset assembly from pinned lakeFS snapshot → tiles/patches for torchgeo (`data/dataset.py`).
- [ ] T026 [A:ml-engineer] Baseline U-Net/ResNet50 training entrypoint (`train/baseline.py`): AMP, grad-accum, IoU/F1 eval, MLflow logging of {lakeFS commit, git SHA, config hash}.
- [ ] T027 [P] [A:infra-scaffolder] Argo workflow `workflows/train.yaml`: preprocess → train → eval → register(Staging); GPU resource requests targeting 5060 Ti.
- [ ] T028 [P] [A:ml-engineer] Registry ops (`registry/ops.py`): promote, archive, rollback as MLflow stage transitions; unit tests.
- [ ] T029 [HUMAN] Submit baseline workflow; verify US2 reproducibility acceptance (re-run within tolerance). Record wall-clock + GPU util.
- [ ] T030 [A:ml-engineer] Research spike (doc only): Clay vs Prithvi-EO for this AOI/bands/16GB — recommendation with fine-tune config, `docs/decisions/fm-selection.md`.
- [ ] T031 [A:ml-engineer] Fine-tune entrypoint (`train/finetune.py`) per T030; baseline-beats gate encoded in eval step.
- [ ] T032 [HUMAN] Run fine-tune workflow; confirm baseline-beats gate behavior in both directions (force a fail once). Phase gate.

## Phase 3 — CT Loop (US4, US5)
- [ ] T033 [A:drift-engineer] Contract tests (failing): drift API, trigger idempotency, hysteresis behavior on synthetic sequences — `tests/contract/test_drift.py`.
- [ ] T034 [A:drift-engineer] Reference-stats builder from training snapshot (`drift/reference.py`).
- [ ] T035 [A:drift-engineer] Drift metrics via standard lib (PSI/KS per band, prediction-class shift) (`drift/metrics.py`); Prometheus export. No bespoke math (Constitution II).
- [ ] T036 [A:drift-engineer] Trigger emitter with hysteresis window + cooldown + queue-depth-1 coalescing (`drift/trigger.py`).
- [ ] T037 [A:pipeline-engineer] Drift DAG `dags/drift.py`: post-ingest sensor → compute → export → maybe-trigger; starvation vs shift distinction (spec edge case).
- [ ] T038 [A:pipeline-engineer] Retrain DAG `dags/retrain.py`: trigger → snapshot → submit Argo train → shadow-eval workflow `workflows/shadow_eval.yaml` → gated promotion (config: auto vs operator-approve).
- [ ] T039 [A:runbook-writer] Runbooks: forced retrain, promotion approval, rollback (`docs/runbooks/04-ct-ops.md`, `05-rollback.md`).
- [ ] T040 [HUMAN] Forced-drift E2E demo: inject shifted scenes, watch trigger → retrain → shadow eval → promotion. Then execute rollback drill; must complete < 10 min (SC-004). Phase gate.

## Phase 4 — Serving & Canary (US6)
- [ ] T041 [A:ml-engineer] Contract tests (failing): serving API, stage-loader, canary split — `tests/contract/test_serving.py`.
- [ ] T042 [A:ml-engineer] FastAPI serving app (`serve/app.py`): loads by registry stage, canary ratio from config, per-version Prometheus metrics.
- [ ] T043 [A:infra-scaffolder] Serving deployment manifests/values on the 8GB GPU; readiness/liveness; single-config revert path.
- [ ] T044 [A:runbook-writer] Runbook: canary operations + regression response, `docs/runbooks/06-canary.md`.
- [ ] T045 [HUMAN] Deploy serving; demonstrate canary regression alert → revert. Phase gate.

## Phase 5 — Observability & Soak (US7, US8)
- [ ] T046 [P] [A:infra-scaffolder] kube-prometheus-stack values + Terraform; Alertmanager routes (DAG failure, drift trigger, canary regression).
- [ ] T047 [P] [A:drift-engineer] Grafana dashboards-as-code: DAG health, Argo states, GPU util/mem/temp, drift series with thresholds, serving per-version — `dashboards/`.
- [ ] T048 [A:runbook-writer] Remaining runbooks: cluster rebuild, GPU-operator recovery, Airflow scheduler failure, lakeFS/SeaweedFS recovery; incident postmortem template; weekly soak-log template — `docs/`.
- [ ] T049 [HUMAN] Deploy observability; verify all three alert classes fire in drills.
- [ ] T050 [HUMAN] (Optional, recommended) Join Tesla P40 as node B; schedule a training job to it; document the heterogeneous-GPU pain in an incident entry. (Deliberate lesson, R-05.)
- [ ] T051 [HUMAN] Rebuild-runbook verification: tear down and rebuild platform once from docs (SC-006).
- [ ] T052 [HUMAN] Run the 6-week soak: weekly logs, incident postmortems, capture ≥1 organic drift retrain. Only the operator may mark this task — and the project — done (Constitution VI).

## Dependencies (summary)
Phase 0: T001 → T002 → **[T004, T006–T010 may proceed in parallel with the T003/T005 hardware gates]** → T011 → T012. The `[HUMAN]` gates T003 and T005 block *execution*; authoring may proceed alongside them. T011 still requires T006–T010 to be review-APPROVED (not merely drafted), since it documents bring-up order for the final artifacts. T012 requires everything. Operator-approved 2026-08-09.

⚠ **T006 is AUTHORED-PROVISIONAL.** An earlier revision of this section claimed "no agent artifact in T004 or T006–T010 depends on the cluster existing." **That was wrong** — `docs/decisions/000-phase0-technical-decisions.md` records four couplings to T003, three of which land directly in T006's GPU Operator values:

| Coupling | Resolved at | Lands in |
|---|---|---|
| GPU UUIDs (`nvidia-smi -L`) | T003 | T006/T010 workload manifests |
| DCGM field support on consumer Blackwell (D-000/D-11) | T003 (`dcgmi dmon -e 203,252,150`) | T006 `dcgmExporter` counters CSV |
| `RUNTIME_CONFIG_SOURCE=file` on k3s + containerd 2.2.5-k3s2 (D-000/D-02) | T005 | T006 `toolkit.env` |
| GPU Operator 26.3.3 vs driver branch 610 (validated list stops at 595.71.05) | T003 | T006 chart pin viability |

**T006 must therefore be re-reviewed against T003's and T005's verification blocks before T011 may cite it.** T004 and T007–T010 carry no GPU coupling and are unconditionally parallel.

All host-specific values are parameterised per Principle III and D-10 — GPU UUIDs use the names already defined in `.env.example`: `ORBITAL_DRIFT_TRAIN_GPU_UUID` and `ORBITAL_DRIFT_SERVE_GPU_UUID`. Never literals; the repo is public.

(Original text: "Phase 0 strictly ordered T001→T012 except [P] block T006–T010.") Phase 1: T013–T015 before T016+; T020 needs T016–T019. Phase 2: T023–T025 before T026; T031 needs T029+T030. Phase 3: T033 before T034–T036; T038 needs T036+T027. Phase 4 needs Phase 2. Phase 5 soak (T052) needs all gates passed.
