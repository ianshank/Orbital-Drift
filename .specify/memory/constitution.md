# Orbital-Drift Constitution

Project: self-retraining Sentinel-2 change-detection system (Continuous Training pipeline) on a home k3s cluster.
Prime directive: this project exists to make the **operator** fluent in MLOps mechanics (K8s, orchestrators, CT loops, data versioning, drift response). Code completion is a by-product, not the goal.

## Principle I — Operator-Learning Primacy (NON-NEGOTIABLE)
- All live-infrastructure mutations are executed by the human operator: `kubectl apply`, `helm install/upgrade`, `terraform apply`, k3s/GPU-operator installation, Airflow/Argo/MLflow/lakeFS deployment, and all incident response during the soak.
- Agents author artifacts (manifests, charts, DAGs, code, tests, runbooks). Agents NEVER execute against the cluster, NEVER hold kubeconfig context, NEVER run `kubectl`, `helm`, or `terraform` in any mode other than `--dry-run`/`validate`/`lint`.
- Every task tagged `[HUMAN]` in tasks.md is out of agent scope. On reaching one, the orchestrator halts, emits the paired runbook, and waits for operator confirmation.

## Principle II — Boring-Standard Stack (NON-NEGOTIABLE)
- Tooling is fixed to industry defaults: Airflow, Argo Workflows, MLflow, lakeFS, Evidently-style drift metrics, Prometheus/Grafana, FastAPI (KServe as stretch), torchgeo/PyTorch.
- MUST NOT import, port, or reimplement **code, metrics, or evaluation-harness logic** from the operator's prior calibration/eval-harness codebases (ianshank/Agents, Edge-DIT, langfuse-eval-harness) or their bespoke metrics. Drift and eval use standard, widely recognized methods only (PSI, KS, ECE only via off-the-shelf libs, prediction-distribution shift, IoU/F1 for segmentation).
- Governance and process artifacts — charters, decision-log formats, review protocols, CI/hook/guard patterns, and planning templates — are explicitly outside this ban and MAY be adopted from the operator's prior work; they carry no ML logic and do not compromise the boring-standard stack.
- Rationale: interview fluency in the tools the employer runs, not another showcase of the operator's own machinery. The ban protects that fluency at the code and metrics layer; process discipline is tool-agnostic and porting it costs nothing the ban was written to protect.

## Principle III — No Hardcoded Values
- AOI geometry, band sets, cloud-cover thresholds, drift thresholds and hysteresis windows, cadences, bucket/repo names, image tags, resource requests: all sourced from Helm values, environment, or `pydantic-settings`. A reviewer finding a magic number in code fails the review.

## Principle IV — Reproducibility
- All chart, image, and package versions pinned. One documented command path rebuilds the full environment from a clean host; the rebuild runbook is itself tested once during the soak.
- Every dataset state is addressable by lakeFS commit; every model by MLflow run ID + registry version. Training jobs record both, plus git SHA and config hash.

## Principle V — Test-First Contracts
- Each pipeline boundary (STAC query client, tile store I/O, training entrypoint, registry promotion, drift API, serving API) gets contract tests written and observed failing before implementation begins.
- DAGs get smoke tests (import + structural validation) in CI. CI runs lint, type-check, unit, contract, and gitleaks on every PR.

## Principle VI — The Soak Is the Deliverable
- Definition of Done for the project: ≥ 6 continuous weeks operated; ≥ 1 organically drift-triggered retrain (not forced); ≥ 3 incidents logged in `docs/incidents/` with postmortems; 1 executed rollback drill.
- A feature-complete repo with no soak record is NOT done. Agents must not mark Phase 5 complete; only the operator can.

## Principle VII — Secrets Hygiene
- No credentials in the repo, ever. gitleaks runs as pre-commit hook and CI gate from T001 onward. Cluster secrets live in K8s Secrets (sealed-secrets as stretch); local dev uses `.env` (gitignored).

## Governance
- Amendments via PR with written rationale; `spec-guardian` reviews every PR against this document and the active spec. Constitution supersedes agent judgment and operator convenience in any conflict, except operator safety.

Version 1.1.0 | Ratified 2026-08-08 | Amended 2026-08-20 (Principle II scoped to code/metrics/eval-harness logic; governance and process artifacts exempted)
