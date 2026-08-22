---
name: orbital-drift-governance
description: Operating rules for all work in Orbital-Drift. Consult before ANY implementation, planning, review, or git action in this repository. Encodes the charter gates, CONFIRM-FIRST decisions, source-of-truth rules, and definition of done.
---

# Orbital-Drift Governance Operating Rules

## Gate table — check BEFORE starting any task
| Phase | Allowed when |
|---|---|
| adopt-governance-kit tasks (openspec/changes/adopt-governance-kit/tasks.md) | ONLY if `docs/decision-log.md` contains a DEC-001 entry. |
| Feature Phase-0 authoring (T002, T004, T006–T011; extended to T001b and T004a by RB-007) | ONLY if the log contains a G-0 entry (T001b/T004a additionally require RB-007). T006 additionally requires re-review against T003/T005 verification blocks before T011 may cite it (tasks.md AUTHORED-PROVISIONAL note); per RB-007 its authoring is deferred until G-1 exists. |
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
- **DEC-004** (08-21): coverage floor 90 over src/orbital_drift; honest-limit note; superseded for the global floor by RB-006 — ratified 85 global + 90 per-file.
- **G-0** (08-21): T001 verified; Phase-0 authoring tasks unlocked.
- **RB-001** (08-21): roster change — spec-implementer added; adversarial-reviewer supersedes peer-reviewer (D5).
- **RB-002** (08-21): settings.json merge — PreToolUse guard + SessionStart check; denies kept; allowlist seeded (DEC-003).
- **RB-002a** (08-21): execution record — kit tasks 0.1–6.1 done across six gate-green commits; 6.2/6.3 pending.
- **RB-003** (08-21): governance-hardening PR authorized after a three-dimension audit (guard fail-opens, coverage floors, wiring gaps).
- **RB-004** (08-21): settings.json hook-path fallback, allow-list for the make-less path, deny-list spelling symmetry.
- **RB-005** (08-21): documentation/validation hardening batch — CHANGELOG, architecture doc, README refresh, agent/skill structural validation, session-start-check tests.
- **RB-006** (08-21): reconciled with origin/main's independent PR #3 (infra T007-T010, coverage gate, runbooks) — origin/main's stage_coverage/host-prep.md kept canonical; this branch's per-file covcheck floor and adversarial-reviewer roster change retained.
- **RB-007** (08-22): Phase-0 completion program — unlock extended to T004a/T001b; DEC-002 M0 budget counter baselined at RB-006 (T004a + T001b-closure + T006 + T011 = 4/4); process batch (status refresh, decision-doc reconciliation, versions re-verify, tasks.md wording) authorized outside the budget; T006 authoring deferred until G-1; unlocks no Phase 1+ task.
- **RB-009** (08-22): SECURITY — the PreToolUse guard failed OPEN at its segment ceiling (256 nested substitutions made `split_segments` return nothing, so `analyze` allowed a denied command and the wrapper exited 0). Fixed fail-closed, with boundary regression tests; loosens nothing; security/process track, outside the M0 feature budget on the RB-007(b) baseline.
- **RB-008c** (08-22): execution record for the gate-integrity FOLLOW-UP PR, kept as its own entry rather than editing merged RB-008b — the concurrency guard was scope-blind (a job-level `concurrency:` override reinstated the RB-008 incident at an unchanged suite result; operator authorized the third cycle under R-5), RB-008a(d)'s glob half recorded UNDISCHARGED with a named owner, `ci/checks.sh` silently ran one stage and exited 0 on several, and a `cancel-in-progress` failure was diagnosed with a grouping narrative. No gate bar VALUE changes; unlocks no feature task.
- **RB-008** (08-22): code-hygiene/gate-integrity program after a four-stream audit — three process PRs (gate integrity: specs fail-closed, projections interpreter preflight, main-push concurrency, per-file-floor binding; hygiene/lockstep: the drifted PREFLIGHT_EXEMPT_PINS false warning, action pins, decision-log ordering test, single-home de-duplication; branch-coverage enablement). No gate bar VALUE changes; unlocks no feature task.
- **RB-008b** (08-22): execution record under RB-008, and the receiving half of the pair whose sending half is RB-008a(c) on the hygiene PR — read them together; neither is a second authorization. The weekly `schedule:` trigger, enumerated in part (2), executes in the part (1) gate-integrity PR because it is safe only alongside that PR's concurrency fix: a scheduled run shares main's concurrency group, so it would otherwise cancel main's run (outright, or by leaving it pending) on a timer. Also records two review-driven `covcheck.py` edits inside part (1)'s already-named subject — the fractional-floor `{floor:g}` output change and the corrected BELOW→ABOVE floor comment — per the RB-007a precedent of logging review-driven scope resolutions. Moves one item between PRs of one batch; adds no scope, changes no gate bar, unlocks nothing.
- **RB-007a** (08-22): execution record — guardian scope finding on the process batch resolved: the D-002/D-03 example correction was a named item of the approved program (F-9b); 005/006 follow-ups annotated in lockstep; ratification checklist delivered in PR #5's description; versions.md re-verification explicitly deferred to its own PR; unlocks nothing beyond RB-007.

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
