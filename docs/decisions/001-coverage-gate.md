# D-001: The `coverage` CI gate

**Status:** decided 2026-08-15.
**Provenance, stated precisely:** the operator asked for "full test suite with coverage". That request is the authority for **FR-011a**; everything below is an agent research conclusion with measurements, not an operator decision — **except the threshold number, which the operator ratified at 85 on 2026-08-16** after being shown D-05's reasoning and the alternatives. It was an agent proposal until that date, and this line said so.
**Decision-ID namespace:** this file's `D-001/D-nn` series is independent of `plan.md`'s Decision Log and of `D-000`'s. Cross-references from spec/plan/tasks to this file are written `D-001/D-nn`.
**Why this exists:** a gate that no requirement asks for is scope creep, and a threshold with no stated rationale is a magic number wearing a config file as a disguise. Both are `spec-guardian` BLOCKs. This file supplies the missing reasoning.

---

## D-01 — The `hooks` precedent does **not** authorise this gate; FR-011a does

The obvious argument for adding an eighth stage is that a seventh already exists: `hooks` is not one of FR-011's six, and it shipped in T001. That argument does not survive contact with *why* `hooks` was allowed.

`hooks` was **ruled conformance, not scope creep**, in both T001 review rounds (`D-000`, follow-up amendments, 2026-08-09) on a specific ground: Constitution VII already requires gitleaks to run "as pre-commit hook **and** CI gate", and a pre-commit config that no CI job ever executes does not satisfy the first half. The stage enforced a requirement that already existed.

No equivalent hook exists for coverage. Nothing in the constitution, `spec.md`, or `plan.md` requires coverage measurement. Principle III constrains *where a threshold lives* once you have one; it does not create one. Principle V requires contract tests to exist and be observed failing; it says nothing about measuring what fraction of lines they reach.

**Therefore:** the requirement was added explicitly as **FR-011a** rather than inferred. An eighth gate with no FR to trace to is exactly the shape of scope creep `spec-guardian` is instructed to block.

Note also that FR-011 (six gates) and Principle V (five: "lint, type-check, unit, contract, and gitleaks", with DAG smoke in the preceding sentence) already disagree on the enumeration. That inconsistency shows these lists are prose floors rather than exhaustive closures — which is why adding a gate is not *per se* a violation — but it does not supply the missing requirement, and it is **not** fixed here. Correcting the constitution's wording in the same change that adds a gate would read as amending the constitution to authorise one's own feature. That fix belongs in a separate PR.

## D-02 — Self-arming is free: it falls out of coverage's own arithmetic, not a filename check

The problem: `src/orbital_drift` currently holds seven `__init__.py` files containing nothing but docstrings. A `--cov-fail-under` therefore looked like a choice between vacuous (`0`) and build-breaking.

**The rejected design** was a branch mirroring `pytest_suite()`'s DECLARED-EMPTY idiom: if the package holds no module other than `__init__.py`, print a message and pass. This was wrong for the reason `ci/checks.sh`'s own `pytest_suite` header already records — a filename heuristic is fragile, and that exact class of condition was previously measured letting a real gap through here. It is worse in this instance: real Python puts real code in `__init__.py`, so the first time somebody lands executable lines in `ingest/__init__.py` the condition would keep the gate disarmed **silently and permanently**.

**Measured instead** (pytest-cov 7.1.0, coverage 7.15.4, CPython 3.12.3, this repo, 2026-08-15):

| Tree state | Stmts | Result |
|---|---|---|
| seven docstring-only `__init__.py` | 0 | `100%` — `Required test coverage of 85% reached` |
| same, plus one uncovered 6-statement module | 6 | `0%` — `FAIL Required test coverage of 85% not reached`, exit 1 |

Coverage reports 100% for zero measurable statements. **So a real threshold can be set from day one and arms itself the instant the first executable line lands** — no branch, no glob, nothing to disarm. The condition is the tool's own statement count, which is the same "put the question to the tool" discipline `pytest_suite()` applies by asking pytest rather than `find`.

The second row also confirms the planted module was counted **despite no test importing it**. That is a property of the path form (below), not a given.

## D-03 — `--cov=src/orbital_drift` (path), not `--cov=orbital_drift` (package name)

The path form has no import-resolution ordering dependency under the `src` layout: `pythonpath = ["src"]` is applied by pytest during initial conftest loading, and coverage's package-name resolution for `source=` wants the name importable at start-of-measurement. The path form sidesteps that entirely and works identically with and without the editable install.

It is also what makes never-imported files report as `0%` rather than being omitted from the report — measured in D-02's second row. A gate that silently ignores the module nobody wired up yet is not measuring what it claims to.

## D-04 — `coverage` is pinned separately from `pytest-cov`

`pytest-cov` is a shim; `coverage` is what computes the number the gate fails on. `pytest-cov 7.1.0` requests `coverage[toml]>=7.10.6` — an unpinned floor. Principle IV ("all chart, image, and package versions pinned") has no transitive exemption, and `HATCHLING_VERSION` is the standing precedent that a transitive, build-time-only dependency still gets a pin.

Pinned as `coverage==7.15.4`, **without** the `[toml]` extras bracket: that extra only pulls `tomli` on Python `<= 3.11.0a6`, so it is inert on the pinned 3.12, and a bracketed name would not match the distribution key the `pyproject`/`versions.env` lockstep test compares against.

`pytest-cov 7.1.0` specifically, rather than an older line: it is the release that made total-coverage computation independent of reporting settings, so `--cov-fail-under` no longer varies with `--cov-report`. A threshold that moves with an unrelated flag is not a threshold.

## D-05 — Why this number

`COVERAGE_MIN_PERCENT` satisfies Principle III's letter by living in `ci/versions.env` as a reviewable pin. Its intent needs a reason, so: the threshold is set at a level the repo **provably clears today** (0 statements → 100%) and which is a defensible floor for the pipeline-boundary code Phases 1–4 will add, where every boundary already gets contract tests by Principle V.

It is deliberately **not** set to a number chosen to match whatever the first real module happens to score. When Phase 1 lands and the number becomes load-bearing, it should be revisited against measured reality and raised — a floor that never moves is a floor nobody is using.

**Status of this number: RATIFIED by the operator, 2026-08-16, at 85.** It was an agent proposal until then, and this paragraph previously said so — recording that `T001a` would stay unchecked in `tasks.md` and its pull request stay a draft until the operator confirmed both the coverage request and this number.

That condition is now partly stale and partly met, and both halves are worth stating precisely rather than deleting. The pull request (#2) **merged on 2026-08-16 without being a draft**, so the draft half of the condition did not hold in practice. The number half did: the operator was asked directly, was shown this section's reasoning and the alternatives (90, and 75–80), and chose 85 — explicitly ratifying the existing pin rather than picking a new one. `T001a` is checked off on that basis, not on the merge.

The gate ran enforcing throughout, which cost nothing and still costs nothing: with zero measurable statements the threshold is cleared by any value, so the pin does not begin to bite until Phase 1 lands executable code. What changes here is only the provenance of the number — from "agent proposal" to "operator decision" — which is the distinction `D-000`'s header format exists to keep honest.

The commitment in the paragraph above is unaffected and still binding: revisit and **raise** this floor when Phase 1 makes it load-bearing.

Lowering it to make a red build green is the failure mode to watch. The correct response to a breach is tests, or an explicit, reviewed change to this pin with the reason recorded here.

## D-06 — Accepted cost: `stage_all` runs `tests/unit` twice

`stage_coverage` runs all suites in one pytest process. It cannot reuse the existing per-suite stages' output because `.github/workflows/ci.yml`'s matrix runs each stage as a **separate job**, and combining per-job coverage would need artifact upload plus a combine step — orchestration logic in a file whose own header forbids gate logic.

Three tempting cheaper designs were rejected because each weakens a gate:

- **Let `stage_coverage` replace `unit`/`contract`/`smoke` in `stage_all`.** A single `pytest tests/` destroys `pytest_suite()`'s exit-5 discrimination: `tests/unit` always collects, so an empty or broken `tests/contract` would no longer produce exit 5 at all and the collection-error branch becomes unreachable.
- **Exclude `tests/unit` from the coverage run.** Under-measures the moment a unit test exercises `src/`.
- **Drop `stage_coverage` from `stage_all`.** Breaks README's contract that `sh ci/checks.sh all` is what CI runs, which is the whole "green locally, red in CI" thesis.

So the ~2× on the dominant local term is **accepted and documented** rather than optimised away. In CI the matrix parallelises it, so wall-clock is roughly unchanged at the cost of one extra runner job.

## D-07 — `stage_coverage` asserts Docker and git, like `stage_unit`

The coverage run includes `tests/unit`, whose gitleaks positive controls drive real containers and real git, and whose `_tool()` helper falls back to `pytest.skip()` outside CI. Without both guards the stage would be a **fail-open**: on a Docker-less machine it would report a green coverage number computed from a run in which eight controls silently skipped. Both guards carry reason strings distinct from `stage_unit`'s and `stage_hooks`'s, so a failure names which stage's dependency is unmet and why.

## D-08 — What is deliberately not changed

- **`addopts` stays `-ra --strict-markers --strict-config`.** Putting `--cov` there would attach coverage to `pytest_suite()`'s `--collect-only` probe and would make every stage hard-fail with "unrecognized arguments: --cov" on any machine without `pytest-cov` — including `contract` and `smoke`, whose pin sets do not assert it.
- **`.pre-commit-config.yaml` gains no hook.** Coverage is a CI gate, not a per-commit one; a hook that runs the full suite on every commit trains the `--no-verify` reflex Principle VII depends on nobody having.
- **The mypy hook's `additional_dependencies` stays `["pytest==9.1.1"]`.** Nothing under `files = ["src", "tests"]` imports `pytest_cov`.

## D-09 — KNOWN GAP, dated: `dags/` is not measured, and three spec edge cases live there

`--cov=src/orbital_drift` measures the package and nothing else. `dags/` is a top-level directory outside the package and outside `[tool.hatch.build.targets.wheel].packages`, and it is where three of `spec.md`'s six edge cases are due to land:

| Task | File | Edge case |
|---|---|---|
| T020 | `dags/ingest.py` | bounded backfill / idempotency after power loss |
| T037 | `dags/drift.py` | **starvation vs distribution shift** — must not retrain on "no clean data" |
| T038 | `dags/retrain.py` | **promotion race** — queue depth 1, later triggers coalesce |

The failure mode is quiet, and it is worth stating plainly: once T020 lands, `tests/smoke/test_ingest_dag.py` will import and exercise `dags/ingest.py`, that code **will execute under measurement** because `stage_coverage` runs `pytest tests`, and it will then **not be counted**, because it is outside the `--cov` source path. The stage would report a green number about `src/orbital_drift` while the DAG deciding whether to retrain has no enforced coverage at all, and nothing would say so.

This is conformant — FR-011a scopes itself to `src/orbital_drift` — and it is still a gate reporting green over something it should be measuring. Recorded as a **deferral, not an oversight**:

**When T020 lands, widen the stage to `--cov=src/orbital_drift --cov=dags` and raise this with the operator in the same PR.** Until `dags/` contains anything, adding the flag would only assert coverage of an empty directory. Noted 2026-08-15.

## D-10 — Escape hatches the gate closes, and one it inherits

`stage_coverage` unsets `PYTEST_ADDOPTS` and says so, mirroring `stage_hooks`'s treatment of `SKIP`. This is not hypothetical: pytest splices that variable into argv, and while a `--cov-fail-under=0` injected there loses to the flag the stage passes last, the **boolean** switches do not lose. `PYTEST_ADDOPTS='--no-cov'` (pytest-cov's documented "disable coverage completely", which warns rather than erroring) and `PYTEST_ADDOPTS='--collect-only'` each produced a green stage over a run that measured nothing — verified before the fix. `test_pytest_addopts_does_not_survive_into_the_coverage_run` asserts the fix behaviourally rather than by grepping for `unset`, because `_saved=$PYTEST_ADDOPTS; unset …; export PYTEST_ADDOPTS=$_saved` satisfies a grep.

The coverage **config** surface is guarded by test rather than by the script: `test_no_coverage_config_silently_redefines_what_the_gate_measures` fails if a `[tool.coverage]` section, `.coveragerc`, `setup.cfg` or `tox.ini` appears. One line — `exclude_also = ["."]` — matches every line in the tree, drops the statement count to zero, and makes coverage report 100% forever with real untested code present. Rejecting a filename heuristic (D-02) while leaving an easier disarm mechanism unguarded would not have been a design.

## D-11 — A second, independent review pass found a real precedence bug in D-10's own override detection

A senior-engineer review pass after D-10 landed (2026-08-16) found that the `python_files`-override detection D-10 describes was itself checking the wrong thing: it treated "does any candidate file, checked independently, contain a `python_files` line" as the question, when the real question is "which ONE file does pytest actually read, and does *that* file have the line". These are different, and MEASURED different — with a real pytest run:

- `pyproject.toml` carrying an **empty** `[tool.pytest.ini_options]` table (no `python_files` line at all) made pytest govern itself by that empty table and **completely ignore** a real `python_files` override sitting in `tox.ini`. A "check every file independently" loop would have reported `tox.ini`'s override as live and failed the stage closed for a file pytest never reads.
- `setup.cfg`'s pytest section is `[tool:pytest]`; a `[pytest]` section in `setup.cfg` is not merely ignored, it is a **hard pytest error** ("no longer supported, change to `[tool:pytest]` instead") — confirmed by running it, not assumed from memory. The original test for this case used the wrong header.

Fixed by replacing the "check each candidate independently" loop with pytest's real precedence chain (`pytest.ini` unconditionally, else `pyproject.toml` if it carries the ini-options table at all — even empty, else `tox.ini` if it carries `[pytest]`, else `setup.cfg` if it carries `[tool:pytest]`) and checking `python_files` only on the one file that governs. `test_a_non_governing_files_override_is_correctly_ignored` pins the fixed behaviour directly; the existing `tox.ini`/`setup.cfg` override tests were corrected to empty `pyproject.toml`'s table first (via `_synthetic_root(pyproject="")`), since leaving it in place — as the original tests did — was itself testing a scenario real pytest cannot produce.

A second, related ordering bug was fixed in the same pass: the `collect_rc == 5` branch checked `collectable_count > 0` (built from pytest's DEFAULT patterns) *before* checking whether an override governs. Once an override is active, a file matching the DEFAULT patterns is not "a module matching pytest python_files" — pytest is looking for a different pattern and correctly found nothing — so checking the count first misreported that correct outcome as a collection error. The override check now runs first.

A third, unrelated finding from the same review: the two `find` commands behind `collectable_count`/`module_count` duplicated their prune clause verbatim, so a future change to what gets pruned had two call sites that could silently drift apart. Extracted into `_pytest_suite_walk()`, a function rather than a shared string variable — POSIX sh has no arrays, and interpolating an unquoted variable containing `-name '*.egg'` would let word-splitting glob-expand `*.egg` against the CWD before `find` ever saw it. Passing the post-prune test as literal call-site arguments through `"$@"` avoids that entirely.

## D-12 — The coverage-vs-test-failure diagnosis (D-10) was not sound; a second pass corrected it

A second, independent adversarial pass (2026-08-16, distinct from D-11's) found that D-10's two-way diagnosis — an unanchored `grep` for the coverage-breach *prose* — was unsound in two ways, one of them severe enough to actively mislead:

**(a) It is self-referential.** `tests/unit/test_coverage_positive_control.py` asserts on the literal string `"Required test coverage of 85% not reached"`. If that assertion itself ever fails — a future pytest-cov wording change, anything — pytest's rewritten-assertion traceback reprints the literal string being checked for. An unanchored grep over the *whole run's combined output* then matches that traceback, not a real coverage report, and reports `COVERAGE BREACH. The tests are not the problem` for a run in which a test — the one designed to catch exactly this class of regression — genuinely failed. Verified directly: pytest prefixes assertion-introspection lines with `>` or `E`, never a bare `FAILED ` at column 0, so anchoring to `^FAILED ` (pytest's own per-test marker) cannot be fooled by that traceback text.

**(b) The two conditions are not mutually exclusive.** Traced against the vendored `pytest_cov/plugin.py`: `_should_report()` only suppresses the "Required test coverage" line on a test failure when `--no-cov-on-fail` is passed, which this stage does not. A single run can print *both* a `^FAILED <nodeid>` line and a `^FAIL Required test coverage...` line — a real test failure and a real coverage breach, together — and the old grep would still report "the tests are not the problem," which is false in that run.

**Fixed** by checking pytest's own `^FAILED ` marker **first, unconditionally** — a structural signal, not prose-matching — and only falling through to the (now line-anchored, not prose-fragment) coverage-breach check if no test failed. A mixed run is reported as a test failure with a secondary note that coverage also breached. A third branch was added for the case neither signal fires (a collection error — foreseeable given D-06's single-process design: a basename collision between `tests/unit`, `tests/contract` and `tests/smoke` hits pytest's "import file mismatch" error, which is neither a named test failure nor a coverage breach).

Testing this required extending the test harness: `shell_harness.py`'s Python stub gained a `pytest_run_stdout` knob, file-based (unlike the existing single-line `pytest_collect_stdout`) because a real combined-suite report is multi-line and newlines cannot be interpolated into the generated stub's double-quoted `printf` argument. Five behavioural tests drive all three branches plus the mixed case and the self-reference regression directly; a mutation check (reverting to the old unanchored two-way logic) confirmed three of the five fail against it and two do not — the two that don't are exactly the simple, non-adversarial cases the old logic also handled correctly, which is the expected shape for a precise regression test rather than incidental padding.

**Accepted, not engineered further:** two narrower evasions remain theoretically open — a `COVERAGE_RCFILE` environment variable pointing at an unwatched config file, and a `--cov-config=PATH` added directly inside `ci/checks.sh`'s own invocation (a `pyproject.toml [tool.coverage]`/`.coveragerc`/`setup.cfg`/`tox.ini` route is already guarded by `test_no_coverage_config_silently_redefines_what_the_gate_measures`, and `--cov-config` appearing in `addopts` specifically *would* be caught there too, since `"--cov"` is a substring of `"--cov-config"`). Neither is live today. Noted rather than guarded against, since the marginal engineering cost of chasing every conceivable pytest-cov configuration surface exceeds the value at this project's current stage — revisit if either becomes a real vector.

## D-13 — A third review pass found the D-12 remediation was misleading, and this doc's own D-11/D-12 citation was wrong in two files

A third pass (`spec-guardian` APPROVE; `peer-reviewer` BLOCK-then-fixed, 2026-08-16) on D-11/D-12's fixes found two real problems, both fixed, plus recorded one residual limitation rather than overclaiming past it.

**The `^FAILED `-branch remediation hardcoded `sh ci/checks.sh unit`** regardless of which suite the failing test actually lives in. Latent while `tests/contract` and `tests/smoke` are still empty — but this stage runs all three suites together (D-06), and the moment either gains its first real test (T013/T014, T020), a failure there would have sent the operator to check `unit`, which reports GREEN for the unrelated reason that the broken test simply isn't in it — and the message's OWN next line ("if they are GREEN and this is RED, ... not a broken test") would then have actively argued them away from an ordinary, real bug. Fixed by reading which suite(s) the `FAILED` lines actually name and suggesting only those, with an explicit fallback to all three if none of the recognised path prefixes match (fail loud, not silent — the same bias `pytest_suite`'s override detection already uses). Four new behavioural tests pin this per-suite, including the fallback case; a mutation check (reverting to the hardcoded suggestion) confirmed three of four catch it.

**This doc's own D-12 was cited as `D-11`** in both `ci/checks.sh` and `tests/unit/test_checks_sh_behaviour.py` — `D-11` is a real, different, earlier entry in this same file (the precedence fix), so the citation pointed at a real-but-wrong decision rather than a nonexistent one. Fixed in both files.

**A mechanical guard was added and then honestly re-scoped after mutation-testing it.** `test_every_d_nn_citation_in_checks_sh_points_at_a_real_decision` (`tests/unit/test_ci_contract.py`) cross-checks every bare `D-NN` citation in `ci/checks.sh` against this file's actual `## D-NN` headings. The first version of its docstring claimed it was "the mechanical watch" for the exact D-11/D-12 mix-up above; reverting the fix and re-running the test proved that claim false — since `D-11` is a genuine heading, an existence check cannot tell "cites the wrong real decision" from "cites the right one". The docstring was corrected to state the narrower, true guarantee (catches a citation to a number with NO heading at all — a typo, a deleted section, a renumbering — not a citation pointed at the wrong real one), and that narrower claim was itself verified: a citation to a genuinely nonexistent `D-99` is caught; the reverted `D-11`-for-`D-12` swap is not. Recorded here as the same discipline this whole document tries to model — a claim that survives only until someone runs the test against it is not yet a verified claim.

## D-14 — The gate measures ONE combined statement+branch rate, not two bars

**Provenance:** agent research conclusion under an operator decision. RB-008 part (3) (`docs/decision-log.md`, 2026-08-22) authorises "branch coverage enabled through the repo's pins-plus-CLI pattern" and states the explicit limit "no gate bar VALUE changes anywhere". The operator decided THAT branch coverage is enabled; everything below is how, with the measurements, and was not separately put to them. The reasoning was written into `src/orbital_drift/covcheck.py`'s module docstring first and is carried here because a module docstring is not where this repo keeps design decisions, and because `ci/checks.sh`'s breach message and `pyproject.toml:139` both send a breaching operator to THIS file for the coverage-gate rationale.

**The decision.** `ci/checks.sh`'s `stage_coverage` passes `--cov-branch`. (Cited by FUNCTION NAME, never by line: this reference read `ci/checks.sh:993` until the merge with `main` inserted lines above the function and moved it — measured at c35afc6, `stage_coverage` then began at :965 and its `--cov-branch` invocation at :1048, while :993 had become prose about the `PYTEST_ADDOPTS` escape hatch. That is D-13's citation-drift class arriving from the direction D-13 records nothing watches: `test_every_d_nn_citation_in_checks_sh_points_at_a_real_decision` checks `D-NN` references *inside the script*, and no guard checks `file:line` references pointing the other way. A function name survives any edit that does not rename it.) With branch measurement on, coverage.py's `summary.percent_covered` — the field the terminal TOTAL `--cov-fail-under` tests, **and** the per-file field `orbital_drift.covcheck` reads out of `--cov-report=json` — stops being the statement rate and becomes the combined rate:

    (covered_lines + covered_branches) / (num_statements + num_branches)

Both floors keep their VALUE (global 85 per D-05/RB-006, per-file 90 per RB-006); the QUANTITY each compares gets strictly harder for any file whose arcs are less covered than its statements. FR-011a, `tasks.md` T001a and the README were amended in the same change to say "statement and branch" rather than "statement" — a gate that enforces more than its requirement claims is the drift Constitution IV exists to prevent.

**Measured, and how.** Statement-only → combined, on the tree at 9de5a0e: `guard.py` 97.81 → 96.55, `remotes.py` 97.22 → 95.45, `projections.py` 98.85 → 98.29, `covcheck.py` 98.15 → 97.30, `traceability.py` 90.74 → 91.45 (**up** — that file's arcs are better covered than its statements), global 96.81 → 96.18. A global number that FALLS on this change is the flag working, not a regression. After the eleven exposed arcs were closed (8d12321), the same invocation measures 533 statements / 200 branches, 0 missed and 0 partial — 100% on both — re-measured 2026-08-22 by running `pytest tests --cov=src/orbital_drift --cov-branch --cov-report=term-missing --cov-report=json --cov-fail-under=85` directly (Docker was down, so the eleven container-gated controls failed; they touch no `src/orbital_drift` line, and `orbital_drift.covcheck --floor 90` over the resulting `coverage.json` reported every module at or above the floor).

**Why the CLI and not a config section.** `[tool.coverage]` in `pyproject.toml` is forbidden outright by `test_no_coverage_config_silently_redefines_what_the_gate_measures` (`tests/unit/test_ci_contract.py`), so the command line is the only home left. `test_the_coverage_stage_measures_branches_not_only_statements` (`tests/unit/test_checks_sh_behaviour.py`) keeps it occupied.

**Rejected: a SEPARATE branch bar.** coverage.py 7.15.4 also emits `percent_statements_covered` and `percent_branches_covered` per file, so splitting the floor in two was available. Rejected for four reasons, in order of weight:

1. A second bar needs a second THRESHOLD VALUE, and RB-008's explicit limit is that no gate bar value changes anywhere in that batch — introducing one is as much a bar change as moving one. That alone settles it; the rest is why the answer would not differ without the constraint.
2. The combined rate at 90 is already strictly harder than the statement rate at 90 for four of the five measured files, so the gate tightened with no threshold edit — which is the point of the change.
3. A branch-only bar is **vacuous on exactly the file it would most need to judge**: coverage.py reports `percent_branches_covered = 100.0` when `num_branches == 0`, so a file with no arcs passes a branch floor by having nothing to fail. Measured 2026-08-22 against coverage 7.15.4 with a purpose-built branchless module that is **only 62.5% covered by statements** and still reports `percent_branches_covered: 100.0`. That module is committed, not merely described — `_BRANCHLESS_MODULE` in `tests/unit/test_coverage_positive_control.py`, asserted by `test_a_file_with_no_arcs_reports_one_hundred_percent_branches_covered`, so a coverage.py release that changes the behaviour fails loudly here rather than silently invalidating this bullet. The combined rate degrades gracefully to the statement rate in that same case (62.5 and 62.5), which is asserted in the same test.
4. It needs no new machinery in `orbital_drift.covcheck` — zero lines change — so there is no new code path to test and none to rot.

**The cost, named.** A breach message reports ONE blended percentage, so it does not say whether statements or arcs caused the shortfall. It does say what the percentage was taken over — `orbital_drift.covcheck` emits `<file>: 85.7% combined statement+branch over 5 statements and 2 branches < per-file floor 90%`, naming the quantity and both counts in its denominator. That wording is not decoration: the message shipped as `85.7% over 5 statements` until a spec-guardian re-review of this PR caught it, which paired a numerator drawn from statements AND arcs with a denominator labelled "statements" and so sent an operator hunting for uncovered LINES in a file whose entire shortfall may be arcs — the same defect class as FR-011a being false about its own gate, one layer down. Pinned by `tests/unit/test_covcheck.py::test_a_breach_message_names_the_quantity_it_measured_not_only_statements`, so this paragraph is a checked claim and not prose. What remains uncarried by the message is the SPLIT of the shortfall, and that is tolerable only because the split is already on the operator's screen: `stage_coverage` prints its `--cov-report=term-missing` table (`Branch`, `BrPart`, and the exact missing arcs, e.g. `214->220`) **before** it runs `covcheck`, never after. If a future decision does want two bars, `percent_statements_covered` and `percent_branches_covered` are already in the report; the reversal costs a DEC/RB entry for the new value, not a redesign.

**Residual risk, stated.** The combined-rate arithmetic is a behaviour of coverage.py, not of this repo, and both floors now depend on it. `tests/unit/test_covcheck.py`'s fixture cannot pin it — that fixture computes the combined rate itself and plants the answer, which proves only that `check()` reads `percent_covered`. The engine-side pin is `test_percent_covered_is_the_combined_rate_computed_from_the_reports_own_counts` (`tests/unit/test_coverage_positive_control.py`), which recomputes the expected value from the four counts the report itself carries; a coverage.py release that put the statement rate back in that field would loosen both floors silently, and that test is the only thing standing between here and there.

---

## Follow-ups found during this review, NOT fixed here

Recorded so they are not rediscovered, per CLAUDE.md's "surface unknowns to the operator". **Each is unscheduled and needs operator triage before it becomes a task** — none corresponds to any of T001–T052, and items 7–10 in particular are new-scope proposals rather than defects in T001's artifacts. Listing them here is not agreement to do them. None blocks FR-011a.

> **Triage, 2026-08-16.** The operator triaged this table. Outcomes, so the next reader does not re-triage it:
>
> | # | Outcome |
> |---|---|
> | 1 | **Accepted.** `D-nn` namespace sweep plus one mechanical guard, landed once, in its own PR. Not bundled — an earlier draft of the Phase 0 plan put the guard in two concurrent branches at once. |
> | 2 | **Accepted.** `docs/ideas/` created; it is the escape valve `ml-engineer.md` and `plan.md` R-06 already cite. |
> | 3 | **Accepted, fixed here.** `infra/k3s/` given an owner (T004) rather than deleted — `D-000/D-02b` requires a version-controlled `config-v3.toml.tmpl`, so the directory is real; only its task assignment was missing. |
> | 4 | **Accepted, fixed here.** `tasks.md` is the plan of record (CLAUDE.md), so `plan.md` was the wrong document: promotion is a `dags/retrain.py` step, not a third Argo workflow. |
> | 5 | **Accepted, fixed here.** `plan.md` R-05 now cites T002/T003. |
> | 6 | **Accepted, fixed here.** Package-relative paths in `tasks.md` expanded to repo-relative. |
> | 7 | **Accepted, deferred to its own PR**, scoped to `github-actions` + `docker` only. A Dependabot bump touching `pyproject.toml` alone reddens `test_versions_env_matches_pyproject_dev_extra` by design, so Python pins stay `pin-a-tool`'s job. |
> | 8 | **Accepted.** PR template. |
> | 9 | **Accepted, operator-applied.** The ruleset is committed as a reviewable artifact; the operator applies it — the same Principle I split that governs the cluster. |
> | 10 | **Accepted.** `py.typed`. |
>
> Items 7–10 were correctly flagged above as new scope. They are **not** absorbed into T001a, which is closed; each carries its own task ID or an explicit recorded ruling that it needs none.

| # | Finding |
|---|---|
| 1 | **`D-nn` namespace collisions.** `plan.md:12` declares that a bare `D-nn` means plan.md's own log and cross-refs are written `D-000/D-nn` — then violates it at `plan.md:37`. Also bare in `.env.example:22` (where `D-03` collides with a *different* real decision), `tasks.md:38` and `:88` (which resolve to **different namespaces on different lines of one file**), `.pre-commit-config.yaml:46`, `tests/unit/test_repo_structure.py:80,85`, `ci/gitleaks.toml`, `versions.md`. |
| 2 | **`docs/ideas/` does not exist** but is the documented escape valve in `ml-engineer.md` and `plan.md` R-06. Agents told to write a note there today cannot. |
| 3 | **`infra/k3s/` is an orphan.** Declared in plan.md:44; no task in T001–T052 populates it. `test_repo_structure.py` will assert its `.gitkeep` forever. |
| 4 | **`workflows/promote.yaml` is promised and never tasked.** plan.md:46 names three Argo workflows; tasks.md delivers two and folds promotion into `dags/retrain.py`. One document is wrong. |
| 5 | **plan.md R-05 cites the wrong task.** It says host CUDA validation happens at T004 (the k3s install runbook); it is T002/T003. |
| 6 | **`tasks.md` mixes path conventions.** T016 writes `src/orbital_drift/ingest/stac_client.py`; T017/T019/T024–T028/T035/T042 write package-relative (`data/labels.py`). A reader cannot tell whether `data/` means the package subdir or a top-level directory. |
| 7 | **No `dependabot.yml`.** Every version is hard-pinned with no automated update path, so a CVE in a pinned tool or a SHA-pinned action sits unnoticed. A repo this committed to pinning needs the counterweight. |
| 8 | **No PR template**, though CLAUDE.md requires PR descriptions to name task IDs and review outcomes and to carry cross-agent handoff notes. |
| 9 | **No committed branch-protection artifact.** FR-011's "all green required to merge" is a social convention, not an enforced gate. |
| 10 | **No `py.typed`** in a package configured for `mypy strict`. Inert while `dependencies = []`; a future consumer would get "missing library stubs or py.typed marker". |

## Verified correct — no action

The gitleaks pre-commit hook carries `stages: [pre-commit]` and is therefore dropped by `stage_hooks`'s `--hook-stage manual`, so it runs in no CI job. This looks like a Principle VII hole and is not one: the hook runs `gitleaks --staged`, which scans the git *index*, and in CI nothing is staged — running it there would produce exactly the vacuous pass `stage_hooks` has two other branches dedicated to refusing. The real scanning is `stage_gitleaks` (working tree **and** full history), and the hook entry itself is covered by two positive controls that drive the pinned image directly.

## Provenance note: the review passes and skills in D-11/D-12

`spec-guardian`'s review of D-11/D-12 correctly flagged that no decision-doc
entry independently recorded the authority for this file's later work, and
that an agent's relayed claim of operator intent is not itself
authorization — recorded here per that finding, and per
`new-decision-doc/SKILL.md`'s own rule that non-obvious process decisions
get recorded rather than living only in session context.

Sequence of events, 2026-08-16: after the FR-011a coverage gate (D-01–D-10)
was drafted, the operator asked, in their own words, for a senior-level
gap analysis and objective peer review of the branch — code hygiene, tech
debt, coverage-gate enforcement, missed edge cases, lint/type/test fixes,
backwards-compatible and reusable code, no hardcoded values, best testing
practices with a full test suite, logging/debugging where relevant, and
explicitly to "identify areas for implementing skills/agents from reusable
actions" and "ensure everything is wired/configured". That request is the
authority for: running the `/code-review` skill and a second independent
`peer-reviewer` pass against the merged coverage-gate work (which surfaced
the D-11/D-12 bugs), and for authoring `.claude/skills/pin-a-tool/SKILL.md`,
`.claude/skills/new-decision-doc/SKILL.md`, `.claude/skills/run-review/SKILL.md`,
and the positive-control-discipline additions to `spec-guardian.md` and
`peer-reviewer.md` — built rather than only recommended, per the explicit
"wired/configured" instruction. No new FR was needed for this work: it is
process/tooling on the agent-orchestration layer, not a new CI gate or
product requirement, and `tasks.md` does not reference `.claude/agents/` or
`.claude/skills/` at all — that layer has always sat outside the T001–T052
task-ID scheme, so this is consistent with existing precedent, not a
departure from it (confirmed by `spec-guardian`'s own review, which checked
this specifically).
