---
name: run-review
description: Drive CLAUDE.md's collaboration protocol (owning agent produces artifact -> spec-guardian reviews -> peer-reviewer reviews -> owning agent addresses findings -> only then checked off) end to end for one task, so a human doesn't have to remember and manually sequence four steps. Use whenever a task's artifact is ready for review, or when explicitly asked to "run the review protocol" or "review task T0xx".
---

# Run review

CLAUDE.md's collaboration protocol is fully specified but nothing in this
repo's history shows it being invoked as a repeatable sequence — the
Phase 0 decision docs' "ROUND 6"/"ROUND 10" language reads like ad hoc manual
iteration by a human orchestrator, not a scripted dispatch. Phase 1 is the
first time this matters at scale: `pipeline-engineer` writes a DAG,
`spec-guardian` and `peer-reviewer` both need to run against it in order,
findings need addressing, and only then does the task get checked off in
`tasks.md`. This skill removes the "did I run both reviewers, in order, and
actually address what they found before checking the box" memory burden.

Takes one argument: a task ID (e.g. `T020`) or a description of the artifact
to review if no formal task ID applies yet.

## The protocol, in order (per CLAUDE.md)

1. **Confirm the artifact is actually ready.** Tests exist and, where the
   task said "tests first", were observed failing before the implementation
   landed (Principle V). Don't start the review sequence on unfinished work.

2. **Dispatch `spec-guardian`.** Give it the diff or the specific files, plus
   which task/FR they're supposed to implement. It checks: constitution
   conformance (especially Principles I, II, III), whether the artifact
   implements its claimed FR/US without inventing scope, whether it's within
   the task's stated boundary, and — if it's a new CI gate — whether it's
   actually required by an FR and ships with a positive control, not just a
   stub test (see `peer-reviewer.md`/`spec-guardian.md`'s explicit checks for
   this, added after the coverage-gate work found a gate that shipped
   without one).

   Expect APPROVE or BLOCK with numbered, file:line findings. **BLOCK is not
   optional to address** — CLAUDE.md's protocol is blocking, not advisory.

3. **Address every spec-guardian finding**, even the ones that feel minor
   (stale comments, wording). This repo's own history shows small
   inconsistencies compound: a BLOCK on "the usage string doesn't list the
   new stage" is exactly the kind of thing that makes the next person's
   review slower. Re-run spec-guardian if the findings were substantial
   enough that a second pass could catch something new — use judgment, but
   default to re-running rather than assuming your fix was complete.

4. **Dispatch `peer-reviewer`** ONLY after spec-guardian approves (not in
   parallel — the protocol is sequential for a reason: an adversarial review
   of scope-creepy or non-conformant code wastes effort on things
   spec-guardian would have blocked anyway). Give it the SAME diff/files,
   framed adversarially: "assume this is wrong and try to prove it." Its
   five priorities (spec.md edge cases, idempotency, test adequacy vs
   "did you test the mock", resource realism, 11pm operational clarity) are
   fixed by its charter — don't narrow the brief.

   Expect APPROVE or BLOCK with severity-ranked, file:line findings and a
   concrete failing scenario for each critical/major one.

5. **Address every critical/major finding.** For each: either fix it, or
   have a stated reason it's out of scope / not a real issue, recorded in a
   decision doc if the reasoning is non-obvious (see the `new-decision-doc`
   skill). Silently dropping a finding because it's inconvenient is the
   failure mode this whole protocol exists to prevent.

6. **Consider a mutation check** for any fix that changes control-flow logic
   (not just wording): temporarily revert the fix and confirm the new test
   actually catches the reverted bug. This isn't in CLAUDE.md explicitly, but
   it's how every fix in this repo's coverage-gate work was actually
   verified, and a test that can't fail against the bug it claims to catch
   is worth exactly as much as no test.

7. **Only now check the task off** in `tasks.md`, and record in the PR
   description (per CLAUDE.md's working agreement) which task IDs were
   touched and what each reviewer's verdict was — APPROVE outright, or
   BLOCK-then-fixed with a one-line summary of what changed. This repo's PRs
   have followed this pattern; keep doing it, since it's what lets a human
   skim a PR and trust the review actually happened rather than re-deriving
   it from the diff.

## When findings disagree or are ambiguous

Per CLAUDE.md: "if you are uncertain whether something violates Principle
II, BLOCK and ask the operator" — that instruction is `spec-guardian`'s, but
the spirit applies to you as the orchestrator too. If spec-guardian and
peer-reviewer findings conflict, or a finding requires an architectural
judgment call rather than a mechanical fix, surface it rather than picking a
side silently.

## What this skill does NOT replace

Cross-agent consultation for handoff contracts (e.g. pipeline-engineer
asking infra-scaffolder for the Argo submit contract) still needs an
explicit handoff note in the PR description per CLAUDE.md — this skill
covers the review sequence, not inter-agent design negotiation.
