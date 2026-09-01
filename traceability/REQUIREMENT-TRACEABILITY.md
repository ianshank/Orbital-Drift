# Requirement Traceability — Orbital-Drift

**Audience:** operator + reviewing agents. **Source of truth for the requirements:**
`specs/001-orbital-drift-ct/spec.md` (FR-001–FR-012; SC-001–SC-006 in the second
table). Requirement text was read at source for this matrix (commit ac01ec4);
summaries below are compressions, not restatements from memory. Where a summary and
the source disagree, the source wins.

**Maintenance.** Updated whenever a task's requirements mapping changes (skill
definition-of-done item 3). Module and test paths are PLANNED (plan.md's module map),
not built, unless the row's status says otherwise.

**Enforcement.** `orbital_drift.traceability` lints this file as the `traceability`
stage of `ci/checks.sh`:

```sh
python -m orbital_drift.traceability --json
```

It fails on: a duplicated requirement; a status outside the enum; an empty cell; and —
the rule that matters as milestones close — **a `Green` row citing a pytest node id
that `pytest --collect-only` does not actually collect**.

**Status enum** (fixed; the linter rejects anything else):

| Status | Meaning |
|---|---|
| `Planned-gated` | Planned work whose gate is not yet satisfied. Nothing here is implemented. |
| `In-progress` | Implementation started; tests not yet green. |
| `Green` | At least one cited pytest node id exists and passes. Verified mechanically. |
| `Uncured-see-owner` | No as-built referent and no engineering cure — routed to the operator. Carried by the row where the limitation originates. |
| `N/A-by-design` | Dispositioned as non-code (say why in Notes). |

| Req | Summary | Planned module(s) | Planned test(s) | Milestone | Status | Notes |
|---|---|---|---|---|---|---|
| FR-001 | Configurable S2 L2A band/AOI/cadence ingest from Earth Search STAC | `src/orbital_drift/ingest/stac_client.py` | `tests/contract/test_stac_client.py` | M1 (T013, T016) | In-progress | RB-010 (2026-09-01): client exists with real retry/backoff, but no pagination (single POST, no next-link following) — see T016/T013 |
| FR-002 | SCL cloud mask; per-scene cloud fraction; threshold exclusion | `src/orbital_drift/ingest/cloud.py`, `ingest/tile_store.py` | `tests/contract/test_tile_store.py` | M1 (T014, T017) | In-progress | RB-010 (2026-09-01): cloud mask/fraction real and tested; tile store is local `.npy` save/load only — no rasterio, COG, windowed reads, or S3 — see T017 |
| FR-003 | SeaweedFS + lakeFS store; branch-per-experiment; main = reference | `src/orbital_drift/data/lakefs_ops.py` | `tests/contract/test_tile_store.py` | M1 (T019) | In-progress | RB-010 (2026-09-01): lakeFS integration is simulated (SHA-256 local hash), not real — no `lakefs`/`lakefs-sdk` import or dependency anywhere — see T019. Real test coverage lives in `tests/contract/test_lakefs_ops.py`, not the module-mapped `test_tile_store.py` |
| FR-004 | Local STAC catalog queryable by training | `src/orbital_drift/ingest/catalog.py` | `tests/contract/test_stac_client.py` | M1 (T018) | Planned-gated | `src/orbital_drift/ingest/catalog.py` does not exist (T018 not started); `ports/catalog.py`'s in-memory `Protocol` fake does not fill this gap |
| FR-005 | Training as Argo Workflow; baseline+finetune; fits 16GB | `src/orbital_drift/train/baseline.py`, `train/finetune.py`, `workflows/train.yaml` | `tests/contract/test_training.py` | M2 (T026, T027, T031) | In-progress | RB-010 (2026-09-01): baseline trainer (T026) real (AMP/grad-accum/IoU-F1) but logs only a local metadata dict, not MLflow (see FR-006); no `workflows/train.yaml` (T027) or `train/finetune.py` (T031) exist |
| FR-006 | MLflow registry stages; promotion/rollback as transitions | `src/orbital_drift/registry/ops.py` | `tests/contract/test_training.py` | M2 (T028) | In-progress | RB-010 (2026-09-01): registry ops are simulated (in-process dict `_mock_registry`), not real MLflow — no `mlflow` import or dependency anywhere — see T028 |
| FR-007 | Drift: PSI/KS per band + class shift; hysteresis; idempotent triggers | `src/orbital_drift/drift/metrics.py`, `drift/trigger.py`, `drift/reference.py` | `tests/contract/test_drift.py` | M3 (T033–T036) | In-progress | RB-010 (2026-09-01): PSI/KS real (KS via `scipy.stats.ks_2samp`) but missing prediction-class-distribution shift and Prometheus export (T035); `drift/reference.py` does not exist (T034); trigger.py (T036) functionally complete but has a stuck-breaker reliability defect fixed separately under RB-010 Part 11 |
| FR-008 | Retrain DAG: trigger→snapshot→train→shadow eval→gated promotion | `dags/retrain.py`, `workflows/shadow_eval.yaml` | `tests/smoke/` (DAG smoke) | M3 (T038) | Planned-gated | Auto vs operator-approve from config |
| FR-009 | FastAPI serving by registry stage; configurable canary | `src/orbital_drift/serve/app.py` | `tests/contract/test_serving.py` | M4 (T041, T042) | In-progress | RB-010 (2026-09-01): `/predict` + canary routing real and tested, but `serve/app.py` never imports `orbital_drift.config` (canary ratio/models settable only via a test-only method) and `/metrics` is hand-rolled JSON, not real Prometheus exposition format — see T042 |
| FR-010 | Prometheus everywhere; dashboards as code; three alert classes | `dashboards/`, `infra/helm-values/kube-prometheus.yaml` | `tests/smoke/` (structural) | M5 (T046, T047) | Planned-gated | — |
| FR-011 | CI gates: lint, type-check, unit, contract, smoke, gitleaks | `ci/checks.sh`, `.github/workflows/ci.yml` | `tests/unit/test_ci_contract.py::test_workflow_matrix_covers_every_stage_label`, `tests/unit/test_coverage_positive_control.py::test_the_threshold_actually_fails_a_run_whose_tests_all_pass`, `tests/unit/test_coverage_positive_control.py::test_percent_covered_is_the_combined_rate_computed_from_the_reports_own_counts` | M0 (T001) | Green | Six stages live since T001; FR-011a (T001a, coverage floor + per-file floor, DEC-004) added `coverage`; adopt-governance-kit added dead/audit/specs/traceability/projections/governance on the same contract. The stage-label test proves `coverage` is WIRED; the two positive controls prove FR-011a's floors MEASURE something — the first that the threshold fails a run whose tests all pass, the second that `percent_covered` is the combined statement+branch rate both floors now compare (D-14). Cited as node ids, not as a bare filename: the linter's `_NODE_ID` regex requires a `::`, so only a node id is mechanically checked |
| FR-011b | Canonical Terraform formatting gated as pre-commit hook + `hooks` CI stage, digest-pinned image | `.pre-commit-config.yaml` (terraform-fmt hook), `ci/versions.env` (three pins), `ci/checks.sh` (`require_terraform_image`, `stage_hooks`) | `tests/unit/test_terraform_fmt_positive_control.py`, `tests/unit/test_terraform_fmt_positive_control.py::test_the_terraform_fmt_hook_entry_uses_the_pinned_image` | M0 (T001b) | Green | Added per spec-guardian ruling 2026-08-22 (D-007/06, FR-011a/D-01 precedent). Green condition met 2026-08-22: CI run 32574454828 (attempt 1, success, head dcc5cb1) executed the three container positive controls against the pinned image — recorded in D-007 "Not resolved here" item 2, now closed. Digest lockstep additionally pinned by the TERRAFORM_IMAGE parametrization of test_container_image_is_digest_pinned_and_agrees_everywhere — that node id is not citable here: the linter's node-id regex truncates parameter tuples containing a slash (D-007 item 4) |
| FR-012 | All thresholds/cadences/AOI/names from configuration | `src/orbital_drift/config.py` | `tests/unit/test_config.py` | M1 (T015) | In-progress | RB-010 (2026-09-01): `config.py` exists and is a real, tested `pydantic_settings.BaseSettings` (T015 DONE per tasks.md), but most modules don't consume it yet — zero `src/orbital_drift` modules import `orbital_drift.config` (confirmed by search); values are duplicated/drifted in module-local defaults instead. Constitution III; reviewer-enforced meanwhile |

**Success criteria (operator-verified during Phase 5; agents never mark these):**

| Req | Summary | Planned module(s) | Planned test(s) | Milestone | Status | Notes |
|---|---|---|---|---|---|---|
| SC-001 | 6-week soak operated | (operations) | (soak log) | M5 (T052) | Planned-gated | Constitution VI; operator-only |
| SC-002 | ≥1 organic drift-triggered retrain | (operations) | (soak log) | M5 (T052) | Planned-gated | Operator-only |
| SC-003 | ≥3 incidents with postmortems | (operations) | (docs/incidents) | M5 (T052) | Planned-gated | Operator-only |
| SC-004 | Rollback drill < 10 min | (operations) | (drill record) | M3/M5 (T040) | Planned-gated | Operator-only |
| SC-005 | Reproducible retrain (US2 tolerance) | (operations) | (verification record) | M2 (T029) | Planned-gated | Operator-only |
| SC-006 | Rebuild from runbooks alone | (operations) | (rebuild record) | M5 (T051) | Planned-gated | Operator-only |
