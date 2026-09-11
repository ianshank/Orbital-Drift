# Orbital-Drift — Forward Roadmap

**Audience:** the operator, deciding what to do next and which decisions only they can make;
and any agent picking up a task from the tracks below.

**Status:** rewritten 2026-09-11 under RB-015 (`docs/decision-log.md`,
`docs/decisions/015-hygiene-hardening-program.md`), replacing a 2026-09-06 revision that
claimed the container was "liveness-healthy at boot" while Docker HEALTHCHECK still curled
`/healthz` (503 with no model) and that claimed T053/T056/T058/T062 "completed" against
unchecked boxes. The evidence for every claim here is D-015 plus the files it names.
RB-012 (`docs/decisions/013-plan-artifact-reconciliation.md`) remains the source for why
this file exists at all.

**Source of truth.** `specs/001-orbital-drift-ct/tasks.md` owns scope; `docs/decision-log.md`
owns gates. **If this file disagrees with either, they win** — this document exists to
sequence work, never to record its status. No checkbox state is asserted here except as a
pointer to tasks.md.

**Why this file exists at all.** `docs/architecture/ARCHITECTURE.md` and RB-010 both defer
open questions to "the forward-roadmap", by name. `docs/development/**` is now a
`governed_path_globs` entry (T065, this PR) so this file cannot rot unowned again.

---

## 1. Where the project actually is

Phase 0 of 6. Ten of the task checkboxes are complete (T001, T001a, T001b, T002, T004, T004a,
T007-T010) — all Phase-0 authoring. Separately, PR#16/#17 landed most of the Phase 1-4
application code ungated; RB-010 marked all of T013-T052 `AUTHORED-PROVISIONAL` pending
retroactive review, and 12 of its 14 remediation parts have shipped (Parts 1, 2, 4, 5, 6-13;
Part 5 landed as three commits 5a/5b/5c, which is not three parts). Parts 3 and 14 remain —
see §2 D-1. Part 12 shipped only half its text: the Dockerfile fix landed, the `checks.sh`
docker stage did not (D-013/04e).

RB-015 retroactively reconcile-forwards PRs #23–#29 (T063 code, T053 probes, T062 lock,
T058 ports_isolation, T061 F3, T056 honest lakeFS, umbrella sprint). Those IDs were created
by RB-012 with **zero** execution authorization; RB-013/RB-014 excluded them. Checkbox
flips for T056 / T058 / T062 happen only after dual review recorded on the task line.
T053 stays `[ ]` PARTIAL. T061 stays `[ ]` (F3 only). T063 stays `[ ]` until D-011.

The honest summary of what that code is:

| Area | Built | Not built |
|---|---|---|
| STAC ingest | real client, real retry/backoff | no pagination; no rate limiting |
| Cloud mask | real SCL masking + cloud fraction | denominator differs from the STAC scene-percent it is compared against |
| Tile store | local `.npy` save/load | no rasterio, no COG, no windowed reads, no S3; writes are not atomic |
| lakeFS | commit/branch/pin **simulated** in-process; IDs deterministic; logs say `[SIMULATED]` | no `lakefs` SDK, no dependency, no server |
| Training | real U-Net, AMP, grad-accum, IoU/F1 | no MLflow; no Argo workflow; no fine-tune entrypoint |
| Registry | real stage machine with locking, including `rollback_production` | **simulated** — an in-process dict; no `mlflow` |
| Drift | real PSI + KS (scipy); F3 hysteresis/cooldown from config | no prediction-class shift; no Prometheus export; no reference builder; T061 F1/F2/F4/F5 open |
| Serving | real FastAPI, real canary routing, `/livez` vs `/readyz` split | never loads a model outside tests; `/metrics` is hand-rolled JSON |
| Orchestration | — | `dags/` and `workflows/` hold only `.gitkeep` |

Two consequences worth stating plainly:

- **The process can stay alive; it is not ready.** HTTP `/livez` is 200 without a model
  (unit-tested). Docker HEALTHCHECK, compose, and the rollback-drill skill now probe
  `/livez` (RB-015 Part H / this PR). `/readyz` and `/healthz` stay 503 until a production
  model is loaded, and nothing outside tests loads one. That remaining gap is T053
  model-load / T059, **Part F GATED**. Dummy `SimpleUNet` weights at startup are forbidden
  (D-015/D-02).
- **lakeFS commit IDs are simulated and labelled as such.** T056 (PR #28) made them
  deterministic and prefixed log lines with `[SIMULATED]`. Replacing the simulation is
  T060 and waits on D-2.

---

## 2. The critical path is three operator decisions, not code

Nothing an agent does moves these, and each blocks work that is otherwise ready.

### D-1. Ratify the Principle II method (`docs/decisions/011-principle-ii-eval-methods.md`)

The memo is PROPOSED and awaiting you. It recommends `arch.bootstrap` (with the cost — two
new transitive dependencies — stated), names `scipy`-as-interval-shell as the fallback, and
asks you to pick a Principle II *interpretation* as well as a library, because that determines
whether logging the choice suffices or a constitution amendment must land first.

T063's merged slice changed `_bin_weights` `searchsorted` side to match sklearn **and** added
a shape `RuntimeError`. Until you answer D-015/D-06 (bugfix of already-sklearn post-processing,
or wait on D-011), T063 stays unchecked.

**Unblocks:** RB-010 Part 3, then Part 14 (the promotion-gate lifecycle test). These are the
last two parts of the remediation program and the only open NON-NEGOTIABLE constitutional
violation. Cheapest high-value move available.

### D-2. Decide the adapter disposition (Track A/B below)

RB-010's EXPLICIT LIMIT defers "the lakeFS/MLflow-adapter-disposition questions" to this
roadmap. The question is whether the simulations become real clients, stay explicitly labelled
simulations, or are deleted. Until it is answered, FR-003 and FR-006 cannot go green and the
0-for-5 port count cannot move. T053 model-load also waits here (a real registry artifact).

### D-3. Execute T003 host prep, log `G-1`

The gate table admits no T013+ authoring without it, and per RB-007 T006 is deferred until it
exists. This is the only path to Phase 0 completion and it is `[HUMAN]` by Constitution I.

Charter R-2 is already live: DEC-002 M0 is 4/4 while G-1 is unresolved. Part F of RB-015 is
gated on a G-1 waiver **or** G-1 plus a DEC-002 ruling. Part H is process-track only because
RB-015 says so.

**A note on ordering:** D-1 and D-2 are independent of the cluster. They can be decided today,
from a laptop, and they unblock more work than D-3 does.

---

## 3. Tracks

Each track's tasks are declared in `specs/001-orbital-drift-ct/tasks.md` (Phase 6). Task IDs
here are pointers, not a second declaration.

### Track A — Make the hexagon load-bearing (T058)

0 of 5 ports have a real adapter; the only implementations are the in-memory fakes defined
alongside the Protocols. `domain/` + `ports/` is a 10-module component the other 30 modules
cannot reach — a second, parallel program.

**Code present (PR #26, unchecked until dual review):** the `ports_isolation` forbidden
contract now prevents `orbital_drift.ports` from importing application-layer modules. The
planted-violation control uses `pytest.fail` when `lint-imports` is absent (RB-015 leftover;
do not grow the D10 skip allowlist).

The measured recommendation for adapters is still to start with the **registry**, wrap rather
than rewrite, and wait on D-2.

### Track B — Replace the simulations (T056, T059, T060)

**Code present (PR #28, unchecked until dual review):** lakeFS commit IDs are deterministic;
every log line naming a lakeFS object says `[SIMULATED]`. Replacing the simulation is T060
and waits on D-2. T059 (MLflow) waits on the same decision.

`get_config()` is still called from nowhere in `src/`. RB-010 Part 4 lakeFS keys stay
required with no default — reversing that is forbidden by RB-015; conditional creds wait on
T060.

### Track C — Close out RB-010 (T057, and Parts 3/14)

The retroactive spec-guardian + adversarial-reviewer review of T013-T052 is required by
RB-010's disposition clause, gates ~40 checkboxes, and was assigned to no part. T057 gives
it those. **RB-015 Part F: GATED.** T057 is not a log-only task: exit condition is recorded
outcomes **and** checkbox flips for tasks that pass. Parts 3 and 14 resume the moment D-1
is logged.

### Track D — Gate integrity (T061, T062)

**T061 F3 only (PR #27):** `hysteresis_window` and `cooldown_scenes` resolve from config.
F1/F2/F4/F5 wait on operator answers in D-015 for F2 (`stride`) and F5 (`limit=10`) before
any remainder PR. Completing T061 is not charter R-5; shipping another partial is the watch.

**T062 code present (PR #25, unchecked until dual review):** `rollback_production` takes
`self._lock`. Do not wrap `get_stage_version` while holding that lock (not re-entrant).

### Track E — Deployment reality (T053, T054, T055)

**T053 PARTIAL (RB-015 Part H, this PR):** probes `/livez` vs `/readyz` landed in PR #24;
HEALTHCHECK/compose/skill now match `/livez`; port env is `ORBITAL_DRIFT_SERVING_PORT` with
default 8000 and a shell wrapper (exec-form `${}` does not expand); stale staging clears;
canary RNG is seeded. **Not done:** loading a production model outside tests. `/readyz` and
`/healthz` stay 503. Dummy weights forbidden.

**T054 / T055: Part F GATED.** `lifespan` already exists and only logs `NOT_LOADED`. T054
can call `configure_logging()` from that lifespan (zero call sites under `src/` today) and
does not need a new factory. T055 is an ASGI body limit before parse.

T066 (docker-smoke CI job) is **not minted**. It needs an FR, `docker inspect` of
Healthcheck.Test, a planted `/healthz` mutation, and a measured `docker run` wall-clock.
Listing that in D-015 is not authorization.

---

## 4. Suggested sequence

1. **Log D-1.** One decision-log line. Unblocks Parts 3 and 14, and unsticks T063's checkbox.
2. **Decide D-2**, then T059/T060 (and T053 model-load).
3. **T003 → G-1**, then a DEC-002 ruling if more feature PRs are wanted. Unlocks Part F
   (T054, T055, T061 remainder, T057, T064) and any G-1-gated T013+ work.
4. **T057** once authorized. Until the retroactive review runs, no Phase 1-4 checkbox can
   flip and the plan of record cannot show progress.

Budget note: DEC-002's M0 counter stands at 4/4 on the RB-007(b) baseline, so a genuine
*feature* PR past T011 triggers mandatory owner review. Part H of RB-015 is process-track
because the RB says so. Part F is not.

**Closed process that does not move this list:** RB-013/RB-014 (PR #30) vectorized
train/cloud hot paths and corrected the gpu-profiler skill's tuple-unpack of
`train_baseline_epoch`. SC-002 remains unmeasured. Do **not** follow with a faster
`eval/bootstrap.py` — that is D-1 / Principle II, not a micro-opt.

---

## 5. Rollback drill — withdrawn, not moved

The previous version of this file carried a four-step rollback drill. It has been removed
rather than corrected, and this section records why so nobody restores it from git history.

Two of its four steps named symbols that do not exist: `ModelRegistryOps.rollback_production_model()`
(the real name is `rollback_production`) and `container.update_canary_ratio(0.0)` (no such
method exists at all; the only mutator is `ModelContainer.set_models`). Correcting the two
names would have produced a procedure that still cannot run, because nothing loads a
production model outside tests — there is no canary to roll back. Those broken names remain
in `.claude/skills/canary-rollback-drill/SKILL.md` and are T039, not this file.

The skill's verification step now queries `/livez` (not `/healthz`) so it does not treat a
readiness-503 as a dead process. That is liveness hygiene, not a working drill.

The rollback runbook is **T039** (`docs/runbooks/04-ct-ops.md`, `05-rollback.md`), owned by
`runbook-writer`, and it is unwritten. SC-004 requires that drill to complete in under 10
minutes; a drill against unwired code cannot be rehearsed, so T039 depends on T053 model-load
and on Track B.

Until T039 lands there is no rollback procedure in this repository, and this file will not
pretend otherwise.
