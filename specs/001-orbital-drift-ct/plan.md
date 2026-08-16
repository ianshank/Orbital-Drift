# Implementation Plan: Orbital-Drift CT Pipeline

**Branch**: `001-orbital-drift-ct` | **Spec**: `specs/001-orbital-drift-ct/spec.md`

## Summary
Continuous Training pipeline for Sentinel-2 change detection on a home k3s cluster: Airflow-scheduled ingestion → lakeFS-versioned data → Argo-executed GPU training → MLflow registry → drift-triggered retrain with shadow eval, staged promotion, canary serving, and rollback. Agents author; the operator applies and operates (Constitution I).

## Technical Context
- **Cluster**: k3s (current stable) — node A: dual-GPU desktop (RTX 5060 Ti 16GB = primary trainer; RTX 5060 8GB = serving/aux). Optional node B: Tesla P40 24GB, added in Phase 5 deliberately to practice heterogeneous GPU scheduling (mixed driver/arch pain is the lesson, not a bug). NVIDIA GPU Operator; verify Blackwell (50-series) driver/CUDA compatibility on host first — [risk R-05].
- **Storage**: SeaweedFS (S3 API) on cluster; lakeFS over it for data versioning; Postgres via CloudNativePG backing Airflow, MLflow, lakeFS. (Was MinIO + Bitnami Postgres — see `docs/decisions/000` items **D-000/D-05** and **D-000/D-04**: MinIO's upstream archived 2026-04-25; Bitnami's public versioned images were deleted 2025-09-29, leaving `latest`-only, which violates Principle IV.)

> **Decision-ID namespaces.** This file's own Decision Log below uses `D-01…D-05`. `docs/decisions/000-phase0-technical-decisions.md` uses an independent `D-01…D-11` series. They collide. Cross-references to the decisions document are always written **`D-000/D-nn`**; a bare `D-nn` in this file means this file's Decision Log.
- **Orchestration**: Airflow via official Helm chart, KubernetesExecutor (each task = pod: forces real K8s fluency). Argo Workflows for training (Airflow triggers Argo via API — the cross-system handoff is JD-relevant).
- **ML**: Python 3.12, PyTorch 2.x, torchgeo. Baseline: U-Net/ResNet50 segmentation on 10m bands. Fine-tune target: Clay or Prithvi-EO geospatial foundation model (final selection = task T030 research spike; both are torchgeo/HF-accessible). AMP + gradient accumulation for 16GB.
- **Data**: Earth Search STAC (Element84), collection `sentinel-2-l2a`, AWS Open Data. AOI: operator-chosen, ~1–4 MGRS tiles. Labels: bootstrap from ESA WorldCover / Dynamic World-style public land-cover rasters for the AOI (weak labels are acceptable — the pipeline is the product, not SOTA accuracy).
- **Drift**: Evidently (or equivalent standard lib) — PSI/KS per band + prediction-distribution shift. No bespoke metrics (Constitution II).
- **Serving**: FastAPI + registry-stage loading + canary ratio; KServe = stretch goal, documented migration path only.
- **Observability**: kube-prometheus-stack, dashboards-as-code in `dashboards/`, Alertmanager → operator notification.
- **IaC**: Helm values + Terraform (providers: helm, kubernetes) for everything above the OS; all pinned.

## Constitution Check
- I: every apply-step in tasks.md is `[HUMAN]` with an agent-authored runbook pair. PASS by construction.
- II: stack fixed above; spec-guardian blocks imports from prior harness repos. PASS.
- III–V, VII: enforced via CI gates defined in Phase 1. PASS.
- VI: Phase 5 completion reserved to operator. PASS.

## Project Structure
```
orbital-drift/
├── .specify/memory/constitution.md
├── specs/001-orbital-drift-ct/{spec.md,plan.md,tasks.md}
├── .claude/agents/            # 7 subagents (see CLAUDE.md)
├── .claude/settings.json      # harness-level Principle I deny-rules
├── CLAUDE.md                  # orchestration + delegation rules
├── README.md                  # bootstrap: one documented command path (Principle IV)
├── pyproject.toml             # Python 3.12, ruff + mypy config, [dev] pins
├── .gitattributes             # eol=lf — authored on Windows, executed on Linux (D-10)
├── .gitignore                 # public repo: state, tfvars, kubeconfig, .env
├── .env.example               # host-specific values the repo deliberately omits (D-000/D-10)
├── .pre-commit-config.yaml    # ruff, mypy, gitleaks, shellcheck (Principle VII)
├── infra/
│   ├── terraform/             # helm releases: airflow, argo, mlflow, lakefs, seaweedfs, cnpg, kube-prometheus
│   ├── helm-values/           # pinned values per chart
│   └── k3s/                   # k3s config artifacts (config-v3.toml.tmpl per D-000/D-02b) — T004
├── dags/                      # Airflow: ingest, drift, retrain-trigger
├── workflows/                 # Argo: train, shadow-eval  (promotion is a dags/retrain.py step, not a workflow — T038)
├── src/orbital_drift/
│   ├── config.py              # pydantic-settings (Constitution III) — T015
│   ├── ingest/                # STAC client, tile store, cloud mask, catalog
│   ├── data/                  # lakeFS ops, dataset assembly, label bootstrap
│   ├── train/                 # baseline + finetune entrypoints, eval
│   ├── drift/                 # metrics, hysteresis, trigger emitter
│   ├── registry/              # MLflow promotion/rollback ops
│   └── serve/                 # FastAPI app, canary, model loader
├── tests/{unit,contract,smoke}/
├── dashboards/                # Grafana JSON
├── docs/{runbooks,incidents,soak-log,decisions}/   # decisions/ per CLAUDE.md + T030
├── ci/                        # gate logic (checks.sh), version pins, gitleaks config
└── .github/workflows/         # thin caller — invokes `sh ci/checks.sh <stage>`, no gate logic
```

## Phases
- **Phase 0 — Substrate** (operator-heavy): repo + CI + gitleaks; host GPU driver validation; k3s up; GPU operator; SeaweedFS/lakeFS/CloudNativePG/MLflow/Airflow/Argo deployed. Gate: `nvidia-smi` inside a pod; hello-world DAG and hello-world Argo GPU job green.
- **Phase 1 — Ingestion & data lifecycle** (US1, US2): contract tests → STAC client → tile store + SCL cloud mask → local catalog → lakeFS commit flow → ingest DAG. Gate: 2 real scenes ingested unattended on schedule.
- **Phase 2 — Training & registry** (US3): label bootstrap → dataset assembly from lakeFS snapshot → baseline training workflow → MLflow logging/registration → fine-tune workflow → baseline-beats gate. Gate: reproducibility check (US2 acceptance) passes.
- **Phase 3 — CT loop** (US4, US5): drift service + reference stats → drift DAG → trigger emitter with hysteresis/cooldown → retrain DAG → shadow eval → gated promotion → rollback procedure + drill. Gate: forced-drift end-to-end demo (inject shifted scenes) completes trigger→promotion; rollback drill < 10 min.
- **Phase 4 — Serving & canary** (US6): FastAPI stage-loader → canary split → per-version metrics. Gate: canary regression auto-alert demonstrated.
- **Phase 5 — Observability & soak** (US7, US8): dashboards, alerts, runbooks, incident templates, P40 node join (optional), rebuild-runbook verification, then the 6-week soak. Gate: SC-001…SC-006 — **operator sign-off only**.

## Risks
- **R-01** VRAM OOM on fine-tune → AMP + grad-accum config first-class; baseline model is the fallback deliverable.
- **R-02** Drift flapping / trigger storms → hysteresis + cooldown in FR-007; tested with synthetic sequences.
- **R-03** STAC/AWS rate limits or schema drift → pinned client, retry budget, contract tests against recorded fixtures.
- **R-04** Home-lab availability (power/ISP) → idempotent DAGs, bounded backfill, UPS optional.
- **R-05** Blackwell driver / GPU-operator mismatch on 50-series → validate host CUDA stack before k3s (**T002** authors the host-prep runbook, **T003** executes it; an earlier revision cited T004, which is the k3s install runbook and runs *after* the CUDA stack is proven); pin operator version known-good; P40 (older arch) isolated to Phase 5 on purpose.
- **R-06** Scope creep toward operator's prior harness work → Constitution II + spec-guardian; any "improve the drift math" impulse becomes a docs/ideas note, not code.

## Decision Log
- **D-01 lakeFS over DVC**: branch/commit semantics on the object store better match "versioning strategies for massive datasets" (JD language) and keep versioning server-side; DVC noted as the lighter alternative.
- **D-02 Argo over Kubeflow Pipelines**: lighter footprint on a home cluster; Airflow+Argo split mirrors the JD's orchestrator plurality. Kubeflow named as read-and-compare, not install.
- **D-03 FastAPI before KServe**: serving mechanics first, platform abstraction second; KServe documented as migration.
- **D-04 Weak public labels**: WorldCover-style bootstrap accepted; accuracy is not the graded axis, the loop is.
- **D-05 Two Airflow/Argo systems instead of one**: deliberate — the cross-orchestrator handoff (Airflow sensor/API → Argo submit → status poll) is a JD-relevant skill.
