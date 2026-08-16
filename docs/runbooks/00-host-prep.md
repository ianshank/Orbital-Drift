# 00 — Host Prep: NVIDIA Driver + CUDA Validation (RTX 50-series / Blackwell)

**Pairs with:** `T002` (this document, authored by `runbook-writer`) / `T003` `[HUMAN]` (executed by the operator).
**Risk addressed:** R-05 — Blackwell driver / GPU-operator mismatch on 50-series (`specs/001-orbital-drift-ct/plan.md`).
**Target host:** node A only (`specs/001-orbital-drift-ct/plan.md` Technical Context — dual-GPU desktop: RTX 5060 Ti 16GB training + RTX 5060 8GB serving/aux).
**Scope:** host-level only. No `kubectl`, `helm`, or `terraform` command appears anywhere in this runbook — k3s is not yet installed at this point in the plan (that's `T004`/`T005`, which run after this). Constitution Principle I is satisfied by construction: nothing here touches a live cluster because no cluster exists yet.
**Explicitly out of scope:** wiring the captured GPU UUIDs into Kubernetes workload manifests (`NVIDIA_VISIBLE_DEVICES` pinning) — that is `T006`/`T010`'s job, built on `docs/decisions/000-phase0-technical-decisions.md` D-03. This runbook's job is narrower: install the correct driver, and *capture* the two UUIDs so they exist in `.env` when `T006`/`T010` need them.

## Prerequisites

- `sudo` access on node A.
- Outbound network egress from node A to `developer.download.nvidia.com`, `download.nvidia.com`, and `github.com`.
- OS assumption: **Ubuntu 24.04**, matching this repo's CI runner and the `/bin/sh` = `dash` assumption baked into `ci/checks.sh` and its tests (see `tests/unit/test_checks_sh_behaviour.py:1484`: "the shell ubuntu-24.04 and node A run"). Confirm before Step 2:
  ```
  cat /etc/os-release
  ```
  If node A is not Ubuntu 24.04, the `apt`/`dpkg` command *shapes* below still apply on any Debian-family host, but the repo suffix in Step 2.2's URL (`ubuntu2404`) must be swapped for the matching entry at `https://developer.download.nvidia.com/compute/cuda/repos/` — do not run Step 2.2 unmodified against a mismatched OS release.
- Read `docs/decisions/000-phase0-technical-decisions.md` D-01 (driver choice) and D-03 (GPU UUID pinning) before running anything — this runbook is built directly on both.

### Note on command provenance

The *version numbers* below (driver `610.57.04`, CUDA `13.x`, GPU Operator `v26.3.3`) are pinned and source-cited in `docs/decisions/versions.md`, which states its own re-verification requirement: **re-check that file if it is more than ~30 days old.** The *installation mechanics* around them — the exact NVIDIA apt-repo URL, keyring package filename, and DCGM package name — follow NVIDIA's standard, long-stable Ubuntu packaging conventions, but this runbook's author (an agent with no Bash and no live web-fetch tool, by design — see `.claude/agents/runbook-writer.md`) could not independently re-confirm them against a live NVIDIA index at authoring time. Every step below that depends on one of these mechanics includes its own live verification sub-check (`apt-cache policy` / `apt-cache search`) specifically so **the operator confirms the exact package/filename against the real repository at execution time** rather than trusting a possibly-stale literal in this document. If a verification sub-check disagrees with this document, trust the live system output and record the discrepancy in the Verification Block.

### Known-good version pins (source: `docs/decisions/versions.md`)

| Component | Pinned version | Source |
|---|---|---|
| NVIDIA driver (host, **open** kernel modules) | `610.57.04` | https://download.nvidia.com/XFree86/Linux-x86_64/ |
| CUDA (ceiling reported by the driver) | `13.x` | https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-610-57-04/index.html |
| RTX 5060 Ti device ID | `2D04` | https://download.nvidia.com/XFree86/Linux-x86_64/610.57.04/README/supportedchips.html |
| RTX 5060 device ID | `2D05` | same |
| GPU Operator chart (installed later at `T006`/`T012`; referenced here only for Step 6's compatibility check) | `v26.3.3` | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html |
| k3s (installed later at `T004`/`T005`, out of scope here) | `v1.35.7+k3s1` — **not** `v1.36.3+k3s1`, see D-000/D-07 | `docs/decisions/versions.md` |

Host prep in this runbook is also a prerequisite for the `RUNTIME_CONFIG_SOURCE=file` question, but no single task fully resolves it — the question stays genuinely open until the toolkit exists. `docs/runbooks/01-k3s-install.md` (authored at `T004`, **executed at `T005`**, matching that document's own Phase A framing and `tasks.md`'s Dependencies table, which names this coupling "Resolved at: T005") Step 6 records one supporting fact on the running k3s host — that containerd is reachable only as a `k3s` subcommand, not a standalone binary — but explicitly does not and cannot confirm the actual unverified question from `docs/decisions/000-phase0-technical-decisions.md`'s "Requires empirical verification" item 3 (whether the toolkit's config-injection fails gracefully in this environment), since the toolkit does not exist yet. `T006`'s GPU Operator Helm values are what actually set `toolkit.env`'s `RUNTIME_CONFIG_SOURCE=file`. The closest thing to real evidence either way is `01-k3s-install.md`'s Phase B containerd-handler-wiring checks, executed at **T012** once the toolkit is live — a correctly-wired `nvidia` handler there is the only available proxy for "the file-mode write worked," since a graceful failure and a successful write are otherwise indistinguishable from the outside.

---

## Step 1 — Pre-flight: identify GPU hardware present

**1.1 — Command:**
```
lspci -nn | grep -i nvidia
```

**Expected output:** exactly two lines, e.g. (bus IDs will vary by motherboard slot — that is expected and irrelevant; PCI bus IDs are never used downstream, only UUIDs, per D-000/D-03):
```
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation Device [10de:2d04] (rev a1)
02:00.0 VGA compatible controller [0300]: NVIDIA Corporation Device [10de:2d05] (rev a1)
```

**Verification check:** exactly two NVIDIA (`10de`) lines are returned. One line's bracketed device ID reads `[10de:2d04]` (RTX 5060 Ti, 16GB, training) and the other `[10de:2d05]` (RTX 5060, 8GB, serving/aux) — the exact IDs the 610.57.04 supported-chips list names.

**1.2 — Corroborating command (optional, if the bracketed IDs are ambiguous on this system's `pciutils` database):**
```
sudo lshw -C display
```
Expected output: two entries with `product:` lines reading "NVIDIA GeForce RTX 5060 Ti" and "NVIDIA GeForce RTX 5060".

**Abort path:**
- **Zero NVIDIA lines returned:** cards are not detected at the PCI level. Power off, reseat both cards and their PCIe power connectors, re-run `lspci -nn | grep -i nvidia`, and check `dmesg | grep -i nvidia` for bus errors before proceeding. Do not continue to Step 2.
- **NVIDIA lines present but device IDs are not `2d04`/`2d05`** (different GPU model, or only one card present): **STOP.** This runbook, `plan.md`'s Technical Context, and D-000/D-03's UUID-pinning design all assume exactly this pair. Installing the driver may still work, but the downstream GPU split (`T027` training placement, `T043` serving placement) needs re-derivation before proceeding. Record the actual hardware observed and open a `docs/decisions/` note per CLAUDE.md's "unknowns discovered mid-task" rule before continuing past this step.

---

## Step 2 — Install `nvidia-driver-610-open` (open kernel modules)

**Why `-open` specifically (D-000/D-01):** the proprietary driver flavor supports "Turing, Ampere, Ada, and Hopper" only; Blackwell (RTX 50-series) requires the open kernel modules. Installing the closed/proprietary package instead produces the documented real failure `NVIDIA GPU at PCI:8:0:0 is not supported by the ... driver`. Do not substitute `nvidia-driver-610` (no `-open` suffix) even if `apt` or `ubuntu-drivers` suggests it as the default.

**2.1 — Check for a conflicting prior install:**
```
dpkg -l | grep -i nvidia
lsmod | grep nouveau
```
**Expected output:** empty on a clean host, or a list of previously-installed NVIDIA packages.

**Verification check:** if `dpkg -l | grep -i nvidia` is non-empty, treat this as a prior install requiring removal before continuing:
```
sudo apt-get remove --purge -y 'nvidia-*' 'libnvidia-*'
sudo apt-get autoremove -y
```
Verify removal: `dpkg -l | grep -i nvidia` returns empty. If a kernel module was loaded, reboot and confirm `lsmod | grep nvidia` is also empty afterward.

**2.2 — Add NVIDIA's CUDA network repository (provides the `-open` packages):**
```
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
```
**Expected output:** `apt-get update` lists a `developer.download.nvidia.com/.../ubuntu2404/x86_64` source with no `404`/GPG errors.

**Verification check:**
```
apt-cache policy nvidia-driver-610-open
```
must show the package as available (a candidate version, not "Unable to locate package"). Because the exact keyring filename can increment over time (see "Note on command provenance" above), if the `wget` returns 404, browse `https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/` directly for the current `cuda-keyring_*.deb` filename and substitute it.

**Abort path:** if `nvidia-driver-610-open` is not found in the repo at all (not just a stale keyring filename), this driver build may not yet be published for this Ubuntu release. Cross-check `https://download.nvidia.com/XFree86/Linux-x86_64/610.57.04/` (the source cited in `docs/decisions/versions.md`) as a fallback runfile-installer path. Do not substitute a *different driver version* to make the install succeed without first updating `docs/decisions/versions.md` via PR (Principle IV — pins are the plan of record, not something to improvise around silently).

**2.3 — Install:**
```
sudo apt-get install -y nvidia-driver-610-open
```
**Expected output:** apt resolves and installs `nvidia-driver-610-open` plus its dependencies (`nvidia-kernel-open-610`, `libnvidia-*-610`, `nvidia-utils-610`, etc.), ending `Setting up nvidia-driver-610-open ...` with exit code 0.

**Verification check:**
```
dpkg --status nvidia-driver-610-open | grep Version
```
must include `610.57.04`.

**2.4 — Reboot to load the new kernel modules:**
```
sudo reboot
```
(No meaningful command-line "expected output" — the SSH/console session drops.)

**2.5 — Post-reboot verification:**
```
nvidia-smi
```
**Expected output:** a table whose header reads `Driver Version: 610.57.04` and `CUDA Version: 13.x`, listing two GPU rows: "NVIDIA GeForce RTX 5060 Ti" and "NVIDIA GeForce RTX 5060".

**Verification check:** exit code `0`; driver version string is exactly `610.57.04`; both GPUs are listed; no `NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver` error; no `is not supported by the ... driver` error (the exact D-000/D-01 failure signature for the wrong driver flavor).

**Rollback / abort path:**
1. `dmesg | grep -i nvidia` — check for module load errors.
2. `mokutil --sb-state` — if Secure Boot is `enabled`, this is the most common open-module load failure (open modules are unsigned by default). Either disable Secure Boot in firmware, or enroll a MOK key for the DKMS-built module. Confirm with `sudo dkms status`, which should show `nvidia/610.57.04` built for the running kernel.
3. Confirm the correct flavor actually installed: `modinfo nvidia | grep -i license` — open modules report `Dual MIT/GPL`; proprietary reports `NVIDIA`. If proprietary was somehow installed, purge and reinstall:
   ```
   sudo apt-get remove --purge -y 'nvidia-*' 'libnvidia-*'
   sudo apt-get install -y nvidia-driver-610-open
   sudo reboot
   ```
4. If `nvidia-smi` still fails after (1)–(3): **STOP.** Do not proceed to k3s install (`T004`/`T005`) — every downstream GPU step depends on this driver loading. Capture `dmesg`, `dpkg -l | grep nvidia`, the `nvidia-smi` error text, `mokutil --sb-state`, and `dkms status`, then open a `docs/decisions/` note per CLAUDE.md before attempting an alternative install path (e.g. the runfile installer).

---

## Step 3 — CUDA 13.x validation

**Context:** the GPU Operator's driver container is disabled on this host (`driver.enabled: false`, per the D-000/D-01 reference block and the "Reference: GPU Operator v26.3.3 values" section of `docs/decisions/000-phase0-technical-decisions.md`). CUDA userspace libraries for *containerized* workloads (training/serving pods) are supplied by the container images themselves via the NVIDIA Container Toolkit, installed later at `T004`/`T006` — not by a host-wide CUDA toolkit package. This step validates only that the installed driver's advertised CUDA ceiling is `13.x`, which every container image running on this host must stay at or under.

**3.1 — Command:**
```
nvidia-smi | grep -oP 'CUDA Version:\s*\K[0-9]+\.[0-9]+'
```
**Expected output:** a single version string beginning `13.` (e.g. `13.0`).

**Verification check:** output matches the pattern `^13\.[0-9]+$`.

**Abort / rollback path:** if the reported CUDA version is not on the `13.x` line (e.g. it reports `12.x`), the installed driver build does not match the `610.57.04` pin — re-run Step 2's verification (`dpkg --status nvidia-driver-610-open | grep Version`) rather than proceeding. **Do not** install a separate host CUDA toolkit package to try to "fix" this number — it is derived purely from the driver, and a toolkit package will not change it.

**3.2 — Optional deeper validation (skip if Docker + NVIDIA Container Toolkit are not yet present on this host — they are formally installed at `T004`/`T006`, not here):**
```
docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu24.04 nvidia-smi
```
**Expected output:** the same `nvidia-smi` table as Step 2.5, rendered from inside the container.

**Verification check:** exit code `0`, both GPUs visible inside the container.

**Abort path:** a non-zero exit or `could not select device driver` error here means the Container Toolkit isn't installed yet — this is expected and fine to defer to `T004`/`T006`. Do not treat a failure in this optional sub-step as a Step 2 driver failure; re-confirm with Step 2.5's bare-metal `nvidia-smi` first, which is the authoritative check.

---

## Step 4 — Capture GPU UUIDs into a local `.env`

**4.1 — Command:**
```
nvidia-smi -L
```
**Expected output** (two lines, exact format per D-000/D-03):
```
GPU 0: NVIDIA GeForce RTX 5060 Ti (UUID: GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
GPU 1: NVIDIA GeForce RTX 5060 (UUID: GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
```
(The `x`-filled string above is a literal placeholder pattern for this document, per D-000/D-10's public-repo parameterization rule — never write a real UUID into this file or any other committed file.)

**Verification check:** exactly two lines; each UUID begins with the literal `GPU-` prefix followed by 8-4-4-4-12 hex groups. **Match each UUID to a card by the model name printed on the same line ("RTX 5060 Ti" vs "RTX 5060"), never by the `GPU 0`/`GPU 1` index** — index-to-card mapping is not guaranteed stable across reboots or PCI slot reordering (D-000/D-03).

**4.2 — Create the local `.env` (never commit this file) — non-destructively:**
```
[ -f .env ] && echo "ALREADY EXISTS — not overwriting, see below" || cp .env.example .env
```
**Expected output:** either `.env` created fresh in the repo root (typical — this is usually the first time `.env` is populated), or the `ALREADY EXISTS` message if a `.env` from a prior partial run, or from something unrelated, is already present. **If it already exists, do not delete or re-copy it** — `.env.example`'s values are all blank, so `cp .env.example .env` would silently discard anything already recorded there. Instead open the existing `.env` and confirm it has the two `ORBITAL_DRIFT_*_GPU_UUID` keys (add them by hand, per 4.3 below, if missing).

**Verification check:**
```
git check-ignore -v .env
```
must print a match against `.gitignore` (line 18, `.env`) — confirming git will refuse to track it — **before** you put any UUID into it.

**4.3 — Edit `.env`, setting the two variables using the UUIDs from 4.1 matched by model name:**
```
ORBITAL_DRIFT_TRAIN_GPU_UUID=GPU-<uuid of the RTX 5060 Ti 16GB card>
ORBITAL_DRIFT_SERVE_GPU_UUID=GPU-<uuid of the RTX 5060 8GB card>
```

**Verification check:**
```
grep -c '^ORBITAL_DRIFT_.*_GPU_UUID=GPU-' .env
```
must return `2`. Then:
```
git status --porcelain .env
```
must print **nothing** (the file stays untracked/ignored).

**Warning — `.env` must never be committed:** it is already listed in `.gitignore` (line 18: `.env`), which Step 4.2's `git check-ignore` confirms, but this is a second, explicit warning because the repository is public and D-000/D-10 states GPU UUIDs specifically "must not be committed." Never paste the contents of `.env` into a commit, a CI log, an issue, or a PR description.

**Abort / rollback path:** if `.env` is ever accidentally staged:
```
git restore --staged .env
```
If it was already **committed** (not just staged): do not push. Constitution VII covers credentials specifically, and these UUIDs are hardware identifiers, not credentials — but treat this as an incident under D-10's public-repo hygiene rule regardless, which forbids them in the repo on its own separate authority. If the commit is local and unpushed, `git reset --soft HEAD~1` (if it is the most recent commit) removes it while keeping the working tree; if other commits already sit on top of it, or if it has already been pushed, stop and escalate rather than force-pushing over shared history — this needs a deliberate history rewrite, not a one-line fix.

---

## Step 5 — DCGM field-support check (consumer Blackwell)

**Context (D-000/D-11 UNVERIFIED item, resolved here):** no NVIDIA source names RTX 50-series or consumer GB20x GPUs in any DCGM support matrix. This step settles it empirically, before `T006` commits to a `dcgmExporter` counters CSV that Phase 5 dashboards (`T047`) depend on.

**5.1 — Determine and install the correct DCGM package.** NVIDIA has reorganized DCGM's packaging across major versions (a single `datacenter-gpu-manager` package in the 3.x era vs. split packages like `datacenter-gpu-manager-4-core` in the 4.x era), and `docs/decisions/versions.md` does **not** currently pin a DCGM version — this is a documented gap (see the open question at the end of this runbook). Search before installing:
```
apt-cache search dcgm
```
**Expected output:** at least one package whose name contains `datacenter-gpu-manager`.

Install whichever `datacenter-gpu-manager*` core package the search surfaces:
```
sudo apt-get install -y datacenter-gpu-manager
```
(Substitute the exact package name — e.g. a `-4-core` variant — if that is what `apt-cache search` returned instead.)

**Verification check:**
```
dpkg -l | grep -i dcgm
which dcgmi
```
must show an `ii` status row and resolve a `dcgmi` binary.

**Abort path:** if `apt-cache search dcgm` returns nothing, re-run `sudo apt-get update` and retry. If still empty, DCGM may not be included in the CUDA network repo snapshot for this OS release — check `https://developer.download.nvidia.com/compute/cuda/repos/` directly.

**5.2 — Start the DCGM host engine:**
```
sudo systemctl enable --now nvidia-dcgm
```
**Expected output:** no error; unit becomes active.

**Verification check:**
```
systemctl is-active nvidia-dcgm
```
must print `active`.

**Abort path:** if the service fails to start, `journalctl -u nvidia-dcgm -n 50`. The most likely cause this early is Step 2's driver not actually loaded — re-verify Step 2.5 before troubleshooting DCGM itself.

**5.3 — Confirm DCGM sees both GPUs:**
```
dcgmi discovery -l
```
**Expected output:** two GPUs listed, same model names as Step 2.5/4.1.

**Verification check:** GPU count = 2.

**5.4 — Run the field-support probe:**
```
dcgmi dmon -e 203,252,150 -c 5
```
(Field `150` = `DCGM_FI_DEV_GPU_TEMP`, `252` = `DCGM_FI_DEV_FB_USED`, `203` = `DCGM_FI_DEV_GPU_UTIL` — the exact three fields D-000/D-11 names for `T006`'s counters CSV, confirmed against NVIDIA's DCGM field-identifier reference. `-c 5` takes 5 samples then exits.)

**Expected output:** a table with one column per requested field, one row per sample per GPU. Each cell is either a numeric value (field supported) or the literal string `Not Supported`.

**Verification check:** record, per field, whether the observed value across all 5 samples × 2 GPUs was a real number or `Not Supported`. **Both outcomes are valid and must be recorded explicitly** — there is no "correct" result here, only an ambiguous (unrecorded) one, which is what would make this step defective. This directly determines what `T006`'s `dcgmExporter` counters CSV may contain: any field reported `Not Supported` here must be dropped from that CSV rather than shipped as a metric that will always read zero/absent on this hardware.

**Abort / rollback path:** this step has no destructive action to roll back. Its only failure mode is `dcgmi` erroring out entirely (e.g. `Unable to establish a connection to the specified host`), which means Step 5.2's service is not actually running — recheck `systemctl is-active nvidia-dcgm` and retry. If you want the host quiescent again afterward: `sudo systemctl disable --now nvidia-dcgm`.

---

## Step 6 — GPU Operator `v26.3.3` / driver branch `610` compatibility verdict

**Context:** `docs/decisions/versions.md`'s UNVERIFIED section flags this explicitly: "Whether GPU Operator 26.3.3 functions on driver branch 610 (validated list stops at 595.71.05)." This runbook does **not** install GPU Operator — that is `T006`'s Helm values plus `T012`'s `terraform apply`, both gated on a k3s cluster this runbook precedes. This step produces a **provisional, desk-check verdict now**, to be empirically reconfirmed at `T012`.

**6.1 — Check NVIDIA's platform-support docs (manual, read-only — open in a browser, no cluster or agent tooling involved):**
```
https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html
```
Look for the "Validated Platforms" / driver-branch compatibility table. Check whether driver branch `610` (or the `610.x` series generally) is (a) explicitly listed as validated, (b) explicitly listed as unsupported/excluded, or (c) simply absent from the table.

**6.2 — Corroborate with GPU Operator's issue tracker (manual, read-only):**
```
https://github.com/NVIDIA/gpu-operator/issues?q=610
```
Read any open/closed issues discussing driver `610` compatibility for signal.

**Expected output:** a determination falling into exactly one of three states.

**Verification check / verdict recording** — combine 6.1 and 6.2 into one of these three, and record it verbatim in the Verification Block:
- **PASS** — positive evidence found (610 explicitly validated, or confirmed working in a closed/resolved issue with no unresolved regression, specifically for the `toolkit`/`devicePlugin`/`dcgmExporter` components — the only GPU Operator components this host-driver-only design (`driver.enabled: false`) actually exercises. A gap in the *driver container's* own chip list is expected per D-000/D-01 and is not itself a FAIL signal here.)
- **FAIL** — positive evidence of incompatibility found (610 explicitly excluded, or an open unresolved issue reproducing a break on 610 in `toolkit`/`devicePlugin`/`dcgmExporter`).
- **UNRESOLVED-PROCEED** — no evidence either way. Expected and acceptable to proceed on; `T006`'s Helm values PR must cite this verdict verbatim, and `T012` (`terraform apply` + `nvidia-smi` in a test pod) remains the actual empirical gate. A `nvidia-operator-validator` crashloop or toolkit failure at `T012` is the real FAIL signal this desk-check cannot substitute for.

**Abort / rollback path:** none — this is a read-only research step, nothing to undo. If the verdict is **FAIL**, do not let `T006` proceed to review-APPROVED with chart `v26.3.3` as-is: open a `docs/decisions/` note (per CLAUDE.md's "unknowns discovered mid-task" rule) proposing either an older GPU Operator version compatible with driver 610, or a different driver still within RTX 50-series support — do not silently downgrade either pin without recording why.

---

## Full Rollback (undo everything in this runbook)

Only needed if host prep must be fully reversed (e.g. hardware swap, starting over on a different driver strategy):
```
sudo systemctl disable --now nvidia-dcgm
sudo apt-get remove --purge -y 'nvidia-*' 'libnvidia-*' datacenter-gpu-manager 'datacenter-gpu-manager-*'
sudo apt-get autoremove -y
sudo rm -f /etc/apt/sources.list.d/cuda*.list /usr/share/keyrings/cuda-archive-keyring.gpg
sudo apt-get update
```
Then remove the local `.env` (it was never committed, so this is a plain filesystem delete, not a git operation):
```
rm .env
```
Verification: `dpkg -l | grep -i nvidia` and `dpkg -l | grep -i dcgm` both return empty; `nvidia-smi` reports command not found.

---

## Verification Block (complete during T003 execution)

Fill in every field. Use `UNRESOLVED` explicitly rather than leaving a field blank — this block is the citable record `T006`'s required re-review and `T011` depend on (see `specs/001-orbital-drift-ct/tasks.md`'s Dependencies section). **Do not write real GPU UUID values into this file** — it is committed to a public repo; record UUID capture as a yes/no plus where they actually live (`.env`, gitignored).

```
Date executed (UTC):
Operator:
Node: node A

--- Step 1: Hardware ---
lspci NVIDIA device IDs observed: [ ] 10de:2d04 present   [ ] 10de:2d05 present
Hardware match: PASS / FAIL (if FAIL: reference the docs/decisions/ note opened)

--- Step 2: Driver ---
Package installed: nvidia-driver-610-open
`dpkg --status nvidia-driver-610-open` Version:
`nvidia-smi` Driver Version reported:
Secure Boot state (`mokutil --sb-state`), if load required troubleshooting:
Result: PASS / FAIL

--- Step 3: CUDA ---
CUDA Version reported by `nvidia-smi`:
Matches `13.x` pin: YES / NO
Optional container validation (3.2) run: YES / NO / SKIPPED — result:

--- Step 4: GPU UUIDs ---
UUIDs captured via `nvidia-smi -L`: YES / NO
`.env` populated (both ORBITAL_DRIFT_*_GPU_UUID set): YES / NO
`git check-ignore -v .env` confirmed ignored: YES / NO
(Reference only — real UUID values live in the gitignored `.env`, never here.)

--- Step 5: DCGM field support ---
DCGM field support: SUPPORTED / NOT SUPPORTED / PARTIAL — fields observed:
  - 150 (GPU_TEMP):
  - 252 (FB_USED):
  - 203 (GPU_UTIL):
Implication for T006 dcgmExporter counters CSV (fields to drop, if any):

--- Step 6: GPU Operator 26.3.3 / driver 610 compatibility ---
Verdict: PASS / FAIL / UNRESOLVED-PROCEED
Evidence (URL(s) checked, date checked):
Notes for T006 Helm values PR:

--- Overall ---
Host prep result: PASS (proceed to T004/T005) / PASS WITH T006 CAVEAT (host prep itself is done — Steps 1-5 all PASS — but Step 6's verdict is FAIL or UNRESOLVED-PROCEED; proceed to T004/T005 as normal, but T006's Helm values PR must cite this block and address the caveat before that PR can be review-APPROVED) / BLOCKED (state the blocking issue — Steps 1-5 did not all PASS)
```
