# Orbital-Drift — Forward Roadmap

**Audience:** the operator, deciding which open decisions and physical tasks to close next; and
any agent picking up a task or package named below.

**Status:** rewritten 2026-10-06 under RB-016 (`docs/decision-log.md`) per
`docs/decisions/016-sdlc-ml-panel-review.md`, replacing the 2026-09-11 revision. Tree facts are
measured at c545701 unless a command is given; the evidence for every finding cited here is D-016.
`D-016/0n[x]` is a D-016 finding. "First pass Hn" names a finding of the orchestrator's
first-pass review that preceded the panel; each such cell in §5 also gives its location or
method, re-measured at c545701 for this rewrite.

**Source of truth.** `specs/001-orbital-drift-ct/tasks.md` owns scope; `docs/decision-log.md`
owns gates. **If this file disagrees with either, they win** — this document sequences work and
asserts no status beyond pointers to those two files.

**Why this file exists at all.** `docs/architecture/ARCHITECTURE.md` and RB-010 both defer open
questions to "the forward-roadmap", by name (RB-012, `docs/decisions/013-plan-artifact-reconciliation.md`).
`docs/development/**` is a `governed_path_globs` entry (T065), so this file cannot rot unowned.

---

## 1. Verdict

1. The binding constraint is the operator's queue of unmade decisions and physical tasks, not
   agent capacity, and nothing measures its age. Every agent-executable task ID is blocked;
   `main` has had 0 merges in 25 days; 0 of the last 10 merged PRs added capability (D-016/01a, 01c).
2. As specified, the plan cannot reach its own definition of done: the drift trigger cannot fire
   under the mandated executor (D-016/04a), the promotion gate cannot reject a regression and
   decides on an inflated metric (D-016/02a-b), rollback can restore a rejected model
   (D-016/03c), and one disk failure ends the 6-week soak because nothing is backed up (D-016/06a).
3. The fix: decide the ballot (§2; most items can be accepted in one decision-log line), then a
   two-stage MVP — a laptop walking-skeleton acceptance test (MVP-L), then the same scenario
   operated on the cluster (MVP-C) — then a Soak Readiness Gate, then the soak (§3, §4).
4. RB-012 answered this same request ("deeper peer review, rewrite plans") 31 days ago; the
   decisions it surfaced are still open. This rewrite is only useful if the ballot is decided.

---

## 2. The operator ballot

`B-nn` in this file means ballot item `D-016/B-nn`. Recommended defaults are panel proposals, not
decisions; nothing below is decided until the operator logs it. D-016/09 holds each item's full
options and rationale. Age is in days to 2026-10-06 from the item's first appearance in git
(D-016/01a); "new" means first raised by D-016.

**Retired labels.** The 2026-09-11 revision's "D-1", "D-2" and "D-3" map to D-1 → B-09,
D-2 → B-12 (with B-11), D-3 → B-01 plus the T003 `[HUMAN]` task; older documents that cite them
resolve through this mapping.

**Group 1 — decide first** (critical path; all decidable from a laptop):

| ID | Decision | Recommended default | Age | Unblocks |
|---|---|---|---|---|
| B-01 | Node A identity and OS path | Dual-boot Ubuntu 24.04 on a dedicated SSD in the current GPU workstation | 51 | T074 Step 0; T003 → G-1 |
| B-02 | Ratify D-008/D-03 (config-v3 deployment mechanism) | Ratify as proposed | 45 | T005 → G-2 |
| B-03 | Re-verify pins: the driver before T003, the rest before T005 | Authorize an infra-scaffolder re-verification of versions.md; driver pinned exactly and held | 45 | Safe T003/T005/T012 |
| B-04 | MVP definition and phase gates | Two-stage MVP-L + MVP-C + Soak Readiness Gate (§3) | new | Phase L; plan.md gates binding |
| B-05 | Authorization before G-1; DEC-002 ruling | Named G-1 waiver for Phase L packages and T071-T073, WIP limit one slice ahead of the last G-x; DEC-002 option C (slice WIP limit plus at most one process PR per product PR); lift RB-015's two prohibitions for the local profile only | 31 | Phase L; T071-T073 execution; RB-015 Part F |
| B-06 | Decision cadence and process rules | Weekly 30-minute decision review; no new process RB while any ballot item is older than 14 days; governance-code freeze until MVP-C; log entries ≤ 150 words; an operator merge to `main` is a decision logged the same day | new | Bounds D-016/01a |

**Group 2 — ML protocol** (before the Phase L packages and T024/T026/T034; B-07 and B-12 first):

| ID | Decision | Recommended default | Age | Unblocks |
|---|---|---|---|---|
| B-07 | Label regime (resolves AR-3); "change detection" wording | Static annual map (ESA WorldCover 2021) over the AOI, version-pinned, permanent spatial-block holdout; title amended by its own PR | ≥ 35 | T024, P-2, P-3 |
| B-08 | Evaluation protocol | Fixed, dispersed spatial-block holdout in a ground CRS, buffered ≥ 92 px, scored on the newest window; champion re-scored on the same manifest | new | P-3, RS:T038 |
| B-09 | Gate method (old D-1; D-011) | `scipy.stats.bootstrap`, paired, one-sided, BCa, ≥ 9,999 seeded resamples over dispersed clusters; delete the moving-block code; extend to drift PSI | 35 | RB-010 Parts 3, 14; T063 |
| B-10 | Decision rule and metric | Point ≥ margin AND one-sided 95% lower bound > 0; PASS / REJECT / INSUFFICIENT_EVIDENCE; pooled present-class IoU with ignore_index; operator-approve until calibrated | new | RS:T026, P-3, P-4 |
| B-11 | Registry semantics (FR-006) | Aliases champion/challenger/baseline with a recorded previous_champion; FR-006 amended later | 35 | T071 naming; RS:T028/T059 |
| B-12 | Adapter disposition (old D-2) | Keep catalog, dataversion, registry ports; reshape the last two; delete tiles and compute; file-backed local adapters for MVP-L | 35 | T059, T060, P-1 |
| B-13 | MVP serving pattern | Batch-first scoring job (classified COG + class histogram per scene); FastAPI + canary stay in Phase 4 | new | P-5, RS:T043 |
| B-14 | Drift trigger; meaning of SC-003 | Per-class input drift on SCL-clear pixels vs a day-of-year-matched reference AND (prediction-class shift OR weak-label mIoU drop); three-state verdict; thresholds frozen after a ≥ 12-month replay | new | RS:T034-T037, P-8 |
| B-15 | Data source and harmonization | Keep Earth Search `sentinel-2-l2a`; harmonize by processing baseline onto a fixed 10 m UTM grid; operator records live fixtures | new | RS:T013/T016/T017, P-7 |
| B-16 | Off-MVP scope | Fine-tune T030-T032 after the Phase 3 gate; T050 after the soak | new | T030-T032, T050 placement |

**Group 3 — platform and soak readiness** (before T005/T012):

| ID | Decision | Recommended default | Age | Unblocks |
|---|---|---|---|---|
| B-17 | Backup target and data disk | Cloud plus a local tier; dedicated data disk with k3s `--default-local-storage-path`; one MGRS tile, AOI-clipped COG, 6-month backfill | new | T073, T074 |
| B-18 | Exposure and security posture | LAN + VPN only; before MVP-C: per-consumer SeaweedFS identities, MLflow auth or NetworkPolicy, git-sync on a pinned `soak` branch, k3s secrets encryption, TF state out of the checkout | new | T072, T074 |
| B-19 | Alerting | One push channel plus an external dead-man heartbeat; observability core into Phase 0 | new | RS:T046/T049 |
| B-20 | Meaning of SC-006 | Add a rebuild-plus-restore drill to the Soak Readiness Gate; T051 stays the once-during-the-soak rebuild test (constitution.md:21); a mid-soak restore does not reset the soak clock | new | RS:T048/T051 |

**Group 4 — already-pending housekeeping:**

| ID | Decision | Recommended default | Age | Unblocks |
|---|---|---|---|---|
| B-21 | D-015/D-06 (T063 checkbox) | Treat the sklearn alignment as a bugfix; check T063 after review | 25 | T063 |
| B-22 | D-012 F2/F5 | F2: training-only stride field, evaluation tiles every pixel once; F5: next-link pagination with sortby | 35 | T061 remainder, RS:T016 |
| B-23 | Close stale items | Mark D-014 triaged; retire RB-008a(b) and RB-008a(e) as WONTFIX; retire T064 only via an amendment to its governance-harness scenario | 30-45 | Housekeeping only |

**Accept-the-defaults path.** The operator may log one decision-log line accepting every
recommended default except the items they strike or change. That turns 23 decisions into one
sitting. Example only — **not logged, and not a decision**:

```
YYYY-MM-DD | <ID per decision-log rule 2> | Accept D-016/09 recommended defaults B-01..B-23 except: <B-nn struck>; <B-nn amended to option x>. EXPLICIT LIMIT: creates no G-x; flips no checkbox. | <operator>
```

Some defaults need a further artifact before they bind: B-07, B-08, B-11, B-13 and B-14 need spec
amendments (B-07's title wording also needs a constitution amendment PR); B-04 adds Phase L, which
the charter's M0-M5 milestone table does not have; B-05's DEC-002 part overrides a CONFIRM-FIRST
decision, so it must be logged as its own DEC line, not inside a bulk RB line, and it changes
charter §6's budget; B-06's fourth rule changes decision-log rule 2's RB-xxxa convention. The bulk
line itself changes no FR, SC, charter or constitution text.

---

## 3. The MVP (proposal pending B-04)

The repo never used "MVP". Constitution VI is unchanged: the 6-week soak is the deliverable. The
MVP is an intermediate milestone whose purpose is to make the soak startable. Nothing here creates
or implies a G-x entry; the gates below bind only once B-04 is logged.

**MVP-L — laptop walking skeleton** (agent-built; needs B-05's waiver). An executable acceptance
test (planned path `tests/acceptance/test_walking_skeleton.py`, package P-6) in a CI stage that
needs no Docker, GPU, network or secrets. It drives the pipeline as separate processes (forcing
persistence, config loading and a composition root) under a local config profile with file-backed
data versioning, an MLflow registry on a sqlite tracking URI, recorded scene fixtures, planted
learnable labels, a pinned holdout manifest and fixed seeds:

1. Ingest → data commit → train v1 → persist artifact + lineage envelope → register →
   operator-approve step → v1 is champion (and baseline).
2. The batch scoring job writes per-scene class histograms with the champion.
3. N shifted scenes, each in a fresh process → exactly one trigger episode; replaying a scene is a
   no-op; an INSUFFICIENT_DATA scene neither advances nor resets hysteresis.
4. Retrain v2 → the gate scores champion and challenger on the holdout → PASS → promotion; a
   label-permuted challenger → REJECT; degenerate evidence → INSUFFICIENT_EVIDENCE.
5. Rollback → v1 is champion and its weight hash matches v1's envelope; a no-target rollback
   refuses and leaves the champion intact.

Gate: MVP-L green in CI. It grows from `tests/e2e/test_user_journey_ct_loop.py`, which it replaces.

**MVP-C — First Operated Loop** (operator-run, `[HUMAN]`; the re-scoped Phase 3 gate, T040). The
same scenario on the cluster, every cluster task an adapter swap that keeps MVP-L green. Exit
evidence: G-1, G-2, G-3 logged; observability core and backups live; ≥ 2 real scenes ingested
unattended on schedule into real lakeFS; an Argo GPU training run whose MLflow run records
{lakeFS commit, git SHA, config hash}; forced drift injected on a lakeFS branch (never `main`)
producing exactly one episode with `trigger_source=forced`; at least one promotion **and** one
rejection; the champion scoring scenes; a timestamped rollback drill under 10 minutes (the first
SC-004 measurement); a reproducibility re-run within a stated tolerance (US2, T029).

**Soak Readiness Gate** (operator-run, `[HUMAN]`; its task is minted when B-04 and B-20 are
logged; before T052): restore drill passed with RTO recorded; every alert class
fire-drilled, including the off-node heartbeat; 72 h unattended burn-in with ≥ 3 scheduled ingests;
projected disk use at day 42 < 60%; drift thresholds frozen by a decision-log line after the
historical replay (in-season null run: 0 triggers); B-18's required security items closed. Then
T052.

**Out of the MVP:** T030-T032 fine-tune; the US6 canary split (T043's canary half, T044, T045);
T050; `eval/calibration.py`, `eval/ranking.py`, `eval/spatial.py`; KServe; T054/T055 except where
MVP-C needs them.

---

## 4. Sequence

Phase numbers match `specs/001-orbital-drift-ct/plan.md` § Phases, which states each gate.
Moves and gates beyond the 2026-09 text are proposals pending the ballot items named.

| Step | Slice | Who | Ballot items | Gates and tasks | Exit |
|---|---|---|---|---|---|
| 0 | Decide the ballot | Operator, laptop | Group 1 first; Group 2 before Phase L packages; Group 3 before T005/T012 | — | Decision-log line(s) |
| 1 | Phase 0 Substrate | Operator; agents author | B-01, B-02, B-03, B-17, B-18, B-19 | T074's Step 0 (node identity) and its exact driver pin and hold before T003 (runbook 00, which T003 executes, installs the driver); the rest of T074 before T005; T003 (G-1) → T005 (G-2); T006 (authored after G-1, re-reviewed after T005), T011, T072 → T012 (G-3); T046/T049 and T073 before the first scheduled DAG | Phase 0 gate; proposal adds an alert reaching the operator and one restored backup |
| L | Phase L walking skeleton (parallel with step 1) | Agents, laptop | B-04, B-05 (waiver), B-07 to B-13 | P-1 to P-6, T071, re-scoped T036 | MVP-L green in CI |
| 2 | Phase 1 Ingestion | Agents, then operator | B-15, B-22 | Re-scoped T013/T016/T017, P-7, T018-T021, T022 `[HUMAN]` | 2 real scenes ingested unattended, harmonized |
| 3 | Phase 2 Training and registry | Agents, then operator | B-07, B-08, B-10, B-11 | T024-T028, P-2, P-3, T029 `[HUMAN]` | Reproducibility plus a tagged holdout |
| 4 | Phase 3 CT loop | Agents, then operator | B-04, B-09, B-10, B-14 | Re-scoped T034-T039, P-4, P-8 (replay), T040 `[HUMAN]` | MVP-C |
| 5 | Phase 4 Serving and canary (post-MVP) | Agents, then operator | B-13, B-16 | T041-T045, T030-T032 | Canary regression alert |
| 6 | Phase 5 Soak | Operator | B-18, B-20 | T047, T048, P-8 freeze, P-9; Soak Readiness Gate (operator-run, includes the rebuild-plus-restore drill); then T052, with T051 during the soak (B-20); T050 after the soak | Soak Readiness Gate, then Constitution VI |

---

## 5. Where the project actually is

(Was §1 in the 2026-09-11 revision.) Phase 0 of 6. Checkbox state lives in tasks.md: 14 of 73
task lines are checked (T001, T001a, T001b, T002, T004, T004a, T007-T010, T056, T058, T062, T065;
`grep -c '^- \[x\] T'` and `grep -c '^- \[[ x]\] T'` over tasks.md). PR#16/#17 landed most Phase
1-4 application code ungated; RB-010 marked T013-T052 AUTHORED-PROVISIONAL pending T057. RB-010
Parts 3 and 14 remain open behind B-09/B-10.

| Area | Built (c545701) | Not built or defective | Evidence |
|---|---|---|---|
| STAC ingest | Real client, retry/backoff, Earth Search `sentinel-2-l2a` | On Earth Search v1 asset keys B02/B03 are dropped without error (synthetic item; unverified live, agent egress blocked); no pagination; a final non-200 returns `[]` silently; the cloud prefilter discards cloudy scenes instead of storing and flagging them | D-016/05a, 05c, 05e; first pass H11 (`ingest/stac_client.py:211-214`) |
| Cloud mask | SCL masking, cloud fraction | Raises IndexError on real 20 m SCL vs 10 m bands; masked pixels filled with 0 (the L2A NO_DATA value); snow counted clear; no PB04 harmonization | D-016/05b, 05c |
| Tile store | Local `.npy` save/load | No COG, windowed reads, S3, retention or atomic writes | D-016/05f |
| lakeFS | Commit/branch/pin simulated in-process, labelled `[SIMULATED]` (T056) | No SDK, no server | T060; B-12 |
| Training | U-Net, AMP, grad-accum, IoU/F1 | Single-epoch function: no fit loop, validation, seeding or checkpoint; no persistence (no `torch.save`/`state_dict` in `src/`) and no entrypoint; `in_channels` literal 4 | D-016/03a, 03b; first pass H3 (search of `src/` for `torch.save`, `state_dict`, `torch.load`: 0 hits) |
| Registry | Stage machine with locking (T062) | An in-process dict not labelled as a simulation; rollback can promote a never-served rejected version, or leave no Production model | D-016/03c (T071); first pass H10 (`[SIMULATED]` appears only in `data/lakefs_ops.py`) |
| Drift | PSI (hand-written) + KS (scipy); hysteresis and cooldown from config | Trigger state lives in memory and cannot survive one pod per task (0 triggers over 30 drifted scenes with a fresh manager per scene); clouds and fill scored as drift; no prediction-class shift, Prometheus export or reference builder | D-016/04a, 04b, 04c, 08 |
| Eval | `superiority_gate`, 2-D moving-block bootstrap | No caller in `src/` or in either e2e test (search for `superiority`); decides on an inflated metric (absent classes score IoU 1.0: a constant predictor gets mIoU 0.850 vs 0.250 present-class); miscalibrated decision rule; Principle II violation open | D-016/02a, 02c, 02d, 08; B-09 |
| Config | pydantic-settings, 38 fields | `get_config()` called nowhere in the repository; 17 of 38 fields unread; lakeFS secrets required in every process | First pass H4 (attribute reads of each `OrbitalDriftConfig` field outside `config.py`); D-016/07c |
| Observability | JSON logging with redaction, context binding | `configure_logging()` has no call site in `src/` (outside the package only `eval/` imports it, for `get_logger`), so redaction never runs in production | T054 |
| Serving | FastAPI, canary routing, `/livez` vs `/readyz` | Never loads a model outside tests; device resolution wrong under UUID pinning; work at import time | D-016/03e, 03f, 07d; T053 |
| Orchestration | — | `dags/` and `workflows/` hold only `.gitkeep`; no `[project.scripts]`; nothing composes the pipeline outside tests | D-016/07a |
| Platform IaC | T007-T010 authored and reviewed | One shared CNPG database and role; no backup; SeaweedFS BestEffort; one shared Admin storage identity | D-016/06a, 06b, 06d, 06g |

---

## 6. Work

**Minted** in tasks.md Phase 7 (RB-016 authorizes the execution of none; each needs its own
authorization):

- **T071** (ml-engineer) Registry rollback restores exactly the previous champion; D-016/03c.
- **T072** (infra-scaffolder) Platform data-plane correctness before T012: a database and role per service, SeaweedFS resources and per-consumer identities, image pins; D-016/06b, 06d, 06g, 06i.
- **T073** (infra-scaffolder) Backup and restore for the soak; D-016/06a; needs T072 and B-17.
- **T074** (runbook-writer) Runbook 00/01 addendum: node identity, data disk, secrets encryption, driver hold, GPU env-form test; D-016/01g, 06c, 06e, 06g, 06h; needs B-01, B-03, B-17; T074's Step 0 (node identity) and its exact driver pin and hold before T003 (runbook 00, which T003 executes, installs the driver), the rest of T074 before T005.
- **T075** (runbook-writer) Runbooks for the `[HUMAN]` tasks that have none: T029, T032, T049, and T050 or its deferral (B-16).

**Re-scoped:** 34 existing task lines carry an RB-016 marker
(`grep -c 'RB-016:\*\* re-scoped' specs/001-orbital-drift-ct/tasks.md`); the re-scope table is in
tasks.md Phase 7, where every element that implements a ballot default is marked as a proposal. No
checkbox changed.

**Minted on decision** (D-016/11). Not in tasks.md: minting a task whose shape depends on an open
ballot item would encode a choice the operator has not made.

| Package | Scope | Needs |
|---|---|---|
| P-1 | Composition root; local and cluster config profiles (keys required only for cluster, SecretStr); CLI entrypoints; no import-time work in `serve/app.py` | B-05, B-12 |
| P-2 | Model artifact and input-contract card: weights-only format, band order, normalization, class map, architecture, lineage; config hash without secrets | B-07 |
| P-3 | Holdout manifest (spatial blocks in a ground CRS, buffered ≥ 92 px, hashed, lakeFS-tagged) and evaluation accumulator (per-cluster confusion matrices, ignore mask) | B-07, B-08, B-10 |
| P-4 | Gate replacement and calibration tests (RB-010 Parts 3 and 14, re-scoped) | B-09, B-10 |
| P-5 | Batch scoring job: classified COG and class histogram per scene, alias version recorded | B-11, B-13, P-2 |
| P-6 | Walking-skeleton acceptance test and its CI stage (needs a new FR), with a positive control: a planted seam defect turns the stage red | B-04, B-05, P-1 to P-5, T071, re-scoped T036 |
| P-7 | Harmonized AOI loader: fixed 10 m UTM grid, SCL nearest-resampled, PB offset applied, NO_DATA and cloud masked, fractions recorded | B-15 |
| P-8 | Historical replay and threshold freeze: ≥ 12 months offline, in-season null window 0 triggers, thresholds into config plus a decision-log line | B-14, P-7 |
| P-9 | Data retention and capacity: compressed AOI crops, retention policy, capacity alert | B-17 |

---

## 7. Former tracks

The 2026-09-11 revision organised work as Tracks A-E; `docs/architecture/ARCHITECTURE.md:72`,
D-013 and Epic E6's summary still cite them.

| Track (2026-09-11) | Now |
|---|---|
| A — make the hexagon load-bearing (T058) | T058 checked; port disposition is B-12; composition root is P-1. ARCHITECTURE.md's "Track A (adapter convergence)" resolves to B-12 plus T071 |
| B — replace the simulations (T056, T059, T060) | T056 checked; T059/T060 after B-12 (and B-11); Phase L uses file-backed local adapters |
| C — close out RB-010 (T057, Parts 3 and 14) | Parts 3 and 14 become P-4 after B-09/B-10; T057 runs on composed code after Phase L |
| D — gate integrity (T061, T062) | T062 checked; the rollback target is T071; T061 F2/F5 per B-22 |
| E — deployment reality (T053, T054, T055) | T053 re-scoped (D-016/03f); MVP serving pattern is B-13; T054/T055 out of the MVP unless MVP-C needs them |

---

## 8. Rollback drill — withdrawn, not moved

(Was §5 in the 2026-09-11 revision; `docs/development/REFERENCE_GUIDE.md` and the SC-004
traceability row cite it by that number.) An earlier version of this file carried a four-step
drill. It was removed rather than corrected, and stays removed: two steps named symbols that do
not exist (`ModelRegistryOps.rollback_production_model()`, real name `rollback_production`;
`container.update_canary_ratio(0.0)`, no such method), and nothing loads a production model outside
tests, so there is nothing to roll back. The broken names remain in
`.claude/skills/canary-rollback-drill/SKILL.md`; fixing them is re-scoped T039.

Correct names would not be enough: `rollback_production` restores the highest-numbered Archived
version, which can be a rejected challenger that never served (D-016/03c). T071 fixes the target;
T039 (`docs/runbooks/04-ct-ops.md`, `05-rollback.md`, runbook-writer, unwritten) then writes the
drill as a move to the recorded previous champion. SC-004's first measurement is part of MVP-C.
Until T039 lands there is no rollback procedure in this repository, and this file will not
pretend otherwise.
