# governance-harness — Spec Delta

<!-- openspec/changes/adopt-governance-kit/specs/governance-harness/spec.md
     Validate with `make specs` after every edit (structural validator, design D13). -->

> Requirements use SHALL. Scenarios use WHEN/THEN and are the TEST ORACLE: the
> spec-implementer writes the failing test directly from the scenario, and the
> adversarial reviewer maps every diff behavior back to one. Behavior with no governing
> scenario is spec gap or scope creep. Every failure mode carries fail-closed scenarios
> in both directions.

## ADDED Requirements

### Requirement: PreToolUse guard blocks cluster-mutating and unauthorized-push commands
The guard script SHALL block any shell command segment that mutates the cluster or
pushes to a non-allowlisted remote, per charter C-1 and C-5 (Constitution I).

#### Scenario: Cluster-mutating segment blocked
- **WHEN** a Bash command contains a segment invoking `kubectl`, `argo`, `argocd`,
  `k3s`, `k9s`, `kustomize build`, a mutating `helm` verb, or a mutating `terraform`
  verb (including `terraform plan`) — even chained after benign segments
  (`git commit && kubectl delete pod x`) or as bare `argo`
- **THEN** the guard SHALL exit 2 with a `BLOCKED (C-1)` reason naming the offending
  segment — never allow it through because an earlier segment was benign

#### Scenario: Ordinary command allowed
- **WHEN** a Bash command contains only benign segments (`pytest`, `git status`,
  `helm template ./chart`, `terraform validate`)
- **THEN** the guard SHALL exit 0 and emit nothing that blocks the tool call

#### Scenario: Push to non-allowlisted remote refused
- **WHEN** a `git push` segment targets a remote not present in
  `.claude/allowed-remotes.txt`
- **THEN** the guard SHALL exit 2 with a `BLOCKED (C-5)` reason — never fall back to
  allowing the push because the allowlist file is missing or unreadable (that state
  SHALL also block)

#### Scenario: Unanalyzable dangerous input fails closed
- **WHEN** the hook payload cannot be parsed but its raw text matches a dangerous token
- **THEN** the guard SHALL exit 2 — never treat a parse failure as permission

#### Scenario: Command too large to segment fails closed
- **WHEN** a Bash command carries more command substitutions than the segmenter's work
  ceiling allows, so the split ends with substitutions still queued and the returned
  segment list is incomplete — empty, or missing the part that holds the real command
- **THEN** the guard SHALL exit 2 with a `BLOCKED (C-1)` reason naming the ceiling and
  its refusal to analyze — never read a short or empty segment list as "nothing to
  object to", and never condition the verdict on what the unread part turns out to say
  (a benign command of that shape SHALL also block)

#### Scenario: Command within the ceiling is judged on its contents
- **WHEN** a Bash command's substitutions are within the segmenter's work ceiling, so
  the split completes
- **THEN** the guard SHALL judge it on its segments as usual — blocking a denied verb
  with a reason naming that verb, and allowing a benign command; the ceiling SHALL NOT
  become a blanket refusal of every nested command

### Requirement: Zero-skip test gate
The test suite SHALL treat any skipped, xfailed, or xpassed test as a gate failure,
except skips whose reason begins with the literal `capability-guard:` (design D10).

#### Scenario: Skip escalates to failure
- **WHEN** a pytest session finishes green but contains a skip whose reason lacks the
  `capability-guard:` prefix
- **THEN** the session exit status SHALL become non-zero and the report SHALL name the
  skipped test — never report success

#### Scenario: Failing run passes through unmodified
- **WHEN** a pytest session already exits non-zero
- **THEN** the guard SHALL NOT alter the exit status or suppress the original failures

#### Scenario: Capability-guard allowance is enumerated
- **WHEN** a new `capability-guard:` skip call site appears outside the enumerated list
- **THEN** the enumeration test SHALL fail — the allowance never grows silently

### Requirement: Generated planning projections cannot drift
`planning/roadmap.md` and `planning/jira-import.csv` SHALL be byte-identical to the
output generated from `src/orbital_drift/planning/roadmap_data.py` (design D9).

#### Scenario: Hand-edit fails the gate
- **WHEN** a committed projection file differs by one or more bytes from regenerated
  output
- **THEN** the `projections` stage SHALL exit non-zero naming the drifted file — never
  regenerate silently during a check

#### Scenario: Trace cites a nonexistent task
- **WHEN** a story's trace cites a task ID absent from
  `specs/001-orbital-drift-ct/tasks.md`, or its status contradicts the checkbox state
- **THEN** the consistency test SHALL fail naming the story and the missing/contradicted
  task ID

> **PARTIALLY IMPLEMENTED — recorded 2026-09-05 under RB-012, owner T064.** The
> task-ID half is implemented and mutation-checked in both directions
> (`tests/unit/test_projections.py`). The **status half is implemented by nothing**:
> `Story` (`src/orbital_drift/planning/roadmap_data.py`) carries no status field, so
> "its status contradicts the checkbox state" can never fail. `roadmap_data.py` and
> the test module both *claimed* it was checked; RB-012 removed that false claim, and
> this note exists so removing the claim does not quietly turn a specified requirement
> into an invisible gap — the failure mode `RB-008a(e)` documented. The scenario is
> deliberately NOT weakened to match the implementation; T064 closes it.

### Requirement: Traceability matrix is linted
`traceability/REQUIREMENT-TRACEABILITY.md` SHALL use only the fixed status enum, carry
no empty cells, every `Green` row SHALL cite at least one pytest node id that
collects, and the set of `FR-`/`SC-` requirement IDs it carries SHALL equal the set
`specs/001-orbital-drift-ct/spec.md` declares.

The last clause is added under RB-012 (`docs/decision-log.md`), following the T001a and
T001b precedent that a new merge-blocking gate rule needs a requirement rather than an
RB citation alone. The matrix's header has always named `spec.md` as its source of
truth; until RB-012 nothing checked that claim, and the linter read the matrix alone.

#### Scenario: Requirement declared but traced nowhere
- **WHEN** `spec.md` declares an `FR-` or `SC-` requirement that no matrix row carries
- **THEN** the `traceability` stage SHALL exit non-zero naming the requirement ID

#### Scenario: Row cites a requirement the spec never declares
- **WHEN** a matrix row's `FR-`/`SC-` requirement ID is absent from `spec.md`
- **THEN** the `traceability` stage SHALL exit non-zero naming the row
- **AND** rows using other recognised prefixes (`NFR-`, `C-`, `R-`, `DEC-`) SHALL be
  excluded from this comparison, because those are declared in the charter and plan,
  which this linter does not read — a rule no edit to `spec.md` could satisfy

#### Scenario: A declaration the parser cannot see fails closed
- **WHEN** a line in `spec.md` is shaped like a requirement declaration but is not in
  the canonical `- **FR-001** ...` form (indented, a different list marker, inside a
  blockquote, or differently emphasised)
- **THEN** the linter SHALL exit non-zero naming the line — never silently shrink the
  declared set, which would let a requirement go untraced past the gate written to
  catch exactly that

#### Scenario: Green row must collect
- **WHEN** a row is marked `Green` and cites a pytest node id that
  `pytest --collect-only` does not collect
- **THEN** the `traceability` stage SHALL exit non-zero naming the row and the node id

#### Scenario: Unknown status refused
- **WHEN** a row uses a status outside the enum
  (`Planned-gated | In-progress | Green | Uncured-see-owner | N/A-by-design`)
- **THEN** the linter SHALL exit non-zero — never coerce or ignore the value

### Requirement: Spec deltas are structurally valid
Every change package under `openspec/changes/` SHALL contain `proposal.md`, `design.md`,
and `tasks.md`, and every spec delta SHALL contain at least one `### Requirement:` each
carrying at least one `#### Scenario:` with both **WHEN** and **THEN** lines.

#### Scenario: Malformed delta fails validation
- **WHEN** a spec delta contains a `### Requirement:` with no scenario, or a scenario
  missing a **WHEN** or **THEN** line
- **THEN** the `specs` stage SHALL exit non-zero naming the file and the defect

#### Scenario: Validator behaves identically everywhere
- **WHEN** the `specs` stage runs locally and in CI on the same tree
- **THEN** it SHALL apply the same checks and produce the same verdict — gate strength
  SHALL NOT depend on optional tooling being installed (design D13)

### Requirement: Governance skill freshness
The governance skill's "Decisions since" section SHALL mention every decision-log entry
dated on or after the section's own since-date.

#### Scenario: Stale skill fails the build
- **WHEN** `docs/decision-log.md` gains an entry whose ID appears nowhere in the skill's
  "Decisions since" section text
- **THEN** the freshness test SHALL fail naming the missing ID
