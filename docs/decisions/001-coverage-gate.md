# D-001: The `coverage` CI gate

**Status:** decided 2026-08-15.
**Provenance, stated precisely:** the operator asked for "full test suite with coverage". That request is the authority for **FR-011a**; everything below is an agent research conclusion with measurements, not an operator decision. The threshold *number* is an agent proposal and is the one item here most in need of operator confirmation.
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

**Open for operator confirmation.** Lowering it to make a red build green is the failure mode to watch; the correct response to a breach is tests, or an explicit, reviewed change to this pin with the reason recorded here.

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

---

## Follow-ups found during this review, NOT fixed here

Recorded so they are not rediscovered. Each is a small, separable PR; none blocks FR-011a.

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
