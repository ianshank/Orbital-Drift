---
name: new-decision-doc
description: Scaffold a new docs/decisions/NNN-*.md file with the structure and provenance discipline this repo enforces (Status/Provenance header, D-nn sections with measurements and sources, a Follow-ups table, a Verified-correct section). Use whenever a non-obvious technical choice needs recording — a threshold value, a library/chart selection, a config workaround — per Constitution IV and the spec-guardian rule that an unexplained number is a magic number in disguise.
---

# New decision doc

Orbital-Drift records non-obvious technical decisions in
`docs/decisions/NNN-topic.md` rather than only in commit messages or PR
descriptions, because commit history is not what a future agent or the
operator reads before touching the same code. `docs/decisions/000-phase0-technical-decisions.md`
(D-01..D-11) and `docs/decisions/001-coverage-gate.md` (D-01..D-12) are the
two existing examples — read at least one in full before writing a new one,
so the tone and rigor match.

**Why this matters enough to be a skill**: `docs/decisions/001-coverage-gate.md`
D-05 states the principle directly — "a threshold with no stated rationale is
a magic number wearing a config file as a disguise", and that's a
`spec-guardian` BLOCK, not a style preference. T030 (`fm-selection.md`, Clay
vs Prithvi-EO) is already planned to need this format; Phases 1-5 will
generate several more (drift-threshold selection, serving-framework choice,
promotion-margin tuning).

## The structure to produce

```markdown
# D-NNN: <short, specific title>

**Status:** decided <date>.
**Provenance, stated precisely:** did the OPERATOR choose between presented
options, or is this an agent research conclusion the operator has not yet
seen? Say which, explicitly, for EACH decision below if they differ — do not
let the reader assume "recorded" means "operator-approved".
**Decision-ID namespace:** this file's `D-NNN/D-nn` series is independent of
[any other decision doc's numbering] and of plan.md's own Decision Log.
Cross-references from spec/plan/tasks are written `D-NNN/D-nn`. (This repo
already has a real namespace-collision bug from skipping this line once —
see docs/decisions/001-coverage-gate.md's follow-up list item 1. Do not
repeat it.)
**Why this exists:** one sentence on what breaks without this doc.

---

## D-01 — <specific, falsifiable title, not "Choice of X">

The decision, stated as a conclusion first, not a narrative. Then:

- The MEASUREMENT or SOURCE that supports it — a real command run, a real
  library behavior confirmed by running it (not assumed from memory or
  training data), a real URL with a retrieval date. "I believe X" is not
  sufficient; "measured: X, confirmed by running <command>, output: <Y>" is.
- What was REJECTED and why, if there were real alternatives — this is often
  more valuable to a future reader than the choice itself.
- Any residual risk or open question, stated explicitly rather than implied.

## D-02 — <next decision>
...

---

## Follow-ups found during this review, NOT fixed here

Only if you found genuine, separate gaps while writing this doc that are out
of scope for it. State plainly: "**Each is unscheduled and needs operator
triage before it becomes a task** — listing here is not agreement to do
them." Omit this section entirely if there's nothing to put in it — an empty
table with a caveat is worse than no section.

## Verified correct — no action

Things that LOOKED like a problem during this review and were checked and
found NOT to be one. Worth recording explicitly so a future reviewer doesn't
waste time re-deriving the same "wait, is this a bug?" question. Omit if
nothing applies.
```

## Rules this repo actually enforces, not just style preferences

- **Every measurement claim must be something you actually ran, not
  something you assumed.** If you're about to write "coverage.py reports X
  for Y", run it and paste the real output before writing the sentence. This
  repo's decision docs are full of "measured: ..." because a peer-reviewer
  pass previously caught an assumed-not-measured claim being wrong.
- **State whether the operator has actually seen and approved this, or
  whether it's still an agent proposal.** Don't let prose imply operator
  sign-off that hasn't happened. If a value in the doc is live/enforcing
  before the operator has confirmed it, say so explicitly and explain why
  that's safe (see `docs/decisions/001-coverage-gate.md` D-05's "Status of
  this number" for the pattern — the gate was live at a value that cost
  nothing yet, which is why it was safe to ship ahead of confirmation).
- **Namespace your `D-nn` IDs and say so at the top.** This repo has three
  independent `D-nn` series already (plan.md's own log, `000-...md`,
  `001-...md`) and at least one live collision from a decision doc that
  didn't declare its namespace. Always write cross-references as
  `<filename-stem>/D-nn`.
- **Reference file:line, not vague description**, when pointing at code the
  decision affects.
- If the decision doc records fixing a bug found during review, name WHO
  found it and HOW (a specific tool, a specific empirical test) — "measured
  by running X" is a stronger and more useful record than "found to be
  wrong".

## After writing it

- If the decision changes what a stage/gate does, update the relevant
  `spec.md` FR, `tasks.md` entry, and `README.md` section in the SAME PR —
  a decision doc that describes behavior nothing else agrees with is exactly
  the drift Constitution IV exists to prevent.
- Run `spec-guardian` on the new doc plus whatever code change it accompanies
  before checking off the task it belongs to (CLAUDE.md's collaboration
  protocol) — see the `run-review` skill to automate that sequence.
