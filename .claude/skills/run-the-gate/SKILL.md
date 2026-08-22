---
name: run-the-gate
description: Run Orbital-Drift's CI gates correctly on this machine. Use whenever asked to run the gates, check if the tree is green, run pre-pr, or diagnose a red stage. Encodes the interpreter, CA-bundle and Docker prerequisites that a plain `sh ci/checks.sh all` gets wrong here.
---

# Running the gates

`ci/checks.sh` is the canonical runner for all fourteen stages (design D1).
`make` is **not installed on this box**, so call it directly — design D12
accepted that as the normal path.

## The command

```sh
PYTHON="$PWD/.venv/Scripts/python.exe" \
REQUESTS_CA_BUNDLE="C:/Users/iansh/.ca-bundle/ca-bundle.pem" \
sh ci/checks.sh all
```

Both variables are load-bearing here:

- **`PYTHON`** — the system interpreter is 3.11; `ci/versions.env` pins 3.12
  and the preflight refuses to run a gate on the wrong one (correctly). Without
  this you get a wall of text about the interpreter, not about your change.
- **`REQUESTS_CA_BUNDLE`** — Norton intercepts TLS on this machine, so
  `pip-audit` (the `audit` stage) fails certificate verification against
  pypi.org. The bundle is certifi + the Norton root. `SSL_CERT_FILE` does
  **not** work: `requests` does not read it.

Single stage: replace `all` with the stage name. Run `sh ci/checks.sh` with an
unknown argument to print the current stage list (it is generated from
`STAGE_LABELS`, so it cannot go stale).

## Check Docker FIRST

Four stages (`unit`, `gitleaks`, `hooks`, `coverage`) need a running daemon:

```sh
docker info >/dev/null 2>&1 && echo up || echo DOWN
```

If it is down, **start Docker Desktop and wait** rather than reading the
failures. A stopped daemon produces eight red positive-control failures in a
stage named `unit` and one in a stage named `gitleaks`; the script says so
explicitly, but the instinct to read them as "the secrets gate is broken" is
what the diagnosis exists to prevent.

Known local failure mode: if the daemon never comes up and
`%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log` shows
`remove ...: The file cannot be accessed`, rename
`%LOCALAPPDATA%\Docker\run` and `%LOCALAPPDATA%\docker-secrets-engine` and
relaunch — stale AF_UNIX socket files block startup.

## After a failed run, check the tree

The `hooks` stage runs pre-commit, which **rewrites files**
(end-of-file-fixer, trailing-whitespace, ruff format). A failed `all` can
therefore leave the tree dirty, and a second run can pass because the first one
fixed things. Always `git status` after a red run and look at what changed.

## Reading a red stage

| Stage | First thing to check |
|---|---|
| `lint` / `typecheck` / `dead` | Real finding. Fix the code (`dead` = vulture; a false positive needs a `# noqa`-equivalent, not a scope change to `[tool.vulture]`). |
| `unit` / `hooks` / `gitleaks` / `coverage` | Is the daemon up? (above) |
| `contract` / `smoke` | Declared-empty until T013/T020 land a test module — a red here before then means the suite directory itself is missing, not a test failure. |
| `audit` | Network/TLS (the CA bundle) before assuming a real CVE. It queries PyPI live, so it can go red with an unchanged tree. |
| `coverage` | Two bars, both pinned in `ci/versions.env`: the global floor (`COVERAGE_MIN_PERCENT`), then `orbital_drift.covcheck`'s per-file floor (`COVERAGE_PER_FILE_MIN_PERCENT`, passed as `--floor`), run only after the first passes. The second names the module. |
| `specs` | A change package is missing proposal/design/tasks, or a requirement has no WHEN/THEN scenario. |
| `traceability` | A `Green` row cites a pytest node id that does not collect — or collection itself failed, which the linter now reports separately. |
| `projections` | `planning/` was hand-edited. Regenerate: `python -m orbital_drift.projections --write`. |
| `governance` | A guard or meta-test regressed. This is the stage that means "the process broke", not "the product broke". |

## Probing the guard without make

```sh
bash scripts/guard_probe.sh 'kubectl apply -f x.yaml'   # -> verdict: BLOCK
GUARD_DEBUG=1 bash scripts/guard_probe.sh 'a && $(b)'   # traces each segment
```
