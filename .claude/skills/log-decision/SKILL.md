---
name: log-decision
description: Record a governance decision in Orbital-Drift. Use when logging a DEC/RB/G entry, authorizing process work, recording a human gate, or when a change touches .claude/ tooling. Handles the two-file coupling the freshness gate enforces.
---

# Logging a decision

Gates read `docs/decision-log.md`. **Prose elsewhere unlocks nothing** — not a
PR description, not a commit message, not a conversation.

## The two-file rule

A decision is not done when the log line lands. `tests/governance/
test_governance_meta.py::test_skill_decision_section_is_fresh` fails if any
entry dated on/after the skill's "Decisions since" date has no matching ID in
`.claude/skills/orbital-drift-governance/SKILL.md`. Do both, in one change:

1. Append the line to `docs/decision-log.md`.
2. Add a one-line summary to the skill's **Decisions since** list.
3. Verify: `sh ci/checks.sh governance`

## Format

```
YYYY-MM-DD | ID | decision; enforcement mechanism; explicit limit | recorded-by
```

## Choosing the ID

| Prefix | Use for | Notes |
|---|---|---|
| `DEC-00x` | A CONFIRM-FIRST decision from charter §5 is confirmed or overridden | Numbered in the charter first |
| `RB-xxx` | Process/re-baseline: PR authorizations, scope corrections, policy amendments | Number sequentially, never reuse |
| `RB-xxxa` | An EXECUTION RECORD under a prior decision | Must say "EXECUTION RECORD under RB-xxx, not a new decision" |
| `G-x` | A human gate: the operator completed a `[HUMAN]` task or signed off | Only the operator's action creates one |

Never use `D-nn` here — that namespace belongs to the ADRs under
`docs/decisions/` and to plan.md. The prefixes are deliberately disjoint (D7).

## Rules that are easy to get wrong

- **Decide, then execute.** The decision is its own line; the PR that executes
  it cites that line. Never bundle "we decided X" and "X is done" into one
  ambiguous entry.
- **State what the entry does NOT do.** End with the explicit limit — e.g.
  "unlocks nothing; M1+ stays locked on G-3". This is what stops a later
  session inferring permission that was never granted.
- **Never spoof a gate.** A progress or transmission record related to a gate
  must NOT carry that gate's ID; gates presence-check the ID. Use a different
  ID and say "EXPLICITLY NOT a G-x entry".
- **Retroactive logging is allowed, but say so.** Operator approval given
  in-session before work started may be logged after, landing with the commits
  it authorizes — the entry must state that.

## When a decision is REQUIRED before acting

Editing `.claude/settings.json`, `.mcp.json`, or installing any hook, skill or
plugin is a governed decision, not housekeeping — the guard sees shell commands
only, so enabling tooling is a constraint-relevant change no later control will
catch. Log it first.
