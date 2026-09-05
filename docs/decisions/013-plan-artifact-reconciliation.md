# D-013: Deep peer-review record and plan-artifact reconciliation (RB-012)

**Status:** recorded 2026-09-05. This is an **evidence record plus a scoped repair**, not a
technical decision: the findings below are made durable and checkable, the subset that is a
*plan artifact telling a falsehood* is repaired in the same change, and everything else is
given a task ID so it stops living in prose. Nothing in `src/orbital_drift/` behaviour
changes here except the traceability linter, which gains one check.

**Audience:** the operator (every Critical finding in D-013/02 is theirs to schedule, and
three open decisions in D-013/04 are theirs to make), and whichever agent picks up the
T053-T065 block this record creates.

**Provenance, stated precisely.** The review was run on 2026-09-05 against HEAD `4b6ac35`
(== `origin/main`, clean tree) by five reviewers working in parallel: `adversarial-reviewer`
(code correctness), `spec-guardian` (document conformance), two `general-purpose` agents
(test adequacy; architecture/wiring), and the orchestrating session itself (plan artifacts).
Findings are attributed below. **Attribution is not verification** — findings marked
`[verified here]` were re-checked directly against source by the orchestrating session and
are load-bearing for this document's repairs; the rest are relayed with their reviewer's
own file:line citation and are recorded as *reported*, to be confirmed by the owning agent
when its task is picked up. The operator has approved no code fix in D-013/02.

**Decision-ID namespace:** this file's `D-013/D-nn` series is independent of every other
`docs/decisions/*.md` series, of `plan.md`'s `D-01…D-05`, and of `docs/decision-log.md`'s
`DEC-`/`RB-`/`G-` namespace (decision-log rule 3). Cross-references read `D-013/nn`, matching this file's headings.

**Environment limit, disclosed up front.** The session ran in a fresh Linux container, not
the operator's dual-GPU host. `sh ci/checks.sh all` was NOT run to completion here: Docker
is unavailable (four stages need it), and the GPU tiers cannot execute. What was run is
recorded in D-013/07. Linux CI remains authoritative, as `README.md:242` already states.

---

## D-013/01 — Why this review happened, and what it covers

RB-010 (2026-09-01) authorized a 14-part remediation program after PR#16/#17 merged
ungated. Parts 1, 2, 4, 5a-c, 6, 7, 8, 9, 10, 11, 12 and 13 have landed; Parts 3 and 14
remain, both blocked on an operator decision that `docs/decisions/011-*.md` requests and
that no `docs/decision-log.md` entry yet makes.

The question put to this session was what the logical next steps are. Answering it honestly
required checking whether the *plans themselves* are true, because two of them are the only
artifacts a reader would consult for that answer. They were not true. The review then
widened to ask the same question of the code the remediation claims to have fixed.

---

## D-013/02 — Critical: four defects in already-remediated code. NOT fixed here

These are reported by the `adversarial-reviewer` lens against parts RB-010 records as
complete. **None is fixed in this change** — each needs its own TDD branch, its own review
pair, and its own green gate run, per RB-010's own per-part rule. Each is given a task ID
in `specs/001-orbital-drift-ct/tasks.md` (T053-T056) so it is scheduled rather than known.

### D-013/02a — The shipped container can never become healthy (T053) [verified here]

`Dockerfile:87-88` declares `HEALTHCHECK ... CMD curl -f http://localhost:${ORBITAL_DRIFT_SERVE_PORT}/healthz || exit 1`.
RB-010 Part 13 changed `/healthz` to return **503** while `container.production_model is None`
(`src/orbital_drift/serve/app.py:218-224`). `set_models()` is called from no production
path — confirmed directly: every call site of `configure_logging`'s sibling entry point
`ModelContainer.set_models` outside `serve/app.py` itself is a test, and the endpoint's own
docstring says so ("today only from tests; real startup wiring to a model registry is a
separate, deferred RB-010/forward-roadmap item, not this task", `serve/app.py:211-214`).

Therefore `production_model` is `None` for the life of every real process, `/healthz` always
returns 503, and Docker marks the container `unhealthy` after `--retries=3`. Under
Kubernetes with the same probe as a liveness probe this is an indefinite CrashLoopBackOff on
an image whose application code is fine.

**Why no gate sees it:** `.github/workflows/ci.yml`'s docker job runs `docker build
--target runtime` and never *runs* the image, never issues a request, never probes
`/healthz`. The gate proves the Dockerfile parses.

**Honest reading of severity.** Part 13 made a dishonest health check honest, which is
correct in isolation; the defect is that Part 13 and Part 12 landed independently and their
composition was never evaluated. The fix is startup wiring, not reverting the 503.

### D-013/02b — The credential-redaction fix never runs in production (T054) [verified here]

RB-010 Part 9 fixed `observability/logging.py`'s redaction. Confirmed directly by search:
`configure_logging` is *called* from six test modules and from nowhere under `src/` — its
only `src/` occurrences are its own definition (`observability/logging.py`) and a re-export
(`observability/__init__.py`). With no handler installed on the `orbital_drift` logger,
records propagate to root and the `JsonFormatter` that performs redaction never runs.

Reported additionally, not re-verified here: even when configured, redaction covers `extra=`
fields only — `observability/logging.py:236` emits `record.getMessage()` unredacted, and
every non-`eval/` module logs by %-interpolation into the message. `ingest/stac_client.py:190-196`
logs `response.text` verbatim, so a proxy that echoes an `Authorization` header puts a bearer
token in the log.

Part 9 was authorized as "logging-redaction fix **+ structured-logging rollout beyond
eval/**". The rollout half is deferred in the commit body with no follow-up entry.

### D-013/02c — Part 13's "request size bound" runs after the body is parsed (T055)

Reported: `serve/app.py:79-91`'s `_bound_image_array_size` is a pydantic *after*-validator,
so Starlette has read the whole body and pydantic has materialised the full
`list[list[list[float]]]` before the bound is evaluated. The file's own comment concedes
there is no Starlette/uvicorn body-size limit. The existing test monkeypatches the constant
to 4 and sends 6 elements, which proves the comparison operator, never the allocation bound.

### D-013/02d — lakeFS commit IDs are fabricated, non-deterministic, and logged as real (T056)

Reported: `data/lakefs_ops.py:52-64` computes `sha256(f"{repo}:{branch}:{scene}:{meta}:{time.time()}")[:16]`
and then logs `"Created lakeFS commit %s ..."`. Nothing is created. The docstring promises a
"Deterministic lakeFS commit ID" while `time.time()` in the payload makes it
non-deterministic, so two ingests of the same scene produce different ids and idempotency is
impossible. The fabricated id feeds `train/baseline.py`'s `lakefs_commit_id`, which is the
lineage triple Constitution IV's reproducibility requirement rests on.

**This is the highest-risk item of the four**: it is the only one that destroys the audit
trail while emitting affirmative success messages. Ranked so explicitly because
`NEXT_STEPS.md` (before this change) told a reader the feature was complete.

### D-013/02e — `eval/calibration.py` can return an ECE above 1, and its test is intermittently red (T063) [verified here, with a reproduction]

Found not by a reviewer but by running the suite: `tests/unit/test_eval_calibration.py::test_ece_is_bounded_for_equal_length_binary_inputs`
is a Hypothesis property test, and on this run it drew a case that falsifies its own property.
Expected calibration error is a weighted mean of absolute deviations and is bounded in
`[0, 1]` by construction. Measured at HEAD with the pinned toolchain:

```
labels = [False, False, False], probabilities = [1.0, 0.5, 0.5], bin_count=2, strategy="quantile"

sklearn populated bins : 2   fraction=[0. 0.]  mean=[0.5 1. ]
repo-derived weights   : 1   [1.]              sum = 1.0
deviations             : 2   [0.5 1. ]
weights * deviations   :     [0.5 1. ]   <- numpy BROADCAST, no error raised
ECE                    : 1.5
```

**Root cause.** `_bin_weights` (`eval/calibration.py:57-78`) re-derives bin occupancy with its
own `np.searchsorted(..., side="right") - 1` + `np.clip` + `np.bincount`, then filters
`counts[counts > 0]`. `calibration_curve` derives its own populated bins by a different rule.
When the two disagree — here 1 populated bin versus 2 — the elementwise product at
`calibration_error:113-114` does not raise: numpy **broadcasts** the length-1 weight array
across the length-2 deviations and sums both. The weights summing to 1.0 makes the bug
invisible to the obvious sanity check; the length mismatch is the actual defect, and nothing
asserts the two arrays are the same shape.

The module's own docstring is where the risk was accepted and not guarded: *"``calibration_curve``
intentionally returns only non-empty bin means, not their counts. This helper mirrors only its
documented strategy boundaries"*. Mirroring an implementation's binning by re-deriving it is
the coupling; the missing shape assertion is what turns a mirror drift into a silent wrong
number.

**Three things make this worth the operator's attention now:**

1. It is in `eval/calibration.py`, one of the three modules RB-010 explicitly certified as
   **Constitution II compliant** because it delegates to `sklearn`. It does delegate — and
   then post-processes the result with hand-rolled weights that do not line up. Compliance was
   assessed by checking the import, not the arithmetic.
2. **CI is intermittently red and nobody knows it — MEASURED, and a contradiction resolved.**
   `@settings(max_examples=20)` means the pathological case is drawn only sometimes.
   Measured on the pinned toolchain, `.hypothesis/` removed before each run:
   **2 failures in 20 runs (~10%)**. The RB-012 adversarial review reported the opposite —
   "8/8 failures, deterministic" — and both observations are correct once Hypothesis's
   example database is accounted for: the first failing draw is written to `.hypothesis/`
   and **replayed on every subsequent run from that directory**, so a reviewer who hits it
   once sees determinism thereafter unless the database is cleared at the location actually
   in use. Fresh runs are ~10%; a machine that has seen it once is 100% until its database
   is cleared.
   Two consequences: this test can redden an unrelated PR at random, and a developer who
   hits it locally cannot make it go away by re-running. It is also consistent with the
   RB-011 triage (2026-09-03) recording the whole suite green — a 90%-per-run pass rate
   makes one clean sweep unremarkable, so this is draw luck rather than a new regression.
   **`main` is therefore red roughly one run in ten at `4b6ac35`, and was before this
   change.**
3. It is a promotion-adjacent statistic. An ECE that can exceed its own bound is not usable as
   evidence in a promotion decision.

**Not fixed here, deliberately.** D-013/06 declares this change fixes no code defect, and I am
not going to breach my own stated limit to land a two-line patch. There is a second reason:
`eval/` is the subject of an open, unanswered Constitution II question
(`docs/decisions/011-*.md`), and changing eval mathematics before the operator rules on method
is exactly the improvisation CLAUDE.md forbids. **T063** owns it.

---

## D-013/03 — The plan artifacts were false. Repaired here

### D-013/03a — `docs/development/NEXT_STEPS.md` [verified here]

Declared "Phase 1: Local & Dual-GPU Verification (Completed)" with ten checked boxes,
contradicting `specs/001-orbital-drift-ct/tasks.md` — which `README.md:283` names as the
tiebreaker — on lakeFS commit tracking, the windowed tile store, MLflow registry, PSI/KS
completeness, and the `dags/`+`workflows/` directories that hold only `.gitkeep`.

Worse, its "Operational Drill Runbook" named **two symbols that do not exist**:

| `NEXT_STEPS.md` step | Real symbol at HEAD |
|---|---|
| `ModelRegistryOps.rollback_production_model()` (line 36) | `rollback_production` (`registry/ops.py:185`) |
| `container.update_canary_ratio(0.0)` (line 37) | **no such method**; the only mutator is `ModelContainer.set_models` (`serve/app.py:140`) |

`docs/development/REFERENCE_GUIDE.md:118` had the first name right, so the two development
docs disagreed with each other as well as with the code. This is a rollback procedure for the
one operation carrying a < 10-minute SLA (SC-004), and `docs/runbooks/05-rollback.md` (T039)
does not exist yet, so it is what an operator would have reached for.

**Repair:** rewritten from status-fabrication into the repository's forward roadmap — the
artifact `docs/architecture/ARCHITECTURE.md:65` already cites by name and which did not
exist (D-013/04d).

### D-013/03b — `traceability/REQUIREMENT-TRACEABILITY.md` success criteria [verified here]

The matrix names `spec.md` as its source of truth. Three of six success criteria did not
match it:

| ID | `spec.md:77-82` | matrix summary before | verdict |
|---|---|---|---|
| SC-001 | ingested+cataloged within 24h, unattended, for 6 weeks | "6-week soak operated" | lossy: drops the measurable half |
| SC-002 | Retrain E2E < 12h on the RTX 5060 Ti | "≥1 organic drift-triggered retrain" | **wrong — that is SC-003** |
| SC-003 | ≥1 organic drift-triggered retrain | "≥3 incidents with postmortems" | **wrong — that is SC-005** |
| SC-004 | Rollback drill < 10 min | "Rollback drill < 10 min" | correct |
| SC-005 | ≥3 incident postmortems; weekly soak logs | "Reproducible retrain (US2 tolerance)" | **wrong — not an SC at all; it is US2's acceptance text** |
| SC-006 | Full rebuild verified once | "Rebuild from runbooks alone" | correct |

Consequence: **spec.md's SC-002 — the only performance budget in the entire specification —
was traced nowhere**, so nothing planned for it and nothing measured it.

### D-013/03c — `FR-011a` had no row at all [found by the new guard, not by a human]

Recorded deliberately, because it is the strongest available evidence that the mechanization
in D-013/07 was worth adding: the parity check was written for D-013/03b, and on its first
run it reported a defect nobody in this review had noticed. `FR-011a` — the coverage-floor
requirement, with its own decision doc, its own operator-ratified threshold, and two positive
controls — was declared in `spec.md:61` and carried by no matrix row. It appeared in the file
only inside FR-011's Notes prose.

### D-013/03d — "confirmed by search" claims that a one-line search now falsifies

`traceability/REQUIREMENT-TRACEABILITY.md` (FR-012, FR-009) and
`specs/001-orbital-drift-ct/tasks.md` (T015, T042) each assert that **zero** `src/orbital_drift`
modules import `orbital_drift.config`, and that `serve/app.py` never does. Eight modules do,
`serve/app.py` among them — RB-010 Part 5a/5b/5c wired them after Part 1 reconciled the docs,
and no later part revisited either file. Reported by two independent lenses; the eight
importers were confirmed here by search.

This is the defect class the governance skill's definition-of-done rule 6 exists to prevent,
in a document whose own header claims measurement.

### D-013/03e — `REFERENCE_GUIDE.md` conformance defects

Reported and spot-checked: it claims a "≥ 80%" coverage floor at line 4 and then uses 85 at
line 52 (the ratified floors are 85 global and 90 per-file); its documented coverage command
omits `--cov-branch`, so it measures a strictly weaker quantity than FR-011a's combined rate
and an operator running it and seeing green has not run the gate; it lists TorchGeo,
Evidently, Airflow, Argo, MLflow, lakeFS and Prometheus as the as-built Principle II stack,
none of which is a declared dependency; and it documents bare `pytest`/`ruff`/`mypy`
invocations as the gate path, bypassing `ci/checks.sh`, which `README.md:240` calls canonical.
Neither `docs/development/` file declared an audience (definition-of-done rule 6).

`scripts/setup_gpu_env.py` at line 14 was checked and **is correct** — recorded so a future
pass does not "fix" a working instruction.

---

## D-013/04 — Work RB-010 named and assigned to no part

Confirmed independently by the `spec-guardian` lens and by this session reading the RB-010
entry's own enumeration of Parts (1)-(14).

### D-013/04a — The retroactive review of T013-T052 has no part number

RB-010's disposition clause requires "retroactive spec-guardian + adversarial-reviewer review
before any checkbox flips to `[x]`". The per-part review requirement covers each remediation
part's *own diff*, not the ~40 pre-existing artifacts. The single activity gating 40
checkboxes therefore has no part number, no owning agent, no budget line, and no completion
criterion — it cannot be scheduled or claimed done. **Owned here as T057.**

### D-013/04b — The port/adapter disconnection has no part

0-for-5 confirmed at HEAD by the architecture lens and by this session: no module under
`ingest/`, `data/`, `train/`, `registry/`, `serve/` or `drift/` imports `orbital_drift.ports`;
the only importers are `ports/__init__.py` and three test modules, always via the in-memory
fakes. `ARCHITECTURE.md:56-66` routes this to "the forward-roadmap's Track A ... not yet
logged as a `docs/decision-log.md` entry" — and per decision-log rule 1, prose elsewhere
unlocks nothing, so Track A did not exist as governance. **Owned here as T058**, and Track A
now has a written home (D-013/04d).

The architecture lens measured what convergence would cost and found the two registries
split on merit: `ports/registry.py` has the better data model (immutable `LineageEnvelope`,
ordered history making rollback exact), `registry/ops.py` has the better semantics (validated
lifecycle, single-Production invariant, locking) and is the one the CT loop actually runs. It
recommends wrapping rather than rewriting: a `registry/adapter.py` implementing
`ModelRegistryPort` over an unmodified `ModelRegistryOps`, plus a port-conformance suite
parametrized over both implementations. Recorded as the reviewer's recommendation, not as a
decision.

### D-013/04c — Real lakeFS and MLflow adapters have no part

RB-010's EXPLICIT LIMIT defers "the lakeFS/MLflow-adapter-disposition questions" to "the
approved plan's forward-roadmap section". **Owned here as T059/T060**, gated on the operator
decision named in D-013/04d.

### D-013/04d — The forward roadmap did not exist [verified here]

`ARCHITECTURE.md:65` and RB-010 both defer open questions to a "forward-roadmap section"
that lived only in an `ExitPlanMode` session plan and was never committed. A merged
architecture document cited, by name, a planning artifact absent from the repository — the
same de-mechanization RB-008a clause (e) already flagged for decision-log entry text.

**Repair:** `docs/development/NEXT_STEPS.md` is now that artifact, and carries Tracks A-E.

### D-013/04e — Other named-but-unowned items, reported

- **RB-010 Part 12 is half-delivered with no deferral record.** Part 12 = "Dockerfile build
  fix **and** a docker-build CI smoke stage." The Dockerfile fix landed; `ci/checks.sh`'s
  `STAGE_LABELS` has no docker entry and the build lives as a bespoke job in
  `.github/workflows/ci.yml`. That placement may be defensible, but it deviates from an
  authorized part's text with no RB-010a-style execution record — exactly what RB-007a,
  RB-008a and RB-008b exist to require. It also breaks `README.md:48-50` and `plan.md:68`,
  both of which state the workflow is a thin caller containing no gate logic.
- **No execution record exists for Parts 4-13.** The decision log jumps from RB-010 to
  RB-011. RB-010 requires each part to have its own review pair and its own green gate run;
  whether those happened is unverifiable from the record of decisions.
- **`docs/decisions/012-*.md` has no owner.** It is `RECORDED — informational` and carries
  five live Constitution III gaps (F1-F5), one of which — `drift/trigger.py`'s
  `hysteresis_window`/`cooldown_scenes` — has config fields that already exist and name that
  file in their own descriptions. It has no decision-log entry and no task ID: precisely the
  "a note under `docs/decisions/` is reached by no gate and by no reading protocol" failure
  RB-008a(e) documented and expected to stop recurring. **Owned here as T061.**
- **`registry/ops.py:185-192` self-declares an unfixed race.** `rollback_production` takes no
  lock while `transition_stage`'s docstring promises unconditionally that two concurrent
  promotions cannot both succeed. It is also the method the old drill runbook designated as
  the SC-004 entry point. Folded into T053's neighbourhood as a named T053 sub-item is
  **rejected** — it is registry-owned; **owned here as T062.**

---

## D-013/05 — Gate-integrity findings, reported

Relayed for the operator's scheduling; not repaired here beyond D-013/07.

1. **`architecture` (RB-010 Part 8) has no positive control.** Both lenses reached this
   independently and both call it a BLOCK under the standing "stub-only is a BLOCK" rule.
   Nothing anywhere asserts `lint-imports` returns non-zero on a real violation, so a
   `.importlinter` with a misspelled module name or empty `source_modules` would exit 0
   forever. The architecture lens went further and **measured** it: installing the pinned
   `import-linter==2.13` and injecting five edges into a scratch copy, an import from
   `ports/dataversion.py` to its own real counterpart `data.lakefs_ops` was **not caught**
   (3 contracts kept, 0 broken), as were `ingest → serve` and `ports/compute → drift.trigger`.
   The contract is load-bearing for domain purity and decorative for the hexagon.
2. **`hardcode` and `deps` can be disarmed from `pyproject.toml`.** Both stages read their
   policy from `pyproject.toml` and the stage invocations pass no arguments, so a
   `fail_on_findings = false` table turns either into a permanent no-op with every existing
   test still green — the tests all pass their own `--policy-file` under `tmp_path`. The repo
   already has the counter-precedent it needed
   (`test_no_coverage_config_silently_redefines_what_the_gate_measures`) and did not apply it.
3. **`hardcode` is green by suppression.** Measured by the adversarial lens: 0 findings with
   pins honoured, **121** without. `# pin:` accepts any non-empty text — no allowlist, no cap,
   no review hook, and no test fails when a pin whose reason says "follow-up" ages out.
4. **Four CI gates enforce rules traceable to no requirement** (`hardcode`, `deps`,
   `architecture`, `docker-build`). This repo litigated and settled that rule against itself
   twice: T001a produced FR-011a and T001b produced FR-011b rather than inferring authority
   from the `hooks` precedent. An RB entry authorizes work; it is not a substitute for a
   requirement.
5. **`smoke` is a green CI job that runs zero tests**, and FR-011 — which names smoke as one
   of its six gates — carries status `Green`. One sixth of that requirement is verified by
   nothing. Repaired in the matrix here (status and Notes); the empty suite is T020's.
6. **The GPU tiers never execute in CI and always will not.** `sanity`, `integration` and
   `e2e` are not CI stages; those tests run only inside `stage_coverage`, where all seven skip
   under the `capability-guard:` allowance on a GPU-less runner. `tests/conftest.py:19-21`
   states that such skips "never fire in CI, and their tests assert that" — for these files
   that is false, and none carries the `if os.environ.get("CI"): raise` refusal that
   `test_terraform_fmt_positive_control.py` uses precisely to stop a control turning itself
   off.
7. **RB-010's CT-loop finding is still true at HEAD.** `tests/e2e/test_user_journey_ct_loop.py`
   computes both models' metrics, logs them, discards them, and promotes unconditionally. No
   lifecycle test anywhere gates a promotion on evidence. This is RB-010 Part 14, blocked on
   Part 3, blocked on the operator.

---

## D-013/06 — What this change does, and what it deliberately does not

**Does:** repairs the false plan artifacts (D-013/03), creates the forward roadmap that two
merged documents already cite (D-013/04d), gives the unowned work task IDs T053-T065 with
owning agents, adds one mechanical check (D-013/07), and records everything above.

**Does not:** fix any Critical or Major code defect in D-013/02 or D-013/05; flip any
checkbox (RB-010 forbids it before the retroactive review); create G-1/G-2/G-3; decide the
Principle II method; decide AR-3, the container-registry strategy, or the adapter
disposition; or change any gate threshold. Charter R-5 note: the config-wiring gap is on its
**second** recorded cycle (RB-010 named it, Part 5 partially fixed it, D-012 re-recorded the
remainder). A third recurrence escalates to the operator rather than looping.

---

## D-013/07 — The mechanization added, honestly scoped

`orbital_drift.traceability` now reads `specs/001-orbital-drift-ct/spec.md` and reports, as
`traceability`-stage failures, a requirement declared in the spec with no matrix row, and a
matrix row citing a requirement the spec never declares. Until now the linter read the matrix
alone, so the matrix's headline claim — that the spec is its source of truth — was checked by
nothing.

**What it does not do, stated so no reader over-trusts it:** it compares which requirement
IDs appear on each side. It does **not** check that a row's summary faithfully compresses the
spec text for that id. The matrix's own header calls its summaries "compressions, not
restatements", and no regex separates a good compression from a wrong one. **D-013/03b's
defect — three summaries carrying another requirement's text — would not have been caught by
this check, and is not caught by it now.** Summary fidelity stays reviewer-enforced. Stating
this matters: a guard advertised as catching more than it does is worse than none, and this
repo has removed unfalsifiable guards before (RB-008).

The check ships with positive controls in both directions plus a guard-the-guard
(`tests/unit/test_traceability_lint.py`), each carrying the mutation that reddens it, and it
earned its place on first run by finding D-013/03c.

**Gate runs performed here.** `tests/unit/test_traceability_lint.py` red-then-green against a
locally built `python3.12` + `pytest==9.1.1` venv; `python -m orbital_drift.projections
--check` clean after regeneration. NOT run: the full `sh ci/checks.sh all` — Docker is
unavailable in this container and four stages require it. Four tests in that file
(`test_the_committed_matrix_is_clean` and the three that reach `_collected_node_ids`) cannot
pass here for an environmental reason the linter reports honestly: `pytest --collect-only`
exits 2 because 25 test modules cannot import without the full dependency set. Linux CI is
authoritative and this change is not claimed green until CI says so.

---

## D-013/08 — Review round 1 on THIS change, and what it cost

Per CLAUDE.md's collaboration protocol the RB-012 diff went to `spec-guardian` and
`adversarial-reviewer`. **Both returned BLOCK.** Recorded here because the most important
finding is one this document would otherwise have claimed the opposite of.

### The finding that mattered: the new guard failed OPEN on the defect it was written for

`adversarial-reviewer` C1, **reproduced by this session before accepting it**: indenting one
declaration in `spec.md` by two spaces (`  - **SC-002**`) removed it from the declared set,
so deleting its matrix row lint()ed **clean**. That is D-013/03b — the untraced performance
budget — reproduced *through the guard added to prevent it*. `_SPEC_REQUIREMENT` matched one
markdown shape; `* **FR-x**`, `> - **FR-x**`, `1. **FR-x**`, `__FR-x__` and a tab after the
dash were all silently invisible.

The module's stated contract is that every failure to understand is a reported problem, and
`_parse_rows` already honoured it for partially-malformed matrix rows. Parity did not.
Fixed: `_SPEC_NEAR_MISS` reports any declaration-shaped line the canonical pattern cannot
see, so a near-miss fails the gate loudly instead of shrinking the declared set. Six
parametrized regression cases; the fix was verified by re-running the original reproduction.

**The lesson is about this document, not the regex.** D-013/07 told a reader the rule's only
limit was summary fidelity. That was false in a second, worse way, and the honest-scope
paragraph made it *more* likely to be trusted. A guard's disclaimer is itself a claim needing
a test.

### Also fixed in round 1

| Finding | Disposition |
|---|---|
| C2 / guardian 2: new merge-blocking rule with no governing requirement | **Fixed.** Three scenarios added to the "Traceability matrix is linted" requirement in the governance-harness delta, per the T001a→FR-011a / T001b→FR-011b precedent. Both reviewers noted the change indicted four other gates for this at D-013/05 item 4 while doing it a fifth time; they were right. |
| M1: `lint()`'s `else:` guard mutation-transparent | **Fixed.** The missing negative assertion added; mutating `else:`→`if True:` now reddens. |
| M2: anti-vacuity branch dead and uncovered | **Fixed.** Pinned directly against `spec_requirement_ids()`, since `lint()` early-returns before parity on an empty matrix. |
| M3: coverage regression, spec-side error paths untested | **Fixed.** The undecodable-spec twin of the matrix test added. |
| M4: disclaimer disclaimed the wrong axis | **Fixed.** Parity was reporting `NFR-`/`C-`/`R-`/`DEC-` rows as undeclared with **no way to satisfy the finding**, since those are declared in the charter and plan, which this linter does not read. `_PARITY_PREFIX` now scopes both sides to `FR-`/`SC-`, and the limit is stated in the code, the matrix header and the new spec scenario. |
| M5: the `[a-z]?` rationale stated a mechanism that does not occur | **Fixed.** Measured: dropping it makes `FR-011a`/`FR-011b` match *nothing* and the committed matrix go loudly red — not "collapse into one, silently vacuous" as originally written. Comment corrected to the measurement. |
| M6 / guardian 6: a specified scenario silently de-mechanized | **Fixed, and it was the right catch.** Removing the false "status matches the checkbox state" docstring claim would have converted a *governing requirement* into an invisible gap. The scenario is annotated PARTIALLY IMPLEMENTED and owned by **T064**; it is deliberately not weakened to match the code. |
| guardian 1 / m1: "13 of 14 remediation parts" | **Fixed.** It is 12 — Part 5 landed as three commits, which is not three parts. |
| guardian 3: scope beyond RB-012's enumeration | **Fixed by RB-012a**, an execution record naming each unenumerated item, per the RB-007a/RB-008a precedent. |
| guardian 4: an unverified claim planted in the plan of record | **Fixed.** The `max_retraining_scenes` re-dispatch claim now carries its demonstration below, or the sentence would have been deleted. |
| guardian 5: three defect claims in NEXT_STEPS with no evidence | **Fixed.** Recorded below, so the file's "evidence for every claim here" header is true. |
| guardian 7: root cause diagnosed then left unowned | **Fixed.** `docs/development/**` being outside `governed_path_globs` is now **T065**. |
| guardian 10: two Phase-6 owners off-roster | **Fixed** for T063 (`eval/` is spec-implementer's per CLAUDE.md). T061 keeps `spec-implementer` as coordinator because its five sites span four owners; the per-module handoffs are named in the task. |
| m2 / 8, 9, 11, 13: ranges, ordering, ID convention, list break | **Fixed.** |

### Two findings REJECTED on measurement, with the evidence

Recorded because a review finding accepted without checking is worth as little as a claim
made without checking.

1. **C3 — "the ECE failure is deterministic, 8/8, not intermittent."** Not reproducible.
   Measured here: **2 failures in 20 runs with `.hypothesis/` removed before each**. The
   reviewer's observation and this one reconcile through Hypothesis's example database,
   which replays a found counterexample on every later run from that directory — see
   D-013/02e, which now carries both the rate and the mechanism. The "intermittent" framing
   stands, made precise. The reviewer's underlying point — that `main` is red and this batch
   does not fix it — stands untouched and is stated plainly in D-013/02e.
2. **M7 — "RB-012's budget exemption is self-granted."** The reviewer rates this [Likely] and
   names the counter-precedent itself: RB-009 used the identical formula. The distinction it
   draws (RB-009 added no new *enforced rule*) is real, and the question of whether a new
   gate rule consumes the M0 feature budget is genuinely the operator's. **Escalated, not
   resolved:** flagged in the PR description for an operator ruling rather than argued away.

### Round-1 items deliberately NOT actioned

- **m7 (stale `file:line` citations in test docstrings).** Real, and pre-existing at
  `4b6ac35`; this change shifts the anchors further. Chasing them is a mechanical sweep that
  belongs in its own PR, not in a batch already spanning thirteen files.
- **m5 (`SPEC` hardcodes the `001-orbital-drift-ct` slug).** True; it fails open by staleness
  once a feature 002 exists. No second feature exists, and inventing a discovery rule now
  would be speculative. Named here so it is not discovered late.
- **m6 (`_TRACE_TASK` extracts ids from anywhere in the acceptance text, not just after
  `Trace:`).** Pre-existing shape, widened slightly by this change. A real weakness in the
  parity direction; not this batch's to redesign.

### Evidence for the claims round 1 found unsupported

Recorded so `NEXT_STEPS.md`'s "evidence for every claim here is D-013" header is true.

- **STAC has retry/backoff but no rate limiting.** `ingest/stac_client.py` builds
  `requests.Session()` with no `HTTPAdapter`, no `urllib3.Retry`, no token bucket; the
  backoff is a per-call attempt budget, which is a different thing.
- **Cloud fraction uses a different denominator than the threshold's other consumer.**
  `ingest/cloud.py:100` divides by `valid_pixels` (excluding NO_DATA and SATURATED) while
  `ingest/stac_client.py` filters on scene-wide `eo:cloud_cover`, and RB-010 Part 5a wired
  both to the same `cloud_cover_max_threshold`. A swath-edge tile that is 60% NO_DATA and
  30% cloud reads as 0.75 locally and 30% at the STAC boundary, so edge tiles are
  systematically over-excluded — cloud starvation caused by the metric.
- **Tile-store writes are not atomic.** `ingest/tile_store.py` writes band `.npy` files one
  at a time with `metadata.json` last and no temp-file-plus-rename. A power cut mid-save
  leaves a scene directory `list_scenes()` skips (it requires `metadata.json`), so the
  pipeline believes the scene is absent — the spec's own home-lab-restart edge case.
- **`drift/trigger.py`'s staleness net can re-dispatch a retrain that is still running**
  (the claim guardian finding 4 objected to). Demonstrated by the adversarial lens with
  `hysteresis_window=2, cooldown_scenes=2, max_retraining_scenes=3`: with a retrain
  dispatched at scene 2 and still in flight, triggers fire again at scenes 5 and 8 — three
  dispatches where queue-depth-1 coalescing promises one. The manager cannot distinguish
  "the caller forgot `mark_retraining_failed()`" from "the job is still running" and assumes
  the former. **Reported, not reproduced by this session**; T057's review of T036 settles it.

## D-013/09 — Review round 2: the CI failure, and what it was not

The RB-012 PR's first CI run reddened one check, `coverage`. It was **not** the ECE defect
of D-013/02e, and diagnosing it produced a third pre-existing flake nobody had named.

**Failing test:** `tests/unit/test_serving_edge_cases.py::test_predict_400_masks_internal_exception_detail`.
It plants a model that raises a marker string, and asserts the marker reaches the server log
but not the HTTP response. CI showed a real `RuntimeError: Given input size: (32x1x1)` — a
genuine `SimpleUNet` shape error, i.e. **a different model ran than the one planted**.

**Why `coverage` and not `unit`:** that stage runs every suite in one pytest process, so it is
the only stage where module-level state crosses suite boundaries. The stage's own diagnostic
says to check whether the ordinary stage agrees; it did — `unit` was green on the same commit.

**Mechanism — two production defects meeting, neither in the RB-012 diff:**

1. `ModelContainer.set_models` assigns `staging_model` only when one is **passed**
   (`serve/app.py:157`), while setting `canary_ratio` **unconditionally** to its `0.10`
   fallback. A caller loading only a production model therefore inherits whatever staging
   model was set before, at a 10% traffic share it did not ask for.
2. Canary routing draws from the unseeded process-global `random.random()`
   (`serve/app.py:263`) — inconsistent with RB-010 Part 5c, which made `drift/metrics`
   *require* a seeded `Generator` for reproducibility.

So one request in ten was routed to a stale staging model, the planted raiser never ran, and
the marker never reached the log.

**Measured, not asserted — and this is what makes it not this PR's:**

| tree | failures |
|---|---|
| `main` at `4b6ac35` (base) | **2 / 30** |
| RB-012 branch | 4 / 30 |
| after the fixture below | **0 / 40** |

Same defect at the same rate within sampling noise, present before this change touched
anything. The RB-012 diff modifies no file under `serve/` and no serving test.

**Disposition.** The cross-test leak is cured by an autouse fixture resetting the shared
`container` in that file — test-only, no production behaviour changed, no assertion weakened,
and every test still runs. That is deliberately *not* quarantining: the rule against skipping
a failing test protects tests that are finding real defects, and this one was finding a
**state-isolation** defect that the fixture removes at its source. **Both production defects
survive and are now owned by T053**, which rewires `set_models` regardless.

**What is NOT fixed, and must not be:** the ECE defect (D-013/02e) still reddens roughly one
run in ten, and that test is *correctly* failing — it found real broken arithmetic. Making it
pass without fixing `eval/calibration.py` would be quarantining. It stays red until T063,
which needs the operator's Principle II ruling first.

## Follow-ups found during this review, NOT fixed here

Each is unscheduled beyond the task IDs noted; listing is not agreement to do them.

| # | Finding | Suggested owner |
|---|---|---|
| 1 | `roadmap_data.py:19-20` and `tests/unit/test_projections.py:5` both claim the tests assert "story status matches the checkbox state". `Story` has no status field and no test asserts it. The module's headline property is half-mechanized and documented as fully mechanized. | fixed here (docstrings corrected) |
| 2 | `_TASK_LINE`'s `\b` made T001a, T001b and T004a invisible to both projection parity tests, so three real tasks were silently absent from the backlog. | fixed here (regex widened, three stories added) |
| 3 | `roadmap_data.py` S1.10 invents gate id `G-4`, which no governed document declares. | operator: declare G-4+ in the gate table, or reword |
| 4 | `plan.md`'s module map omits `tests/architecture,sanity,integration,e2e`, `Dockerfile`, `.importlinter`, `docs/development/`, and asserts `II: ... PASS` for a principle RB-010 records as violated. | spec-implementer, own PR |
| 5 | `README.md:19` asserts the eval layer satisfies Constitution II; `CHANGELOG.md:35,39` still claim real lakeFS and MLflow integration. Part 1 corrected neighbouring lines and left these. | spec-implementer, own PR |
| 6 | `README.md:240` says "eighteen stages"; `STAGE_LABELS` holds 17 plus the `all` alias. | spec-implementer, own PR |
| 7 | `ARCHITECTURE.md:228` pins "PyTorch 2.11+cu128" against `pyproject.toml`'s `torch==2.13.0`; `:260` claims a 95% coverage floor superseded at RB-006. | spec-implementer, own PR |
| 8 | The forbidden-third-party list has two hand-kept homes (`.importlinter` and `pyproject.toml`) with different spellings and no lockstep test — the defect class `LABEL_COLUMNS` was single-homed to cure. | infra-scaffolder |
| 9 | `eval/superiority.py`'s `min`/`max` clamp of the percentile interval is a project-authored estimator with no citation. It is conservative in the pass direction, so not a safety hazard, but Part 3's method decision should name it rather than inherit it silently. | folds into the D-011 decision |

## Verified correct — no action

- **`scripts/setup_gpu_env.py`** exists; `REFERENCE_GUIDE.md:14` is right about it.
- **`plan.md`'s hexagonal-layer paragraph and `ARCHITECTURE.md` section 0** are accurate and
  self-critical, including the 0-of-5 adapter count. RB-010 Part 1 did its job on the
  artifacts it enumerated; the two `docs/development/` files were simply never in its scope,
  which is why they and not the others were the stalest documents in the repository.
- **The governance half of the test suite** (`tests/governance/`, `test_ci_contract.py`,
  `test_checks_sh_behaviour.py`, `test_coverage_positive_control.py`,
  `test_gitleaks_positive_control.py`, `test_guard.py`) is tested to a materially higher
  standard than the ML half, per the test-adequacy lens. The asymmetry, not the governance
  tests, is the finding.
- **Parts 3 and 14 are correctly blocked**, not forgotten. Their blocker is a decision only
  the operator can make.
