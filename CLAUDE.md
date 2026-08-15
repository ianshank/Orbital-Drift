# CLAUDE.md — Orbital-Drift Orchestration Rules

Read `.specify/memory/constitution.md` before any work. It supersedes this file; this file supersedes agent judgment.

## Prime constraints (restated)
1. **Never touch the live cluster.** No `kubectl apply`, `helm install/upgrade`, `terraform apply`, or any mutating cluster command — in any agent, ever. Allowed: `--dry-run=client`, `helm template`, `terraform validate`, `terraform fmt -check`, `terraform init -backend=false`, `kubeconform`, linters. **`terraform plan` is NOT allowed** — with the helm/kubernetes providers it initializes providers and refreshes state, which requires live cluster credentials that Principle I forbids agents from holding. **Harness-level reality check:** `.claude/settings.json` denies `kubectl`, `argo`, `argocd`, `k3s`, `k9s`, and `kustomize build` in *every* mode — including the `--dry-run=client` and `lint` forms the constitution permits in principle. Local validation is therefore `helm template` + `kubeconform` + `terraform validate` + `yamllint` only. If you need `argo lint` or `kubectl --dry-run=client`, hand off to the operator rather than assuming the deny is a bug. Tasks tagged `[HUMAN]` in tasks.md: stop, present the paired runbook, wait for the operator to confirm completion before proceeding.
2. **No imports from prior harnesses.** Any code or metric ported from ianshank/Agents, Edge-DIT, or langfuse-eval-harness fails review (Constitution II).
3. **Bash usage** (agents that have it): tests, linting, type-checks, local tooling only.

## Subagent roster & delegation map
| Task tag | Agent | Scope |
|---|---|---|
| `[A:infra-scaffolder]` | infra-scaffolder | Helm values, Terraform, K8s manifests, Argo YAML |
| `[A:pipeline-engineer]` | pipeline-engineer | Airflow DAGs, ingest/data modules, their tests |
| `[A:ml-engineer]` | ml-engineer | training/eval/registry/serving code, their tests |
| `[A:drift-engineer]` | drift-engineer | drift metrics/triggers, dashboards, their tests |
| `[A:runbook-writer]` | runbook-writer | runbooks, incident/soak templates, decision docs |
| (every artifact) | spec-guardian | conformance review vs constitution + spec |
| (every artifact) | peer-reviewer | adversarial technical review |

## Collaboration protocol (default ON)
Agents collaborate by default. For every task:
1. Owning agent produces the artifact (tests first where the task says so — observe them fail).
2. `spec-guardian` reviews: constitution violations, spec drift, hardcoded values, scope creep, forbidden imports. Blocking.
3. `peer-reviewer` reviews: correctness, failure modes, edge cases from spec.md, test adequacy. Blocking.
4. Owning agent addresses findings; only then is the task checked off in tasks.md.
Cross-agent consultation is encouraged (e.g., pipeline-engineer asks infra-scaffolder for the Argo submit contract) — do it via explicit handoff notes in the PR description, not silent assumptions.

## Working agreements
- One task (or one `[P]` group) per branch/PR; PR description names task IDs and review outcomes.
- CI (lint, mypy, unit, contract, smoke, gitleaks) must be green; a red gitleaks halts everything.
- Unknowns discovered mid-task: write a short note in `docs/decisions/` and surface to the operator; do not improvise architecture.
- When tasks.md and reality disagree, update tasks.md via PR — the file is the plan of record.
