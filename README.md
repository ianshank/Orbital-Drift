# Orbital-Drift

Self-retraining Sentinel-2 change-detection pipeline on k3s.

Spec, plan and task list live in `specs/001-orbital-drift-ct/`. The project
constitution is `.specify/memory/constitution.md` and supersedes everything
else, including this file.

This README covers **developer bootstrap only** — how to get the gates running
on a checkout. Operating the cluster is out of scope here; those procedures live
in `docs/runbooks/`.

## Requirements

| Thing | Version | Why |
|---|---|---|
| Python | 3.12 | `pyproject.toml` sets `requires-python = ">=3.12,<3.13"` |
| Docker | any recent daemon | gitleaks and shellcheck run as pinned containers, so no Go or Haskell toolchain is needed |
| Git | any recent version | the gitleaks history scan and the pre-commit hooks both drive git |

Every tool version the gates use is pinned in [`ci/versions.env`](ci/versions.env),
which is the single source of truth. `pyproject.toml` and
`.pre-commit-config.yaml` mirror it, and `tests/unit/test_version_pins.py` fails
if the three ever disagree.

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
```

`lint`, `typecheck`, `unit`, `contract`, `smoke` and `gitleaks` are FR-011's six
gates. The other two are not, and each has its own requirement:

* `coverage` implements **FR-011a** — a minimum measured statement coverage of
  `src/orbital_drift`, with the threshold pinned in `ci/versions.env` as
  `COVERAGE_MIN_PERCENT` rather than written into the script (Constitution III).
  It needs no special case for "there is no product code yet": coverage reports
  100% for zero measurable statements, so the gate clears today and arms itself
  the moment the first executable line lands. See
  `docs/decisions/001-coverage-gate.md`.
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
| `coverage` | Python 3.12 + the pinned `pytest`, `pytest-cov` and `coverage`, **plus Docker and git** — it re-runs `tests/unit`, so it inherits that suite's real dependencies. Both are asserted before pytest starts: a control that skipped instead of running would inflate the number this stage reports, which is a fail-open, not a missing test |
| `gitleaks` | **Docker and git only** |
| `hooks` | Python 3.12 + the pinned `pre-commit`, Docker, git |

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
