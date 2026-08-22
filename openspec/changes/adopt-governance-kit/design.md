# Design — adopt-governance-kit

<!-- openspec/changes/adopt-governance-kit/design.md
     Read BEFORE tasks.md (charter working agreement). -->

> Each design decision gets an ID (D1, D2, …) so tasks, specs, and the traceability
> matrix can cite it. Decisions requiring owner confirmation also appear in charter §5 as
> DEC-00x; the text here is the *default proposal* until the decision-log entry exists.
> Rejected alternatives are recorded with one line of why.

## D1 — `ci/checks.sh` stays the canonical gate runner; the Makefile is a thin front-end
**Status:** decided via DEC-001 on 2026-08-20.
**Design:** every Makefile gate target is exactly `sh $(ROOT)/ci/checks.sh <stage>`; new
gates are new checks.sh stages so the existing parametrized meta-tests police them. A
Makefile gate target lands only in the same PR as its stage — no dangling targets. The
governance meta-test asserts the mapping in this direction only: every existing gate
target maps to a dispatch label and never reconstructs a gate inline.
**Rationale:** ~4,900 lines of harness tests assert checks.sh's dispatch/preflight
architecture and that CI calls `sh ci/checks.sh <stage>`; inverting to "CI calls make"
rewrites them for zero governance gain. "One harness, invoked identically everywhere" is
satisfied with checks.sh as the one harness.
**Rejected:** adopt the template Makefile as the canonical runner — regresses a stronger,
tested harness.

## D2 — Keep pip; drop uv
**Status:** decided via DEC-001.
**Design:** `make install` = `python -m pip install -e ".[dev]"` + hook install. The
SessionStart hook is a version *check* against `ci/versions.env` (warn + exit 0), never
an install.
**Rationale:** Constitution IV, README, CI bootstrap, and `test_version_pins.py` all
encode the single pip command path.
**Rejected:** adopt uv — forces a Principle IV amendment plus CI/test rewrites; a
session-start `pip install` — slow and silently mutating.

## D3 — New gates land as new checks.sh stages
**Status:** decided via DEC-001.
**Design:** stages `dead` (vulture), `audit` (pip-audit), `specs` (structural spec
validator), `cov` (coverage floor), `traceability` (matrix linter), `projections`
(byte-drift check). Full stage recipe each time: `stage_x()` function, preflight pin
claim incl. the `all)` branch, dispatch case, `stage_all`, `STAGE_LABELS`, ci.yml matrix
entry. `stage_traceability` claims the `pytest` pin (it shells out for `--collect-only`).
**Rejected:** make-native gate logic — splits the single harness.

## D4 — Gitleaks: reuse the docker digest-pinned stage; no second scanner
**Status:** decided via DEC-001.
**Design:** `make secrets` = `sh ci/checks.sh gitleaks`; no root `.gitleaks.toml`;
template `secrets-install` dropped.
**Rejected:** template's go-install two-pass gitleaks — weaker pinning, needs a Go
toolchain.

## D5 — Agent roster: add spec-implementer; adversarial-reviewer supersedes peer-reviewer
**Status:** decided via DEC-001.
**Design:** review chain everywhere: owning agent → spec-guardian → adversarial-reviewer.
spec-implementer is the default implementer for governance/untagged tasks; its TDD
protocol binds all implementers via CLAUDE.md. All live references updated (CLAUDE.md,
tasks.md legend, ml-engineer.md); historical provenance comments left untouched.
**Rejected:** four overlapping reviewer/implementer agents — duplicate, conflicting
verdicts; renaming adversarial-reviewer to keep the old name — full adoption chose the
kit's protocol, and stale text would misstate the fix-cycle cap.

## D6 — Spec-format coexistence
**Status:** decided via DEC-001.
**Design:** Spec-Kit (`specs/001-orbital-drift-ct/`) remains the plan of record for the
feature. OpenSpec change packages (`openspec/changes/<id>/`) govern changes to the
system or its governance from now on; this import is the first such package.
**Rejected:** migrating the feature spec to OpenSpec — churns a working 52-task plan
mid-flight.

## D7 — Decision-log unification
**Status:** decided via DEC-001.
**Design:** `docs/decisions/` keeps ADRs (`D-nn` namespace, rationale documents).
`docs/decision-log.md` is the mechanical gate ledger; IDs restricted to `DEC-00x`,
`RB-xxx[a]`, `G-x` — prefix-disjoint from `D-nn`. Log line decides; ADR explains.
**Rejected:** a third parallel decision series — the repo already survived one namespace
collision (see docs/decisions/000).

## D8 — Charter subordinate to the constitution
**Status:** decided via DEC-001.
**Design:** charter constraints C-1…C-6 are the mechanized form of principles, each
naming its principle and enforcing hook/stage. Milestone rows cite plan.md phase gates
by reference, never restate them. Scope stays owned by the tasks.md files.
**Rejected:** restating exit criteria in the charter — a second unpoliced copy drifts.

## D9 — Projections generated from a dataclass module
**Status:** default proposal confirmed via DEC-001.
**Design:** `src/orbital_drift/planning/roadmap_data.py` (frozen dataclasses) emits
`planning/roadmap.md` + `planning/jira-import.csv`; CI byte-drift check; a
trace-consistency test asserts every story trace cites task IDs that exist in
`specs/001-orbital-drift-ct/tasks.md` and story status matches checkbox state.
**Rejected:** parsing tasks.md directly as the source — brittle markdown parsing; the
consistency test gives the same cannot-disagree property.

## D10 — Zero-skip guard with a hard-coded capability allowance
**Status:** decided via DEC-001.
**Design:** escalate-only conftest; skips whose reason begins with the literal
`capability-guard:` are permitted. Exactly two sites qualify:
`tests/unit/test_checks_sh_behaviour.py` (iconv guard) and
`tests/unit/test_gitleaks_positive_control.py` (docker/git/sh off PATH locally, never in
CI). An enumeration test pins the call-site list. The gitkeep logic-skip in
`test_repo_structure.py` is restructured as a single always-asserting statement — not an
early `return`, which the harness's own doctrine flags as a silent-pass hazard.
**Rejected:** verbatim adoption (reddens every docker-less local run); green
early-returns for capability guards (silently weakens them).

## D11 — Office documents not imported
**Status:** decided via DEC-001.
**Design:** the tracker `.xlsx` and overview `.docx` stay external; cited by filename in
the charter's Source-documents line.
**Rejected:** committing them — binary, hand-maintained, and full of the donor project's
live data.

## D12 — Windows/LF discipline
**Status:** decided via DEC-001.
**Design:** all new shell scripts LF (enforced by `.gitattributes`), shellcheck-clean via
the pinned container; `Makefile text eol=lf` added to `.gitattributes`; documented
Windows fallback is `sh ci/checks.sh <stage>` directly; Linux CI is authoritative.
**Rejected:** requiring GNU make locally — every target delegates, so make adds nothing
on a box without it.

## D13 — The `specs` gate is deterministic
**Status:** decided via DEC-001.
**Design:** `ci/validate_specs.sh` is a structural validator only (change-dir
completeness; every `### Requirement:` has ≥1 `#### Scenario:` with **WHEN** and
**THEN**); identical behavior locally and in CI. Adopting the OpenSpec Node CLI later is
its own decision with a pinned install in the matrix job.
**Rejected:** "strict CLI if present, fallback otherwise" — environment-conditional gate
strength is exactly the green-locally/red-in-CI divergence this harness forbids.

## D14 — Template CI workflow not adopted; repo-privacy job dropped
**Status:** decided via DEC-001.
**Design:** `.github/workflows/ci.yml` stays the thin caller; new stages enter its
matrix. The template's repo-privacy assertion job is inapplicable — this repo is
deliberately public.
**Rejected:** wholesale template workflow — duplicates existing SHA-pinned, tested
wiring.
