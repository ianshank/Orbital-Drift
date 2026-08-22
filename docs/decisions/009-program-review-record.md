# D-009: Program peer-review record — Phase-0 completion program (RB-007)

**Status:** recorded 2026-08-22 by the orchestrating session that drafted RB-007, as the disposition of the adversarial-reviewer's finding F-1 on the RB-007 process batch ("F-9b is a dangling citation"). This is an **evidence record, not a decision**: it makes the program peer-review findings that RB-007a cites durable and checkable. Audience: reviewers and the operator verifying RB-007/RB-007a's citations, and whoever executes the follow-ups below.

**Decision-ID namespace:** independent of `plan.md`'s `D-01…D-05` and every other `docs/decisions/` series. Cross-references should read `D-009/D-nn`. **Numbered `009`, not `008`:** `008` is claimed by the concurrent `task/T004a` branch (`docs/decisions/008-config-v3-deployment.md`, in review at recording time). Skipping it here is deliberate — the 003/004/005 cross-branch numbering collision this repo already paid for is avoided by yielding the number to the branch that claimed it first.

---

## D-009/01 — What was reviewed, and when

On 2026-08-22, before anything was presented to the operator, the sequenced Phase-0 completion program (workstreams A/feature, B/operator, C/process — later authorized as RB-007) was drafted by a planning agent and then adversarially peer-reviewed in the same session. The reviewer returned a BLOCK with 4 Major and 6 Minor findings; all were incorporated into the revised program; the operator approved the revised program in-session; RB-007 and RB-007a then recorded the authorization in `docs/decision-log.md`.

## D-009/02 — The Major findings (all incorporated before operator approval)

1. **F-1 (Major):** T004a and T001b appeared in no logged unlock entry — G-0 enumerates T002, T004, T006–T011 by ID, and both tasks postdate that list. → became RB-007(a), the unlock extension.
2. **F-2 (Major):** the program's "exactly 4 feature PRs" budget claim silently reset DEC-002's counter with no stated baseline, and carried no hour estimates. → became RB-007(b), the budget baseline at RB-006.
3. **F-3 (Major):** the draft's "author T006 provisionally now — deferral buys nothing" rationale was false: only the GPU UUIDs of T006's four hardware couplings are parameterizable; the DCGM counters and the chart-pin-vs-driver-610 viability shape the artifact's content and resolve only at T003. → T006 authoring deferred until G-1 exists (recorded in RB-007 and tasks.md).
4. **F-4 (Major):** D-008's ratification was sequenced after T005, but runbook 01's Step-12 escalation path is exercised *during* T005. → D-008 ratification made an explicit precondition of the T005 handoff.

## D-009/03 — F-9b verbatim (the finding RB-007a cites)

The reviewer's Minor finding F-9 read, in relevant part:

> **Two explicitly flagged follow-ups are omitted.** … (b) D-002's stale helm-provider block-syntax example, flagged for correction in `docs/decisions/005-t010-argo-workflows.md:42` ("should be corrected there when that document is next reachable") — not named in C3's scope. | Fold both into A2/C3 respectively.

The revised program folded the D-002/D-03 correction into C3's scope accordingly; the operator approved that revised program. RB-007a's statement that "the correction was a named item of the approved program (F-9b)" refers to this finding.

## Follow-ups

| # | Item | Owner / trigger |
|---|---|---|
| 1 | **Content-review pass for docs 002 (post-APPROVE D-09/D-12 additions), 003, 004, 005** — the reviews their status headers queue "under RB-007". Dispatch spec-guardian → adversarial-reviewer over each document's own content; update each header with the citation on APPROVE. | Dedicated process PR under RB-007(c)'s reconciliation scope; before or alongside the RB-007(b) owner review at the G-2 boundary. |
| 2 | **RB-007a taxonomy question** — rule 2 assigns scope corrections a new RB number; RB-007a reads as a transcription-correction execution record. The operator decides at PR #5 review; if read as a scope amendment, add a follow-up log line (never rewrite the logged one). | Operator, at PR #5 merge. |
| 3 | **Decision-log chronological ordering** — the 08-22 entries sit above RB-006 (08-21); no ordering rule exists, but "last line = latest" now reads false. Both the batch adversarial-reviewer (F-5: "optional one-line reorder in a future process PR; do not do it in this one") and Copilot's PR #5 review flagged it. | Optional one-line reorder in a future process PR. |

## Verified-correct

- The F-9b quotation above is reproduced from the review delivered in-session on 2026-08-22, before RB-007 was logged; the four Major findings match what RB-007's own text records as its (a)/(b) parts and the T006-deferral clause.
- `008` confirmed claimed by `task/T004a` (its D-008 file exists on that branch) at recording time; `docs/decisions/` on this branch holds `000`–`007` + `versions.md`.
