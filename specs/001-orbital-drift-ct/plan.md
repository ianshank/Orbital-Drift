# Implementation Plan: Orbital-Drift CT Pipeline

**Branch**: `001-orbital-drift-ct` | **Spec**: `specs/001-orbital-drift-ct/spec.md`

> **RB-016 (2026-10-06).** Summary, Technical Context, Constitution Check, Project Structure, Phases and Risks were rewritten under RB-016 (`docs/decision-log.md`) per `docs/decisions/016-sdlc-ml-panel-review.md` (D-016; tree facts measured at c545701). `B-nn` in this file means ballot item `D-016/B-nn`: an open operator decision with a recommended default, not a decision. Anything marked **proposal** binds only once the named ballot item is logged; until then the text it would replace stands. The Decision Log (D-01…D-05) is unchanged.

## Summary
Continuous Training pipeline for land-cover segmentation of Sentinel-2 L2A imagery on a home k3s cluster: Airflow-scheduled ingestion → lakeFS-versioned data → Argo-executed GPU training → MLflow registry → drift-triggered retrain with shadow eval, gated promotion, serving, and rollback. Agents author; the operator applies and operates (Constitution I). The model is a segmentation model; the "change detection" wording in spec.md's title and the constitution's project line is ballot B-07, which an amendment PR (not this file) would settle.

## Technical Context
- **Cluster**: k3s, pinned to the exact patch release `docs/decisions/versions.md` records (deliberately not the current stable line; D-000/D-07) — node A: dual-GPU desktop (RTX 5060 Ti 16GB = primary trainer; RTX 5060 8GB = serving/aux). Optional node B: Tesla P40 24GB, added in Phase 5 deliberately to practice heterogeneous GPU scheduling (mixed driver/arch pain is the lesson, not a bug). NVIDIA GPU Operator; verify Blackwell (50-series) driver/CUDA compatibility on host first — [risk R-05]. Node A's identity and OS install path are ballot B-01: the only GPU host the repo has measured runs Windows, while runbook 00 assumes Ubuntu 24.04 (D-016/01g; T074). **Proposal (B-16):** the P40 join (T050) moves after the soak.
- **Storage**: SeaweedFS (S3 API) on cluster; lakeFS over it for data versioning; Postgres via CloudNativePG backing Airflow, MLflow, lakeFS. (Was MinIO + Bitnami Postgres — see `docs/decisions/000` items **D-000/D-05** and **D-000/D-04**: MinIO's upstream archived 2026-04-25; Bitnami's public versioned images were deleted 2025-09-29, leaving `latest`-only, which violates Principle IV.) As authored, the three services share one CNPG database and role (D-016/06b; T072), and nothing is backed up (D-016/06a; T073, backup target B-17).

> **Decision-ID namespaces.** This file's own Decision Log below uses `D-01…D-05`. `docs/decisions/000-phase0-technical-decisions.md` uses an independent `D-01…D-11` series. They collide. Cross-references to the decisions document are always written **`D-000/D-nn`**; a bare `D-nn` in this file means this file's Decision Log.
- **Orchestration**: Airflow via official Helm chart, KubernetesExecutor (each task = pod: forces real K8s fluency). Argo Workflows for training (Airflow triggers Argo via API — the cross-system handoff is JD-relevant). One pod per task means drift-trigger state must live outside the process (D-016/04a; re-scoped T036/T037); the Airflow → Argo parameter and output contract is re-scoped into T027/T038 (D-016/04f).
- **ML**: Python 3.12, PyTorch 2.x, torchgeo. Baseline: U-Net/ResNet50 segmentation on 10m bands. Fine-tune target: Clay or Prithvi-EO geospatial foundation model (final selection = task T030 research spike; both are torchgeo/HF-accessible). AMP + gradient accumulation for 16GB. **Proposal (B-16):** the fine-tune (T030–T032) executes after the Phase 3 gate.
- **Data**: Earth Search STAC (Element84), collection `sentinel-2-l2a`, AWS Open Data. AOI: operator-chosen, ~1–4 MGRS tiles. Labels: bootstrap from ESA WorldCover / Dynamic World-style public land-cover rasters for the AOI (weak labels are acceptable — the pipeline is the product, not SOTA accuracy). Label regime is B-07 (resolves AR-3) and the evaluation holdout B-08. Not built: radiometric harmonization across processing baseline 04.00 and a common 10 m grid for the 20 m SCL band (D-016/05b); source and harmonization are B-15 (package P-7).
- **Drift**: Evidently (or equivalent standard lib) — PSI/KS per band + prediction-distribution shift. No bespoke metrics (Constitution II). As built, PSI is hand-written in numpy (D-016/05d; B-09's default extends the library decision to `drift/`), and per-band pixel PSI/KS tracks season and nuisance rather than model harm (D-016/04b); trigger composition is B-14.
- **Registry**: MLflow tracking + registry. FR-006 is written against model-version stages, which MLflow deprecated in 2.9 in favour of aliases [domain knowledge]; the pinned server is 3.15.1 (`docs/decisions/versions.md`). Aliases vs stages is B-11; rollback-target semantics are T071 (D-016/03c).
- **Serving**: FastAPI + registry-stage loading + canary ratio; KServe = stretch goal, documented migration path only. The MVP serving pattern is B-13 (default batch-first: a scoring job writing a classified COG and a class histogram per scene; online FastAPI and canary stay in Phase 4; D-016/03e).
- **Observability**: kube-prometheus-stack, dashboards-as-code in `dashboards/`, Alertmanager → operator notification. **Proposal (B-19):** the observability core (T046/T049) moves into Phase 0 before the first scheduled DAG, with a PVC, ≥ 56-day retention and an off-node dead-man heartbeat (D-016/06f).
- **IaC**: Helm values + Terraform (providers: helm, kubernetes) for everything above the OS; all pinned.

## Constitution Check
- I: every apply-step in tasks.md is `[HUMAN]`; agents never execute them. PASS for the execution boundary. The runbook pairing is incomplete — T029, T032, T049 and T050 have no runbook-writer task — and is owned by T075.
- II: stack fixed above; spec-guardian blocks imports from prior harness repos. **OPEN VIOLATION**, recorded by RB-010 (`docs/decision-log.md`, 2026-09-01): `eval/bootstrap.py` and `eval/superiority.py` hand-roll resampling and the promotion gate. The method choice is pending B-09 (`docs/decisions/011-principle-ii-eval-methods.md`). Not PASS.
- III–V, VII: enforced by CI gates live since T001 in Phase 0 — lint, type-check, unit, contract, DAG smoke, gitleaks (FR-011), coverage (FR-011a) and the adopt-governance-kit stages — not "defined in Phase 1" as an earlier revision said. PASS for the gates' existence; the smoke suite collects zero tests until T020 (traceability FR-011 note). Known gaps the gates do not catch: Principle III — `SuperiorityConfig.minimum_effect` has no config source and `auto_promote_margin` is never read (D-016/07c); Principle IV — the config hash is unusable as a lineage key (D-016/03g) and nothing is backed up (D-016/06a).
- VI: Phase 5 completion reserved to operator. PASS.

## Project Structure
```
orbital-drift/
├── .specify/memory/constitution.md
├── specs/001-orbital-drift-ct/{spec.md,plan.md,tasks.md}
├── .claude/agents/            # 10 subagents (see CLAUDE.md roster)
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
│   ├── serve/                 # FastAPI app, canary, model loader
│   ├── domain/                # pure domain primitives — see note below
│   ├── ports/                 # hexagonal Protocols + in-memory fakes — see note below
│   ├── eval/                  # promotion/calibration statistics — see note below
│   ├── observability/         # structured logging, context, decision records — see note below
│   └── quality/               # AST-based hardcode scanner — see note below
├── tests/{unit,contract,integration,e2e,sanity,smoke,governance,architecture}/
│                              # tiers present at c545701; golden/ holds lineage fixtures;
│                              # tests/acceptance/ is planned under package P-6 (B-04)
├── dashboards/                # Grafana JSON
├── docs/{runbooks,incidents,soak-log,decisions}/   # decisions/ per CLAUDE.md + T030
├── docs/decision-log.md       # mechanical gate ledger (DEC/RB/G ids; adopt-governance-kit D7)
├── charter/                   # PROJECT-CHARTER.md — constraints C-1..C-6 (adopt-governance-kit D8)
├── openspec/changes/          # OpenSpec change packages; governance changes (adopt-governance-kit D6)
├── .claude/skills/            # orbital-drift-governance skill (gate table; staleness-checked)
├── ci/                        # gate logic (checks.sh), version pins, gitleaks config
└── .github/workflows/         # thin caller — invokes `sh ci/checks.sh <stage>`, no gate logic
```

Additional paths land incrementally with the adopt-governance-kit change (see
`openspec/changes/adopt-governance-kit/tasks.md`): `Makefile` + `ci/validate_specs.sh` +
`tests/governance/` (its Phase 3), `traceability/` (Phase 5), `scripts/` + `planning/` +
`.claude/allowed-remotes.txt` (Phase 6). Each lands in the same PR that extends
`tests/unit/test_repo_structure.py` for it.

**Hexagonal layer (`domain/`, `ports/`, `eval/`, `observability/`, `quality/`), added
outside this task list (RB-010, `docs/decision-log.md`, 2026-09-01).** PR#17
("Phase 0-R", merged 2026-08-24) added five further `src/orbital_drift/` packages: `domain/`
(pure, zero-dependency primitives — geometry, temporal ranges, scene metadata, lineage
hashing, a domain-error hierarchy), `ports/` (`Protocol` abstractions plus in-memory
stdlib fakes for five boundary interfaces — catalog, compute, dataversion, registry,
tiles), `eval/` (promotion/calibration statistics), `observability/` (structured
logging, execution-context binding, a decision-record ledger), and `quality/` (an
AST-based hardcode scanner). No task ID in this file (T001–T052) targets any of
them — they landed with no G-1/G-2/G-3 gate and no spec-guardian/adversarial-reviewer
review before merge. They are currently structurally disconnected from the "real"
modules listed above: **0 of the 5 `ports/*.py` Protocols have a real adapter** — each
port's only concrete implementation is its own in-memory fake defined within `ports/`
itself, and no module under `ingest/`, `data/`, `train/`, `registry/`, or `serve/`
imports anything from `orbital_drift.ports` (confirmed by search). This paragraph
documents what exists; it does not redesign the architecture. See
`docs/architecture/ARCHITECTURE.md` section 0 ("Reality Check") for the full
code-vs-integration audit, and its "Known follow-ups (not fixed by RB-010)" note for
the two open architectural questions this layer surfaces: convergence of the two
disconnected registry implementations (`registry/ops.py` vs `ports/registry.py`), and
AR-3, the still-open OSCD-vs-DynamicEarthNet dataset decision. **RB-016:** the port
disposition is ballot B-12 (D-016/07b) and AR-3 is folded into B-07; T071 replaces
D-013/04b's "wrap an unmodified `ModelRegistryOps`", whose rollback target is wrong
(D-016/03c).

## Phases
Phase numbers are kept. Each phase states its gate as written; a **proposal** clause states the gate or move D-016 recommends and the ballot item that would make it binding. tasks.md Phases 6 (RB-012) and 7 (RB-016) are task containers, not pipeline phases: they carry no gate of their own, and their tasks are authorized only by an RB or a named G-1 waiver that lists them (see the Phase 7 intro in tasks.md).

- **Phase 0 — Substrate** (operator-heavy): repo + CI + gitleaks; host GPU driver validation; k3s up; GPU operator; SeaweedFS/lakeFS/CloudNativePG/MLflow/Airflow/Argo deployed. Gate: `nvidia-smi` inside a pod; hello-world DAG and hello-world Argo GPU job green. **Proposal (B-01–B-03, B-17, B-19):** B-01 decided before T003; B-03's driver pin before T003 if it is ready, otherwise the version T003 installs is recorded and held before T005, and the rest of B-03 before T005; B-02 before T005. T074's Step 0 and driver pin are preferred before T003 but never block it: if T074 has not landed, the operator runs T003 on runbook 00 as written, records node identity and the exact driver version installed in T003's verification block, and T074 then pins and holds that version before T005; the rest of T074 before T005; before T012, T072; before the first scheduled DAG, the observability core (T046/T049) and backups (T073, restore runbook in T048). Gate adds: an alert reaches the operator; one backup has completed and been restored into a scratch namespace.
- **Phase L — Walking skeleton** (agents, laptop) — **proposal (B-04, B-05).** Runs in parallel with Phase 0's `[HUMAN]` gates only under B-05's waiver. Packages P-1–P-6 plus T071, the re-scoped T036 and T059 re-scoped as the alias registry adapter (tested against sqlite-backed MLflow pinned to the server version via pin-a-tool; mlflow becomes a pinned dependency when T059 executes), building the MVP-L acceptance test (`docs/development/NEXT_STEPS.md` §3): separate processes; a local config profile; the MLflow client on a sqlite tracking URI for the registry (the real registry API, no server) and a file-backed adapter only for data versioning (lakeFS has no embedded mode [domain knowledge]); recorded fixtures, planted learnable labels, a pinned holdout and fixed seeds. Gate: MVP-L green in CI. Why: every seam defect otherwise first appears on node A at T040/T045 (D-016/07a).
- **Phase 1 — Ingestion & data lifecycle** (US1, US2): contract tests → STAC client → tile store + SCL cloud mask → local catalog → lakeFS commit flow → ingest DAG. Gate: 2 real scenes ingested unattended on schedule. **Proposal (B-15, B-22):** adds harmonization (re-scoped T016/T017, P-7), operator-recorded live fixtures and pagination; the gate adds 4 bands + SCL resolved, harmonized across PB04, valid fraction recorded.
- **Phase 2 — Training & registry** (US3): label bootstrap → dataset assembly from lakeFS snapshot → baseline training workflow → MLflow logging/registration → fine-tune workflow → baseline-beats gate. Gate: reproducibility check (US2 acceptance) passes. **Proposal (B-07, B-08, B-16):** labels and a holdout manifest exist before any real training run; the gate is reproducibility (T029) plus a tagged holdout manifest; the fine-tune and T032 leave this phase, so T032 is no longer described as its gate.
- **Phase 3 — CT loop** (US4, US5): drift service + reference stats → drift DAG → trigger emitter with hysteresis/cooldown → retrain DAG → shadow eval → gated promotion → rollback procedure + drill. Gate: forced-drift end-to-end demo (inject shifted scenes) completes trigger→promotion; rollback drill < 10 min. **Proposal (B-04): the gate becomes MVP-C** (T040 re-scoped): G-1, G-2, G-3 logged; observability core and backups live; ≥ 2 real scenes ingested unattended on schedule into real lakeFS; an Argo GPU training run whose MLflow run records {lakeFS commit, git SHA, config hash}; two forced episodes, each injected on a lakeFS branch (never main) and each producing exactly one episode with `trigger_source=forced`: one with the normal retrain configuration (expected PASS, so a promotion) and one with a deliberately degraded training configuration such as permuted labels (expected REJECT); for each, the trigger-to-verdict wall-clock is recorded against SC-002's 12 h budget; the champion scoring scenes; a timestamped rollback drill under 10 minutes (first SC-004 measurement); a reproducibility re-run within a stated tolerance. Why: the written gate asks only for trigger→promotion, which rewards weakening the promotion gate (D-016/04e).
- **Phase 4 — Serving & canary** (US6): FastAPI stage-loader → canary split → per-version metrics. Gate: canary regression auto-alert demonstrated. **Proposal (B-13, B-16, B-19):** post-MVP; an alias-polling FastAPI loader; canary judged on label-free proxies (prediction-class divergence, error rate, latency); the Alertmanager this gate needs exists from Phase 0, which removes the inversion where a Phase 4 gate depended on Phase 5 alerting; the fine-tune T030–T032 lands here.
- **Phase 5 — Observability & soak** (US7, US8): dashboards, alerts, runbooks, incident templates, P40 node join (optional), rebuild-runbook verification, then the 6-week soak. Gate: SC-001…SC-006 — **operator sign-off only** (Constitution VI, unchanged). **Proposal (B-04, B-16, B-20):** a Soak Readiness Gate (operator-run, `[HUMAN]`; its task is minted when B-04 and B-20 are logged) precedes T052 — restore drill passed with RTO recorded; every alert class fire-drilled including the off-node heartbeat; 72 h unattended burn-in with ≥ 3 scheduled ingests; projected disk use at day 42 < 60%; drift thresholds frozen by a decision-log line after the operator-run historical replay, as the most sensitive (lowest-threshold) set meeting B-14's false-trigger bound; B-18's required security items closed; a pre-soak reset: the champion, the trigger state and the drift reference are re-established from a model trained on main only, so no branch-injected data reaches the soak, and previous_champion is reset and the promotion history archived, not deleted, so no branch-trained version is reachable by rollback during the soak. A rebuild-plus-restore drill joins the Soak Readiness Gate while T051 stays the once-during-the-soak rebuild test constitution.md:21 requires (B-20); T050 moves after the soak (B-16).

## Risks
- **R-01** VRAM OOM on fine-tune → AMP + grad-accum config first-class; baseline model is the fallback deliverable.
- **R-02** Drift flapping / trigger storms → hysteresis + cooldown in FR-007; tested with synthetic sequences.
- **R-03** STAC/AWS rate limits or schema drift → pinned client, retry budget, contract tests against recorded fixtures.
- **R-04** Home-lab availability (power/ISP) → idempotent DAGs, bounded backfill, UPS optional.
- **R-05** Blackwell driver / GPU-operator mismatch on 50-series → validate host CUDA stack before k3s (**T002** authors the host-prep runbook, **T003** executes it; an earlier revision cited T004, which is the k3s install runbook and runs *after* the CUDA stack is proven); pin operator version known-good; P40 (older arch) isolated to Phase 5 on purpose.
- **R-06** Scope creep toward operator's prior harness work → Constitution II + spec-guardian; any "improve the drift math" impulse becomes a docs/ideas note, not code.
- **R-07** Promotion-gate validity under static weak labels and spatial leakage: a temporal holdout rewards memorising locations, the current metric scores absent classes as IoU 1.0, and the moving-block bootstrap under-controls false promotions under spatial autocorrelation (D-016/02) → a dispersed spatial-block holdout buffered by the receptive field (B-08, P-3); pooled present-class IoU with `ignore_index` and a point-estimate-plus-one-sided-bound rule with an INSUFFICIENT_EVIDENCE verdict (B-10); calibration tests (P-4); operator-approve promotion until they pass.
- **R-08** Seasonal and nuisance false triggers: per-band pixel PSI/KS tracks phenology and radiometric nuisance rather than model harm, and clouds and fill are scored as drift (D-016/04b, 04c) → a trigger that needs input drift on SCL-clear pixels against the champion's training reference AND a prediction-class or weak-label signal, so harmless drift does not fire while seasonal drift that hurts the model still does; a day-of-year comparison stays a diagnostic (B-14); a false-trigger bound pre-registered in configuration before the replay (at most 0.5 triggers per 42 replayed no-harm days [panel judgement; free parameter]); thresholds frozen by a decision-log line as the most sensitive (lowest-threshold) set meeting that bound after an operator-run ≥ 12-month historical replay that also estimates the expected organic triggers in the soak window (P-8).
- **R-09** Trigger state lost across pods: KubernetesExecutor gives every task a fresh process, so in-memory hysteresis never accumulates and SC-003 cannot be met organically (D-016/04a) → durable, event-sourced trigger state keyed by acquisition, idempotent under retries (re-scoped T036/T037), with a kill-and-rerun smoke test.
- **R-10** Single-node data loss: one disk failure loses registry, lakeFS commits and lineage and restarts the soak clock (D-016/06a) → off-node backups (T073, B-17); a restore drill in the Soak Readiness Gate and T051's in-soak rebuild test (B-20).
- **R-11** Disk exhaustion: k3s evicts at 5% free nodefs/imagefs with no earlier signal [upstream source], and nothing reclaims lakeFS or image storage (D-016/06c) → a dedicated data filesystem (T074), retention and a capacity alert (P-9, B-17), and a Soak Readiness Gate check that projected day-42 usage is < 60%.
- **R-12** Operator decision latency: decisions and physical tasks only the operator can close had aged 25–51 days at 2026-10-06, and nothing measures their age (D-016/01a) → the D-016 ballot with an accept-the-defaults path; a weekly decision review and a decisions-before-programs rule (B-06).

## Decision Log
- **D-01 lakeFS over DVC**: branch/commit semantics on the object store better match "versioning strategies for massive datasets" (JD language) and keep versioning server-side; DVC noted as the lighter alternative.
- **D-02 Argo over Kubeflow Pipelines**: lighter footprint on a home cluster; Airflow+Argo split mirrors the JD's orchestrator plurality. Kubeflow named as read-and-compare, not install.
- **D-03 FastAPI before KServe**: serving mechanics first, platform abstraction second; KServe documented as migration.
- **D-04 Weak public labels**: WorldCover-style bootstrap accepted; accuracy is not the graded axis, the loop is.
- **D-05 Two Airflow/Argo systems instead of one**: deliberate — the cross-orchestrator handoff (Airflow sensor/API → Argo submit → status poll) is a JD-relevant skill.
