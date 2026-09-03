# Full-Suite QA Triage — Round 2 (post-RB-011 baseline, RCA)

**Status:** NO NEW DEFECTS. The only red stage (`typecheck`) is the same environmental artifact resolved by RB-011a — re-verified against this host's installed torch build at the mechanism level. Every other gate stage is green, and all three GPU-gated live tiers executed for real (zero capability skips) and passed. This document makes no `src/` change; there is nothing to fix.
**Audience:** whichever operator/agent reviews this round-2 triage.
**Authorized by:** `docs/decision-log.md` RB-012 (round-2 triage authorization).
**Measured at:** branch `qa/full-suite-triage-r2` cut from `main` @ `4b6ac35` (the PR #20 merge that closed RB-011) plus exactly one commit, `75801cd` (the RB-012 authorization entry itself — docs-only, no code or test surface touched). The merge-base check `git merge-base --is-ancestor qa/full-suite-triage origin/main` exited 0 before branching, confirming round 1's work was fully merged with nothing left unmerged.
**Host:** Windows, dual-GPU (NVIDIA RTX 5060 Ti 16 GB + RTX 5060 8 GB, driver 596.49, CUDA runtime ceiling 13.2), Docker Desktop (server 29.4.2), Git for Windows (MSYS `sh.exe` for all `ci/checks.sh` invocations — WSL's `bash`/`sh` deliberately NOT used, since `ci/checks.sh` is written against MSYS-specific path-conversion behavior).
**Environment note (carried from round 1, load-bearing):** the `.venv` still carries `torch==2.12.0.dev20260408+cu128` (CUDA-enabled nightly, verified below to drive both GPUs for real) rather than the pinned `torch==2.13.0`. This is deliberate: round 1 measured that a naive `pip install -e ".[dev]"` on this host would resolve torch to a **CPU-only** Windows wheel and silently turn every GPU-gated test into a vacuous `capability-guard:` skip. The nightly is kept; Finding 1 below is the known, bounded cost of that choice.

## Method

Stages were run **individually** (17 distinct stages, canonical `stage_all` order verified at `ci/checks.sh:2329`), one invocation per stage — `sh ci/checks.sh all` stops at the first red and would have hidden every downstream stage behind the `typecheck` artifact. A single `all` run is reserved as the final green proof if and when the typecheck artifact is eliminated. Per-stage logs: `baseline-results/r2_<stage>.log` (outside the repo, tree stays clean). `ci/checks.sh` accepts exactly one stage per invocation (extra arguments refused with rc=2 — the RB-008c measured-defect guard).

## Summary

| Stage | Result | Notes |
|---|---|---|
| lint | PASS | ruff clean |
| typecheck | **FAIL on this host** / known-environmental | 2 errors — Finding 1 (round-1 Finding 1, unchanged; mechanism re-verified below). CI's pinned `torch==2.13.0` passes this same source |
| unit | PASS | 949 passed, 0 failed, 2 legitimate capability-guard skips (302.86 s) |
| contract | PASS | green |
| smoke | PASS | declared-empty, tolerated |
| coverage | PASS | 1159 passed, 0 failed, 2 legitimate skips (351.73 s); global 98.89% (≥85% floor); `covcheck: every measured module is at or above 90%`. Whole-tree run — **includes the GPU live tiers, which executed for real and passed** |
| gitleaks | PASS | clean (working-tree + full-history) |
| dead | PASS | vulture clean |
| audit | PASS | no known vulnerabilities |
| specs | PASS | no structural issues |
| traceability | PASS | no problems |
| projections | PASS | no byte-drift |
| governance | PASS | 163 passed (includes the decision-log ↔ SKILL.md mirror check for the RB-012 entry) |
| hardcode | PASS | no hardcoded-config findings |
| deps | PASS | dependency contract holds |
| architecture | PASS | import-linter boundaries hold |
| hooks | PASS | `pre-commit run --all-files` clean |
| tests/sanity (standalone, `-v`) | PASS | 3/3 executed and passed (1.38 s) — CUDA runtime/driver, dual-GPU topology + VRAM, tensor-core allocation |
| tests/integration (standalone, `-v`) | PASS | 3/3 executed and passed (3.39 s) — live AMP fp16 training epoch on GPU 0, PSI/KS drift on CUDA tensors, GPU 0/GPU 1 train-serve isolation |
| tests/e2e (standalone, `-v`) | PASS | 2/2 executed and passed (6.55 s) — full dual-GPU CT + canary-serve lifecycle; full CT + rollback lifecycle |
| live STAC probe (ad-hoc, uncommitted) | PASS | real Earth Search endpoint `https://earth-search.aws.element84.com/v1`, San Francisco bbox `(-122.5,37.7,-122.3,37.9)`, window 2026-08-25→2026-09-03: **4 real `sentinel-2-l2a` scenes in 0.52 s** (first: `S2C_10SEG_20260831_0_L2A`) |

Net: **zero new defects.** One red stage, classified known-environmental with the mechanism re-verified on this exact host (Finding 1). Zero-skip guard held throughout — the only skips across every stage are the two enumerated `capability-guard:` MSYS entries already in the closed allowlist (`tests/unit/test_checks_sh_behaviour.py:1340,1516`). Test-count growth vs round 1 (unit 947→949, coverage-tree 1153→1159) is PR #19/#20's merged regression tests plus the parametrized separator variants.

## Scope notes the request named explicitly

- **"With and without mocks":** the mocked tier (unit: `monkeypatch`/`tmp_path` isolation; contract: local fixtures, no network) and the no-mock tier (sanity/integration/e2e: real CUDA tensor ops, real adapter code paths, no mocking layer) both ran fully green. The coverage stage's whole-tree pytest run executes both tiers in one process and is the repo's own comprehensive proof of that.
- **"Live LLM API":** verified by whole-repo search — this codebase has **no LLM API surface at all** (no openai/anthropic/litellm or similar dependency; no network call in the guard scripts, which are pure lexers). There is therefore no LLM mode to enable or disable; this is recorded as a scope note, not a defect. The only live external API boundary the system has is Earth Search STAC, probed live above.
- **"GPU-based changes to origin":** all GPU tiers executed for real on the dual-GPU host (not capability-skipped) against `origin/main` @ `4b6ac35` plus the docs-only RB-012 commit — see the standalone-tier rows above.
- **Live backing services (deliberately not brought up):** `docker-compose.yaml` defines only the FastAPI serving app — there are no lakeFS/MLflow/MinIO service definitions in this repo, and the serving app's build is the deferred RB-010 Part 12 Dockerfile defect (`src/` never copied into the builder stage), so a compose bring-up was deliberately not attempted. Source-verified: `LakeFSOps.commit_scene` (`src/orbital_drift/data/lakefs_ops.py:33-62`) computes a deterministic local SHA-256 and never issues a network call to its configured endpoint; `ModelRegistryOps` follows the same simulated-backend pattern (RB-010: "lakeFS/MLflow simulated, not real"). Live-service integration is therefore RB-010-tracked future work, and this round's "live" execution means real GPU compute plus the real adapter code paths over those simulated backends — by design, not by omission.

## Finding 1 — mypy: `torch.amp` attr-defined errors (KNOWN-ENVIRONMENTAL — round-1 Finding 1, re-verified; no source defect)

**Symptom (identical to round 1):**
```
src\orbital_drift\train\baseline.py:279: error: Module "torch.amp" does not explicitly export attribute "GradScaler"  [attr-defined]
src\orbital_drift\train\baseline.py:285: error: Module "torch.amp" does not explicitly export attribute "autocast"  [attr-defined]
Found 2 errors in 1 file (checked 110 source files)
```

**Root cause (re-verified on this host, this venv, this round):** probed the installed build directly — `torch 2.12.0.dev20260408+cu128`, and its `torch/amp/__init__.py` contains **no `__all__` declaration** (probe output: `amp __init__ has __all__: False`). mypy's `--strict` implicit-reexport check therefore correctly refuses to treat `GradScaler`/`autocast` as public surface *for that specific build*. Round 1 (RB-011a) already fetched the pinned `torch==2.13.0` release source and confirmed it declares `__all__ = ["autocast", ..., "GradScaler"]` — so `baseline.py:279,285` is correct code against the pinned dependency, and CI's `typecheck` job (which installs the exact pin) passes: PR #20's CI ran `typecheck` green on this exact source tree.

**Disposition:** no source or test change. The same nightly build that produces this false positive is the build that drives both GPUs for real (verified in the Phase-1 preflight: `cuda_avail: True`, both devices enumerated) — and round 1 measured that replacing it via the naive install path yields a CPU-only wheel and vacuous green GPU skips. The bounded cost of a known, understood, host-local mypy artifact is accepted over the unbounded cost of silently losing GPU-tire signal. **Recommendation (unchanged from round 1, still optional):** when convenient, reinstall torch/torchvision as `torch==2.13.0` from a CUDA≥12.8 wheel index (never the default PyPI Windows wheel) to eliminate this false positive permanently.

---

## Blast radius and confinement

- Finding 1 touches no production code behavior: it is a static-typing surface artifact of one specific torch pre-release build, present only on this host, and invisible to CI.
- No other stage showed any failure, skip outside the closed capability-guard allowlist, or coverage regression (98.89% global / ≥90% per-file, matching round 1's post-fix actuals exactly).

## Regression and QA posture

Round 1's regression artifact (`tests/unit/test_coverage_positive_control.py::test_summary_lookup_is_path_separator_agnostic`, parametrized over both path separators) is present in this tree and passed in both the `unit` and `coverage` stages — the merged RB-011b fix is holding. This round produced no new defects, so no new fix branches or regression tests were required; the QA posture for a clean round is this report plus the RB-012a execution record in `docs/decision-log.md`, with the same decision-log ↔ SKILL.md mirror mechanically asserted by the `governance` stage.

## Review gate

This document is the deliverable RB-012 authorizes. Per its EXPLICIT LIMIT, any future finding a reviewer approves out of a report like this one lands as its own task/PR with a failing regression test first (CLAUDE.md one-task-per-branch + TDD). This round has no such finding.
