# Orbital-Drift — Forward Roadmap

**Audience:** the operator, deciding what to do next and which decisions only they can make;
and any agent picking up a task from the tracks below.

**Status:** rewritten 2026-09-05 under RB-012 (`docs/decision-log.md`), replacing a version
that declared Phase 1 complete and named two API symbols that do not exist. The evidence for
every claim here, and the full review that produced it, is
`docs/decisions/013-plan-artifact-reconciliation.md`.

**Source of truth.** `specs/001-orbital-drift-ct/tasks.md` owns scope; `docs/decision-log.md`
owns gates. **If this file disagrees with either, they win** — this document exists to
sequence work, never to record its status. No checkbox state is asserted here.

**Why this file exists at all.** `docs/architecture/ARCHITECTURE.md:65` and RB-010 both defer
open questions to "the forward-roadmap", by name. Until this rewrite that artifact lived only
in a session plan and was never committed, so two merged documents cited a planning file that
was not in the repository. This is now that file.

---

## 1. Where the project actually is

Phase 0 of 6. Ten of the task checkboxes are complete (T001, T001a, T001b, T002, T004, T004a,
T007-T010) — all Phase-0 authoring. Separately, PR#16/#17 landed most of the Phase 1-4
application code ungated; RB-010 marked all of T013-T052 `AUTHORED-PROVISIONAL` pending
retroactive review, and 12 of its 14 remediation parts have shipped (Parts 1, 2, 4, 5, 6-13;
Part 5 landed as three commits 5a/5b/5c, which is not three parts). Parts 3 and 14 remain —
see §2 D-1. Part 12 shipped only half its text: the Dockerfile fix landed, the `checks.sh`
docker stage did not (D-013/04e).

The honest summary of what that code is:

| Area | Built | Not built |
|---|---|---|
| STAC ingest | real client, real retry/backoff | no pagination; no rate limiting |
| Cloud mask | real SCL masking + cloud fraction | denominator differs from the STAC scene-percent it is compared against |
| Tile store | local `.npy` save/load | no rasterio, no COG, no windowed reads, no S3; writes are not atomic |
| lakeFS | commit/branch/pin **simulated** in-process | no `lakefs` SDK, no dependency, no server |
| Training | real U-Net, AMP, grad-accum, IoU/F1 | no MLflow; no Argo workflow; no fine-tune entrypoint |
| Registry | real stage machine with locking | **simulated** — an in-process dict; no `mlflow` |
| Drift | real PSI + KS (scipy) | no prediction-class shift; no Prometheus export; no reference builder |
| Serving | real FastAPI, real canary routing | never loads a model outside tests; `/metrics` is hand-rolled JSON |
| Orchestration | — | `dags/` and `workflows/` hold only `.gitkeep` |

Two consequences worth stating plainly, because a reader skimming the "Built" column will
otherwise miss them:

- **The container cannot become healthy.** `/healthz` returns 503 until a production model is
  loaded, and nothing outside tests loads one. See Track E / T053.
- **lakeFS commit IDs are fabricated and logged as if real.** They feed the reproducibility
  triple. See Track B / T056.

---

## 2. The critical path is three operator decisions, not code

Nothing an agent does moves these, and each blocks work that is otherwise ready.

### D-1. Ratify the Principle II method (`docs/decisions/011-principle-ii-eval-methods.md`)

The memo is PROPOSED and awaiting you. It recommends `arch.bootstrap` (with the cost — two
new transitive dependencies — stated), names `scipy`-as-interval-shell as the fallback, and
asks you to pick a Principle II *interpretation* as well as a library, because that determines
whether logging the choice suffices or a constitution amendment must land first.

**Unblocks:** RB-010 Part 3, then Part 14 (the promotion-gate lifecycle test). These are the
last two parts of the remediation program and the only open NON-NEGOTIABLE constitutional
violation. Cheapest high-value move available.

### D-2. Decide the adapter disposition (Track A/B below)

RB-010's EXPLICIT LIMIT defers "the lakeFS/MLflow-adapter-disposition questions" to this
roadmap. The question is whether the simulations become real clients, stay explicitly labelled
simulations, or are deleted. Until it is answered, FR-003 and FR-006 cannot go green and the
0-for-5 port count cannot move.

### D-3. Execute T003 host prep, log `G-1`

The gate table admits no T013+ authoring without it, and per RB-007 T006 is deferred until it
exists. This is the only path to Phase 0 completion and it is `[HUMAN]` by Constitution I.

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

The measured recommendation is to start with the **registry**, because it is the only
port/counterpart pair with name-level overlap and no granularity mismatch, and to *wrap*
rather than rewrite: a `registry/adapter.py` implementing `ModelRegistryPort` over an
unmodified `ModelRegistryOps`, plus a port-conformance suite parametrized over both
implementations so one suite pins both. Catalog and tiles need domain design first — the tile
port is tile-granular while `TileStore` is scene-granular, which is a shape change, not a
rename.

Cheapest first step, and it touches no production code: close the `.importlinter` contract
hole. Measured, the current contract set does **not** catch a port importing its own concrete
counterpart. Until it does, every adapter added is un-policed.

### Track B — Replace the simulations (T056, T059, T060)

lakeFS and MLflow are simulated in-process with no SDK, no dependency, and no server.
Sequencing: fix the fabricated-and-logged-as-real commit IDs first (T056 — it is an audit-trail
defect, not a feature gap), then the adapters once D-2 is decided.

Prerequisite for both, and currently missing: a **composition root**. `get_config()` is called
from nowhere in `src/`; there is no factory, no DI point, and every consumer names a concrete
class. Note the self-inflicted blocker — RB-010 Part 4 made the lakeFS credentials required
with no default, so `get_config()` raises in any environment lacking them, which is the stated
reason `serve/app.py` never calls it; and the fields those credentials guard are read by
nobody. Making them conditionally required (validated when `storage_backend == "lakefs"`)
preserves Part 4's fail-fast intent and unblocks the root.

### Track C — Close out RB-010 (T057, and Parts 3/14)

The retroactive spec-guardian + adversarial-reviewer review of T013-T052 is required by
RB-010's disposition clause, gates ~40 checkboxes, and was assigned to no part — so it has no
owner, no budget line, and no completion criterion. T057 gives it those. Parts 3 and 14
resume the moment D-1 is logged.

### Track D — Gate integrity (T061, T062, and the D-013/05 items)

The newest gates are weaker than they read. `architecture` has no positive control and,
measured, misses the violation it most needs to catch. `hardcode` and `deps` can be disarmed
from `pyproject.toml` with every existing test still green. `hardcode` is green by
suppression — 0 findings with pins honoured, 121 without. Four gates enforce rules traceable
to no requirement, which this repo has twice ruled insufficient (FR-011a, FR-011b).

### Track E — Deployment reality (T053, T054, T055)

Three defects in already-remediated code that no gate can see, because the docker job builds
the image and never runs it: the permanently-unhealthy container, the redaction fix that never
executes in production, and the request-size bound that runs after the body is parsed.

---

## 4. Suggested sequence

1. **Log D-1.** One decision-log line. Unblocks Parts 3 and 14.
2. **T053 + T054** (Track E). The container cannot serve and logs are unredacted; both are
   cheap relative to their blast radius, and T053's startup wiring is a prerequisite for any
   real deployment.
3. **Close the `.importlinter` hole** (Track A, first step). ~6 lines, no production change,
   and it stops the decay before adapters land.
4. **T057** (Track C). Until the retroactive review runs, no Phase 1-4 checkbox can flip and
   the plan of record cannot show progress.
5. **Decide D-2**, then Track B.
6. **T003 → G-1 → T006 → T011** whenever hardware time allows. Independent of 1-5.

Budget note: DEC-002's M0 counter stands at 4/4 on the RB-007(b) baseline, so a genuine
*feature* PR past T011 triggers mandatory owner review. Everything above is process,
remediation, or `[HUMAN]` track.

---

## 5. Rollback drill — withdrawn, not moved

The previous version of this file carried a four-step rollback drill. It has been removed
rather than corrected, and this section records why so nobody restores it from git history.

Two of its four steps named symbols that do not exist: `ModelRegistryOps.rollback_production_model()`
(the real name is `rollback_production`) and `container.update_canary_ratio(0.0)` (no such
method exists at all; the only mutator is `ModelContainer.set_models`). Correcting the two
names would have produced a procedure that still cannot run, because nothing loads a
production model outside tests — there is no canary to roll back.

The rollback runbook is **T039** (`docs/runbooks/04-ct-ops.md`, `05-rollback.md`), owned by
`runbook-writer`, and it is unwritten. SC-004 requires that drill to complete in under 10
minutes; a drill against unwired code cannot be rehearsed, so T039 depends on T053 (startup
wiring) and on Track B. Note also that `registry/ops.py`'s `rollback_production` is the one
mutation path RB-010 Part 10 left unlocked (T062) — the drill's own entry point.

Until T039 lands there is no rollback procedure in this repository, and this file will not
pretend otherwise.
