# Runbook 01 — k3s Install (Single Node, GPU Node Labels, Containerd NVIDIA Runtime)

| Field | Value |
|---|---|
| Task ID | T004 (this document) |
| Paired `[HUMAN]` task | **T005** — "Install k3s on node A per runbook; commit kubeconfig location note (not the kubeconfig) to docs." |
| Owning agent | `runbook-writer` |
| Upstream prerequisite runbook | `docs/runbooks/00-host-prep.md` (T002), executed by T003 |
| Downstream | T006 (GPU Operator Helm values, authored, not applied here), T011 (platform bring-up order runbook), T012 (`terraform apply` — this is where T006's chart is actually installed) |
| Decisions cited | D-000/D-01, D-000/D-02, D-000/D-02b, D-000/D-03, D-000/D-07, D-000/D-10 (`docs/decisions/000-phase0-technical-decisions.md`); version provenance in `docs/decisions/versions.md` |

## Scope

This runbook installs **k3s itself**, applies an informational GPU node label, and documents the containerd/NVIDIA-runtime checkpoint that must be performed once — but **after**, not during, the GPU Operator install (T006, applied at T012). This runbook does **not** install the GPU Operator, the NVIDIA driver, or any Helm chart. It does not touch the live cluster on the agent's behalf — it is executed entirely by the human operator per Constitution I / CLAUDE.md Prime constraint 1.

Two clearly separated phases follow:
- **Phase A — Initial bring-up** (Steps 1–9): performed during T005, on the day k3s first goes up.
- **Phase B — Post-T006 containerd checkpoint** (Steps 10–13): performed **after** the GPU Operator Helm release from T006 is actually applied to the cluster (that happens during T012, per the platform bring-up runbook T011 will produce) — **not** part of initial k3s bring-up. Do not skip ahead to Phase B before T006's chart has been applied; there is nothing to verify yet.

---

## Prerequisites (read before Step 1)

1. **Host prep complete (T002/T003).** NVIDIA driver `610.57.04`, open kernel modules (D-000/D-01), must already be installed and verified on node A via `nvidia-smi`. Confirm T003's runbook verification block (`docs/runbooks/00-host-prep.md`) is filled in and green before continuing. Step 1 below re-checks this immediately before mutating anything.

2. **`.env` populated locally, never committed.** This runbook uses `${NODE_A_LAN_IP}` and `${NODE_A_HOSTNAME}` as shell-variable placeholders throughout, sourced from your local, gitignored `.env` (copied from `.env.example`). Constitution III: this file is public — never substitute the literal IP/hostname into this document, a commit message, or a PR description. Before Step 1, run:
   ```
   set -a; source .env; set +a
   ```
   and confirm both variables are non-empty (`echo "$NODE_A_HOSTNAME"` / `echo "$NODE_A_LAN_IP"` — fine to view locally, just don't paste the output into a shared/committed artifact).

3. **STOP CONDITION — `infra/k3s/config-v3.toml.tmpl` must exist before you proceed past Step 2.**
   > This runbook references `infra/k3s/config-v3.toml.tmpl` as the versioned, checked-in template for any persistent containerd configuration this project needs on k3s (containerd 2.0+; D-000/D-02b), because k3s regenerates `/var/lib/rancher/k3s/agent/etc/containerd/config.toml` on every start and silently discards hand-edits made directly to that file. Check now, before proceeding past Step 2: does `infra/k3s/config-v3.toml.tmpl` exist in your checkout? **If it does not exist yet, STOP.** Do not hand-write containerd config as a substitute, and do not proceed past Step 2 of this runbook. Instead, request `infra/k3s/config-v3.toml.tmpl` from a follow-up `infra-scaffolder` dispatch (it is that agent's artifact per T004's task text and CLAUDE.md's handoff-note protocol, not this runbook's), and resume this runbook once it lands. If it does exist, read its header comment for scope before continuing — this runbook does not modify it.

   Phase A (Steps 1–9) does not itself require the template to be *applied* anywhere — k3s's automatic runtime-handler wiring (D-000/D-02b) does not depend on it under normal operation. The template only becomes load-bearing as an **escalation path** in Phase B (Step 12) if automatic wiring fails after a restart. The stop condition above exists so that escalation path is never blocked on a missing artifact at the moment you actually need it.

4. **You are on node A, not the Windows authoring machine.** Per D-000/D-10, Terraform and this runbook's commands run on node A's own shell (bash), not from a remote box, and node A's own kubeconfig works without `--tls-san`. Confirm you are physically or via SSH on the host identified by `${NODE_A_HOSTNAME}` / `${NODE_A_LAN_IP}` before running any command below.

5. **`sudo` access and outbound HTTPS** to `get.k3s.io` and `github.com/k3s-io/k3s/releases` are required for Step 4's install script to fetch and verify the pinned binary.

---

## Phase A — Initial Bring-up (performed during T005)

### Step 1 — Re-verify GPU visibility immediately before install

**Command:**
```
nvidia-smi -L
```

**Expected output:** two lines, one per card, of the form (per D-000/D-03's documented format — UUIDs below are illustrative, not literal):
```
GPU 0: NVIDIA GeForce RTX 5060 Ti (UUID: GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
GPU 1: NVIDIA GeForce RTX 5060 (UUID: GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
```

**Verification:** exit code `0`; exactly two `GPU N:` lines; both UUIDs begin with the `GPU-` prefix (PCI bus IDs / indices are not acceptable here or anywhere downstream — D-000/D-03).

**Rollback/Abort:** No mutation performed by this step. If the command is not found, returns fewer than two GPUs, or a GPU shows as not-supported: **STOP — do not proceed to Step 2.** Return to `docs/runbooks/00-host-prep.md` (T002) and confirm T003 was actually completed and its verification block is green before re-attempting.

---

### Step 2 — Confirm the `infra/k3s/config-v3.toml.tmpl` prerequisite

**Command:**
```
test -f infra/k3s/config-v3.toml.tmpl && echo PRESENT || echo MISSING
```
(run from the repo root on node A)

**Expected output:** `PRESENT`

**Verification:** literal string `PRESENT` printed.

**Rollback/Abort:** No mutation performed. If output is `MISSING`: **STOP per the Prerequisites section above** — do not proceed to Step 3. File the request to `infra-scaffolder` for the template and pause this runbook until it lands. (You may still complete Step 1 in isolation as a hardware sanity check, but do not start the k3s install in Step 4 until this reads `PRESENT`, since Phase B's escalation path depends on it existing and you do not want to discover that gap mid-incident.)

---

### Step 3 — Load and confirm host-specific placeholders

**Command:**
```
set -a; source .env; set +a
echo "NODE_A_HOSTNAME is set: $([ -n "$NODE_A_HOSTNAME" ] && echo yes || echo no)"
echo "NODE_A_LAN_IP is set: $([ -n "$NODE_A_LAN_IP" ] && echo yes || echo no)"
```

**Expected output:**
```
NODE_A_HOSTNAME is set: yes
NODE_A_LAN_IP is set: yes
```

**Verification:** both lines read `yes`. This step deliberately does not print the values themselves, to keep them out of terminal-scrollback copies that might later get pasted somewhere public.

**Rollback/Abort:** No mutation performed. If either reads `no`: **STOP** — populate `.env` from `.env.example` (`cp .env.example .env`, fill in locally, never commit) before continuing.

---

### Step 4 — Install k3s, pinned to `v1.35.7+k3s1`

**Why this pin, not `v1.36.3+k3s1` ("latest stable"):** GPU Operator `v26.3.3` supports k3s `1.33`–`1.35` and containerd `1.7`–`2.2`. k3s `v1.36.3+k3s1` ships containerd `2.3.2` — outside both supported ranges. k3s `v1.35.7+k3s1` (released 2026-08-04) ships containerd `2.2.5-k3s2` — inside both. Chasing "latest" buys nothing here and moves the component most likely to fail (plan.md risk R-05) outside its tested envelope. Full reasoning: `docs/decisions/000-phase0-technical-decisions.md` D-07. Version provenance: `docs/decisions/versions.md`, "Host / cluster" table.

**Command:**
```
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="v1.35.7+k3s1" sh -s - server \
  --node-name "${NODE_A_HOSTNAME}"
```

**Expected output:** the install script logs its progress to stdout; look for these markers (exact wording may vary slightly by script revision — do not treat this as a byte-for-byte match, that is what Step 5's Verification is for):
```
[INFO]  Using v1.35.7+k3s1 as release
[INFO]  Downloading binary https://github.com/k3s-io/k3s/releases/download/v1.35.7+k3s1/k3s
[INFO]  Verifying binary download
[INFO]  Installing k3s to /usr/local/bin/k3s
[INFO]  systemd: Starting k3s
```
Exit code of the whole pipeline should be `0`.

**Verification:** run Step 5 immediately — do not consider this step done on console output alone; the console text is not the pass/fail gate, it is context for troubleshooting.

**Rollback/Abort:**
- If the script exits non-zero, or Step 5 fails: the server install ships an uninstaller at `/usr/local/bin/k3s-uninstall.sh`. Run:
  ```
  sudo /usr/local/bin/k3s-uninstall.sh
  ```
  Confirm removal: `which k3s` should print nothing and `systemctl status k3s` should report `Unit k3s.service could not be found`. Then re-attempt Step 4 (the install is idempotent — safe to re-run without uninstalling first if the only issue was, e.g., a transient network failure; run the uninstaller first if you suspect a partial/wrong-version install rather than a transient failure).
- Do not proceed to Step 5 on a non-zero exit code.

---

### Step 5 — Verify the k3s service and version

**Command:**
```
sudo systemctl is-active k3s
k3s --version
```

**Expected output:**
```
active
k3s version v1.35.7+k3s1 (<git-sha>)
Go version go<...>
```

**Verification:** first command prints exactly `active`; second command's first line contains `v1.35.7+k3s1`. Both conditions required — a running service on the wrong version is a defect, not a pass.

**Rollback/Abort:** If `systemctl is-active` reports anything other than `active` (e.g. `activating`, `failed`), wait 30s and re-check once (first boot can take a moment to pull embedded images); if still not `active`, run `sudo journalctl -u k3s -n 100 --no-pager` to inspect, then fall back to Step 4's rollback (uninstall, re-install). If the version string does not match `v1.35.7+k3s1`, this is a pin violation (Constitution IV) — uninstall and re-run Step 4, double-checking `INSTALL_K3S_VERSION` was exported/passed correctly.

---

### Step 6 — Confirm containerd is not directly invokable, and `RUNTIME_CONFIG_SOURCE=file` is therefore required

**Context:** this closes the third empirical-verification item in `docs/decisions/000-phase0-technical-decisions.md`'s "Requires empirical verification on the hardware (T003/T005)" section: *"the default `command,file` runs `containerd config dump`, and k3s ships containerd as a subcommand; unverified whether it fails gracefully. `file` is the safe setting."* T006's reference GPU Operator values (same decision doc, `toolkit.env`) already set `RUNTIME_CONFIG_SOURCE=file` on that basis. This step confirms the environmental fact that motivates the override, on this actual host — it does **not**, and cannot, confirm that the toolkit's file-mode write itself succeeds, since the toolkit is not installed until T006/T012. That second half of the verification is what Steps 11/13 below already check (a correctly-wired `nvidia` containerd handler is only possible if the toolkit's file-mode config write worked — a broken write and a wired handler are mutually exclusive outcomes, so no separate "did file-mode work" check exists beyond that one).

**Command:**
```
command -v containerd
```

**Expected output:** no output; non-zero exit — `containerd` is not present as a standalone binary on PATH, only as a subcommand of `k3s`.

**Verification:** exit code non-zero (`echo $?` after the command, or run `command -v containerd; echo "exit: $?"`). Corroborate with:
```
sudo k3s containerd config dump | head -5
```
which should succeed via the `k3s` subcommand path — a differently-invoked mechanism than the bare `containerd config dump` the toolkit's default `command`-mode would attempt against a standalone binary that does not exist here.

**Rollback/Abort:** No mutation performed. If `command -v containerd` unexpectedly **succeeds** (a standalone `containerd` binary is present — e.g. from a prior Docker Engine or containerd install on this host), the risk profile `RUNTIME_CONFIG_SOURCE=file` was chosen to avoid may not apply the way D-000 assumed. Record this deviation in the Verification Block below and flag it explicitly in T006's Helm values PR description as a fact to reconsider before assuming `file` mode is still the correct setting — do not silently proceed as if nothing changed.

---

### Step 7 — Locate and record the kubeconfig path

k3s writes its kubeconfig to `/etc/rancher/k3s/k3s.yaml` by default (root-owned, mode `600`). This runbook does not loosen that permission or copy the file anywhere — all `kubectl`-equivalent commands in this runbook use `sudo k3s kubectl ...`, which reads that file automatically with no `KUBECONFIG` export needed (this is also why D-000/D-10 notes no `--tls-san` is required for a single-node, on-node install).

**Command:**
```
sudo test -f /etc/rancher/k3s/k3s.yaml && echo FOUND
```

**Expected output:** `FOUND`

**Verification:** literal string `FOUND`.

**Rollback/Abort:** No mutation performed. If not found, k3s did not install correctly — return to Step 4's rollback path.

**Record now, in the Verification Block at the end of this document, the local file path you will treat as authoritative** — either the default (`/etc/rancher/k3s/k3s.yaml`) or, if you later copy it to a personal-tooling location (e.g. `~/.kube/config`) for convenience, that path instead. **Record the path only. Never the file's contents, and never commit the file itself** — this satisfies T005's requirement to "commit kubeconfig location note (not the kubeconfig) to docs."

---

### Step 8 — Apply the GPU node label

Context (one sentence, per D-000/D-03): this label is organizational/informational only, not a scheduling mechanism — GPU-aware placement in this project does not use node labels or `nvidia.com/gpu` resource requests, because both physical cards live on this one node and D-000/D-03 instead pins each workload to a specific GPU by UUID via `NVIDIA_VISIBLE_DEVICES`.

**Command:**
```
sudo k3s kubectl label node "${NODE_A_HOSTNAME}" node-role.kubernetes.io/gpu=true --overwrite
```

**Expected output:**
```
node/<value of NODE_A_HOSTNAME> labeled
```

**Verification:** run Step 9 to confirm.

**Rollback/Abort:** Labeling a node is non-destructive and instantly reversible. If applied in error, remove it:
```
sudo k3s kubectl label node "${NODE_A_HOSTNAME}" node-role.kubernetes.io/gpu-
```
(trailing `-` is k8s's remove-label syntax). If the command errors with `NotFound` on the node name, confirm `--node-name` was passed correctly in Step 4 and that `${NODE_A_HOSTNAME}` matches exactly (`sudo k3s kubectl get nodes` to list actual node names).

---

### Step 9 — Verify the GPU node label

**Command:**
```
sudo k3s kubectl get node "${NODE_A_HOSTNAME}" --show-labels | grep -o 'node-role.kubernetes.io/gpu=true'
```

**Expected output:**
```
node-role.kubernetes.io/gpu=true
```

**Verification:** exact string printed; empty output = fail.

**Rollback/Abort:** No mutation performed by this step. If empty, re-run Step 8; if it still fails, run `sudo k3s kubectl get node "${NODE_A_HOSTNAME}" -o yaml | grep -A5 labels:` to inspect the full label set for typos.

---

**Phase A is complete here.** This is the point at which T005 is done from k3s's perspective — a single-node cluster is up, pinned, labeled, and the operator has a recorded kubeconfig location. Do **not** continue to Phase B yet; there is nothing to check until the GPU Operator (T006) has actually been applied, which happens during T012.

---

## Phase B — Post-T006 Containerd Checkpoint (perform *after* GPU Operator install — NOT part of initial k3s bring-up)

> **When to run this phase:** only after the GPU Operator Helm release from T006 has been applied to the cluster (during T012's platform bring-up, per the runbook T011 will produce). If you are reading this immediately after Phase A and T006 has not been applied yet, stop here — there is no toolkit DaemonSet on this node yet, and the checks below will legitimately show "not wired" for a reason that has nothing to do with a k3s defect.

> ⚠ **NRI plugin warning — read this before touching any GPU Operator / containerd runtime setting.**
> Do **not** enable the NRI plugin (`cdi.nriPluginEnabled: true`) for this GPU Operator install, even though NVIDIA's own k3s documentation recommends it as a way to avoid touching containerd's `config.toml`. Enabling it **deletes the `nvidia` RuntimeClass** (verified in GPU Operator source: `clearRuntimeClasses()` → `client.Delete`), which D-000/D-03's UUID-pinning GPU split depends on existing — and because k3s re-stages `runtimes.yaml` on every server start, this produces *flapping*, not a one-time failure. `nriPluginEnabled: false` is already the GPU Operator v26.3.3 chart default (see the reference values block in `docs/decisions/000-phase0-technical-decisions.md`) — this is a warning against changing it, not an action to take. Full reasoning: `docs/decisions/000-phase0-technical-decisions.md` D-02.

### Step 10 — Confirm the toolkit DaemonSet has actually landed on this node

**Command:**
```
sudo k3s kubectl get pods -A -o wide | grep -i nvidia-container-toolkit
```

**Expected output:** at least one pod in `Running` state, scheduled on `${NODE_A_HOSTNAME}`.

**Verification:** a `Running` row present. If empty, the GPU Operator release has not reached this node yet — stop this phase and confirm T012's status before continuing.

**Rollback/Abort:** No mutation performed. If missing, this is a T006/T012 concern, not a k3s defect — do not proceed to Step 11 until it resolves.

### Step 11 — Check whether the `nvidia` runtime handler is wired into containerd

Per D-000/D-02b: k3s scans its runtime-binary PATH **once per k3s start**. If the toolkit DaemonSet's binaries land at `/usr/local/nvidia/toolkit` *after* k3s is already running, the handler will be missing from the generated config until k3s restarts — this is expected on a first-time install, not a bug.

**Command:**
```
grep -A3 'runtimes.*nvidia' /var/lib/rancher/k3s/agent/etc/containerd/config.toml
```

**Expected output (handler wired):** a block resembling:
```
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.nvidia]
  runtime_type = "io.containerd.runc.v2"
  ...
```

**Verification:** `echo $?` immediately after the `grep` — `0` means the pattern was found (wired); `1` means not found (not wired). Do not rely on eyeballing the terminal alone; check the exit code.

**Rollback/Abort:** No mutation performed by this step (it is read-only). If not wired (exit code `1`), proceed to Step 12 — this is the documented, expected recovery path, not an abort condition.

### Step 12 — If not wired: restart k3s and re-verify

**Command:**
```
sudo systemctl restart k3s
```

Wait for the service to report active again (reuse Step 5's `sudo systemctl is-active k3s`, expect `active`), then re-run Step 11's exact grep command.

**Expected output:** Step 11's grep now returns the `runtimes.nvidia` block, exit code `0`.

**Verification:** same as Step 11 — exit code `0` on re-run.

**Rollback/Abort:**
- If the handler is wired after restart: proceed to Step 13. Record "k3s restart required after GPU Operator install: yes" in the Verification Block below.
- If the handler is **still** not wired after one restart: **do not hand-edit `config.toml`** — per D-000/D-02b, k3s regenerates that file on every start and silently discards direct edits. Escalate instead: open a follow-up `infra-scaffolder` dispatch to confirm `infra/k3s/config-v3.toml.tmpl` exists, correctly encodes the `nvidia` containerd runtime stanza, and is deployed to `/var/lib/rancher/k3s/agent/etc/containerd/` on this node. **Note:** the exact mechanism for getting the repo's `infra/k3s/config-v3.toml.tmpl` onto the node's filesystem (manual copy, a provisioning step, or something else) is not yet specified anywhere in this repo as of this runbook's authoring — do not invent one, request it explicitly in the escalation. Do not restart k3s a second time until that gap is resolved, since a second restart without a corrected template will not change the outcome.

### Step 13 — Verify the `nvidia` RuntimeClass exists AND cross-check the handler wiring

Per D-000/D-02b, k3s ships `RuntimeClass/nvidia` as a **static manifest with no conditional logic** — its presence alone is **not** evidence the containerd handler is actually wired. Both must be checked; this step exists specifically to prevent trusting the first alone.

**Command:**
```
sudo k3s kubectl get runtimeclass
```
followed by re-running Step 11's grep once more as the second half of this check:
```
grep -A3 'runtimes.*nvidia' /var/lib/rancher/k3s/agent/etc/containerd/config.toml
```

**Expected output:** `kubectl get runtimeclass` lists a row named `nvidia`; the grep independently returns the `runtimes.nvidia` block (exit code `0`).

**Verification:** **both** conditions must hold simultaneously:
1. `nvidia` row present in `kubectl get runtimeclass` output, and
2. Step 11's grep exit code is `0`.

A `nvidia` RuntimeClass with a grep exit code of `1` is **not verified** — treat it as "not wired" and return to Step 12, not as a pass.

**Rollback/Abort:** No mutation performed by this step. If the RuntimeClass row is absent entirely (unusual — per D-000/D-02b it ships natively and unconditionally with k3s), this indicates a k3s install problem rather than a GPU Operator problem: fall back to Step 4's full uninstall/reinstall rollback path, then repeat Phase A and Phase B in order.

**Optional additional confirmation** (from the D-000 reference verification block, not required to pass this runbook but useful for deeper troubleshooting): `ls -la /var/run/cdi/` should show a generated `nvidia.com-gpu.yaml`; `nvidia-smi -L` should still list both UUIDs unchanged from Step 1.

---

## Verification Block

Complete this during T005 execution (Phase A fields) and again after Phase B is actually performed (post-T006, at T012). This is the artifact T005 refers to when it says "commit kubeconfig location note (not the kubeconfig) to docs" — commit this completed block (or a copy of it) with the change that closes T005.

| Field | Value |
|---|---|
| Date/time executed (UTC) | |
| Operator | |
| k3s version installed (`k3s --version` output) | |
| Node label applied (`node-role.kubernetes.io/gpu=true`)? | yes / no |
| kubeconfig local file path (path only — NEVER contents, NEVER commit the file itself) | |
| — Phase B (complete after T006/T012) — | |
| RuntimeClass `nvidia` present? (`kubectl get runtimeclass`) | yes / no / not yet reached |
| containerd `nvidia` handler wired? (Step 12/13 grep) | yes / no / not yet reached |
| k3s restart required after GPU Operator install? | yes / no / not yet reached |
| Deviations from this runbook (if any) | |

---

## References

- `.specify/memory/constitution.md` — Principle I (operator executes, agents author), Principle III (no hardcoded values), Principle IV (pinned, reproducible)
- `CLAUDE.md` — Prime constraint 1
- `docs/decisions/000-phase0-technical-decisions.md` — D-01 (host driver, no operator-managed driver), D-02 (NRI stays off), D-02b (containerd wiring/restart hazard, `config-v3.toml.tmpl`), D-03 (UUID pinning — why no GPU resource quotas here), D-07 (k3s version pin reasoning), D-10 (Terraform/runbooks run on node A)
- `docs/decisions/versions.md` — "Host / cluster" table, k3s version provenance
- `.env.example` — `NODE_A_LAN_IP`, `NODE_A_HOSTNAME`
- `specs/001-orbital-drift-ct/tasks.md` — T004, T005, T006, T011, T012, and the Dependencies summary
- `specs/001-orbital-drift-ct/plan.md` — Project Structure (`infra/k3s/`), Technical Context (dual-GPU node A)
