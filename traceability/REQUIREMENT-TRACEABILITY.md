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

It fails on: a duplicated requirement; a status outside the enum; an empty cell; **a
`Green` row citing a pytest node id that `pytest --collect-only` does not actually
collect** (the rule that matters as milestones close); and — added 2026-09-05 under
RB-012 — **a requirement declared in `spec.md` with no row here, or a row citing a
requirement `spec.md` never declares**.

That last rule checks the source-of-truth claim this header makes, which until RB-012
nothing did: the linter read this file alone. On its first run it found that `FR-011a`
had no row. **Its honest scope:** it compares which requirement IDs appear on each side.
It does NOT check that a summary faithfully compresses the spec text for that id —
summaries here are deliberate compressions and no regex separates a good one from a
wrong one. The three mis-carried `SC-` summaries RB-012 repaired would NOT have been
caught by it. Summary fidelity stays reviewer-enforced; see
`docs/decisions/013-plan-artifact-reconciliation.md` D-013/07.

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
| FR-009 | FastAPI serving by registry stage; configurable canary | `src/orbital_drift/serve/app.py` | `tests/contract/test_serving.py` | M4 (T041, T042) | In-progress | RB-010 (2026-09-01): `/predict` + canary routing real and tested; `/metrics` is hand-rolled JSON, not real Prometheus exposition format — see T042. **Corrected 2026-09-05 (RB-012):** this note previously said `serve/app.py` "never imports `orbital_drift.config`". It does, at `serve/app.py:21`, since RB-010 Part 5b. What survives is the startup-wiring gap — models are set only via `ModelContainer.set_models`, called from no production path — which composes with Part 13's honest `/healthz` 503 into a container that can never report healthy. Owned by **T053** |
| FR-010 | Prometheus everywhere; dashboards as code; three alert classes | `dashboards/`, `infra/helm-values/kube-prometheus.yaml` | `tests/smoke/` (structural) | M5 (T046, T047) | Planned-gated | — |
| FR-011 | CI gates: lint, type-check, unit, contract, smoke, gitleaks | `ci/checks.sh`, `.github/workflows/ci.yml` | `tests/unit/test_ci_contract.py::test_workflow_matrix_covers_every_stage_label` | M0 (T001) | Green | Six stages live since T001; FR-011a (T001a, coverage floor + per-file floor, DEC-004) added `coverage`; adopt-governance-kit added dead/audit/specs/traceability/projections/governance on the same contract. The stage-label test proves `coverage` is WIRED; the two positive controls prove FR-011a's floors MEASURE something — the first that the threshold fails a run whose tests all pass, the second that `percent_covered` is the combined statement+branch rate both floors now compare (D-14). Cited as node ids, not as a bare filename: the linter's `_NODE_ID` regex requires a `::`, so only a node id is mechanically checked. **Disclosure added 2026-09-05 (RB-012), status deliberately NOT downgraded:** `tests/smoke/` holds only `.gitkeep`, so one of the six gates this requirement names collects zero tests and passes vacuously. `Green` is kept because the enum defines it as "at least one cited pytest node id exists and passes" and FR-011 requires the gates to EXIST and be merge-blocking, which they do -- the content of the smoke suite is T020's. The non-disclosure was the defect, not the status |
| FR-011a | CI enforces a minimum measured statement+branch coverage of `src/orbital_drift` as ONE combined rate, threshold a reviewable pin, never a literal | `ci/checks.sh` (`stage_coverage`), `ci/versions.env` (`COVERAGE_MIN_PERCENT`, `COVERAGE_PER_FILE_MIN_PERCENT`), `src/orbital_drift/covcheck.py` | `tests/unit/test_coverage_positive_control.py::test_the_threshold_actually_fails_a_run_whose_tests_all_pass`, `tests/unit/test_coverage_positive_control.py::test_percent_covered_is_the_combined_rate_computed_from_the_reports_own_counts` | M0 (T001a) | Green | **Row added 2026-09-05 (RB-012).** FR-011a was declared in `spec.md` and carried by NO row — it appeared in this file only inside FR-011's Notes prose, so the requirement with its own decision doc (`docs/decisions/001-coverage-gate.md`), its own operator-ratified threshold (85, D-05, ratified 2026-08-16) and two positive controls was traced nowhere. Found by the spec/matrix requirement-parity check this same change added to `orbital_drift.traceability`, on its first run — not by a reader. The two cited node ids previously sat in FR-011's row; they belong here, and FR-011 keeps `test_workflow_matrix_covers_every_stage_label`. Per-file floor (90) is additive per RB-006 and binds via `--floor` argv |
| FR-011b | Canonical Terraform formatting gated as pre-commit hook + `hooks` CI stage, digest-pinned image | `.pre-commit-config.yaml` (terraform-fmt hook), `ci/versions.env` (three pins), `ci/checks.sh` (`require_terraform_image`, `stage_hooks`) | `tests/unit/test_terraform_fmt_positive_control.py`, `tests/unit/test_terraform_fmt_positive_control.py::test_the_terraform_fmt_hook_entry_uses_the_pinned_image` | M0 (T001b) | Green | Added per spec-guardian ruling 2026-08-22 (D-007/06, FR-011a/D-01 precedent). Green condition met 2026-08-22: CI run 32574454828 (attempt 1, success, head dcc5cb1) executed the three container positive controls against the pinned image — recorded in D-007 "Not resolved here" item 2, now closed. Digest lockstep additionally pinned by the TERRAFORM_IMAGE parametrization of test_container_image_is_digest_pinned_and_agrees_everywhere — that node id is not citable here: the linter's node-id regex truncates parameter tuples containing a slash (D-007 item 4) |
| FR-012 | All thresholds/cadences/AOI/names from configuration | `src/orbital_drift/config.py` | `tests/unit/test_config.py` | M1 (T015) | In-progress | RB-010 (2026-09-01): `config.py` exists and is a real, tested `pydantic_settings.BaseSettings` (T015 DONE per tasks.md). **Corrected 2026-09-05 (RB-012):** this note previously claimed "zero `src/orbital_drift` modules import `orbital_drift.config` (confirmed by search)". RB-010 Parts 5a/5b/5c wired eight after Part 1 reconciled this file, and no later part revisited it — measured at HEAD: `ingest/{stac_client,tile_store,cloud}.py`, `data/dataset.py`, `drift/metrics.py`, `train/baseline.py`, `registry/ops.py`, `serve/app.py`. The residual gap is narrower: five sites in `docs/decisions/012-*.md` (F1-F5), plus `data/lakefs_ops.py` and `drift/trigger.py`, which hold FR-012 values and consult no config at all. Owned by **T061**. Also unresolved: there is no composition root — `get_config()` is called from nowhere in `src/`, so every wired `config=` parameter is `None` at runtime. Constitution III; reviewer-enforced meanwhile |

**Success criteria (operator-verified during Phase 5; agents never mark these):**

| Req | Summary | Planned module(s) | Planned test(s) | Milestone | Status | Notes |
|---|---|---|---|---|---|---|
| SC-001 | New scene ingested + cataloged within 24h, unattended, for 6 weeks | (operations) | (soak log) | M5 (T052) | Planned-gated | Constitution VI; operator-only. **Summary corrected 2026-09-05 (RB-012):** previously "6-week soak operated", which dropped the 24h/unattended criterion -- the measurable half of the requirement |
| SC-002 | Retrain E2E (trigger to promoted-or-rejected) < 12h on the RTX 5060 Ti | (operations) | (retrain wall-clock record, T029/T040) | M3/M5 (T040) | Planned-gated | Operator-only. **Row corrected 2026-09-05 (RB-012):** this row previously carried SC-003's text ("≥1 organic drift-triggered retrain"), so `spec.md`'s SC-002 -- the ONLY performance budget in the specification -- was traced nowhere, and nothing planned for or measured it. T029 records wall-clock for the baseline run; T040's forced-drift E2E is where the full trigger-to-verdict budget first becomes measurable |
| SC-003 | ≥1 organic drift-triggered retrain during the soak | (operations) | (soak log) | M5 (T052) | Planned-gated | Operator-only. **Row corrected 2026-09-05 (RB-012):** previously carried SC-005's text ("≥3 incidents with postmortems") |
| SC-004 | Rollback drill executed < 10 min, documented | (operations) | (drill record) | M3/M5 (T040) | Planned-gated | Operator-only. Blocked beyond T040: the rollback runbook (T039) is unwritten, the drill's entry point `registry/ops.py::rollback_production` is the one unlocked mutation path (T062), and nothing loads a production model outside tests (T053) -- so there is no canary to roll back. See `docs/development/NEXT_STEPS.md` section 5 |
| SC-005 | ≥3 incident postmortems; weekly soak logs complete | (operations) | (docs/incidents, docs/soak-log) | M5 (T052) | Planned-gated | Operator-only. **Row corrected 2026-09-05 (RB-012):** previously carried "Reproducible retrain (US2 tolerance)", which is US2's acceptance text and not any numbered success criterion -- so SC-005's real content was absent from the matrix. US2 reproducibility remains T029's acceptance, tracked there and under FR-005/FR-006, not as an SC. Both cited directories hold only `.gitkeep`; the postmortem and soak-log templates are T048 |
| SC-006 | Full environment rebuild from runbook verified once | (operations) | (rebuild record) | M5 (T051) | Planned-gated | Operator-only |
