# Adopt the SSD Governance Kit

<!-- openspec/changes/adopt-governance-kit/proposal.md -->

## Why
The project runs a hardened CI harness and a seven-agent review protocol, but its
governance is prose: gates live in convention, decisions are scattered between chat and
`docs/decisions/`, and nothing mechanically fails when a governance artifact rots. The
operator's SSD template kit packages seven interlocking mechanisms (charter, gated
decision log, governance skill, opposed implementer/reviewer agents, layered guards,
single-harness gates, linted traceability) proven over ~3 weeks of agent-driven
development elsewhere. Importing it was authorized by the operator on 2026-08-20 and is
permitted by Constitution v1.1.0 (Principle II scoped to code/metrics/eval-harness
logic; governance/process artifacts exempt).

## What Changes
- Add a project charter (`charter/PROJECT-CHARTER.md`): constraints C-1…C-6 as
  mechanisms, milestones referencing plan.md phase gates, CONFIRM-FIRST decisions.
- Add a mechanical decision log (`docs/decision-log.md`): gates presence-check IDs here;
  namespace `DEC-00x`/`RB-xxx[a]`/`G-x`, disjoint from `docs/decisions/` ADR `D-nn` ids.
- Add the governance skill (`.claude/skills/orbital-drift-governance/SKILL.md`) with the
  gate table, mechanically staleness-checked against the decision log.
- Extend the gate harness: new `ci/checks.sh` stages `dead`, `audit`, `specs`, `cov`,
  `traceability`, `projections`; a thin Makefile front-end delegating every gate to
  `ci/checks.sh`; a zero-skip conftest guard; governance meta-tests.
- Add agents: `spec-implementer` (default implementer, TDD protocol) and
  `adversarial-reviewer` (supersedes `peer-reviewer`).
- Add layered guards: PreToolUse guard, pre-push scan with remote allowlist, merged
  (never replaced) `.claude/settings.json`.
- Add requirement traceability (`traceability/REQUIREMENT-TRACEABILITY.md` + linter) and
  generated planning projections (`planning/roadmap.md`, `planning/jira-import.csv`).
- Maintain a **requirement-traceability matrix** mapping spec FR-001…FR-012 to modules
  and tests.

## Impact
- Affected specs: `governance-harness` (new, this change). `specs/001-orbital-drift-ct/`
  is unchanged and remains the plan of record for the feature (design D6).
- Affected code: `ci/checks.sh` (new stages), `Makefile` (new), `scripts/` (new),
  `src/orbital_drift/{traceability,remotes,projections}.py` + `planning/` package (new),
  `tests/{conftest.py,governance/}` (new), `tests/unit/test_repo_structure.py` and
  `test_version_pins.py` (extended), `.claude/` (agents, skill, settings merge),
  `CLAUDE.md`, `specs/001-orbital-drift-ct/{plan.md,tasks.md}` (structure block, legend).
  Namespace decision: decision-log IDs are prefix-disjoint from ADR `D-nn` (design D7).
- Depends on: Constitution v1.1.0 amendment (landed first, same change, Phase-1 PR);
  nothing external. Docker daemon required locally for the gitleaks/shellcheck stages —
  already true before this change.
- Constraint inherited from charter C-1: nothing in this change may execute
  cluster-mutating commands; all guard work targets *blocking* them (Constitution I).
