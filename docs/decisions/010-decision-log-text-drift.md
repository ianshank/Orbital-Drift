# D-010: entry-text drift in `docs/decision-log.md` is unmechanized — the gap, why the obvious fixes do not work, and what closing it would cost

**Status:** authored 2026-08-22 alongside `tests/governance/test_governance_meta.py` (PR #11, RB-008 part 2). Recorded per CLAUDE.md working agreements — *"Unknowns discovered mid-task: write a short note in `docs/decisions/` and surface to the operator; do not improvise architecture."* Raised by `spec-guardian` review of commit `e13dda7`, which ruled that recording this gap in a test docstring alone was insufficient. **Proposes nothing for execution**: closing the gap needs its own `RB` entry first, for the reason in D-010/03.

**Decision-ID namespace:** independent of `plan.md`'s `D-01…D-05` and of `000-phase0-technical-decisions.md`'s series. Cross-references read `D-010/nn`. `010` confirmed free by listing `docs/decisions/` before authoring (`000`–`009` exist).

---

## D-010/01 — The gap

`docs/decision-log.md` is the authorization substrate for this repo. Gates presence-check IDs in it; `RB-007` keeps T013+ locked, `RB-008` declares "no gate bar VALUE changes", `DEC-004` sets the coverage floor. The log's own rules say **"change not one character of any entry's text — the text is the decision."**

As of PR #11 the mechanized invariants over the log and its governance-skill mirror are:

| property | mechanized? | by |
|---|---|---|
| every logged ID appears in the skill's list | yes | `test_skill_decision_section_is_fresh` |
| the two lists agree in **order** | yes | `test_the_skill_decision_summary_lists_decisions_in_the_logs_order` |
| the two lists agree on each bullet's **date** | yes (new, this PR) | same test, `(id, MM-DD)` pairs |
| entries are in **chronological** order | yes | `test_decision_log_entries_are_in_chronological_order` |
| an entry's **TEXT** is unchanged after logging | **no** | review only |

So an edit to `RB-007(b)`'s budget arithmetic, or to `RB-008`'s "no gate bar VALUE changes" limit, or to any entry's stated `EXPLICIT LIMIT`, passes every gate. That is the one rule among its neighbours with nothing behind it.

## D-010/02 — Two obvious mechanizations, and why neither works

**Compare the skill bullet's text against the log entry's text.** Rejected: not decidable. The bullet is a one-line summary of a paragraph-long entry — a paraphrase *by design*. Any check strict enough to catch a meaning change would reject every legitimate summary; any check loose enough to accept summaries catches nothing. This is not a tuning problem.

**Forbid edits to the log in review.** Already the rule, and already the thing that failed: it is exactly what "review only" means in the table above.

## D-010/03 — What would work, and why it is NOT proposed for execution here

An **immutability manifest**: a checked-in table of `(entry_id, sha256(entry line))`, appended to when an entry is added, asserted by a governance test. Deterministic, no false positives, and a failure names precisely which decision was rewritten.

It is not proposed here because **it changes who may edit what**, which is a governance policy decision and not hygiene. Under a manifest, a *legitimate* correction to a logged entry — a typo, a broken cross-reference, the kind of fix RB-007a and RB-008a already made to their own predecessors' framing — would require a manifest update **plus** an authorizing log entry. That is a real cost imposed on a real workflow, and the repo has not decided it wants to pay it.

Per the `log-decision` skill's rule 4 ("decide, then execute"), that decision belongs in an `RB` entry logged **before** any manifest lands.

## D-010/04 — Owner, so this cannot become permanent by silence

Owner: **its own follow-up PR under RB-008**, to be authorized by a new `RB` entry per D-010/03. It is carried by neither the gate-integrity nor the branch-coverage PR.

This mirrors the two deferrals RB-008a already records with named owners — clause (b), `REPO_ROOT`/`_relative` single-homing, and clause (d), the `ci/*.sh` errexit-sweep handoff. Recording this one the same way is what the `spec-guardian` review required; the asymmetry with those two was the finding.

## D-010/05 — Verified-correct at authoring

- The date half of mirror drift **is** now closed: mutating a skill bullet's date from `08-22` to `08-19` was invisible before commit `e13dda7` and reddens after it. Only the text half remains.
- `docs/*` is already on `PUBLIC_CANDIDATE_ALLOWLIST` (`tests/governance/test_governance_meta.py`), and no test enumerates `docs/decisions/` filenames, so adding this file reddens no gate.
- This document is a `D-nn` artifact. Per decision-log rule 3 the `D-nn` and `RB-nn`/`DEC-nn`/`G-n` namespaces are deliberately disjoint, so this file needs **no** decision-log line and **no** governance-skill bullet — it does not trip the two-file rule.
