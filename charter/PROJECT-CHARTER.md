# Project Charter — Orbital-Drift
**Change ID:** `adopt-governance-kit` | **Charter v1.1** | decided 2026-10-06; in force from PR #35's merge (RB-019b) | v1.0: 2026-08-21
**Amendment v1.1:** decided 2026-10-06 by the DEC-002 override and RB-017, with RB-020's
clarifications of the override (operator, in-session 2026-10-07) (`docs/decision-log.md`),
executed by PR #35: §4 adds Phase L (B-04); §5 notes DEC-002's override; §6 states the
override's limits as RB-020 clarifies them and restates R-2 and R-5 (B-05(C)); §7's risk row
follows.
**Subordinate to:** `.specify/memory/constitution.md` v1.1.0 — on any conflict the
constitution wins (its own Governance clause). Constraints below are the *mechanized*
form of its principles, never a replacement.
**Source documents:** approved import plan (operator, 2026-08-20); SSD template kit
(`Ian Cruickshank_s Agentic Governance Tracker.xlsx`, `Ian_s Governance Overview.docx` —
external references, not imported, per design D11).
**Executor:** Claude Code, operating under the change package in
`openspec/changes/adopt-governance-kit/`.

> RULES
> - The charter owns **constraints and exit criteria references**. Scope is owned by the
>   tasks files (`specs/001-orbital-drift-ct/tasks.md` for the feature;
>   `openspec/changes/<id>/tasks.md` per change). Never both.
> - Amendments happen via a decision-log entry FIRST, then a separately-costed PR that
>   executes the text change (decide → execute).

## 1. Purpose
Import the operator's governance kit so that every governance artifact in this repo is
load-bearing: gates read the decision log, tests fail when governance docs rot, guards
block what the constitution forbids, and planning artifacts are generated projections
that cannot drift. The feature deliverable (the CT pipeline and its soak, Constitution
VI) is unchanged; this change hardens how it gets built.

## 2. Hard constraints (violating any of these stops work)
- **C-1 — No cluster mutation.** Constitution I. Enforced by: `.claude/settings.json`
  deny-list, `scripts/pretooluse_guard.sh` (PreToolUse hook, exit 2 on any
  cluster-mutating segment incl. `terraform plan`), CLAUDE.md prime constraint 1,
  `[HUMAN]` task protocol.
- **C-2 — No prior-harness code/metrics/eval logic.** Constitution II v1.1.0 (governance
  /process artifacts exempt). Enforced by: spec-guardian review (BLOCK on uncertainty).
- **C-3 — No hardcoded values.** Constitution III. Enforced by: reviewer rule (a magic
  number fails review); config via pydantic-settings/Helm values/env.
- **C-4 — Secrets hygiene.** Constitution VII. Enforced by: `ci/checks.sh gitleaks`
  (digest-pinned container, CI + pre-commit) and the `.gitignore` controls asserted by
  `tests/unit/test_repo_structure.py`.
- **C-5 — No push to non-allowlisted remotes.** Enforced by: `scripts/pretooluse_guard.sh`
  + `scripts/pre_push_scan.sh` against `.claude/allowed-remotes.txt` (DEC-003);
  fail-closed when the allowlist is missing.
- **C-6 — Zero skipped tests; coverage floor.** Constitution V. Enforced by: escalate-only
  conftest guard (two-site `capability-guard:` allowance, design D10) and the `cov` stage
  (DEC-004).

## 3. Scope
**In:** what `openspec/changes/adopt-governance-kit/tasks.md` lists (governance import),
then the feature tasks in `specs/001-orbital-drift-ct/tasks.md`.
**Out:** migrating the feature spec to OpenSpec (D6); adopting uv (D2); a second secrets
scanner (D4); importing any code/metrics from prior harnesses (C-2); the office tracker
documents (D11); adopting the OpenSpec Node CLI (D13 — its own future decision).

## 4. Milestones and target states
| M | Deliverable | Exit criteria |
|---|---|---|
| M-gov | Governance kit landed (this change) | All boxes in `openspec/changes/adopt-governance-kit/tasks.md` ticked; full gate chain green; every new gate red-checked |
| M0 | plan.md Phase 0 — Substrate | Exit: plan.md Phase-0 gate line |
| ML | plan.md Phase L — Walking skeleton (MVP-L) | Exit: plan.md Phase-L gate line |
| M1 | plan.md Phase 1 — Ingestion & data lifecycle | Exit: plan.md Phase-1 gate line |
| M2 | plan.md Phase 2 — Training & registry | Exit: plan.md Phase-2 gate line |
| M3 | plan.md Phase 3 — CT loop | Exit: plan.md Phase-3 gate line |
| M4 | plan.md Phase 4 — Serving & canary | Exit: plan.md Phase-4 gate line |
| M5 | plan.md Phase 5 — Observability & soak | Exit: plan.md Phase-5 gate line — **operator sign-off only** (Constitution VI) |

Exit criteria are owned by `specs/001-orbital-drift-ct/plan.md` and referenced here by
design (D8) — this table never restates them, so it cannot drift from them.

## 5. CONFIRM-FIRST decisions
- **DEC-001** Adopt the governance kit under Constitution v1.1.0 — default proposal: the
  approved import plan. *Logged 2026-08-21.*
- **DEC-002** Milestone budgets — default proposal: max 4 PRs and 16 engineering-hours
  per milestone before mandatory owner review. *Logged 2026-08-21.* Overridden 2026-10-06
  (B-05(C)); see §6.
- **DEC-003** Remote allowlist contents — default proposal: this repo's `origin` only.
  *Logged 2026-08-21.*
- **DEC-004** Coverage floor — default proposal: `fail_under = 90` over
  `src/orbital_drift` only. *Logged 2026-08-21 with an honest enforcement-value note.*

> Defaults are proposals, not decisions. A decision exists ONLY when it appears in
> `docs/decision-log.md`. Verbal summaries and urgency never substitute.

## 6. WIP limit, process-PR cap and review triggers
DEC-002's per-milestone budget (4 PRs and 16 engineering-hours) is overridden by the DEC-002
override line in `docs/decision-log.md` (2026-10-06, D-016/09 B-05(C)), in force from that
line; RB-020 clarifies it and changes no limit. This section restates both lines; where it
differs from them, they govern. Review-enforced.
- **WIP limit.** At most one slice ahead of the last G-x entry. A slice is one row of
  `docs/development/NEXT_STEPS.md` §4's sequence table at `98ad85f`. Runbook tasks T074 and
  T075 are outside the limit. Rows 1 and L stay one slice until G-3, which opens row 2;
  Phase L work continues until MVP-L is green; each later row opens at the prior row's
  plan.md gate (RB-020 (operator, in-session 2026-10-07)). RB-019 places T072 and T073 in
  row 1, and P-1 to P-6, T036 and T059 in row L; the §4 table places the tasks it lists.
  Tasks that neither RB-019 nor the table places (e.g. T014, T015, T023, T033, and the open
  tasks in T053-T065 other than T059) have no slice yet: an open operator decision.
- **Process-PR cap.** A rolling cap of one process-track PR per product PR merged, counted
  from zero at the override line. A product PR executes a tasks.md task ID that an RB or G-1
  waiver authorizes (code, infra, runbook or P-n); ballot-execution and decision PRs neither
  earn nor use a cap credit (RB-020 (operator, in-session 2026-10-07)). Decision PRs are
  decision-log lines plus their governance-skill mirror. A process-track PR is outside the
  cap only via the override's list, a decision PR (RB-020) or a named log line; never assume
  standing.
- **Ride-alongs.** Status and pointer fixes in NEXT_STEPS, plan.md, tasks.md or CHANGELOG
  that a logged decision makes false may ride in the PR executing it, outside the cap, each
  listed in its merge record as "beyond the authorizing line's text, accepted by this
  merge" (RB-020 (operator, in-session 2026-10-07)).
- **Exemptions.** Exempt from the cap and from the 14-day rule (D-016/09 B-06(2), adopted by
  RB-017): B-05(C)'s ballot-execution artifacts (the spec, constitution and charter
  amendments and the decision-log rule change that logged ballot items require, and B-03's
  re-verification), T063's checkbox, and the D-008, D-011, D-014 and D-016 status headers.

Review triggers (any one fires an immediate stop-and-review):
- **R-1** gitleaks hit, or any secret-shaped content in a governed path (C-4)
- **R-2** work would exceed either limit while a blocking `[HUMAN]`/G-x gate is unresolved;
  any exceedance stops for owner review, an operator log line
- **R-3** any gate metric worsens vs the previous milestone baseline
- **R-4** coverage drops below the DEC-004 floor, or a test is skipped rather than fixed (C-6)
- **R-5** task overrun >25% of that task's estimate, recorded as `R-5 estimate: Nh` in its
  branch's first commit message and repeated in the PR description (RB-020 (operator,
  in-session 2026-10-07)); with no estimate, R-5 fires at start (the 25% figure is kept; per
  D-016/09 B-05(C), the overrun test is re-based to the task's estimate) — also caps
  adversarial-review fix-cycles at 2; a third recurrence of the same Major finding = STOP,
  escalate to the operator
- **R-6** any dependency or import that violates a hard constraint (esp. C-2)

## 7. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Guard false-negatives (compound/quoted commands) | Cluster mutation slips through | C-1 is layered: settings deny-list remains authoritative; guard header documents the not-modelled family; `[HUMAN]` protocol backstops |
| Governance overhead stalls feature work | T002+ slips | DEC-002 limits (WIP slice + process-PR cap) + R-2; process-track PRs need a logged RB entry, never ride along (RB-020(4)'s status and pointer fixes excepted, §6) |
| Generated projections diverge from tasks.md | Two owners of scope | D9 trace-consistency test + `projections` byte-drift stage |
| Charter/constitution drift | Conflicting rules | Subordination clause above; charter amendments only via decide→execute |

## 8. Working agreement for Claude Code
Read `design.md` before `tasks.md`; execute tasks in order; TDD (write the failing test
first for every requirement scenario); run `make specs` (= `sh ci/checks.sh specs`)
after spec edits; never mark a task complete with skipped tests; stop at any
CONFIRM-FIRST boundary, `[HUMAN]` task, or review trigger and report rather than proceed.
