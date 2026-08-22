# Tasks — adopt-governance-kit

<!-- Hand-maintained. This file OWNS the scope of the adopt-governance-kit change (skill
     rule); specs/001-orbital-drift-ct/tasks.md owns feature scope. Re-baseline ONLY via
     a decision-log entry, recorded in this file via PR. -->

> Milestones map to the phased import plan (charter M-gov). Each task cites its design
> decision. Every milestone that adds a gate ends by proving the gate can fail
> (red-check), not only that it passes.

## 0. Constitution amendment (import Phase 1)
- [x] 0.1 Amend Principle II to v1.1.0 (scope ban to code/metrics/eval-harness logic;
      exempt governance/process artifacts); sync CLAUDE.md and spec-guardian.md
- [x] 0.2 Verify `sh ci/checks.sh all` green and no stale Principle-II wording remains

## 1. Control plane (import Phase 2)
- [x] 1.1 OpenSpec change package: proposal, design (D1–D14), governance-harness spec
      delta, this tasks file
- [x] 1.2 Charter `charter/PROJECT-CHARTER.md` (C-1…C-6; milestones reference plan.md
      gates per D8)
- [x] 1.3 Decision log `docs/decision-log.md` seeded (DEC-001…DEC-004, G-0) per D7
- [x] 1.4 Governance skill `.claude/skills/orbital-drift-governance/SKILL.md` (gate
      table; "Decisions since" seeded)
- [x] 1.5 plan.md Project Structure block + test_repo_structure.py extended for this
      phase's paths only

## 2. Harness (import Phase 3)
- [x] 2.1 Restructure the gitkeep logic-skip in test_repo_structure.py as an
      always-asserting statement (D10) — BEFORE the zero-skip guard lands
- [x] 2.2 Makefile (thin front-end, D1/D2; only targets whose stages exist this PR)
- [x] 2.3 checks.sh stages `dead`, `audit`, `specs` via the full stage recipe (D3);
      author deterministic `ci/validate_specs.sh` (D13)
- [x] 2.4 pyproject: pytest-cov/vulture/pip-audit pins; coverage source config;
      governed_path_globs key; versions.env + test_version_pins.py lockstep
- [x] 2.5 ci.yml matrix extended with `dead`, `audit`, `specs`
- [x] 2.6 Zero-skip conftest with two-site `capability-guard:` allowance (D10) +
      tests/governance/test_zero_skip_guard.py; red-check: a scratch skip reddens the
      suite; a broken spec-delta heading fails `specs`

## 3. Agents (import Phase 4)
- [x] 3.1 spec-implementer agent instantiated (D5)
- [x] 3.2 adversarial-reviewer agent instantiated; peer-reviewer.md deleted (D5)
- [x] 3.3 All live references updated: CLAUDE.md roster + protocol, tasks.md legend,
      ml-engineer.md; RB entry logged + skill "Decisions since" refreshed

## 4. Traceability + meta-tests (import Phase 5)
- [x] 4.1 traceability/REQUIREMENT-TRACEABILITY.md seeded from FR-001…FR-012
- [x] 4.2 src/orbital_drift/traceability.py (failing tests first) + `traceability` stage
- [x] 4.3 tests/governance/test_governance_meta.py (§1 Makefile-delegates per D1;
      §2 skill freshness; §3 governed globs + negative control)
- [x] 4.4 `cov` stage armed per DEC-004; red-checks for traceability + freshness

## 5. Guards + projections (import Phase 6)
- [x] 5.1 scripts/pretooluse_guard.sh (real, fail-closed) + src/orbital_drift/remotes.py
      + .claude/allowed-remotes.txt (DEC-003) + guard tests incl. bare `argo` (F4)
- [x] 5.2 scripts/pre_push_scan.sh + scripts/install_hooks.sh
- [x] 5.3 .claude/settings.json MERGED (denies kept verbatim; PreToolUse hook;
      SessionStart version-check; make/checks.sh allows) — logged as a tooling decision
- [x] 5.4 Projections: roadmap_data.py + projections.py + generated files + fixture +
      trace-consistency test + `projections` stage (D9); red-check: one-byte hand-edit
      fails the stage
- [x] 5.5 Makefile `cov`/`traceability`/`projections`/`guard-probe` targets landed with
      their stages; `pre-pr` chain completed

## 6. Close-out (import Phase 7)
- [x] 6.1 README Governance section; RB execution record; skill freshness updated
- [x] 6.2 Final `sh ci/checks.sh all` + `make pre-pr` green; push; CI green
      (GitHub run 32520929851: all thirteen stages green, 2026-08-21)
- [x] 6.3 Resume feature task T002 under the new loop (spec-guardian →
      adversarial-reviewer; T003 is [HUMAN] — stop and hand off)
      (T002 authored by runbook-writer; spec-guardian APPROVE; adversarial-reviewer
      APPROVE after one fix cycle closing 2 Majors; T003 handed to the operator)
