# D-015: Hygiene-hardening program (RB-015)

**Status:** decided 2026-09-11. Part H is authorized to execute; Part F is
recorded as GATED and is not authorized by this document.
**Provenance, stated precisely:** the operator approved the v2 plan in-session
on 2026-09-11 with the words "Implement the plan as specified" (RB-013/RB-014
rule-6 precedent). Two earlier forks (T053 dummy-model vs S6.1 amendment;
G-1 waiver vs hygiene-only) were presented and skipped; this document
records the plan's **stated defaults**, not a later verbal override.
**Audience:** operator, spec-guardian, adversarial-reviewer, and the agents
executing Part H.
**Decision-ID namespace:** this file's `D-015/D-nn` series is independent of
every other `docs/decisions/*.md` series, of plan.md's `D-01…D-05`, and of
`docs/decision-log.md`'s `DEC-`/`RB-`/`G-` namespace (decision-log rule 3).
Cross-references read `D-015/D-nn`.
**Why this exists:** without it, a later session can treat "implement the
reflection plan" as a G-1 waiver, a dummy-U-Net startup, a new CI gate, or
a reversal of RB-010 Part 4 — all of which both reviewers BLOCKED.

Measured at HEAD `38c55ba2b777115385b000b557137db7103f35b3` (`origin/main`,
PR #30) unless a sentence names a later commit.

---

## D-01 — Part H is process/hygiene; Part F stays GATED

Part H may proceed without G-1 because it does not load a production model,
does not reverse RB-010 Part 4, does not add a merge-blocking CI stage, and
does not flip T013–T052 checkboxes. It is process-track and sits outside the
DEC-002 M0 4/4 feature counter **only because RB-015 says so**.

Part F (T053 model-load, T054, T055, T061 F1/F2/F4/F5, T066 docker-smoke,
T057 checkbox flips, T064) is **not authorized**. Starting any of it requires
a later RB that either (a) logs G-1 or (b) explicitly waives G-1 for named
IDs **and** rules on DEC-002. Charter R-2 (budget exhausted while a blocking
G-x remains unresolved) is already live; this program does not trip it by
shipping more feature work under a process slogan.

**Rejected:** v1's "unlocks no G-1" sentence followed by executing T053
model-load. spec-guardian: that is not a waiver.

---

## D-02 — Dummy weights are forbidden; S6.1 is amended to liveness-only

`set_models(SimpleUNet())` (or any untrained network) at production startup
is forbidden. It would make `/readyz` 200 while `/predict` is garbage —
worse than today's honest 503 (adversarial-reviewer AR-2, [Certain]).

S6.1's acceptance text currently requires "a production model is loaded
outside tests, /healthz reports ok in the shipped image"
(`src/orbital_drift/planning/roadmap_data.py` story S6.1). Landed tests pin
the opposite probe split: no model → `/livez` 200, `/readyz`/`/healthz` 503
(`tests/unit/test_serving_edge_cases.py`). Part H amends S6.1 to:

Docker HEALTHCHECK and compose probe `/livez`; `/readyz` and `/healthz` stay
503 until a production model is loaded (T059); Dockerfile port env is
`ORBITAL_DRIFT_SERVING_PORT` matching `config.serving_port`.

T053's checkbox stays `[ ]` with a PARTIAL note. Model-load remains Part F /
T059.

**Rejected:** loading random weights to satisfy S6.1 as written.

---

## D-03 — Serving port is 8000, injected by a wrapper, not exec-form `${}`

Measured at HEAD:

- `Dockerfile:46` `ORBITAL_DRIFT_SERVE_PORT=8000` (wrong env name)
- `Dockerfile:90` `ENTRYPOINT ["uvicorn", …, "--port", "8000"]` (exec-form;
  JSON does not expand `${…}`)
- `Dockerfile:85` `EXPOSE 8000`
- `docker-compose.yaml:28` `ORBITAL_DRIFT_SERVING_PORT=8000`
- `src/orbital_drift/config.py:219-221` `serving_port` default **8080**

D-014 recommended `["uvicorn", …, "--port", "${ORBITAL_DRIFT_SERVING_PORT:-8000}"]`.
That would pass the literal dollar-string to uvicorn. **Rejected.**

Part H: default `serving_port=8000` (one number matching EXPOSE/compose);
Dockerfile ENV `ORBITAL_DRIFT_SERVING_PORT=8000`; HEALTHCHECK shell-form
expands that env and curls `/livez`; ENTRYPOINT is `scripts/serve_entrypoint.sh`
which `exec`s uvicorn with `--port "$ORBITAL_DRIFT_SERVING_PORT"`.

---

## D-04 — Retroactive reconcile-forward of the 2026-09-06 landings

RB-012 created T053–T065 and authorized none. RB-013/RB-014 excluded those
IDs. These PRs merged anyway (`gh pr list --state merged`, 2026-09-11):

- **#23** T063 ECE bin-alignment (`cursor/t063-ece-fix-d864`)
- **#24** T053 probe split (`cursor/t053-serve-health-d864`)
- **#25** T062 `rollback_production` lock (`cursor/t062-rollback-lock-d864`)
- **#26** T058 `ports_isolation` (`cursor/t058-importlinter-d864`)
- **#27** T061 F3 hysteresis/cooldown (`cursor/t061-config-wiring-d864`)
- **#28** T056 honest lakeFS simulation (`cursor/t056-lakefs-honest-d864`)
- **#29** umbrella "Comprehensive Tech Debt Remediation Sprint"

Disposition: reconcile forward (RB-010/RB-006), not revert. Checkbox flips
for T056 / T058 / T062 happen only after Part H's leftover fixes (T058 skip)
and a review recorded on the task line. T063 checkbox waits on D-011 (D-015/D-06).
T053 stays `[ ]` (PARTIAL). T061 stays `[ ]` (F3 only).

---

## D-05 — `# pin:` count at HEAD

Measured by walking `src/orbital_drift/**/*.py` for the substring `# pin:`
at `38c55ba`:

- **113** pins in **20** files
- **19** of those are in `quality/hardcode_scan.py` (scanner self-pins)
- pipeline/product surface ≈ **94**

Constitution III debt is the 94, not 113. Pin-laundering inventory is a
follow-up, not Part H.

---

## D-06 — T063 bin-alignment waits on D-011

T063's merged slice changed `_bin_weights` `searchsorted` side to match
sklearn (`src/orbital_drift/eval/calibration.py`) **and** added a shape
`RuntimeError`. The task text said to coordinate with D-011 before changing
eval mathematics. `docs/decisions/011-principle-ii-eval-methods.md` is still
PROPOSED.

Operator question, unanswered: is the sklearn alignment a bugfix of
already-sklearn post-processing, or a Principle II method change?

Until a log line answers that, do not checkbox T063. A later bugfix PR may
add a **value** pin for the RB-012 reproduction (`labels=[F,F,F]`,
`probabilities=[1.0,0.5,0.5]`, `bin_count=2`, `quantile`) — not merely
`ECE <= 1`.

---

## D-07 — Coverage rate is unmeasured at this SHA

The 98.89% figure in `docs/incidents/2026-09-03-full-suite-triage.md` predates
PRs #23–#30. The coverage **job** on `38c55ba` succeeded; this session did
not re-read the percentage from GHA logs. Do not raise 85→95 off a stale
number. Floors stay `COVERAGE_MIN_PERCENT=85` and
`COVERAGE_PER_FILE_MIN_PERCENT=90`.

---

## D-08 — `get_config()` and lakeFS keys are unchanged

`get_config()` has zero call sites under `src/` or `tests/` (comments only,
`serve/app.py:211-215`). `lakefs_access_key` / `lakefs_secret_key` remain
required `Field()` with no default (`config.py:95-106`) — RB-010 Part 4
fail-fast. Part H does not call `get_config()` and does not make those
fields conditional. Conditional creds wait on T060 / D-2.

---

## Follow-ups found during this review, NOT fixed here

**Each is unscheduled and needs operator triage before it becomes a task —
listing here is not agreement to do them.**

- REPO_ROOT single-home (three Python copies; RB-008a deferral)
- Decision-log rule 8 + entry-text immutability (RB-008a(e))
- Widen the errexit sweep glob to `ci/*.sh`
- ruff C901 / PLR0915 after an FR and measured baseline
- Split `ci/checks.sh` (~2430 lines + ~8k test lines)
- Dedicated `schedule:` concurrency group (documented cancel risk in
  `.github/workflows/ci.yml`)
- Lazy-import torch so the runtime image can be `.[serve]` only
- `get_stage_version` locked reads without deadlock (`threading.Lock` is
  not re-entrant; `rollback_production` already holds the lock and calls
  the unlocked getter)
- Pin-laundering inventory excluding `hardcode_scan.py` self-pins
- Re-measure coverage % at HEAD and stamp SHA
- T066 docker-smoke: needs an FR, `docker inspect` Healthcheck.Test, a
  planted `/healthz` mutation, and a measured `docker run` wall-clock
  (torch import vs `start-period=10s` is unverified)
- T054 `configure_logging()` in the existing `lifespan` (independent of
  model load; still Part F)
- T055 ASGI body limit before parse
- T061 F1/F2/F4/F5 after operator answers F2 (`stride`) and F5 (`limit=10`)
- T057 outcomes **and** checkbox flips (log-only is not the exit condition)
- T064 story-status scenario
- T053 model-load from a real registry (T059 / D-2)
- Minting T066–T070 (projections/story tax; default is mint none)

---

## Verified correct — no action

- `lifespan` already exists (`serve/app.py:28-52`) and only logs
  `NOT_LOADED`. v1's "missing composition root" was stale.
- `.claude/settings.json` deny-list has no `docker` entries. The
  `ci.yml` comment that settings deny `docker build` is stale; do not copy
  it. Local docker verification is a daemon question.
- `import-linter` is in `[dev]`; the T058 planted-skip is unreachable in
  CI. The sibling test already `pytest.fail`s. Part H makes the planted
  control match.
- No top-level `adapters/` package. `.importlinter` (RB-010 Part 8) stays.
- M0 4/4 on the RB-007(b) baseline is unchanged.
- `docs/decisions/011-*.md` remains PROPOSED. This document does not
  decide Principle II.
