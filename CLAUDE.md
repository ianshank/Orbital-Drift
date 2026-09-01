# CLAUDE.md — Orbital-Drift Orchestration Rules

Read `.specify/memory/constitution.md` before any work. It supersedes this file; this file supersedes agent judgment.

## Prime constraints (restated)
1. **Never touch the live cluster.** No `kubectl apply`, `helm install/upgrade`, `terraform apply`, or any mutating cluster command — in any agent, ever. Allowed: `--dry-run=client`, `helm template`, `terraform validate`, `terraform fmt -check`, `terraform init -backend=false`, `kubeconform`, linters. **`terraform plan` is NOT allowed** — with the helm/kubernetes providers it initializes providers and refreshes state, which requires live cluster credentials that Principle I forbids agents from holding. **Harness-level reality check:** `.claude/settings.json` denies `kubectl`, `argo`, `argocd`, `k3s`, `k9s`, and `kustomize build` in *every* mode — including the `--dry-run=client` and `lint` forms the constitution permits in principle. Local validation is therefore `helm template` + `kubeconform` + `terraform validate` + `yamllint` only. If you need `argo lint` or `kubectl --dry-run=client`, hand off to the operator rather than assuming the deny is a bug. Tasks tagged `[HUMAN]` in tasks.md: stop, present the paired runbook, wait for the operator to confirm completion before proceeding.
2. **No imports from prior harnesses.** Any code, metric, or eval-harness logic ported from ianshank/Agents, Edge-DIT, or langfuse-eval-harness fails review (Constitution II). Governance and process artifacts (charters, decision-log formats, review protocols, CI/hook/guard patterns, planning templates) from those sources are permitted per the Principle II amendment (Constitution v1.1.0).
3. **Bash usage** (agents that have it): tests, linting, type-checks, local tooling only.

## Subagent roster & delegation map
| Task tag | Agent | Scope |
|---|---|---|
| `[A:infra-scaffolder]` | infra-scaffolder | Helm values, Terraform, K8s manifests, Argo YAML |
| `[A:pipeline-engineer]` | pipeline-engineer | Airflow DAGs, ingest/data modules, their tests |
| `[A:ml-engineer]` | ml-engineer | training/eval/registry/serving code, their tests |
| `[A:drift-engineer]` | drift-engineer | drift metrics/triggers, dashboards, their tests |
| `[A:runbook-writer]` | runbook-writer | runbooks, incident/soak templates, decision docs |
| `[A:mlops-ct-agent]` | mlops-ct-agent | automated continuous training loops, shadow eval, canary promotion |
| `[A:gpu-qa-agent]` | gpu-qa-agent | dual-GPU profiling, AMP precision verification, VRAM leak analysis |
| (untagged + governance tasks) | spec-implementer | default implementer; its TDD protocol binds ALL implementers |
| (every artifact) | spec-guardian | conformance review vs constitution + spec |
| (every artifact) | adversarial-reviewer | adversarial technical review (supersedes peer-reviewer, adopt-governance-kit D5) |

`src/orbital_drift/{observability,domain,ports,quality}/` (added by PR#17, "Phase 0-R";
`eval/` too, though its scope already overlapped ml-engineer's) carry no owning-subagent
delegation tag anywhere in `specs/001-orbital-drift-ct/tasks.md` — no task ID targets
them (RB-010, `docs/decision-log.md`, 2026-09-01). They fall under spec-implementer's
default ("untagged + governance tasks") ownership per the roster row above; this line
makes that explicit rather than assumed, since their absence from the roster table was
previously silent.

## Collaboration protocol (default ON)
Agents collaborate by default. For every task:
0. Consult the `orbital-drift-governance` skill's gate table and `docs/decision-log.md`; an unsatisfied gate is a STOP, not a judgment call.
1. Owning agent produces the artifact following spec-implementer's TDD protocol (failing test first — observe it fail for the right reason).
2. `spec-guardian` reviews: constitution violations, spec drift, hardcoded values, scope creep, forbidden imports. Blocking.
3. `adversarial-reviewer` reviews: correctness, failure modes, edge cases from spec.md, test adequacy, charter constraints, gate/budget accounting. Blocking; max 2 fix cycles, a third recurrence of the same Major escalates to the operator (charter R-5).
4. Owning agent addresses findings; updates `traceability/REQUIREMENT-TRACEABILITY.md` if the requirement mapping moved; only then is the task checked off in tasks.md.
Cross-agent consultation is encouraged (e.g., pipeline-engineer asks infra-scaffolder for the Argo submit contract) — do it via explicit handoff notes in the PR description, not silent assumptions.

## Working agreements
- One task (or one `[P]` group) per branch/PR; PR description names task IDs and review outcomes.
- CI (lint, mypy, unit, contract, smoke, gitleaks + the adopt-governance-kit stages) must be green; a red gitleaks halts everything. Run `make pre-pr` (or `sh ci/checks.sh all`) before every PR — checks.sh is the single gate source (design D1).
- Unknowns discovered mid-task: write a short note in `docs/decisions/` and surface to the operator; do not improvise architecture.
- When tasks.md and reality disagree, update tasks.md via PR — the file is the plan of record.
