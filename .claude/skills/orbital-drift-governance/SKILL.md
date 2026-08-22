---
name: orbital-drift-governance
description: Operating rules for all work in Orbital-Drift. Consult before ANY implementation, planning, review, or git action in this repository. Encodes the charter gates, CONFIRM-FIRST decisions, source-of-truth rules, and definition of done.
---

# Orbital-Drift Governance Operating Rules

## Gate table — check BEFORE starting any task
| Phase | Allowed when |
|---|---|
| adopt-governance-kit tasks (openspec/changes/adopt-governance-kit/tasks.md) | ONLY if `docs/decision-log.md` contains a DEC-001 entry. |
| Feature Phase-0 authoring (T002, T004, T006–T011) | ONLY if the log contains a G-0 entry. T006 additionally requires re-review against T003/T005 verification blocks before T011 may cite it (tasks.md AUTHORED-PROVISIONAL note). |
| Any `[HUMAN]` task (T003, T005, T012, T022, T029, T032, T040, T045, T049–T052) | NEVER executed by an agent (Constitution I). STOP, hand off the paired runbook, wait; the operator logs the matching G-x entry on completion. |
| Feature Phase 1+ tasks (T013+) | ONLY if the log contains the G-x entry for the preceding phase gate (G-1 = T003 done, G-2 = T005 done, G-3 = T012 done, then per plan.md phase gates). |
| Process/docs-track PRs | ONLY via a logged RB entry naming the PR batch. NEVER via urgency, and never as a side effect of engineering work. |
| Any `git push` | ONLY to remotes listed in `.claude/allowed-remotes.txt` (charter C-5; guard-enforced once Phase-6 lands). |

If a gate is not satisfied: STOP, report which entry is missing, and do nothing else on
that task. Never treat urgency, partial approval, or verbal summaries as a logged
decision.

## CONFIRM-FIRST
DEC-001…DEC-004 (charter §5). Implement only to a failing stub until the decision-log
entry exists. Status as of 2026-08-21: all four are logged. Proposed defaults live in
`openspec/changes/adopt-governance-kit/design.md`; defaults are proposals, not
decisions. Re-check the log before trusting this snapshot.

## Source of truth
`specs/001-orbital-drift-ct/tasks.md` owns feature scope;
`openspec/changes/<id>/tasks.md` owns each change's scope. The charter owns
constraints; the constitution supersedes everything. `planning/roadmap.md` and
`planning/jira-import.csv` are generated projections — never hand-edit them; regenerate
ONLY at a re-baseline.
Charter version pin: **v1.0** (2026-08-21) — if the charter header disagrees, stop and
reconcile before relying on any constraint.

## Budgets (charter §6, DEC-002)
4 PRs AND 16 engineering-hours per milestone before mandatory owner review.
Process-scope PRs sit outside milestone budgets ONLY when a named RB entry says so —
never assume standing.

## Decisions since 2026-08-20, one line each
- **DEC-001** (08-21): governance kit adopted; Constitution v1.1.0 amendment.
- **DEC-002** (08-21): budgets 4 PRs / 16 h per milestone.
- **DEC-003** (08-21): remote allowlist = origin only (armed at Phase 6).
- **DEC-004** (08-21): coverage floor 90 over src/orbital_drift; honest-limit note.
- **G-0** (08-21): T001 verified; Phase-0 authoring tasks unlocked.
- **RB-001** (08-21): roster change — spec-implementer added; adversarial-reviewer supersedes peer-reviewer (D5).
- **RB-002** (08-21): settings.json merge — PreToolUse guard + SessionStart check; denies kept; allowlist seeded (DEC-003).
- **RB-002a** (08-21): execution record — kit tasks 0.1–6.1 done across six gate-green commits; 6.2/6.3 pending.
- **RB-003** (08-21): governance-hardening PR authorized after a three-dimension audit (guard fail-opens, coverage floors, wiring gaps).
- **RB-004** (08-21): settings.json hook-path fallback, allow-list for the make-less path, deny-list spelling symmetry.
- **RB-005** (08-21): documentation/validation hardening batch — CHANGELOG, architecture doc, README refresh, agent/skill structural validation, session-start-check tests.
- **RB-006** (08-21): reconciled with origin/main's independent PR #3 (infra T007-T010, coverage gate, runbooks) — origin/main's stage_coverage/host-prep.md kept canonical; this branch's per-file covcheck floor and adversarial-reviewer roster change retained.

**This section is mechanically checked for staleness** —
`tests/governance/test_governance_meta.py` fails if a decision-log entry dated on/after
this section's own "since" date has no corresponding ID anywhere in the section text. A
red result here means: add at least one line naming the missing ID before proceeding
with anything else.

## Definition of done (every task)
1. Failing test written first; passes at completion; zero skipped tests (D10 allowance
   only).
2. `make specs` (= `sh ci/checks.sh specs`) clean after any spec edit.
3. Traceability matrix updated when the task's requirement mapping changes.
4. Gate thresholds and guard patterns are NEVER edited in the same PR as a failing gate
   run; such changes require their own PR + decision-log entry.
5. No new dependency that violates a hard constraint (charter §2).
6. Every new document declares its audience in its header; every factual claim about the
   tree is mechanically checked or carries the commit it was measured at — a
   hand-asserted, undated number is a defect.

## Collaboration default
After tests pass, hand the diff to the adversarial-reviewer subagent (after
spec-guardian) before marking any task complete. Review findings of Major severity block
completion. This applies to **tooling and configuration diffs too**, not only task
diffs.

## Tooling changes
Editing `.mcp.json`, `.claude/settings.json`, or installing any hook or plugin: treat it
as a governed decision, not housekeeping — the guard sees shell commands only, so
enabling an MCP server is a C-1-relevant decision no later control will catch.

## Local gates
`make pre-pr` runs everything CI runs, in CI order; every Makefile gate target delegates
to `sh ci/checks.sh <stage>` — checks.sh is the single source of truth for how each gate
is invoked (design D1), and CI's matrix calls the same stages. On a box without GNU
make, call `sh ci/checks.sh <stage>` directly (design D12).

## Decision log convention
`docs/decision-log.md`, one entry per line:
`YYYY-MM-DD | DEC-00x or RB-xxx[a] or G-x | decision | recorded-by`.
