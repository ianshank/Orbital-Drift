# Feature Specification: Orbital-Drift — Self-Retraining Sentinel-2 Change-Detection System

**Feature Branch**: `001-orbital-drift-ct`
**Status**: Draft
**Input**: Build a Continuous Training (CT) pipeline that ingests Sentinel-2 imagery for a configured AOI on its revisit cadence, trains/serves a land-cover segmentation model, detects real seasonal/atmospheric drift, and automatically retrains, shadow-evaluates, promotes, and (when needed) rolls back — all on a home k3s cluster, operated by a single human.

## Actors
- **Operator** (human): applies infra, responds to incidents, approves promotions in Phase 3, owns the soak.
- **Scheduler** (Airflow): time/cadence-driven DAGs.
- **Training executor** (Argo Workflows): GPU training jobs.
- **Registry** (MLflow): experiment tracking + model stages.
- **Drift monitor**: computes input/prediction drift, raises retrain triggers.
- **Serving layer**: inference endpoint with canary + rollback.

## User Stories

### US1 — Ingestion (P1)
As the operator, I want new Sentinel-2 L2A scenes for my AOI ingested automatically within 24h of availability, so the system has a continuous, real data feed.
**Acceptance**: Given a new scene is published to the Earth Search STAC for the AOI, when the ingest DAG runs on its schedule, then the required bands + SCL mask are stored in the tile store, registered in the local STAC catalog, and committed to lakeFS with scene metadata; scenes exceeding the configured cloud-cover threshold are stored but flagged `excluded_from_training`.

### US2 — Versioned data lifecycle (P1)
As the operator, I want every training dataset to be an immutable, addressable snapshot, so any model can be reproduced exactly.
**Acceptance**: Given a training run, when it starts, then it pins a lakeFS commit ID; when it logs to MLflow, then the commit ID, config hash, and git SHA are recorded; re-running with the same triple reproduces metrics within tolerance.

### US3 — Training pipeline + registry (P1)
As the operator, I want a one-trigger training pipeline producing a registered, staged model, so training is a repeatable operation rather than a notebook ritual.
**Acceptance**: Given a pinned dataset snapshot, when the training workflow is submitted, then it runs preprocess → train → eval → register as an Argo workflow on the GPU node, logs metrics/artifacts to MLflow, and lands the model in `Staging`. A classical baseline (U-Net/ResNet) MUST exist and be beaten on IoU/F1 before any foundation-model fine-tune (Clay or Prithvi via torchgeo) is promoted.

### US4 — Drift monitoring (P1)
As the operator, I want standard drift metrics computed on every new scene and on live predictions, so retraining is triggered by evidence, not a calendar.
**Acceptance**: Given a newly ingested scene, when the drift DAG runs, then per-band input statistics (PSI/KS vs. training reference) and prediction-distribution shift are computed, stored, and exported to Prometheus; when thresholds (with hysteresis over N consecutive scenes) are exceeded, then a retrain trigger event is emitted exactly once per episode.

### US5 — Automated retrain → shadow eval → staged promotion → rollback (P1)
As the operator, I want drift triggers to launch retraining and gated promotion, so the CT loop closes without me hand-holding it — but never promotes a regression.
**Acceptance**: Given a retrain trigger, when the retrain DAG completes, then the candidate runs shadow evaluation on a held-out recent window; if it beats `Production` on the primary metric by the configured margin, it is promoted (operator-approval mode configurable); if the promoted model regresses on live canary metrics beyond threshold, then rollback to the prior registry version completes in < 10 minutes via a documented, rehearsed procedure.

### US6 — Serving with canary (P2)
As the operator, I want an inference endpoint that can split traffic between `Production` and a candidate, so promotion risk is observable before full cutover.
**Acceptance**: Given two registry versions, when canary mode is enabled, then requests split by configured ratio, per-version metrics are exported, and a single config change reverts to 100% `Production`.

### US7 — Observability (P2)
As the operator, I want dashboards for DAG health, GPU utilization, drift metrics, and per-model-version serving quality, so I can run the soak like a production system.
**Acceptance**: Grafana shows: Airflow task success/duration, Argo workflow states, GPU util/memory/temp per node, drift metric time series with threshold lines, serving latency/error rate per model version. Alerts fire on DAG failure, drift trigger, and canary regression.

### US8 — Soak operations (P1)
As the operator, I want runbooks and incident templates, so six weeks of operation produce interview-grade evidence.
**Acceptance**: Runbooks exist for: cluster rebuild, GPU operator recovery, Airflow scheduler failure, lakeFS/object-store recovery, forced retrain, rollback drill. `docs/incidents/` template captures timeline, impact, root cause, remediation. Weekly soak log summarizes uptime, triggers, and actions.

## Functional Requirements
- **FR-001** Ingest configurable Sentinel-2 L2A bands (default B02,B03,B04,B08 + SCL) for a configurable AOI from the Earth Search STAC API on a configurable cadence.
- **FR-002** Cloud-mask via SCL; per-scene cloud fraction computed and persisted; threshold-based training exclusion.
- **FR-003** S3-compatible object store (SeaweedFS) fronted by lakeFS; branch-per-experiment supported; main = training reference. (Originally MinIO; changed per decision **D-000/D-05** in `docs/decisions/000-phase0-technical-decisions.md` — MinIO's upstream was archived 2026-04-25 and its community edition no longer ships patched binaries. Only the S3-compatible + path-style interface is depended upon, so the substitution is transparent to lakeFS, MLflow artifacts, and Airflow remote logging.)
- **FR-004** Local STAC catalog of ingested scenes queryable by the training pipeline.
- **FR-005** Training as Argo Workflow with GPU resource requests; supports baseline and fine-tune configs; fits 16GB VRAM (gradient accumulation permitted).
- **FR-006** MLflow tracking + model registry with stages None/Staging/Production/Archived; promotion and rollback are registry transitions plus serving reload.
- **FR-007** Drift service computes PSI and KS per band vs. pinned reference, plus prediction-class-distribution shift; hysteresis window configurable; emits trigger events idempotently.
- **FR-008** Retrain DAG: trigger → snapshot data → submit training → shadow eval → gated promotion (auto or operator-approve, configurable).
- **FR-009** Serving endpoint (FastAPI) loads by registry stage; canary ratio configurable; KServe migration path documented (stretch).
- **FR-010** Prometheus metrics from every component; Grafana dashboards as code; Alertmanager rules for the three alert classes in US7.
- **FR-011** CI: lint, type-check, unit, contract, DAG smoke, gitleaks; all green required to merge.
- **FR-011a** CI enforces a minimum measured statement and branch coverage of `src/orbital_drift`, as ONE combined rate — `(covered statements + covered arcs) / (statements + arcs)` — against a single threshold. The threshold is a reviewable pin, never a literal in the gate runner (Constitution III). Added on operator request; not implied by FR-011, which enumerates six gates and does not mention coverage. Branch measurement was added under RB-008 part 3 (2026-08-22) with no threshold VALUE change: the quantity the pin compares got harder, the pin did not move. Rationale and rejected designs: `docs/decisions/001-coverage-gate.md` (`D-001/D-14` for the one-combined-bar decision and the alternatives rejected).
- **FR-011b** CI enforces canonical Terraform formatting: a formatting check runs both as a pre-commit hook and in the `hooks` CI stage against files classified as Terraform, using the digest-pinned container image. The version and digest are reviewable pins in `ci/versions.env`, never literals in the gate runner (Constitution III/IV). Added on operator request (Phase-0 plan step 7, 2026-08-16; unlocked by RB-007); explicitly not implied by FR-011, which enumerates six gates and does not mention formatting. Rationale and rejected readings: `docs/decisions/007-terraform-fmt-hook.md`.
- **FR-012** All thresholds, cadences, AOI, names: configuration, never constants (Constitution III).

## Edge Cases
- STAC API outage or rate-limit → ingest retries with backoff; DAG fails visibly after budget, alert fires.
- Extended cloudy period → training exclusions accumulate; drift monitor distinguishes "no clean data" from "distribution shift" and does not trigger retrain on starvation.
- Drift flapping around threshold → hysteresis prevents trigger storms; max one retrain per configurable cooldown.
- OOM on 16GB during fine-tune → batch/accumulation auto-config documented; failure surfaces in Argo, not silently truncated.
- Promotion race (second trigger during active retrain) → queue depth 1, later triggers coalesce.
- Home-lab realities (power loss, ISP outage) → all DAGs idempotent; catch-up backfill bounded and documented.

## Non-Goals
- Multi-tenant platform, petabyte-scale storage economics, video data, LLM/agent runtimes, custom calibration research (Constitution II), Kubeflow full-platform install, real-time streaming ingest.

## Success Criteria (project-level)
- **SC-001** New-scene → ingested+cataloged within 24h, unattended, for 6 weeks.
- **SC-002** Retrain E2E (trigger → promoted-or-rejected) < 12h on the RTX 5060 Ti.
- **SC-003** ≥ 1 organic drift-triggered retrain during the soak.
- **SC-004** Rollback drill executed < 10 min, documented.
- **SC-005** ≥ 3 incident postmortems; weekly soak logs complete.
- **SC-006** Full environment rebuild from runbook verified once.
