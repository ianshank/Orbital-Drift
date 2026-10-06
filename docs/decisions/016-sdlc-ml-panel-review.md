# D-016: SDLC and ML panel review of c545701, operator ballot, and two-stage MVP proposal

**Audience:** the operator, who decides the ballot (D-016/09); agents executing any package or
task that a later decision-log line authorizes.
**Status:** RECORDED 2026-10-06 under RB-016 — findings and proposals; every ballot default is
a panel PROPOSAL, not a decision.
**Provenance, stated precisely:** at the operator's in-session request for a deeper peer review
and a rewrite of the plans by SDLC and ML experts (RB-016, decision-log rule 6), seven
review-only panelists (roster below) reviewed main at `c545701`, and the orchestrator (ORCH)
independently reproduced three findings (D-016/08). The review builds on an adversarially
verified first-pass synthesis of the same commit (H1-H19, M1-M5; not committed), cited "first
pass Hn" with its evidence restated. Written by runbook-writer from the orchestrator's brief;
the panel reports are not committed. **The operator has NOT seen or chosen any option here.**
**Decision-ID namespace:** this file's `D-016/0n` findings (sub-findings cited `D-016/02a`) and
`D-016/B-nn` ballot items are independent of plan.md's `D-01…D-05`, of every other
`docs/decisions/*.md` `D-nn` series, of `docs/decision-log.md`'s `DEC-`/`RB-`/`G-` IDs, and of the
retired roadmap labels D-1/D-2/D-3, which map to B-09, B-12 (+B-11), and B-01 + the T003
[HUMAN] task. NEXT_STEPS.md and plan.md may cite ballot items as bare `B-nn`.
**Measured at:** main `c545701` (2026-10-06); every file:line below is at that commit.
`[domain knowledge]` / `[upstream source]` = not provable from the tree; "synthetic" = a
simulation number, not real imagery; UNVERIFIED = not checkable from this environment.
**Why this exists:** NEXT_STEPS.md, plan.md and tasks.md Phase 7 cite these IDs; without this
file those citations point at chat.

| Code | Panel role | Agent (all review-only) |
|---|---|---|
| SDLC | engineering director / delivery process | general-purpose |
| EVAL | principal ML scientist, evaluation statistics | general-purpose |
| DRIFT | remote-sensing data + drift detection | drift-engineer |
| MLE | senior ML engineer: training, registry, serving | ml-engineer |
| CT | staff MLOps / continuous-training architect | mlops-ct-agent |
| ARCH | principal software architect + test strategist | Plan agent |
| PLAT | platform / SRE / security | infra-scaffolder |
| ORCH | orchestrator (verification only) | — |

Severity: **Critical** invalidates the plan or MVP as specified; **Major** changes sequencing,
scope or a decision; **Minor**. Repro = independently reproduced by another panelist or ORCH.
Dispositions: `T07x` minted task; `RS:Txxx` re-scope of an existing task (tasks.md Phase 7;
checkbox unchanged); `B-nn` ballot item; `P-n` package minted on decision; `FU` unscheduled
follow-up; `FIX` fixed in the RB-016 PR.

---

## Summary

1. **The binding constraint is the operator's queue of unmade decisions and physical tasks, not
   agent capacity, and nothing measures its age** (D-016/01a). Every agent-executable task ID is
   blocked (first pass H1); main has had 0 merges in 25 days; 0 of the last 10 merged PRs added
   capability (D-016/01c).
2. **As specified, the plan cannot reach its own definition of done.** The drift trigger cannot
   fire under the mandated executor (D-016/04a); the promotion gate cannot reject a regression and
   decides on an inflated metric (D-016/02a-b); rollback can restore a rejected model
   (D-016/03c); one disk failure ends the 6-week soak because nothing is backed up (D-016/06a).
3. **The proposed fix:** decide the ballot (D-016/09; most items carry a recommended default and
   can be accepted in one decision-log line), then a two-stage MVP — a laptop walking-skeleton
   acceptance test (MVP-L), then the same scenario operated on the cluster (MVP-C) — then a Soak
   Readiness Gate, then the soak (D-016/10).
4. **RB-012 answered this same request** ("deeper peer review, rewrite plans") 31 days ago; the
   decisions it surfaced are still open. This rewrite is useful only if the ballot is decided.

---

## D-016/01 — Delivery process and governance load

Panel: SDLC unless noted. Repro: none beyond first-pass items cited inline.

- **01a Critical — operator decision latency is the binding constraint, and is unmeasured.**
  Ages from git dates: T003 startable 51 d (T002 runbook ready 08-16; G-0 logged 08-21, 46 d);
  D-008/D-03 PROPOSED 45 d (08-22); versions.md last re-verified 45 d ago against its own ~30-day
  rule (versions.md:5); D-011 memo (old "D-1"), adapter disposition (old "D-2") and D-012 F2/F5
  35 d (09-01); AR-3 >= 35 d (open since RB-010); RB-012a(d) budget escalation 31 d; D-014
  "awaiting triage" 30 d; D-015/D-06 25 d. All 16 log entries since G-0 are RB (0 G, 0 DEC); 22
  of 27 entries say "EXPLICIT LIMIT" or "unlocks no". → B-06; NEXT_STEPS §2.
- **01b Major — effort is inverted against Constitution II's rationale** ("not another showcase
  of the operator's own machinery", constitution.md:15). `wc -l`: product src 4,549; product
  tests 6,467 (385 collected); infra 3,196 (11 tests); governance src 2,556; governance tests
  16,669 (831 collected, 67.7%); ci/scripts/hooks/.github 4,488; .claude/openspec/charter 1,623.
  Governance outweighs product + infra 1.78x. Decision log + decision docs 56,558 words vs
  runbooks 7,793 (2 of >= 7 written). → B-06 (governance freeze until MVP-C).
- **01c Major — flow stopped.** PRs merged per week, Aug 10-Oct 5: 3, 13, 0, 12, 2, 0, 0, 0, 0.
  All 30 merged PRs: 4 capability (#3, #7 Phase-0 infra; #16, #17 ungated), 10 remediation, 16
  process. On or after 09-06: 10 merged (9 remediation, 1 process, 0 capability). #23-#28 show
  empty diffs against their first parent because #29 bundled them. → B-05, B-06.
- **01d Major — DEC-002 cannot constrain anything.** The counter is per milestone and every
  post-#17 PR is M1-M4 work on unopened milestones, so "outside M0" is true by construction;
  hours were never measured (only RB-007's "<= 12 h" estimate); the two reserved M0 slots (T006,
  T011) need G-1. The first pass's count stands: 24 PRs outside the budget, 2 inside (H7).
  → B-05.
- **01e Major — gates bind only Claude.** Commit authors (`git log --format=%an`): Claude 91,
  operator 20, Cursor Agent 15, copilot 2. PRs #16/#17 (7,085 product lines, no G-1, no review)
  were operator commits; #30/#31 came from Cursor. "Gates read THIS file" (decision-log.md:3),
  yet a grep for G-1/G-0 in ci/, tests/, src/, scripts/, .github/ finds only a docstring
  (tests/governance/test_governance_meta.py:283) and story text (roadmap_data.py:174). → B-06
  (an operator merge is a decision, logged the same day).
- **01f Major — status is asserted in 11 places; 5 disagree with tasks.md.** README.md:9-21 (six
  contradicted claims: "Planetary Computer" vs Earth Search at stac_client.py:42; rate-limited
  and windowed reads; MLflow logging; Prometheus telemetry; sub-10-minute rollback drills; eval/
  "satisfying Constitution II"); README.md:285 ("10 of the 55" vs 14 of 68); NEXT_STEPS.md:102
  and :178 vs D-015:33-35 (three different Part-F unlock rules); plan.md:32 ("7 subagents" vs 10);
  D-014's header ("awaiting operator triage", though #23-#29 executed its findings 1-3, 6, 7).
  → FIX for NEXT_STEPS and plan.md in the RB-016 PR; FU for README and the D-014 header.
- **01g Major — an unrecorded physical prerequisite is on the critical path.** The only GPU host
  the repo has measured is a Windows machine with node A's GPU pair
  (docs/incidents/2026-09-03-full-suite-triage.md:7); runbook 00 assumes Ubuntu 24.04
  (00-host-prep.md:13); D-000/D-10 treats the Windows box as the authoring machine. T003 may mean
  reimaging or dual-booting the authoring workstation; no document says which. → B-01, T074.
- **01h Minor — the decision log has become a narrative store.** 27 entries, 7,830 words, mean
  290, median 185; 08-21 entries average 90 words, 09-05 onward 894; RB-xxxa execution records
  are 36.7% of the words. → B-06 (entries <= 150 words). RB-016 follows that limit.
- **01i Minor — orphaned deferrals open 45 d:** RB-008a(b) REPO_ROOT single-homing (3 copies
  remain) and RB-008a(e) rule 8. → B-23 (retire as WONTFIX; T064 only via a scenario
  amendment).

## D-016/02 — Evaluation and the promotion gate

Panel: EVAL. Repro: MLE, CT, ARCH; ORCH reproduced 02a.

- **02a Critical — the primary metric is inflated and can rank models backwards.**
  `compute_iou_f1` (train/baseline.py:219-262) scores a class absent from both prediction and
  target as IoU 1.0 and macro-averages over all `num_classes`. ORCH: constant all-class-0
  predictor, target classes {0,1}, `num_classes=10` → mIoU 0.850; present-class mIoU 0.250. One
  stray pixel of an absent class moves a patch from 1.00 to 0.90, 5x the default
  `auto_promote_margin` 0.02 (config.py:225). Per-patch-mean vs pooled mIoU reverses a ranking
  (A-B = -0.206 vs +0.262; synthetic, 50 patches). No `ignore_index`: 25% label-255 pixels cap a
  perfect prediction at 0.95; label padding uses class 0, a real class (data/dataset.py:96-99).
  RB-013 pinned the empty-class convention. → B-10; RS:T026.
- **02b Critical — no leakage-safe split exists or is planned.** data/dataset.py has no split and
  no ground coordinates; stride < patch_size overlaps patches. Both e2e tests evaluate the
  challenger on its own training patch, discard the metric and promote unconditionally
  (tests/e2e/test_user_journey_ct_loop.py:114,130,142; tests/e2e/test_dual_gpu_e2e_live.py:211-229).
  Under static weak labels a temporal "held-out recent window" (spec.md:35) rewards memorising
  locations. [domain knowledge] Adjacent MGRS tiles overlap ~10 km. No task creates a holdout.
  → B-08; P-3.
- **02c Major — the 2-D moving-block bootstrap does not control false promotions under spatial
  autocorrelation.** EVAL synthetic simulation (300 seeds, 399 replicates, true difference =
  margin; correct rate 0.05): 16x16 iid 0.053; 6x6 iid 0.10-0.18; moderate autocorrelation
  0.21-0.41; strong 0.34-0.45; same area as dispersed clusters >= 4.5 sigma apart 0.033-0.073.
  Edge cells are under-sampled (eval/bootstrap.py:108-109 does not wrap). No block-size rule or
  config field. → B-09.
- **02d Major — the decision rule tests the wrong bound.** `passes = lower > minimum_effect` on
  a two-sided interval's lower bound (eval/superiority.py:121-124): the real level is
  (1-confidence)/2. At SE 0.01, margin 0.02, true gain 0.03 (synthetic) it promotes 26% of the
  time vs 84% under rule B (point >= margin AND one-sided 95% lower bound > 0); false promotion
  0.0% vs 2.3%. `replicates=1` is accepted; no INSUFFICIENT_EVIDENCE outcome exists. → B-10.
- **02e Major — the gate cannot express the primary metric.** `superiority_gate` applies one
  metric to a flat vector of per-cell floats (superiority.py:84-119); pooled mIoU is a ratio of
  class-summed counts. Fix shape: per-cluster KxK confusion matrices summed under resampling.
  → B-09/B-10 (RB-010 Part 3 re-scope).
- **02f Minor — off the MVP path:** calibration.py (binary ECE; calibration research is a spec
  non-goal, spec.md:74), ranking.py (no consumer), spatial.py (patches
  `numpy.random.permutation` process-wide via `unittest.mock`, spatial.py:19,91). → B-21
  (defer), FU.

## D-016/03 — Training, artifacts, registry and serving

Panel: MLE. Repro: CT, ARCH; ORCH reproduced 03c; ARCH and MLE both reproduced 03g.

- **03a Major — training is a single-epoch function:** no fit loop, validation, early stopping,
  checkpoint or LR schedule (train/baseline.py:265-317); no seeding in src/ (grep
  `manual_seed|cudnn|worker_init_fn`: 0); GradScaler recreated each epoch (:296); the last
  partial accumulation window is under-weighted; 4.1% of a 10,980-px tile is never trained or
  scored (edge strips dropped, data/dataset.py:65-66). [domain knowledge] Blackwell supports bf16
  autocast with no scaler. f=32 U-Net: 1.93M params; batch 16 ~3 GB fp32, so gradient
  accumulation is unnecessary on 16 GB and perturbs BatchNorm. → RS:T026.
- **03b Major — the input contract cannot travel with the model.** `in_channels` defaults to a
  literal 4 (baseline.py:147) while bands are configurable (13 bands raises RuntimeError);
  `num_classes=10` (config.py:155) matches no candidate label set (WorldCover 11 codes, Dynamic
  World 9, DynamicEarthNet 7, OSCD 2 [domain knowledge]); normalization is one global scalar
  divisor (default 10000, data/dataset.py:90); `LineageEnvelope` carries no band order, scaling,
  class map or architecture. → P-2; B-07.
- **03c Critical — rollback can restore a model that never served.** `rollback_production`
  (registry/ops.py:185-209) promotes the highest-numbered Archived version. ORCH: v1 →
  Production, v2 → Production (v1 archived), v3 → Archived (rejected, never served); rollback
  returns 3. With one version in Production, rollback archives it and returns None, leaving no
  Production model. Tests cover the two-version happy path and pin the single-version `None`
  without asserting what remains in Production (tests/unit/test_registry_ops.py:88-116). T062
  fixed locking only. ports/registry.py's in-memory fake already has ordered-history semantics
  (:59-68). This overturns D-013/04b's recommendation to wrap an unmodified ModelRegistryOps.
  → T071; B-11/B-12.
- **03d Major — FR-006 (spec.md:55) is written against stages.** [domain knowledge, high] MLflow
  deprecated stages in 2.9 for aliases and tags; the pinned server is 3.15.1
  (infra/helm-values/mlflow.yaml:21). Aliases keep no history, so the rollback target must be
  recorded at promotion. T062's `threading.Lock` does not serialise across Airflow pods;
  queue-depth-1 is the real serialiser. → B-11.
- **03e Major — online serving is the wrong MVP shape.** `/predict` takes a client-normalized
  nested JSON float list (serve/app.py:102-104); one 4x256x256 request is 5.1 MB, one MGRS tile
  ~9 GB of JSON; nothing produces scene-level predictions, which FR-007's prediction-class shift
  needs; "canary regression" has no metric, threshold or config field (spec.md:35,43). → B-13.
- **03f Major — device resolution is wrong under the planned GPU pinning.**
  `config.serve_device` defaults to "cuda:1" (config.py:113-114), but UUID pinning exposes one
  GPU as cuda:0, so the config path fails as well as the H9 fallback (serve/app.py:237-243 →
  cpu). [domain knowledge, medium-high] uvicorn binds only after lifespan startup, so loading a
  model in lifespan leaves `/livez` dark during download and CUDA init; torch import alone
  measured 3.1 s vs a 10 s start period (Dockerfile:91). → RS:T053, RS:T043; T074 (env-form
  verification at T012).
- **03g Minor — the config hash is unusable as a lineage key.**
  `build_run_metadata(cfg.model_dump())` raises TypeError (PosixPath); with `mode="json"` the hash
  includes `lakefs_secret_key` and changes on rotation and on serving-only fields. Two lineage
  producers exist (train/baseline.py:320-332, domain/lineage.py:89-180). → RS:T026/T029; P-2.
- **03h Minor — SC-002 (< 12 h) holds only if work is capped.** 55.4 GFLOP fwd+bwd per patch; 6
  dates x 40 epochs ~94 PFLOP, 3.3-8.7 h at an assumed 3-8 TFLOPS sustained [low-medium
  confidence]; nothing enforces a budget. Measure patches/s, `max_memory_allocated`, per-stage
  seconds, trigger-to-verdict timestamps. → RS:T029.

## D-016/04 — The CT loop and the drift trigger

Panel: CT, DRIFT. Repro: PLAT; ORCH reproduced 04a.

- **04a Critical — trigger state cannot survive the mandated executor.** DriftTriggerManager
  keeps counters, cooldown and the active flag in memory (drift/trigger.py:103-117); Airflow
  runs KubernetesExecutor, one pod per task (infra/helm-values/airflow.yaml:31; plan.md:13).
  ORCH: 30 consecutive drifted verdicts, fresh manager per scene → 0 triggers (one long-lived
  manager → 1). Naive persistence double-counts Airflow retries and multi-MGRS items of one
  acquisition; starvation either resets hysteresis or reads as drift. SC-003, a Constitution VI
  definition-of-done item (constitution.md:29), cannot be met organically. T033/T036 are marked
  AUTHORED-PROVISIONAL — DONE on this premise. → RS:T033/T036, RS:T037.
- **04b Critical — per-band PSI/KS on pixels tracks season and nuisance, not model harm.** DRIFT
  ran a synthetic phenology + nuisance model through the repo's own functions at default
  thresholds. Every case with 0.10 <= PSI < 0.25 had KS p < 0.05 (84 of 84; critical D ~0.027 at
  n=5000): the effective threshold is PSI >= 0.10 and 0.25 never decides. Nuisance only (1.5-3%
  gain, +/-30 DN) flags 20-40% of scenes; P(>= 1 false trigger in 6 weeks) 9-52%. An off-season
  or pooled reference flags 17 of 17 and retrains every ~12 days (the hysteresis-3 + cooldown-5
  maximum). A harmful shift (accuracy 0.887 → 0.758) kept whole-scene PSI at 0.04-0.07 while
  predicted-class Jensen-Shannon divergence rose 0.036 → 0.38. The direction held across two
  parameterisations; magnitudes did not. → B-14; P-8.
- **04c Major — clouds and fill are scored as drift.** `evaluate_scene_drift` takes no mask
  (drift/metrics.py:201-212); 15% cloud fill below the exclusion threshold gives PSI 0.14
  (flagged); 35% NO_DATA gives 0.64-0.76; an all-snow SCL scene reports `cloud_fraction` 0.0.
  → RS:T035, RS:T017.
- **04d Major — no reference rebasing** after promotion, rollback or rejection, and no backoff
  after a reject (`mark_retraining_completed` only resets the counter, trigger.py:217-222).
  → RS:T034.
- **04e Major — the Phase-3 gate rewards weakening the promotion gate.** plan.md:103 asks only
  for trigger → promotion. T040 injects forced-drift scenes onto main, the training reference
  (spec.md:52); nothing records forced vs organic triggers. → MVP-C requires one promotion AND
  one rejection, injection on a lakeFS branch, and a recorded `trigger_source`; RS:T038/T040.
- **04f Major — nobody owns the Airflow → Argo handoff.** Argo server auth mode unset
  (infra/helm-values/argo-workflows.yaml:63-75); no RoleBinding lets Airflow's service account
  create Workflows in orbital-drift-training; T027/T038 define no parameters or outputs. CT's
  proposed contract, the starting design for RS:T027/T038: template `orbital-drift-train-eval`;
  params `data_ref` (commit id, never a branch), `eval_ref`, `git_sha`, `image_digest`,
  `config_hash`, `config_uri`, `seed`, `episode_id` = sha256 of the first four; Workflow name
  `train-<episode_id[:12]>`, so AlreadyExists means attach-and-poll; outputs `mlflow_run_id`,
  `candidate_version`, `gate_evidence_uri`; `activeDeadlineSeconds` below the SC-002 budget;
  retry infrastructure errors only. → RS:T027, RS:T038.
- **04g Major — static-label retraining is legitimate CT with a known bias:** it learns a fixed
  land-cover mapping robust to new radiometry but penalises real land change after the label
  year [domain knowledge], and shadow eval measures agreement with the label product, meaningful only on spatially
  unseen blocks. → B-07, B-08.

## D-016/05 — The Sentinel-2 data path

Panel: DRIFT; partly corroborated by MLE.

- **05a Major, UNVERIFIED against the live catalog — docs and code name different sources; two
  default bands may be silently dropped.** Code targets Earth Search v1, `sentinel-2-l2a`
  (stac_client.py:42-43; config.py:47-53; spec.md:50); README.md:9 and CHANGELOG.md:108 say
  Planetary Computer. On a synthetic item shaped like Earth Search v1 (asset keys
  blue/green/red/nir/scl [domain knowledge, ~85%]) stac_client.py:224-241 resolves only B04, B08
  and SCL; B02 and B03 are dropped without error. UNVERIFIED: the egress proxy blocked
  earth-search.aws.element84.com. Fixtures are hand-written v0-style keys
  (tests/contract/test_stac_client.py:58-66), not the recorded fixtures T013 requires. → B-15;
  RS:T013, RS:T016.
- **05b Major — no radiometric harmonization or common grid.** [domain knowledge, high]
  Processing baseline 04.00 (from 2022-01-25) adds a +1000 DN offset; nothing reads
  `s2:processing_baseline` or BOA offsets. In a synthetic simulation a PB < 04.00 reference vs a
  PB >= 04.00 scene gives PSI ~10 in three bands (instant trigger). SCL is 20 m vs 10 m bands, so
  `apply_cloud_mask` raises IndexError on real-resolution arrays (ingest/cloud.py:128-130, DRIFT
  probe); masked pixels are filled with 0, the L2A NO_DATA value (cloud.py:116). → RS:T017; P-7;
  B-15.
- **05c Major — one threshold drives three different cloud fractions, and the catalog prefilter
  destroys the starvation signal.** stac_client.py:156-176 filters
  `eo:cloud_cover <= threshold*100`, so scenes above it are never stored (contradicting US1's
  "stored but flagged", spec.md:19) and "cloudy" cannot be told from "nothing published"
  (spec.md:67). No dilation; snow counts as clear. → RS:T016, RS:T017.
- **05d Major — PSI is hand-written** in numpy (drift/metrics.py:54-92) though pyproject.toml:89
  says "nothing bespoke" and plan.md:16 names a standard library; D-011/D-1 covers only eval/.
  Thresholds tuned to `EPSILON=1e-6` (metrics.py:19) will not carry over to a library's binning.
  → B-09 (extended to drift/) before thresholds are frozen.
- **05e Minor — backfill truncates.** `limit=10`, no sortby or pagination
  (stac_client.py:134,170-178) truncates any backfill beyond ~2 weeks; SC-001 needs the item's
  publication time, not acquisition time. → B-22, RS:T016.
- **05f Minor — volume is unplanned.** The default bbox (2,433 km²) is ~201 MB per acquisition as
  uint16 (785 MB float64); a full MGRS tile ~0.99 GB; 6 weeks of 8-17 acquisitions [domain
  knowledge] is 1.6-3.4 GB
  of AOI crops or 16-34 GB of full tiles; uncompressed .npy (ingest/tile_store.py), no retention
  policy. → B-17; P-9.

## D-016/06 — Platform reliability, capacity and security

Panel: PLAT. Repro: none recorded, except 06b (= first pass H8).

- **06a Critical — no backup or restore.** cnpg-cluster.yaml:61-64 `backups: {}` (barman
  deferred: needs cert-manager); SeaweedFS single replica on hostPath (seaweedfs.yaml:24-47;
  [upstream source] chart 4.41 `defaultReplication "000"`); CNPG PVC on local-path under
  /var/lib/rancher/k3s, which k3s-uninstall.sh deletes [domain knowledge, high]; no Terraform
  backend (state in the checkout); lakeFS encrypt key and Airflow Fernet key only in TF_VAR,
  state and Secrets; lakeFS needs both its Postgres KV and the blockstore `_lakefs/` ranges. A
  disk failure in week 4 loses registry, commits and history, breaks every lineage triple, and
  restarts the soak clock. CNPG barman to SeaweedFS is circular (same disk, same node).
  constitution.md:21 says the rebuild is tested "during the soak", but tasks.md orders T051
  before T052. → T073; B-17, B-20; RS:T048, RS:T051.
- **06b Critical — Airflow, MLflow and lakeFS share CNPG database `app` and one role**
  (airflow.tf:153; mlflow.tf:106 + terraform.tfvars.example:65; lakefs.tf:162;
  cnpg-cluster.yaml:45-51). → T072.
- **06c Major — capacity is unplanned and disk exhaustion is silent.** [upstream source] k3s
  evicts at 5% free nodefs/imagefs with no memory.available signal; SeaweedFS goes read-only only
  at 1% free; local-path PVC sizes are not quotas; lakeFS OSS GC is a Spark job (outside the fixed
  stack), so nothing is reclaimed; side-loaded images are GC'd under DiskPressure with no
  registry to re-pull from. → B-17; T074 (data filesystem); P-9.
- **06d Major — SeaweedFS runs BestEffort:** seaweedfs.yaml:62-68 sets a top-level `resources:`
  that [upstream source] chart 4.41 ignores (it reads per-component keys); logs default to
  hostPath /storage, outside the data directory. → T072.
- **06e Major — UUID pinning may not go through the path D-000/D-03 verified.** [upstream
  source] GPU Operator v26.3.3 enables CDI by default and forces
  `NVIDIA_CONTAINER_RUNTIME_MODE=cdi`; then `NVIDIA_VISIBLE_DEVICES=GPU-<uuid>` resolves against
  static CDI specs, and only `runtime.nvidia.com/gpu=GPU-<uuid>` uses the just-in-time generator.
  Env pinning hides GPU use from the scheduler, so training and shadow eval can stack on the 16 GB
  card. → T074 (two-pod env-form test at T012); RS:T027 (Argo mutex per GPU).
- **06f Major — failure detection arrives after the CT loop.** T046/T049 sit in Phase 5;
  ServiceMonitors are disabled until Prometheus CRDs exist (e.g. argo-workflows.yaml:55-59);
  [upstream source] kube-prometheus-stack defaults to 10-day retention on emptyDir. An ingest that
  "succeeds" with zero scenes (first pass H11: stac_client.py:211-214 returns `[]` when every
  attempt is non-200) or a crash on day 9 goes unseen until the weekly log; a power cut takes
  Alertmanager down with the node. → B-19; RS:T046, RS:T049 (Phase 0 before the first scheduled
  DAG; PVC; >= 56-day retention; off-node dead-man heartbeat).
- **06g Major — blast radius (ranked for a LAN-only lab).** One SeaweedFS Admin identity is
  shared by every consumer and copied into the training namespace (seaweedfs.tf:117-148;
  argo_workflows.tf:263-283), so a training pod can rewrite lakeFS storage underneath
  versioning; MLflow auth is off (mlflow.yaml:84-85) with no NetworkPolicy, and pickled PyTorch
  models make a registry write code execution in serving [domain knowledge]; git-sync tracks main HEAD
  (airflow.yaml:143-151), so every merge deploys mid-soak; plaintext secrets in local TF state; no
  `--secrets-encryption` in the k3s install (01-k3s-install.md:107-108); no serving auth; no
  image signing or SBOM. → B-18; T072 (identities); T074 (secrets-encryption).
- **06h Major — pins are stale and the host is unpinned.** versions.md is 45 d old. [upstream
  source] k3s v1.35.9 (2026-09-30) exists, still on containerd 2.2.7 inside D-000/D-07's range,
  with a gRPC CVE fix; GPU Operator v26.7.1 exists; the P40 is Pascal, whose last driver branch is
  R580 and whose PyTorch wheels (cu126) stop at 2.14, so T050 needs a second driver branch and
  image. 00-host-prep.md:102 installs the `nvidia-driver-610-open` branch package with no exact
  version and no hold (a mid-soak apt upgrade breaks NVML until reboot). → B-03, B-16; T074.
- **06i Minor.** 20-platform is one root module (T012 is all-or-nothing); SeaweedFS image
  untagged (seaweedfs.yaml:14-18); CNPG Postgres image unset (restore compatibility);
  versions.md:18-24 duplicates :11-17. → T072 (pins); FU (module split, dedupe).

## D-016/07 — Architecture and test strategy

Panel: ARCH. Repro: none recorded; the three seam defects counted in 07a are D-016/08's.

- **07a Critical — nothing composes the pipeline before a [HUMAN] hardware gate.** No
  `[project.scripts]`; every `__main__` is governance tooling; dags/ and workflows/ are empty.
  tests/e2e/test_user_journey_ct_loop.py alone covers 19% of src, 571 of 1,707 product statements
  (33%), and 0% of domain/, ports/, eval/, observability/, ingest/stac_client.py,
  ingest/cloud.py. The test is the composition root and the artifact store (models reach serving
  by Python reference, :83,148,172). Under the current plan every seam defect first appears on
  node A at T040/T045. → B-04 (MVP-L); P-1, P-6.
- **07b Major — port fit.** catalog vs STACClient → wire (shapes fit; TemporalRange/BoundingBox
  are STAC-shaped); dataversion vs LakeFSOps → reshape (no tags or commit metadata; real lakeFS
  assigns commit ids server-side); registry → reshape (register should return the version MLflow
  assigns); tiles → delete (per-tile vs per-scene; copies buffers); compute → delete (each pod
  sees its one pinned GPU as cuda:0; in-process role-to-device mapping causes H9). → B-12.
- **07c Major — every process must hold lakeFS secrets, and RB-015 blocks the fix.**
  config.py:95-106 requires the keys; `get_config()` with an empty environment raises;
  .env.example has no LAKEFS line; `repr(config)` prints both secrets (plain `str`);
  `tile_store_path="s3://bucket/tiles"` becomes `PosixPath('s3:/bucket/tiles')` and
  `storage_backend="s3"` is accepted but never read, so writes go to local disk; the promotion
  margin lives in two places (`auto_promote_margin`, never read; `SuperiorityConfig.minimum_effect`,
  no config source). → P-1; B-05 (lifting RB-015's prohibitions is an operator decision).
- **07d Major — serve/app.py does work at import time:** it queries torch.cuda and builds the
  container (app.py:246-247); tests mutate the global. → P-1, RS:T053.
- **07e Major — the 98.85% coverage figure measures tooling and unconnected modules.**
  Governance tooling is 967 of 2,674 statements (36%); domain + ports 425 (16%) with no
  production importer; 667 of 1,011 unit tests test the CI runner or governance; the contract
  tier has 0 parametrized or port tests and pins H11's silent `[]` as contract
  (tests/contract/test_stac_client.py:156-166). → FU (report product and tooling coverage
  separately; floor on product) — a gate change, so its own RB.
- **07f Major — Docker blocks all 1,011 unit tests for ~11 positive controls**
  (checks.sh:928,945,949); 344 product tests run in 25.9 s without Docker; tests/e2e, integration
  and sanity run only inside the coverage stage, whose failure hint points at the wrong stages
  (checks.sh:1157-1186). → FU (controls-stage split) — a gate change, its own RB, subject to
  B-06's freeze.
- **07g Minor — the command guard splits on quoted `|`, `;` and `&` before lexing**
  (guard.py:180-183): `echo 'a|b'` is blocked. Not in the accepted false-positive list. → FU.

---

## D-016/08 — Orchestrator verification

Environment: venv Python 3.12.3, torch CPU, `c545701`. ORCH reproduced 03c, 02a and 04a with
one script, recorded verbatim; imports omitted (`ModelRegistryOps`, `compute_iou_f1`,
`DriftTriggerManager` from `orbital_drift.registry.ops`, `.train.baseline`, `.drift.trigger`;
`torch`).

```python
r = ModelRegistryOps(tracking_uri="file:///tmp/x")
for _ in range(3):
    r.register_model_version("m", run_id="r", artifact_path="a")
r.transition_stage("m", 1, "Production")
r.transition_stage("m", 2, "Production")  # v1 archived
r.transition_stage("m", 3, "Archived")  # rejected challenger, never served
r.rollback_production("m")  # -> 3
targets = torch.zeros(1, 8, 8, dtype=torch.int64)
targets[:, :, 4:] = 1
logits = torch.zeros(1, 10, 8, 8)
logits[:, 0] = 1.0
compute_iou_f1(logits, targets, num_classes=10).mean_iou  # -> 0.850
sum(
    DriftTriggerManager(hysteresis_window=3, cooldown_scenes=5)
    .process_scene_verdict(is_drifted=True, scene_id=f"s{i}")
    .should_trigger
    for i in range(30)
)  # -> 0
```

Outputs:

```
rollback returns: 3 (v1 expected)
single-version rollback returns: None production now: None
constant predictor mIoU=0.850 (present-class mIoU would be 0.250)
triggers over 30 drifted scenes, fresh manager per scene: 0
triggers over 30 drifted scenes, one long-lived manager: 1
```

Output lines 2 and 5 come from variants the block does not show (one registered version; one
manager constructed outside the loop).

---

## D-016/09 — Operator ballot

Per item: options (where one is listed, the alternative is the status quo); recommended
default and rationale; age at 2026-10-06; what it unblocks. **Defaults are panel proposals, not
decisions.** NEXT_STEPS.md §2 condenses this into a table.

**Decide first:** Group 1 (B-01 to B-06); then B-07 and B-12, which every Phase L package
needs.

**Accepting the defaults.** The operator may log one decision-log line accepting all
recommended defaults except the items that line strikes or amends. Until such a line (or an
item-by-item line) exists, every default is a proposal and unlocks nothing (decision-log rule 1).
Some defaults need a further artifact before they bind:

- B-07, B-08, B-11, B-13, B-14: spec amendments; B-07's title wording also needs a constitution
  amendment PR.
- B-04: Phase L is absent from the charter's M0-M5 milestone table
  (charter/PROJECT-CHARTER.md:56-61).
- B-05's DEC-002 part overrides a CONFIRM-FIRST decision (charter §5), so it must be logged as its
  own DEC line, not inside a bulk line; it also changes charter §6's budget.
- B-06 rule 4 changes decision-log rule 2's RB-xxxa convention.

The bulk line itself changes no FR, SC, charter or constitution text. Shape, placeholders only:
`YYYY-MM-DD | <ID per rule 2> | Accept D-016/09 defaults B-01..B-23 except: <struck>; <amended>. EXPLICIT LIMIT: creates no G-x; flips no checkbox. | <operator>`

### Group 1 — decide first (critical path; all decidable at a laptop)

- **B-01 Node A identity and OS path.** (a) dual-boot Ubuntu 24.04 on a dedicated SSD in the
  current GPU workstation; (b) reimage that workstation; (c) a separate Linux box with the GPUs
  moved. **Default (a):** keeps the authoring environment; matches 00-host-prep.md:13. Age: T003
  startable 51 d. Unblocks: T074 Step 0; T003 → G-1.
- **B-02 Ratify D-008/D-03** (config-v3.toml.tmpl deployment; "required before T005",
  D-008:3,54,99). **Default: ratify as proposed.** Age 45 d. Unblocks: T005 → G-2.
- **B-03 Re-verify pins before T005** via an infra-scaffolder pass over versions.md
  (candidates: k3s v1.35.9; GPU Operator stays 26.3.3 unless the T012 env-form test fails; NVIDIA
  driver at an exact version and held). **Default: authorize; the driver pin before T003, which
  installs the driver, and the rest before T005.** Age 45 d (rule: ~30 d). Unblocks: a safe
  T003/T005/T012.
- **B-04 MVP definition and new phase gates.** (a) two-stage MVP-L + MVP-C + Soak Readiness Gate
  (D-016/10); (b) cluster-only "First Operated Loop" (MVP-C alone); (c) no MVP milestone (status
  quo: Phase-3 gate as written). **Default (a):** MVP-L moves seam defects off node A; MVP-C keeps
  the operator's hands-on loop primary (Principle I). Age: new. Unblocks: Phase L; plan.md phase
  gates becoming binding.
- **B-05 Authorization before G-1, and the DEC-002 ruling.** Authorization: (a) strict G-1
  before any T013+ work (status quo; agents idle); (b) a named G-1 waiver for the Phase L
  packages and T071-T073, with a WIP limit of one slice ahead of the last G-x entry; (c) classify
  as process track (reject: repeats RB-010's ungated-work pattern). **Default (b):** G-1 certifies
  a CUDA host; this CPU-only work never touches one. DEC-002: (A) rule RB-007(b) an allocation
  (reverses seven standing entries, RB-009..RB-015); (B) rule it consumed and the owner review
  complete; (C) supersede the per-milestone counter with the slice WIP limit plus a rolling cap of
  one process-track PR per product PR merged. **Default (C):** the only option that bounds
  unopened milestones. Also proposed: lift, for the local profile only, RB-015's two prohibitions
  (no `get_config()` at serve startup; no reversal of Part 4's required lakeFS keys); keys stay
  required for the cluster backend. Age 31 d (RB-012a(d)). Unblocks: Phase L; T071-T073
  execution; RB-015 Part F.
- **B-06 Decision cadence and process rules.** Adopt all, some or none of: (1) a weekly 30-minute
  operator decision review on this ballot, pre-filled 24 h ahead, producing DEC/RB/G lines; (2)
  decisions-before-programs: no new process-track RB or multi-agent review program while any
  ballot item is older than 14 days; (3) governance-code freeze until MVP-C (no new checks.sh
  stage, governance test module, skill or hook; bug fixes only); (4) decision-log entries <= 150
  words, execution recorded in the PR, not RB-xxxa lines; (5) an operator merge or push to main is
  a decision, logged the same day. **Default: all five.** Age: new. Unblocks nothing directly;
  bounds D-016/01a.

### Group 2 — ML protocol (before the Phase L packages and T024/T026/T034)

- **B-07 Label regime (resolves AR-3) and the "change detection" wording.** [domain knowledge
  for dataset facts] (a) a static annual land-cover map over the AOI (ESA WorldCover 2021;
  plan.md:15 and plan.md D-04 already chose a WorldCover-style bootstrap), version-pinned, with a
  permanent spatial-block holdout; (b) Dynamic World per-scene labels (Earth Engine auth; labels
  are another model's outputs); (c) OSCD (reject: 24 bitemporal L1C city pairs 2015-18, 2 classes,
  no data stream); (d) DynamicEarthNet (reject for the loop: 3 m PlanetScope, 2018-19, fixed sites;
  optional offline replay to prove the gate promotes better and rejects worse). **Default (a)**,
  (d) as optional stretch. The system does segmentation; the "change-detection" title (spec.md:1,
  constitution.md:3) needs an amendment PR with rationale. Age >= 35 d. Unblocks: T024, P-2, P-3.
- **B-08 Evaluation protocol.** (a) a fixed, dispersed spatial-block holdout in a ground CRS,
  buffered by at least the receptive field (92 px measured for SimpleUNet), scored on the newest
  window, champion re-scored on the same manifest at every gate; (b) temporal-only window
  (spec.md:35 literally; leaks under static labels); (c) a fixed golden scene set (stale after
  drift). **Default (a)**; amend US5 to "held-out spatial blocks of the recent window". Age: new.
  Unblocks: P-3, RS:T038.
- **B-09 Gate method (old "D-1"; D-011).** (A) arch.bootstrap with a project-written 2-D
  subclass (D-011's recommendation; resampling stays bespoke; adds pandas + statsmodels); (B)
  scipy.stats.bootstrap as an interval shell around the existing resampler; (C)
  scipy.stats.bootstrap (paired, vectorized, `alternative='greater'`, BCa, >= 9,999 resamples,
  seeded) over dispersed holdout clusters, deleting the moving-block code. **Default (C):** no new
  dependency, no project-written resampling; Principle II Reading A suffices because it is a
  library bootstrap of a listed metric. Extend the decision to drift/metrics.py's PSI (05d)
  before thresholds freeze. Age 35 d. Unblocks: RB-010 Parts 3 and 14 (re-scoped as P-4); the
  T063 question.
- **B-10 Decision rule and metric** (status quo: superiority.py:121-124; RB-013's empty-class =
  1.0). **Default:** promote when point estimate
  >= margin AND one-sided 95% lower bound > 0; verdict PASS / REJECT / INSUFFICIENT_EVIDENCE
  (insufficient below 30 valid clusters; aim for 50); metric = per-class IoU pooled from summed
  confusion matrices, macro-averaged over classes with ground-truth pixels, `ignore_index` for
  cloud, nodata and padding (superseding RB-013's convention for evaluation); margin >= the
  run-to-run spread T029 measures over >= 3 seeds; operator-approve mode until gate calibration
  tests pass. Age: new. Unblocks: RS:T026, P-3, P-4.
- **B-11 Registry semantics (FR-006).** Aliases champion/challenger/baseline with a recorded
  `previous_champion` and promotion history, or deprecated stages behind the adapter. **Default:
  aliases.** Proposed text for the later amendment (not the RB-016 PR): "Registry aliases
  champion/challenger/baseline; promotion moves champion to a version whose gate verdicts against
  @champion and @baseline pass; rollback moves it to the recorded previous_champion; consumers
  resolve aliases at job start (batch) or by poll (online)." Age 35 d (part of old "D-2").
  Unblocks: T071's final naming; RS:T028/T059.
- **B-12 Adapter disposition and the hexagon (old "D-2").** (A) keep the catalog, dataversion
  and registry ports, reshape dataversion and registry, delete tiles and compute; real clients
  (T059/T060) behind them, file-backed local adapters for MVP-L; (B) delete ports/, keep domain/;
  (C) write five adapters to the ports as they stand (reject: two shape mismatches; the registry
  adapter would inherit 03c). **Default (A).** Age 35 d. Unblocks: T059, T060, P-1.
- **B-13 MVP serving pattern.** Batch-first (a scoring job writes a classified COG and a class
  histogram per scene; FastAPI + canary stay in Phase 4, US6 is P2), or online FastAPI in the
  MVP. **Default: batch-first** (03e); needs a later FR-009 amendment. Age: new. Unblocks: P-5,
  RS:T043.
- **B-14 Trigger composition and the meaning of SC-003.** (a) per-band PSI/KS alone (status
  quo); (b) PSI/KS as diagnostics only; (c) per-class input drift on SCL-clear pixels against a
  day-of-year-matched reference AND (prediction-class shift OR weak-label mIoU drop), verdict
  DRIFTED / STABLE / INSUFFICIENT_DATA. **Default (c).** SC-003: "organic" means fired by the
  pre-registered, frozen configuration on real imagery; thresholds frozen by a decision-log line
  after a >= 12-month historical replay whose in-season null window yields 0 triggers; a trigger
  whose candidate fails the gate is logged as a false alarm (at most one per soak). Defining
  "organic" this way is a reading that tightens Constitution VI ("≥ 1 organically drift-triggered
  retrain (not forced)", constitution.md:29) and needs no amendment; making the false-alarm cap
  binding would put new text in SC-003 and needs a spec amendment. Age: new. Unblocks:
  RS:T034-T037, P-8.
- **B-15 Data source and harmonization.** Keep Earth Search `sentinel-2-l2a` (FR-001), or move to
  `sentinel-2-c1-l2a` [unverified]. **Default: keep**, harmonize reflectance by processing
  baseline onto a fixed 10 m UTM grid, fix the README's "Planetary Computer"; the operator
  captures recorded live fixtures (pre- and post-PB04, two tiles) because agent egress to the
  catalog is blocked. Age: new. Unblocks: RS:T013/T016/T017, P-7.
- **B-16 Off-MVP scope.** **Default:** fine-tune T030-T032 executes after
  the Phase-3 gate (resolving tasks.md:54 calling T032 the Phase-2 gate vs plan.md:102's
  reproducibility gate); T050 (P40) moves after the soak ([upstream source] Pascal: R580 last
  driver branch; PyTorch Pascal wheels stop at 2.14). Age: new. Unblocks: D-016/10's Phase 2/4/5
  moves.

### Group 3 — platform and soak readiness (before T005/T012)

- **B-17 Backup target and data disk.** Internal disk; LAN box; cloud object storage plus a local
  tier. **Default: cloud plus a local tier** (only off-node copies survive losing node A).
  Data disk: a dedicated disk for /var/lib/orbital-drift with k3s `--default-local-storage-path`
  on it. Ingest scope: one MGRS tile, AOI-clipped, compressed COG, 6-month backfill (total data
  under ~50 GB). Age: new. Unblocks: T073, T074.
- **B-18 Exposure and security posture.** **Default: LAN + VPN only**, which justifies deferring
  serving auth and image signing (wider exposure would not). Still required before MVP-C:
  per-consumer SeaweedFS identities; MLflow auth or a NetworkPolicy allowlist (pickled models);
  git-sync on a pinned `soak` branch promoted by PR; k3s `--secrets-encryption`; Terraform state
  out of the checkout. Age: new. Unblocks: T072, T074.
- **B-19 Alerting.** **Default:** one push channel plus an external dead-man heartbeat;
  observability core (T046/T049) into Phase 0 before the first scheduled DAG. Age: new.
  Unblocks: RS:T046/T049.
- **B-20 Meaning of SC-006.** **Default:** add a rebuild-plus-restore drill to the Soak
  Readiness Gate, and keep T051 as the rebuild-runbook test that constitution.md:21 requires
  once *during* the soak; a data restore during the soak does not reset the soak clock. This
  reading needs no constitution amendment; moving the only rebuild test before the soak would.
  Age: new. Unblocks: RS:T048/T051.

### Group 4 — already-pending housekeeping

- **B-21 D-015/D-06 (T063 checkbox).** Is the sklearn alignment a bugfix or a Principle II method
  change? **Default: bugfix**; check T063 after review; calibration.py stays off the MVP path
  (spec non-goal). Age 25 d.
- **B-22 D-012 F2/F5.** F2 `stride` field vs derived; F5 `limit=10` config vs documented constant
  (D-012:5-7). **Default:** F2 `stride` becomes a training-only config field and evaluation tiles
  every pixel exactly once; F5 becomes next-link pagination with sortby. A page size is a
  transport parameter, not a threshold, cadence, AOI or name under FR-012, because next-link
  pagination followed to exhaustion returns the same scene set for any page size; it is recorded
  as a deliberate constant with that rationale. Age 35 d. Unblocks: T061 remainder, RS:T016.
- **B-23 Close stale items.** **Default:** mark D-014 triaged (findings 1-3, 6, 7 executed by
  #23-#29; 4 and 5 map to tasks); retire RB-008a(b) and RB-008a(e) as WONTFIX. T064 is retired
  only through an amendment to the governance-harness scenario it implements ("Trace cites a
  nonexistent task", its status half; openspec/changes/adopt-governance-kit/specs/governance-harness/spec.md:86-100),
  not as a bare WONTFIX; otherwise the hidden-gap failure RB-008a(e) documented recurs. Age
  30-45 d.

### Proposed numbers and their sources

| Number | Where used | Source or derivation |
|---|---|---|
| 92 px holdout buffer | B-08, P-3 | Measured receptive field of SimpleUNet (EVAL). |
| >= 30 valid clusters; aim for 50 | B-10, P-4 | EVAL power calculation: 80% power needs 25-39 clusters at per-cluster SD 0.04-0.05. |
| >= 3 seeds | B-10, RS:T029 | Minimum to estimate a run-to-run spread; panel judgement. |
| 9,999 resamples | B-09 | scipy.stats.bootstrap's default `n_resamples` [upstream source]. |
| One-sided 95% bound | B-10 | Conventional alpha 0.05; panel judgement. |
| 14 days; weekly 30 min; 24 h pre-fill; 150 words | B-06 | [panel judgement; free parameter] — parameters of a process rule. |
| One slice of WIP; one process-track PR per product PR | B-05 | [panel judgement; free parameter]. |
| 56-day metrics retention | 06f, B-19, RS:T046/T049 | 6-week soak (42 days) plus two weeks of postmortem margin (PLAT). |
| 72 h burn-in; >= 3 scheduled ingests | Soak Readiness Gate | Panel judgement: spans a weekend and one Sentinel-2 revisit at the short end of its ~2.5-5-day range [domain knowledge]; 72 h does not span a 5-day revisit. |
| < 60% disk at day 42 | Soak Readiness Gate | Panel judgement: headroom above k3s's 5%-free eviction threshold (06c) for growth after the soak. |
| >= 12-month replay | B-14, P-8 | One full phenological cycle [domain knowledge]. |
| <= 1 false alarm per soak | B-14 | [panel judgement; free parameter]. |
| 6-month backfill; ~50 GB total | B-17 | PLAT estimate from the 05f and 06c volumes. |
| One MGRS tile; two fixture tiles | B-17, B-15 | [panel judgement; free parameter]. |
| False-pass <= 0.05 + 2 MC SE; power >= 0.8 at 2x margin | P-4 | Conventional alpha and power; 2 MC SE covers Monte Carlo noise at 300 seeds. |

Every number adopted becomes a configuration value, never a literal (Principle III).

---

## D-016/10 — MVP definition and re-sequencing

The repo never uses "MVP". **Constitution VI is unchanged:** the 6-week soak stays the
deliverable (constitution.md:28-30); the MVP is an intermediate milestone that makes the soak
startable. Everything here is proposed under B-04 and binds nothing until B-04 is logged; it
creates no G-x entry and changes no gate bar.

**MVP-L — laptop walking skeleton** (agent-built; needs B-05's waiver). An executable acceptance
test (planned path tests/acceptance/test_walking_skeleton.py) in a CI stage needing no Docker,
GPU, network or secrets. It drives the pipeline as **separate processes** (forcing persistence,
config loading and a composition root) under a local config profile: file-backed data
versioning, an MLflow registry on a sqlite tracking URI, recorded scene fixtures, planted
learnable labels, a pinned holdout manifest, fixed seeds.

1. ingest → data commit → train v1 → persist artifact + lineage envelope → register →
   operator-approve step → v1 is champion (and baseline).
2. The batch scoring job writes per-scene class histograms with the champion.
3. N shifted scenes, each in a fresh process → exactly one trigger episode; replaying a scene is
   a no-op; an INSUFFICIENT_DATA scene neither advances nor resets hysteresis.
4. Retrain v2 → the gate scores champion and challenger on the holdout → PASS → promotion; a
   label-permuted challenger → REJECT; degenerate evidence → INSUFFICIENT_EVIDENCE.
5. Rollback → v1 is champion and its weight hash matches v1's envelope; a no-target rollback
   refuses and leaves the champion intact.

Gate: MVP-L green in CI. Grown from tests/e2e/test_user_journey_ct_loop.py, which it replaces.

**MVP-C — First Operated Loop** (operator-run, [HUMAN]; the re-scoped Phase-3 gate, T040). The
same scenario on the cluster, every cluster task an adapter swap that keeps MVP-L green. Exit
evidence: G-1, G-2, G-3 logged; observability core and backups live; >= 2 real scenes ingested
unattended on schedule into real lakeFS; an Argo GPU training run whose MLflow run records
{lakeFS commit, git SHA, config hash}; forced drift injected on a lakeFS branch (never main)
producing exactly one episode with `trigger_source=forced`; at least one promotion AND one
rejection; the champion scoring scenes; a timestamped rollback drill under 10 minutes (first
SC-004 measurement); a reproducibility re-run within a stated tolerance (US2/T029).

**Soak Readiness Gate** (operator-run, [HUMAN]; task minted when B-04 and B-20 are logged;
before T052): rebuild-plus-restore drill passed with RTO recorded (B-20); every alert class
fire-drilled, including the off-node heartbeat; 72 h unattended burn-in with >= 3 scheduled
ingests; projected disk use at day 42 < 60%; drift thresholds frozen by a decision-log line after
the historical replay (in-season null run: 0 triggers); B-18's required security items closed.
Then T052.

**Out of the MVP:** T030-T032 fine-tune; the US6 canary split (T043's canary half, T044, T045);
T050; calibration.py, ranking.py, spatial.py; KServe; T054/T055 except where MVP-C needs them.

| Phase (numbers kept) | Proposed contents | Proposed gate |
|---|---|---|
| 0 Substrate | Before T003: B-01, B-03's driver pin, T074's Step 0 and driver hold. Before T005: B-02, the rest of B-03 and of T074. Before T012: T072. Before the first scheduled DAG: observability core (T046/T049) and backups (T073; restore runbook in T048). | plan.md:100 plus: an alert reaches the operator; one backup restored into a scratch namespace. |
| L Walking skeleton | New; parallel to Phase 0 only under B-05's waiver. P-1..P-6, T071, RS:T036. | MVP-L green in CI. |
| 1 Ingestion | Adds RS:T016/T017, P-7, live fixtures, pagination. | 2 real scenes on schedule; 4 bands + SCL; harmonized across PB04; valid fraction recorded. |
| 2 Training, registry | Labels + holdout manifest before real training; T032 not the gate. | plan.md:102 reproducibility (T029) + tagged holdout manifest. |
| 3 CT loop | Re-scopes per D-016/11. | MVP-C (T040). |
| 4 Serving, canary (post-MVP) | Alias-polling loader; canary on label-free proxies (prediction-class divergence, error rate, latency); T030-T032 (B-16). | Canary regression alert; Alertmanager from Phase 0 removes first pass H18's inversion. |
| 5 Soak | Soak Readiness Gate; then T052, with T051 during the soak (B-20); T050 after (B-16). | Unchanged (plan.md:105). |

---

## D-016/11 — Dispositions

| Finding | Disposition |
|---|---|
| 01a | B-06; NEXT_STEPS §2 |
| 01b, 01e, 01h | B-06 |
| 01c | B-05, B-06 |
| 01d | B-05 |
| 01f | FIX (NEXT_STEPS, plan.md); FU (README, D-014 header) |
| 01g | B-01; T074 |
| 01i | B-23 |
| 02a | B-10; RS:T026; P-3 |
| 02b | B-08; P-3 |
| 02c | B-09; P-4 |
| 02d | B-10; P-4 |
| 02e | B-09, B-10; P-4 |
| 02f | B-21; FU |
| 03a | RS:T026 |
| 03b | B-07; P-2 |
| 03c | T071; B-11, B-12 |
| 03d | B-11 |
| 03e | B-13; P-5 |
| 03f | RS:T053, RS:T043; T074 |
| 03g | RS:T026, RS:T029; P-2 |
| 03h | RS:T029 |
| 04a | RS:T033/T036, RS:T037 |
| 04b | B-14; P-8 |
| 04c | RS:T035, RS:T017 |
| 04d | RS:T034 |
| 04e | MVP-C (B-04); RS:T038, RS:T040 |
| 04f | RS:T027, RS:T038 |
| 04g | B-07, B-08 |
| 05a | B-15; RS:T013, RS:T016 |
| 05b | B-15; RS:T017; P-7 |
| 05c | RS:T016, RS:T017 |
| 05d | B-09 (extended to drift/) |
| 05e | B-22; RS:T016 |
| 05f | B-17; P-9 |
| 06a | T073; B-17, B-20; RS:T048, RS:T051 |
| 06b, 06d | T072 |
| 06c | B-17; T074; P-9 |
| 06e | T074; RS:T027 |
| 06f | B-19; RS:T046, RS:T049 |
| 06g | B-18; T072; T074 |
| 06h | B-03, B-16; T074 |
| 06i | T072; FU |
| 07a | B-04; P-1, P-6 |
| 07b | B-12 |
| 07c | B-05; P-1 |
| 07d | P-1; RS:T053 |
| 07e, 07f | FU (gate change, own RB; 07f also under B-06's freeze) |
| 07g | FU |
| first pass H13 | T075 |

Per-task re-scope text is one table in tasks.md Phase 7; checkboxes are unchanged (T062 stays
`[x]`: its locking is done; the rollback-target defect is T071's). Re-scopes that implement a
ballot default are proposals: they bind only once that item is logged (and, for B-08, B-11, B-13
and B-14, after the spec amendment); until then the task text and spec.md govern.

**Minted now** — decision-independent defects or rule gaps. **RB-016 authorizes the execution of
none**; each needs its own authorization.

- **T071 [A:ml-engineer]** Rollback restores exactly the previous champion, from an ordered
  promotion history; one conformance suite over both registries (03c). Supersedes D-013/04b;
  naming per B-11. Serves FR-006, SC-004.
- **T072 [A:infra-scaffolder]** Data-plane correctness before T012: a database and role per
  service; SeaweedFS per-component resources and per-consumer identities; images pinned (06b,
  06d, 06g, 06i). Reopens T007-T009. Serves FR-003, FR-006, SC-006.
- **T073 [A:infra-scaffolder]** Backup and restore: nightly pg_dump, bucket replication to B-17's
  off-node target, backups encrypted client-side before leaving the node, Terraform state
  relocated, key escrow, freshness metric; no key material, state file or backup credential in
  the repo (Principle VII) (06a). Applied by the operator at T012's bring-up (the T011 runbook
  must cover it); exercised at T051. Depends on T072, B-17. Serves SC-006, SC-001.
- **T074 [A:runbook-writer]** Runbook 00/01 addendum: B-01 OS path, data filesystem,
  secrets-encryption, held driver, two-pod GPU env-form test at T012 (01g, 06c, 06e, 06g, 06h).
  Step 0 (node identity) and the exact driver pin and hold before T003, because runbook 00, which
  T003 executes, installs the driver; the rest before T005. Depends on B-01, B-03, B-17. Serves
  SC-001, SC-006, R-05.
- **T075 [A:runbook-writer]** Runbooks for unpaired [HUMAN] tasks T029, T032, T049; T050 a runbook
  or a B-16 deferral (tasks.md:7; first pass H13, corrected to include T049). Serves US8.

T074 and T075 precede G-1 but are numbered in the T013+ range, so the RB that authorizes their
execution must name the gate-table row it relies on.

**Minted on decision** — not in tasks.md:

| Package | Scope | Needs | From |
|---|---|---|---|
| P-1 | Composition root; local/cluster config profiles (SecretStr; keys required only for cluster); CLI entrypoints; no import-time work in serve/app.py | B-05, B-12 | 07a, 07c, 07d |
| P-2 | Model artifact (safetensors or weights_only load) + input-contract card (bands, normalization, class map, architecture, lineage); secret-free config hash | B-07 | 03b, 03g |
| P-3 | Holdout manifest (ground-CRS blocks, buffered >= 92 px, hashed, lakeFS-tagged) + evaluation accumulator (per-cluster confusion matrices, ignore mask) | B-07, B-08, B-10 | 02a, 02b |
| P-4 | Gate replacement + calibration tests = RB-010 Parts 3, 14 re-scoped (false-pass <= 0.05 + 2 MC SE at 30 clusters; power >= 0.8 at 2x margin; identical/degraded REJECT; degenerate INSUFFICIENT_EVIDENCE) | B-09, B-10 | 02c-e |
| P-5 | Batch scoring job (classified COG + class histogram per scene; CRS kept; 100% of valid pixels; alias version recorded) | B-13, B-11, P-2 | 03e |
| P-6 | Walking-skeleton acceptance test + CI stage (a new FR, per RB-012a(b)), with a positive control: a planted seam defect turns the stage red | B-04, B-05, P-1..P-5, T071, RS:T036 | 07a |
| P-7 | Harmonized AOI loader (10 m UTM grid; SCL resampled; PB offset; NO_DATA/cloud masked, never 0; fractions recorded) | B-15 | 05b |
| P-8 | Historical replay (>= 12 months) and threshold freeze (null window 0 triggers; decision-log line) | B-14, P-7 | 04b |
| P-9 | Data retention and capacity (compressed crops, retention, capacity alert) | B-17 | 05f, 06c |

**Why P-n are not minted:** minting a task whose shape depends on an open ballot item would
encode a choice the operator has not made. A P-n gets a task ID only after its decisions are
logged.

**Why new IDs start at T071:** T066-T070 stay reserved — T066 is the unminted docker-smoke item
(D-015:178), and D-015:188 reserves the range.

---

## Follow-ups found during this review, NOT fixed here

**Each is unscheduled and needs operator triage before it becomes a task** — listing here is not
agreement to do them.

| # | Follow-up | From |
|---|---|---|
| 1 | README.md:9-21's six false capability claims and :285's "10 of the 55" (14 of 68); CHANGELOG.md:108 repeats "Planetary Computer". | 01f, 05a |
| 2 | D-014's header still says "awaiting operator triage" (B-23 proposes closing it). | 01f |
| 3 | Report product and tooling coverage separately, floor on product — a gate change needing its own RB. | 07e |
| 4 | Split the Docker positive controls into their own stage so unit tests need no Docker; fix the coverage-stage hint — a gate change, own RB, subject to B-06's freeze. | 07f |
| 5 | guard.py:180-183 quoted-pipe false positive. | 07g |
| 6 | Split the 20-platform root module; dedupe versions.md:18-24 against :11-17. | 06i |
| 7 | eval/spatial.py:91 patches `numpy.random.permutation` process-wide during Moran inference. | 02f |

## Verified correct — no action

- **PSI and KS compute what they claim within one process.** The defects are what they are
  applied to (04b, 04c) and their provenance (05d), not the arithmetic.
- **IoU/F1 vectorization matches its own pinned convention** (train/baseline.py:231-262); the
  convention is the problem (02a), not the code.
- **Trigger locking is correct within one process** (drift/trigger.py:107-116); the defect is
  cross-process state (04a).
- **T062's lock is correct** (registry/ops.py:192); the rollback-target defect is T071's.
- **Terraform providers are pinned** (helm 3.2.0, kubernetes 3.2.1, in all three stages'
  versions.tf); **the MLflow image is digest-pinned** (infra/helm-values/mlflow.yaml:21,28).
- **13 of 17 checks.sh stages are green** on this host with the venv on PATH; the other four
  (unit, coverage, gitleaks, hooks) fail only at the Docker-daemon guard (first pass H6).
- **Coverage is 98.85% vs a floor of 85, every file >= 90** (lowest config.py, 91.80; first pass
  H6). The measurement is right; what it measures is 07e's point.
