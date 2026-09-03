# Full-Suite QA Triage — Baseline, RCA, and Proposed Fixes

**Status:** RECORDED — triage/RCA report only. No `src/` change is made by this document or its branch. Each proposed fix below requires separate review approval before it lands, then ships as its own task/PR with a failing regression test first (TDD), per CLAUDE.md's one-task-per-branch rule.
**Audience:** whichever operator/agent reviews this triage and authorizes the follow-up fix PRs.
**Authorized by:** `docs/decision-log.md` RB-011.
**Measured at:** `main` @ `84d45ed` (fast-forwarded from `63413f8`; 21 commits), branch `qa/full-suite-triage` cut from the same commit with zero diff — so every result below applies unchanged to both.
**Host:** Windows, dual-GPU (NVIDIA RTX 5060 Ti 16 GB + RTX 5060 8 GB, driver 596.49, CUDA runtime ceiling 13.2), Docker Desktop (server 29.4.2), Git for Windows (MSYS `sh.exe` used for all `ci/checks.sh` invocations — WSL's `bash`/`sh` were deliberately NOT used, since `ci/checks.sh` is written against MSYS-specific path-conversion behavior).
**Environment note (not a defect, but load-bearing context):** the pre-existing `.venv` carries `torch==2.12.0.dev20260408+cu128` (a CUDA-enabled nightly build with a verified real CUDA kernel launch) rather than the pinned `torch==2.13.0`. A `pip install -e ".[dev]" --dry-run` showed the pinned version would resolve to a wheel with **no `nvidia-cu*` runtime dependencies** — almost certainly a CPU-only build — which would have silently disabled every GPU-gated test via vacuous `capability-guard:` skips. To keep the GPU tiers meaningful, the full `[dev]` install was **not** run; only the three genuinely-missing packages (`httpx2==2.12.0`, `httpcore2==2.12.0`, `truststore==0.10.4`) plus the local editable package were installed, leaving `torch`/`torchvision` untouched. This choice is why Finding 1 below carries an environment caveat.

## Summary

| Stage | Result | Notes |
|---|---|---|
| specs | PASS | no structural issues |
| projections | PASS | no byte-drift |
| lint | PASS | ruff clean, 163 files formatted |
| typecheck | **FAIL** | 2 errors, see Finding 1 (environment-caveated) |
| dead | PASS | vulture clean |
| audit | PASS | no known vulnerabilities (benign PyPI-resolution skips for torch/torchvision/orbital-drift, expected given the environment note above) |
| traceability | PASS | no problems |
| governance | PASS | 163 passed |
| contract | PASS | 37 passed, 1 benign warning |
| smoke | PASS | declared-empty, tolerated |
| unit | **FAIL** | 943 passed, 2 failed (Finding 2), 2 legitimate capability-guard skips |
| gitleaks | PASS | clean |
| hooks | PASS | `pre-commit run --all-files` clean |
| coverage | **FAIL** | 1153 passed, 2 failed (same as Finding 2), 2 legitimate skips; global floor met at 98.89% (≥85% required); **GPU tiers (sanity/integration/e2e) executed for real and all passed** |
| live STAC probe (ad-hoc, uncommitted) | PASS | real Earth Search endpoint, 5 scenes in 0.46 s |

Net: **2 finding-classes**, everything else green, including all live GPU tests and the live STAC boundary. Zero-skip guard held throughout — the only skips seen across every stage were the two enumerated `capability-guard:` MSYS entries already in the closed allowlist (`tests/unit/test_checks_sh_behaviour.py:1340,1516`), not a new or unauthorized skip.

---

## Finding 1 — mypy: `torch.amp` attr-defined errors (ENVIRONMENT-CAVEATED, not confirmed as a real source defect)

**Symptom:**
```
src\orbital_drift\train\baseline.py:279: error: Module "torch.amp" does not explicitly export attribute "GradScaler"  [attr-defined]
src\orbital_drift\train\baseline.py:285: error: Module "torch.amp" does not explicitly export attribute "autocast"  [attr-defined]
```
Reproduced identically in the standalone `typecheck` stage (only stage that runs mypy).

**Root cause analysis:** mypy's implicit-reexport check depends on how the **installed** `torch` package's `torch/amp/__init__.py`/`.pyi` declares `__all__` for `GradScaler`/`autocast`. The installed build is `torch==2.12.0.dev20260408+cu128` (a nightly dev snapshot), not the pinned `torch==2.13.0` stable release. Nightly builds are known to have incomplete or transiently-differing stub/export declarations. Both symbols exist and work correctly at runtime (exercised successfully by the live GPU e2e/integration tests in this same triage run), so this is very likely a stub-surface artifact of the version mismatch described in the environment note above, not a defect in `src/orbital_drift/train/baseline.py`.

**Blast radius:** `typecheck` gate only; does not affect runtime behavior (GPU training tests using these exact APIs passed).

**Cannot confirm real-vs-environmental from this host alone.** Recommended before treating as actionable:
- Re-run `sh ci/checks.sh typecheck` against a correctly-pinned `torch==2.13.0` CUDA build (or trust the Linux CI runner's own typecheck result, which installs the exact pin), OR
- If reproducible against the correct pin too: fix by importing `GradScaler`/`autocast` from their fully-qualified submodule path (e.g. `torch.cuda.amp.GradScaler` / `torch.cuda.amp.autocast`, or whatever the 2.13.0 stable public API path is) instead of the top-level `torch.amp` re-export, or add a narrow `# type: ignore[attr-defined]` with a comment citing this finding.

**Proposed regression test:** none until confirmed real — a regression test asserting mypy cleanliness of this exact import would only be meaningful once the pin mismatch is eliminated from the equation.

**Proposed AQA:** none proposed yet, pending confirmation. If confirmed real, add the fix inline; if confirmed environmental, consider a `tests/unit/test_version_pins.py`-style check that fails fast when the installed `torch` version doesn't match `ci/versions.env`/`pyproject.toml`'s pin exactly (would have caught the mismatch immediately instead of requiring this investigation).

---

## Finding 2 — Windows path-separator bug in `test_coverage_positive_control.py` (CONFIRMED, reproducible, Windows-only)

**Symptom:** both of the following fail identically in both the `unit` stage (standalone) and the `coverage` stage (whole-tree, single-process run) — `ci/checks.sh`'s own diagnostic states that agreement between the two stages rules out a single-process interaction artifact, confirming this is a genuine broken test:
- `tests/unit/test_coverage_positive_control.py::test_percent_covered_is_the_combined_rate_computed_from_the_reports_own_counts`
- `tests/unit/test_coverage_positive_control.py::test_a_file_with_no_arcs_reports_one_hundred_percent_branches_covered`

```
AssertionError: probe_pkg/mod.py absent from the report: ['probe_pkg\\__init__.py', 'probe_pkg\\mod.py']
assert 'probe_pkg/mod.py' in {'probe_pkg\\__init__.py': {...}, 'probe_pkg\\mod.py': {...}}
```

**Root cause:** `coverage.py` (7.15.4) writes `coverage.json`'s per-file `files` dict keyed by the **OS-native path separator** — backslash on Windows. The test's helper `_summary(root, filename)` (`tests/unit/test_coverage_positive_control.py:367`) is called by both failing tests with a hardcoded POSIX forward-slash literal (`"probe_pkg/mod.py"`, used again at line 520), and does a literal-string dict-key lookup with no separator normalization. This test suite was authored and validated only against Linux CI (`ubuntu-24.04`, per `ci/checks.sh`'s own `/bin/sh`=dash assumption); it has never been exercised on native Windows before this triage.

**Blast radius:** test-only; does not affect `src/orbital_drift/covcheck.py` or any production code path — `covcheck.py` itself reads `coverage.json` correctly regardless of separator (it doesn't do a literal-string key lookup against a hardcoded POSIX path). Confined to these two positive-control tests failing on Windows only; Linux CI is unaffected (forward slash is also the native separator there, so the bug is invisible in the environment these tests were written against).

**Proposed fix:** in `_summary()` (and any other helper doing the same string-literal lookup, e.g. around line 520), normalize both the expected filename and the report's keys to a common form before comparing — e.g. `Path(filename).as_posix()` compared against `{Path(k).as_posix(): v for k, v in files.items()}`, or simply build the expected key via `os.sep.join(...)` / `PurePosixPath`/`PureWindowsPath` conversion instead of a hardcoded literal. The fix belongs in the shared helper so both call sites (and any future one) are covered by a single change.

**Proposed regression test:** parametrize (or duplicate) the existing two tests to run their assertions through `_summary()` on both a POSIX-style and a Windows-style synthetic `coverage.json` fixture (constructed in-memory, no real OS dependency needed) so the separator-handling is verified without requiring a Windows CI runner.

**Proposed AQA:** add a `tests/unit/test_coverage_positive_control.py`-scoped note (or a lightweight `sys.platform`-aware CI matrix leg, if the project ever adds Windows CI) that documents this suite was Linux-only until this fix; alternatively, a small pytest fixture/helper shared by all "read `coverage.json` and look up a file" tests that always normalizes separators, so no future test can reintroduce the same class of bug by hand-rolling another literal lookup.

---

## Confirmed clean (no defects found)

- **Live GPU tiers** — `tests/sanity/test_gpu_sanity.py` (3 tests, incl. the RTX-5060-Ti-specific VRAM > 12 GB hard assertion), `tests/integration/test_gpu_pipeline_live.py` (3 tests), `tests/e2e/test_dual_gpu_e2e_live.py` (1 test), `tests/e2e/test_user_journey_ct_loop.py` (1 test) — all executed for real (confirmed via log inspection: no `capability-guard:` skip lines for any of them) and all passed on the first attempt. No flakiness observed, so the planned 3× flake-rerun protocol was not needed (it only triggers on a failing GPU test).
- **Live STAC boundary** — an ad-hoc, uncommitted probe (`STACClient().search_scenes(...)` with a real `requests.Session` against the live Earth Search endpoint) returned 5 valid scenes in 0.46 s. Confirms the mock-vs-live seam (`session` constructor injection) works correctly in both directions. Not committed under `tests/` — a committed live-network test would need a governed capability-guard allowlist addition, which is out of scope for this triage per the earlier plan discussion.
- **Coverage floors** — global 98.89% (≥85% required, comfortably clear); per-file table shows every file ≥95% (comfortably clear of the 90% per-file floor too), though `covcheck` itself did not get a chance to run this pass since `ci/checks.sh` aborts on the pytest-level failure (Finding 2) before reaching that step — re-run once Finding 2 is fixed to get an authoritative per-file floor result.
- **Zero-skip guard** — held throughout; the only skips seen anywhere were the two enumerated `capability-guard:` MSYS entries, not a new or unauthorized one.
- **Secrets / hygiene** — `gitleaks` and `hooks` (`pre-commit run --all-files`) both clean.

## Minor hygiene observation (not a defect requiring a fix, informational only)

The pre-existing local `.venv` has `torchvision==0.27.0.dev20260407+cu128` installed, which is **not declared anywhere** in `pyproject.toml` (no extra lists it, and `orbital_drift.quality.dep_contract` only scans `src/`, so an unused, undeclared runtime package is invisible to that gate by design). Since nothing under `src/orbital_drift` imports `torchvision`, this is inert rather than a real dependency-contract gap — recorded here only because it's an artifact of this specific hand-built `.venv` (assembled prior to this triage, not via the documented `pip install -e ".[dev]"` path) and could confuse a future operator inspecting this environment.

---

## Review gate

This report is the deliverable of the triage branch `qa/full-suite-triage`. **No `src/` change has been made.** Per RB-011's explicit limit and the plan this branch executes under: each finding above requires separate reviewer sign-off before any fix work begins, and each approved fix ships as its own task/PR with a failing regression test authored first (TDD), never bundled with this triage branch or with each other.
