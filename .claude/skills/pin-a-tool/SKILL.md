---
name: pin-a-tool
description: Add or bump a pinned developer-tool version (ruff, mypy, pytest, a new pytest plugin, a container image, etc.) across every file that must agree, and leave the mechanical lockstep checks self-extending rather than hand-maintained. Use whenever a new tool is added to the CI toolchain, or an existing pin needs bumping.
---

# Pin a tool

Orbital-Drift's Constitution IV requires every tool version pinned with
provenance, and `ci/versions.env`'s own header states the rule this skill
enforces: pins are "DERIVED, not announced" — adding one and forgetting to
wire it up must be a hard error, never a silent gap. Every phase (T007-T042
alone add SeaweedFS/lakeFS/MLflow/Airflow/Argo/torch/torchgeo clients) will
need this again.

A tool pin touches up to six places. Miss one and you get "green locally, red
in CI" — the exact failure mode `ci/versions.env`'s header names.

## Before you start

Read `ci/versions.env`'s header comment and `docs/decisions/001-coverage-gate.md`
D-04/D-05 for the reasoning this skill is built on (why `coverage` is pinned
separately from `pytest-cov` even though the latter pulls it transitively —
Principle IV has no transitive exemption).

Decide up front:
- **Is this a Python distribution** (pip-installable, has a `[dev]`-extra
  home) or a **pinned container image** (digest-pinned, runs via `docker run`,
  like gitleaks/shellcheck)? The two paths below diverge at step 2.
- **Resolve the real version with provenance BEFORE writing anything.**
  `python -m pip index versions <pkg>` for PyPI distributions; check the
  registry/release page for container images. A pin with no provenance URL is
  a `spec-guardian` BLOCK (`ci/versions.env`'s own rule, mechanically enforced
  by `test_every_pin_in_versions_env_carries_a_provenance_url`).
- **Check transitive dependencies.** Does the tool you're pinning pull in
  another unpinned package as a floor (`>=`)? If so, pin that one too,
  separately — see D-04's `coverage`-vs-`pytest-cov` precedent.

## For a Python distribution (the common case)

1. **`ci/versions.env`** — add `<NAME>_VERSION=<version>` with a comment block
   immediately above it containing a provenance URL
   (`pypi.org/project/<pkg>/<version>`) and, if non-obvious, WHY this version
   specifically (a compatibility floor with another pin, a behaviour change
   you're relying on — see the `PYTEST_COV_VERSION` comment for the pattern).

2. **`pyproject.toml`** — add `"<pkg>==<version>"` to `[project.optional-dependencies].dev`.
   Do this step even if you think the lockstep test will catch a miss — it
   will, but only after you've told it the pin exists.

3. **`ci/checks.sh`**, in order:
   - `tool_version()` — add a probe arm. Prefer
     `importlib.metadata.version("<dist>")` over `import <pkg>; print(<pkg>.__version__)`:
     it's canonical (works even if the package exposes no `__version__`), and
     — load-bearing — its argv contains no substring that collides with an
     existing `case` arm in `tests/unit/shell_harness.py`'s `PYTHON_STUB`.
     Check this explicitly: does your probe's literal argv contain `import
     pytest`, `pytest-cov`, or any other existing arm's match pattern as a
     substring? `case` patterns are top-down substring globs — a collision
     silently answers your probe with the WRONG tool's version. (This bit
     the coverage-gate work directly: see `ci/checks.sh`'s comment block above
     `PYTHON_STUB` for the concrete case.)
   - `stage_python_pins()` — add the pin name to whichever stage(s) actually
     invoke the tool, AND to the `all)` arm's union. Missing the `all)` arm
     specifically breaks `test_all_run_prints_the_preflight_banner_once_not_once_per_stage`
     — a stage whose preflight meets an unlogged tool for the first time
     prints a second banner.
   - If this pin backs a NEW stage (not just a new dependency of an existing
     one), see the "New CI stage" section below.

4. **`tests/unit/test_version_pins.py`** — nothing to do if `_dev_extra_pins()`
   is still deriving from `ci/versions.env` (it should be, as of the fix
   documented in that file's own history — verify by grepping for a hardcoded
   parametrize list; if you find one, that itself is the bug this skill exists
   to prevent recurring, and fixing it is worth doing before adding your pin).

5. **Run the lockstep proof, not just the tests**: temporarily add a
   throwaway `FAKEPIN_VERSION=1.2.3` to `ci/versions.env` and confirm
   `pytest tests/unit/test_version_pins.py` fails until you add it to `[dev]`
   too. Remove the throwaway pin. This is the difference between "the test
   exists" and "the test actually derives" — see `de24ee7`'s commit message
   in this repo's history for why that distinction mattered here.

## For a pinned container image

Same `ci/versions.env` provenance discipline, but pin by **digest**, not tag
— a tag is mutable; `ghcr.io/<image>:<tag>@sha256:<digest>` is not. Add
`<NAME>_VERSION`, `<NAME>_DIGEST`, and `<NAME>_IMAGE` (the combined form),
following `GITLEAKS_VERSION`/`GITLEAKS_DIGEST`/`GITLEAKS_IMAGE` exactly. Add
the tool name to `PREFLIGHT_EXEMPT_PINS` in `ci/checks.sh` with a one-line
reason (it runs as a container, not a Python distribution — `tool_version()`
has no probe for it) and add a `require_pinned_image`-style assertion in
whichever stage invokes it, following `require_gitleaks_image`/
`require_shellcheck_image`.

## New CI stage (optional second half)

If the pin backs a brand-new `stage_*`, not just a dependency of an existing
one, this is the checklist that's currently only encoded as scattered
comments in `ci/checks.sh`:

- `STAGE_LABELS` — add the label.
- Dispatch `case` at the bottom of `ci/checks.sh` — add the arm, and update
  the `usage:` string in the same edit (these drifted apart once already —
  see `spec-guardian`'s finding on the coverage-gate PR).
- `stage_python_pins()` — the stage's own pin set, per above.
- `stage_all()` — insert in the right position. Ask: does this stage need to
  run before or after the pytest-based stages (contiguous, for readability),
  and must anything that rewrites the working tree (`stage_hooks`) still run
  last?
- If the stage runs anything that needs Docker or git for reasons beyond the
  usual preflight, call `docker_or_fail`/`git_or_fail` with a reason string
  DISTINCT from every other stage's — `tests/unit/test_ci_contract.py`'s
  `_OR_FAIL_STAGES` tuple and its distinctness tests will force this; add
  your stage's name to that tuple (it's derived-and-cross-checked, not
  hand-maintained, as of the coverage-gate work — keep it that way).
- `tests/unit/test_ci_contract.py`'s `_STAGE_TEST_DIRS` — add an entry if the
  stage runs pytest against specific suite directories, so
  `test_no_stage_silently_needs_docker_or_git_without_asserting_it` actually
  sweeps it rather than passing vacuously.
- `.github/workflows/ci.yml`'s matrix — add the stage. Forced by
  `test_workflow_matrix_covers_every_stage_label`, but do it anyway rather
  than waiting to be told.
- README.md's stage list and "what each stage needs" table.
- **Is this stage required by an FR, or does it need one added?** The
  `hooks` stage is NOT a transferable precedent for "an extra stage is fine"
  — it was ruled conformant because Constitution VII already required it.
  `spec-guardian` will (and should) BLOCK an unrequirement'd gate; see
  `docs/decisions/001-coverage-gate.md` D-01 for the reasoning and
  `specs/001-orbital-drift-ct/spec.md`'s FR-011a for the precedent of adding
  an FR explicitly rather than inferring one.
- A **decision doc** recording why the threshold/config for this gate is
  what it is — see the `new-decision-doc` skill.
- Both a stub-based behavioural test of the stage's command line AND a
  **positive control** against the real tool proving the flags do something
  — see `peer-reviewer.md`'s charter and `tests/unit/test_coverage_positive_control.py`
  for the pattern. `spec-guardian` and `peer-reviewer` both now check for
  this explicitly.

## Verify before calling it done

```sh
export PYTHON=/path/to/venv/bin/python
sh ci/checks.sh lint && sh ci/checks.sh typecheck
python -m pytest tests/unit -q
```

If `unit`/`hooks`/`gitleaks` need Docker and it's unavailable in your
environment, say so explicitly rather than reporting them green — CI is
where those actually run.
