# Orbital-Drift

Automated Self-Retraining Sentinel-2 Land-Cover Change Detection Pipeline with Live Dual-GPU Continuous Training & Canary Deployment.

## System Overview & Hardware Topology

Orbital-Drift runs an automated, self-healing continuous training loop driven by multi-spectral satellite imagery and statistical drift detection:

1. **Ingest Plane**: Planetary Computer STAC client with rate-limited retries, Sentinel-2 SCL cloud masking, and windowed tile store I/O.
2. **Lineage & Versioning Plane**: lakeFS isolated branch commits and immutable lineage hashes `{lakefs_commit_id, git_sha, config_hash}` logged to MLflow Model Registry.
3. **Statistical Drift Sensor**: Population Stability Index (PSI with 10 quantile bins) and Kolmogorov-Smirnov 2-sample tests with queue-depth-1 retrain triggering.
4. **Model Plane (Dual-GPU Partitioning)**:
   - **Primary Trainer (GPU 0)**: NVIDIA GeForce RTX 5060 Ti 16GB VRAM running multi-spectral `SimpleUNet` spatial segmentation with PyTorch AMP fp16 autocast and gradient accumulation.
   - **Canary Inference Server (GPU 1)**: NVIDIA GeForce RTX 5060 8GB VRAM running FastAPI serving container (memory ceiling 4GB) with configurable traffic routing and Prometheus telemetry.
5. **Continuous Training Loop**: Automated transition through Staging shadow evaluation, baseline beat gates (IoU/F1 threshold checks), canary deployment, and sub-10-minute automated rollback drills.
6. **Hexagonal Architecture & Observability (Phase 0-R)**:
   - **Domain Layer (`src/orbital_drift/domain/`)**: Pure primitives (`geometry`, `temporal`, `scene`, `lineage`, `errors`) with zero 3rd-party dependencies and canonical JSON SHA-256 provenance hashing.
   - **Ports Layer (`src/orbital_drift/ports/`)**: Abstract protocols and deterministic stdlib fakes (`catalog`, `compute`, `dataversion`, `registry`, `tiles`) enabling CPU-only fast feedback and isolation.
   - **Evaluation Layer (`src/orbital_drift/eval/`)**: Standardized statistical evaluators (`bootstrap`, `calibration`, `ranking`, `spatial`, `superiority`) satisfying Constitution II.
   - **Observability Plane (`src/orbital_drift/observability/`)**: Structured logging with recursive credential redaction (`logging.py`), execution context binding (`context.py`), and durable 4-state `DecisionRecord` gate ledger (`records.py`).
   - **Quality & Boundary Enforcement (`src/orbital_drift/quality/`, `.importlinter`)**: AST scanner for hardcoded literals (Constitution III) and formal architectural layer boundary contracts.

Spec, plan and task list live in `specs/001-orbital-drift-ct/`. The project constitution is `.specify/memory/constitution.md` and supersedes everything else, including this file.

## Bootstrap

One documented command path (Constitution Principle IV), from the repo root:

```sh
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pre-commit install
```

`pip install -e ".[dev]"` installs ruff, mypy, pytest and pre-commit at exactly
the pinned versions. `pre-commit install` wires the hooks into `git commit`; CI
runs the same hook set regardless, so skipping it only costs you the fast local
feedback.

`.github/workflows/ci.yml` runs that same `pip install -e ".[dev]"` — not a
list of package names — so this command path is exercised on every push. If the
build backend, the `[project]` table or `requires-python` breaks, CI says so
rather than letting it surface first on node A.

## Running the gates

`ci/checks.sh` is the canonical definition of every gate. CI
(`.github/workflows/ci.yml`) is a thin caller that invokes it, so this command
is what CI runs:

```sh
sh ci/checks.sh all
```

Individual stages:

```sh
sh ci/checks.sh lint        # ruff check + ruff format --check
sh ci/checks.sh typecheck   # mypy strict
sh ci/checks.sh unit        # pytest tests/unit
sh ci/checks.sh contract    # pytest tests/contract
sh ci/checks.sh smoke       # pytest tests/smoke
sh ci/checks.sh coverage    # pytest tests --cov=src/orbital_drift
sh ci/checks.sh gitleaks    # secret scan: working tree AND full git history
sh ci/checks.sh hooks       # pre-commit run --all-files
sh ci/checks.sh dead        # vulture dead-code scan (adopt-governance-kit)
sh ci/checks.sh audit       # pip-audit dependency vulnerability scan
sh ci/checks.sh specs       # OpenSpec structural validation
sh ci/checks.sh traceability  # requirement-traceability matrix lint
sh ci/checks.sh projections   # generated planning/ byte-drift check
sh ci/checks.sh governance    # tests/governance/: guard corpus, meta-tests
sh ci/checks.sh deps          # dependency contract: pyproject.toml vs src/ imports
sh ci/checks.sh architecture  # tests/architecture/: import-linter boundary contract
```

`lint`, `typecheck`, `unit`, `contract`, `smoke` and `gitleaks` are FR-011's six
gates. The other two are not, and each has its own requirement:

* `coverage` implements **FR-011a** — a minimum measured statement **and
  branch** coverage of `src/orbital_drift`, as one combined rate, with the
  threshold pinned in `ci/versions.env` as `COVERAGE_MIN_PERCENT` rather than
  written into the script (Constitution III). Branch measurement
  (`--cov-branch`) was added under RB-008 part 3 without moving that pin — the
  quantity it compares got harder, the number did not change; see D-14 in
  `docs/decisions/001-coverage-gate.md`.
  It needs no special case for "there is no product code yet": coverage reports
  100% for zero measurable statements, so the gate clears today and arms itself
  the moment the first executable line lands. It measures **`src/orbital_drift`
  only** — `dags/` is outside the package and is not counted, so once T020 lands
  the ingest/drift/retrain DAGs will run under measurement without contributing
  to the number. That is a dated deferral, not an oversight: see D-09 in
  `docs/decisions/001-coverage-gate.md`. The stage also unsets `PYTEST_ADDOPTS`
  (and says so) — `--no-cov` set there would otherwise turn the gate green over a
  run that measured nothing. Once the global floor passes, the same stage also
  runs `orbital_drift.covcheck` over the `coverage.json` that run produced — a
  per-file floor (charter C-6, DEC-004) that catches a single untested module
  hiding behind a healthy aggregate, which a global average alone cannot. That
  second bar is pinned the same way as the first: `COVERAGE_PER_FILE_MIN_PERCENT`
  in `ci/versions.env`, passed to covcheck as `--floor` (RB-008 F4). The
  module's own `PER_FILE_FLOOR` constant is only the fallback for a hand-run,
  and a test holds it equal to the pin.
* `hooks` exists so the pre-commit config is enforced in CI rather than only on
  machines where somebody remembered to install it.

`coverage` runs all three suites in **one** pytest process, because the CI matrix
runs each stage as a separate job and per-job coverage cannot be combined without
putting orchestration into a workflow file whose own header forbids gate logic.
The consequence is that `sh ci/checks.sh all` runs `tests/unit` twice — once bare
and once under measurement — which roughly doubles the dominant local term. That
is accepted rather than optimised away; every cheaper arrangement weakens a gate,
and D-06 of the decision doc records which one each of them breaks. In CI the two
runs are parallel matrix jobs, so wall-clock is largely unchanged.

The remaining `dead`, `audit`, `specs`, `traceability`, `projections` and
`governance` stages are not part of FR-011's six either; they extend the same
contract under the adopt-governance-kit change (see
`charter/PROJECT-CHARTER.md` and `openspec/changes/adopt-governance-kit/`).
Two more, `deps` and `architecture`, extend it again under RB-010 Parts 7-8
(`docs/decision-log.md`) rather than under adopt-governance-kit itself. Run
`sh ci/checks.sh` with an unrecognized stage name to print the current,
authoritative stage list — it is generated from `STAGE_LABELS` inside the
script, never hand-copied, so this README cannot silently disagree with what
actually dispatches. A thin `Makefile` fronts every stage (`make lint` =
`sh ci/checks.sh lint`, `make pre-pr` = `sh ci/checks.sh all`) for boxes with
GNU make — on Windows, call `sh ci/checks.sh <stage>` directly (GNU make is not
installed on the authoring machine, and every target delegates, so nothing is
lost by skipping it).

If the `python` on your `PATH` is not 3.12, point the script at the right
interpreter instead of changing your `PATH`:

```sh
PYTHON=/path/to/python3.12 sh ci/checks.sh all
```

### What each stage actually needs

| Stage | Prerequisites |
|---|---|
| `lint` | Python 3.12 + the pinned `ruff` |
| `typecheck` | Python 3.12 + the pinned `mypy` |
| `unit` / `contract` / `smoke` | Python 3.12 + the pinned `pytest` (`unit` additionally drives Docker, git and `pre-commit`: its positive-control tests exercise the pinned gitleaks container over real git repositories it builds itself, plus the merged pre-commit hook set) |
| `coverage` | Python 3.12 + the pinned `pytest`, `pytest-cov` and `coverage`, **plus Docker and git** — it re-runs `tests/unit`, so it inherits that suite's real dependencies. Both are asserted before pytest starts: a control that skipped instead of running would inflate the number this stage reports, which is a fail-open, not a missing test. After the global floor passes it also runs `orbital_drift.covcheck` over the `coverage.json` the same pytest invocation produced — no extra pin, since covcheck is pure stdlib |
| `gitleaks` | **Docker and git only** |
| `hooks` | Python 3.12 + the pinned `pre-commit`, Docker, git |
| `dead` | Python 3.12 + the pinned `vulture` |
| `audit` | Python 3.12 + the pinned `pip-audit` (network to pypi.org; behind a TLS-intercepting proxy/AV set `REQUESTS_CA_BUNDLE` to a bundle containing its root) |
| `specs` | POSIX sh + awk only |
| `traceability` | Python 3.12 + the pinned `pytest` (shells out to `--collect-only`) |
| `projections` | Python 3.12, no external tool |
| `governance` | Python 3.12 + the pinned `pytest`, git (the meta-tests enumerate tracked paths) |

Before a gate runs, a preflight asserts the interpreter and the pins **that
stage executes** — not the whole toolchain. That scoping is deliberate: the
secrets gate used to assert ruff, mypy, pytest and pre-commit while running none
of them, so a PyPI outage or a stray `mypy` on `PATH` reddened a job named
`gitleaks`, and a fresh clone with Docker but no Python could not run
`sh ci/checks.sh gitleaks` at all. A stage header that names a version is a
claim the script has already checked, not a label.

There is no way to switch the preflight off. `SKIP=` and
`PRE_COMMIT_ALLOW_NO_CONFIG` are unset inside the script. The pin comparison
itself carries no memoised "already checked" flag at all: it re-probes and
re-compares the actual installed tool against `ci/versions.env` on every
single stage invocation of a `sh ci/checks.sh all` run, not once at the start
with later stages trusting a cached result. Earlier revisions did memoise it,
and several review rounds each found one more way an environment variable, or
a caching idiom internal to the script, could flip that memo or reintroduce
its effect without being caught by the previous round's detector — proof that
"enumerate every way this could be bypassed" does not converge, no matter the
mechanism.

This claim is enforced by two layers, deliberately not one, because neither
alone was enough:

* **Primary — behavioural, black-box.** `tests/unit/test_checks_sh_behaviour.py`
  runs the real script with `git`/`docker`/`python` stubbed, and — for the
  interpreter check and for a pinned-tool check — feeds the SAME probe a
  correct answer on one call and a wrong answer on a later call, within a
  single `sh ci/checks.sh all` run. It asserts the later, wrong answer is what
  the stage actually acts on. This does not read `ci/checks.sh`'s source at
  all, so it cannot be defeated by any shape a bypass might take — a renamed
  flag, a lazy-init cache variable, or a mechanism nobody has invented yet —
  the way a source-text scan can. This is what actually PROVES the property.
* **Secondary — source-level, defense-in-depth.** `tests/unit/test_ci_contract.py`
  also bans several known-dangerous SHAPES in the source (`eval`; bare
  `env`/`printenv`/`export`/`set`; an early `return 0` or bare `return` in the
  preflight functions; a handful of environment-variable-reference patterns).
  These are fast, cheap, and still catch real mistakes on sight, but they are
  explicitly NOT exhaustive — an earlier version of this file overclaimed that
  one of them "enumerates every environment variable `ci/checks.sh` reads and
  fails on any addition", which a later review round showed to be false by
  direct counterexample (a caching variable assigned and read in the same
  function evades that specific detector's classification). That test's own
  docstring now says so; this paragraph is corrected to match. Treat every
  test in this file as "catches common mistakes early", never as the proof
  that a bypass is impossible.

To see the gitleaks working-tree config the script generates:

```sh
DEBUG=1 sh ci/checks.sh gitleaks
```

### A failing run can leave your working tree modified

`hooks` runs pre-commit, and four of those hooks **rewrite files**: `ruff-check
--fix`, `end-of-file-fixer`, `trailing-whitespace` and `mixed-line-ending
--fix=lf`. So:

* a red `sh ci/checks.sh all` may have edited files on its way to failing;
* running it again can then go green, because the second run sees the rewritten
  tree — the failure "fixed itself" and there is no record of what changed.

Always `git status` / `git diff` after a failing run, and commit or discard the
rewrites deliberately. `hooks` is ordered last in `all` precisely so these
rewrites cannot influence a gate that already ran, but they still land on disk.

CI does not have this problem in a useful way — the runner is discarded — which
is exactly why it is called out here: it is a local-only footgun.

### When gitleaks flags something that is not a secret

It will happen, most likely on the first runbook in `docs/runbooks/` that spells
out a `kubectl create secret ... --from-literal=...` line. The only sanctioned
response is a `stopwords` entry on the `[[rules.allowlists]]` of the rule that
fired. Never `--no-verify`, never `SKIP=gitleaks`, and never a `paths` key on a
global `[[allowlists]]`. The full procedure, with the reasoning and the exact
TOML to write, is at the top of [`ci/gitleaks.toml`](ci/gitleaks.toml).

## Governance

The repo runs under the adopt-governance-kit change (Constitution v1.1.0;
`openspec/changes/adopt-governance-kit/` holds the proposal, design decisions
D1–D14, spec deltas, and task record):

- **Gates.** `ci/checks.sh` is the canonical runner for all seventeen stages;
  the `Makefile` is a thin front-end (`make pre-pr` = `sh ci/checks.sh all`),
  and on a box without GNU make you call checks.sh directly — Linux CI is
  authoritative. The zero-skip conftest escalates any parked skip; the
  coverage floors (global + per-file, both inside the `coverage` stage),
  vulture, pip-audit, spec validation, traceability lint, and projection
  drift checks are all stages, and `tests/governance/` — the guard regression
  corpus and the meta-tests that watch the process — runs under its own
  `governance` stage rather than being attributed to a red `coverage` job
  whose bare `pytest tests` invocation happens to collect it too. Two more
  (RB-010 Parts 7-8): `deps` reconciles `pyproject.toml`'s declared
  dependencies against the imports `src/orbital_drift` actually makes
  (`orbital_drift.quality.dep_contract`), and `architecture` runs
  `tests/architecture/` — the import-linter `.importlinter` boundary contract,
  independently re-derived via AST.
- **Control plane.** `charter/PROJECT-CHARTER.md` (constraints C-1…C-6,
  subordinate to the constitution) + `docs/decision-log.md` (the mechanical
  gate ledger — gates presence-check IDs there; prose unlocks nothing) +
  three skills: `orbital-drift-governance` (the gate table, staleness-checked
  against the log), `run-the-gate` (the machine-specific invocation — CA
  bundle, interpreter, Docker preflight), and `log-decision` (the two-file
  coupling the freshness check enforces).
- **Guards.** `.claude/settings.json` deny-list + PreToolUse guard
  (`scripts/pretooluse_guard.sh`, classification logic in
  `src/orbital_drift/guard.py`) enforce Constitution I at the harness layer;
  the native pre-push hook (`bash scripts/install_hooks.sh` on every fresh
  clone) enforces the remote allowlist (`.claude/allowed-remotes.txt`) via
  `scripts/pre_push_scan.sh`, the authoritative C-5 gate. Probe either
  without GNU make: `bash scripts/guard_probe.sh '<command>'`.
- **Planning.** `planning/roadmap.md` and `planning/jira-import.csv` are
  generated projections of `src/orbital_drift/planning/roadmap_data.py` —
  never hand-edit; regenerate with
  `python -m orbital_drift.projections --write`.
- **Architecture & history.** `docs/architecture/ARCHITECTURE.md` (C4 context
  + container, built-vs-planned) and `CHANGELOG.md` (chronological, cites the
  commit for every claim) — both updated in the same PR that changes the
  shape they describe.

## Current status

Snapshot as of 2026-09-01 (RB-010, `docs/decision-log.md`) — **the live
source is `specs/001-orbital-drift-ct/tasks.md`; if this disagrees with it,
the tasks file wins.** Phase 0 of 6 (`specs/001-orbital-drift-ct/plan.md`);
10 of the 55 task checkboxes are complete: T001, T001a, T001b, T002, T004,
T004a, T007, T008, T009, T010 (T001b and T004a checked per RB-007).

That count describes governance-gated authoring only. Separately — and
**without** a `G-1` entry ever having been logged — PR #16 (2026-08-23) and
PR #17 (2026-08-24, "Phase 0-R") authored most of the Phase 1–4 application
code (T013–T045: ingest/data/drift/train/registry/serve) plus a new
hexagonal domain/ports/eval/observability/quality layer, with no
spec-guardian or adversarial-reviewer review and no RB batch authorization
before merge. RB-010 (`docs/decision-log.md`) is the governance
reconciliation: a six-lens SDLC review found significant gaps — a
NON-NEGOTIABLE Constitution II violation (`eval/bootstrap.py`,
`eval/superiority.py` hand-roll statistics), a non-building Dockerfile,
unwired `config.py`, a `drift/trigger.py` stuck-breaker, an unauthenticated
`serve/app.py` with no startup wiring, two phantom CI gates, and 0 of 5
hexagonal ports having a real adapter, among others — and authorized a 14-part
remediation program. `tasks.md`'s existing `AUTHORED-PROVISIONAL` status
(previously used only for T006) now applies retroactively to T013–T052
pending spec-guardian + adversarial-reviewer review; see the per-task status
annotations in `specs/001-orbital-drift-ct/tasks.md`'s Phase 1–4 section for
the evidence-based detail per task.

**Next: T003 `[HUMAN]`** — the operator executes
`docs/runbooks/00-host-prep.md` on node A and logs `G-1` in
`docs/decision-log.md`; per RB-007, T006 authoring is deferred until that
`G-1` entry exists. T003 is the next physical cluster-bring-up action and is
unrelated to the RB-010 remediation program above. See `CHANGELOG.md` for
what has shipped and `docs/architecture/ARCHITECTURE.md` for what is built
versus planned versus actually integrated.

## Local configuration

```sh
cp .env.example .env
```

`.env` is gitignored and stays that way. The gitleaks working-tree scan derives
its exclusions from `git ls-files`, so a local `.env` does not redden the gate —
but committing or staging one does, deliberately and loudly.

Never commit credentials. The repository is public; `.gitignore` is the primary
control and gitleaks is the backstop (Constitution VII).

### Clones the secret scan refuses

`sh ci/checks.sh gitleaks` will not run on a **shallow** (`--depth 1`) or
**partial** (`--filter=blob:none`) clone. Both make the full-history scan read
almost nothing while printing the same banner as a clean pass; the blobless case
is not detectable via `git rev-parse --is-shallow-repository`, which reports
`false` for it. Use a full clone, or `git fetch --unshallow`. In CI,
`actions/checkout` must keep `fetch-depth: 0` and must not set `filter:`.
