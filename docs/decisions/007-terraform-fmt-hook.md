# D-007: `terraform fmt -check` pre-commit hook — digest resolution without a Docker daemon, the tag-prefix defect, and an open FR-provenance question

**Status:** authored 2026-08-16 alongside `.pre-commit-config.yaml`, `ci/versions.env`, `ci/checks.sh`, `tests/unit/test_version_pins.py`, `tests/unit/test_ci_contract.py`, `tests/unit/shell_harness.py`, and `tests/unit/test_terraform_fmt_positive_control.py` — Step 7 of the Phase 0 plan the operator approved 2026-08-16. Reviewed 2026-08-22 (T001b-closure PR): `spec-guardian` APPROVE, ruling in D-007/06; adversarial review same PR. (`peer-reviewer` was superseded by `adversarial-reviewer`, RB-001/RB-006.)

**Decision-ID namespace:** independent of `plan.md`'s own `D-01…D-05` and of `docs/decisions/000-phase0-technical-decisions.md`'s `D-000/D-nn` series. Cross-references from other docs should read `D-007/D-nn`. `007` confirmed free by listing `docs/decisions/` before authoring (`000` through `006` exist).

---

## D-007/01 — Resolving a digest-pinned container reference with no Docker daemon and no `terraform` binary on PATH

Every prior digest pin in `ci/versions.env` (gitleaks, shellcheck) was resolved via `pin-a-tool`'s default method: `docker pull` + `docker inspect`. Neither Docker nor a `terraform` binary existed in the environment that first authored this pin. The Docker Registry HTTP API v2 gives the same answer without either:

```sh
# 1. Anonymous bearer token, scoped to pull access on the one repository.
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:hashicorp/terraform:pull" | jq -r .token)

# 2. Manifest-list digest for the tag, requesting the v2 manifest-list media type
#    explicitly -- the default Accept header returns a config blob digest, not the
#    multi-arch manifest-list digest `docker pull`/`docker inspect` would report.
curl -s -H "Authorization: Bearer ${TOKEN}" \
     -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
     "https://registry-1.docker.io/v2/hashicorp/terraform/manifests/1.15.8" \
     -D - -o /dev/null | grep -i docker-content-digest
```

This is not asserted on faith: the same two calls were run first against `koalaman/shellcheck:v0.11.0`, and reproduced the already-known-correct `SHELLCHECK_DIGEST` in `ci/versions.env` byte-for-byte before the identical method was trusted for `hashicorp/terraform:1.15.8`, giving:

```
TERRAFORM_DIGEST=sha256:7ae513256f7ce67879e218ae8593d6fbe216ec9e123abe6c94e4e10704857963
```

Independently corroborated two more ways, neither of which depends on the registry API: `releases.hashicorp.com/terraform/1.15.8/` (the GitHub-release side of HashiCorp's distribution, confirming `1.15.8` is a real, currently-shipped release and not a typo) and `internal/command/version.go` read at the matching `v1.15.8` tag (confirming the CLI's own version-banner format, D-007/03 below). This recipe is reusable for the next container pin this repo adds without a Docker-capable authoring host — genuinely the point of writing it down rather than letting it live only in a shell history.

**RE-VERIFY before this pin is bumped.** HashiCorp ships Terraform patch releases frequently; a `1.15.9`+ appearing between authoring and merge is expected, not alarming, and does not by itself indicate this recipe was wrong.

## D-007/02 — `hashicorp/terraform`'s Docker Hub tags carry no `v` prefix, unlike gitleaks/shellcheck; this broke two hardcoded assumptions, fixed with one `tag_prefix` parameter

Confirmed against the live registry's own tag list (the same query as D-007/01, `GET /v2/hashicorp/terraform/tags/list`): `1.15.8` exists, `v1.15.8` does not. `ghcr.io/gitleaks/gitleaks` and `koalaman/shellcheck` both happen to use `v`-prefixed tags, which is why every existing pin in `ci/versions.env` carries a `v` and no code path had ever needed to vary it.

Two places hardcoded the `v`, found by direct re-read before writing any code (not merely by running the tests and reacting to red):

1. `tests/unit/test_version_pins.py`'s `test_container_image_is_digest_pinned_and_agrees_everywhere` asserted `f"{repository}:v{version}@{digest}"` unconditionally. A naive third parametrize tuple for terraform would force choosing between a test that fails against the real, pullable image, or a fabricated `v1.15.8` tag that does not exist on the registry.
2. `ci/checks.sh`'s `require_pinned_image` printed its FAIL-diagnostic remediation (`docker pull %s:v%s`) with the same hardcoded `v`, regardless of tool. This is a much rarer path — it only prints when a pin-drift failure actually happens — but is a real, findable defect if left as-is: it would suggest an invalid `docker pull hashicorp/terraform:v1.15.8` to an operator debugging a real failure.

Both are one defect wearing two hardcoded spellings, not two independent bugs — fixed with one `tag_prefix` parameter threaded through both: `require_pinned_image`'s signature gained a 4th positional (`rp_tag_prefix`, before the extractor function), with `require_gitleaks_image`/`require_shellcheck_image` passing the literal `"v"` they always meant implicitly, and `require_terraform_image` passing `""`. `test_container_image_is_digest_pinned_and_agrees_everywhere`'s parametrize list gained the same dimension. Confirmed the FAIL-diagnostic string itself was not separately asserted by any test before this change (grepped for the literal `docker pull %s`), so no test needed updating purely for that message's wording — only for the tag it now embeds.

## D-007/03 — The stub-collision-avoidance reasoning for `-version` (single dash), verified against real terraform, not just cobra's documented alias table

`tests/unit/shell_harness.py`'s `DOCKER_STUB` dispatches on the trailing suffix of the whole probe command line: `*" version")` (gitleaks) and `*" --version")` (shellcheck). `terraform_reported_version()`'s probe therefore could not reuse either spelling without colliding — `require_terraform_image` calls `require_pinned_image` with `-version` (single dash) as the trailing probe argument specifically because no existing `case` arm's pattern is a suffix-match for it: `*" version")` requires the character before `version` to be a space, and `-version` has `-` there instead; `*" --version")` requires two dashes, and `-version` has only one. A third `DOCKER_STUB` arm, `*" -version")`, was added rather than extending either existing one.

`-version` is a real alias terraform's own `VersionCommand` accepts identically to `-v`/`--version` — confirmed both from `internal/command/version.go` at the `v1.15.8` tag (which the Plan agent read during research) and empirically here, against a real downloaded `terraform_1.15.8_linux_amd64` static binary (checksum-verified against HashiCorp's published `SHA256SUMS`, no Docker required):

```
$ ./terraform -version
Terraform v1.15.8
on linux_amd64
```

Byte-for-byte the same two-line banner `-version`/`--version`/`version` all three produce — the alias changes nothing about the output shape `terraform_reported_version()`'s `sed -n '1s/^Terraform v//p'` parses, only which argv string reaches the stub's `case` dispatch.

## D-007/04 — `terraform fmt -check`'s exit-code contract and file-list acceptance, measured against the real binary, not only read from source

The Plan agent's research read `internal/command/fmt.go` at the pinned tag and reported: `-check` exits `2` on a genuine HCL parse error and `3` when the input parses but is not canonically formatted; the command accepts an explicit list of file paths, not only a directory, each stat'd and processed independently; and neither `-check` nor plain `fmt` makes any network call (no `CheckFunc`/checkpoint reference in the command, unlike `version`).

All three re-measured directly against the real static binary before being written into `tests/unit/test_terraform_fmt_positive_control.py`'s assertions, rather than trusted from the source read alone:

| Fixture | Command | Exit | Notes |
|---|---|---|---|
| Valid HCL, wrong indentation | `fmt -check -recursive -no-color` | `3` | prints the misformatted filename to stdout |
| Canonically formatted | `fmt -check -recursive -no-color` | `0` | no output |
| Unbalanced braces (genuine syntax error) | `fmt -check -recursive -no-color` | `2` | `Error: Missing expression` diagnostic |
| Two explicit file paths (not a directory) | `fmt -check -no-color misformatted/main.tf canonical/main.tf` | `3` | named only the misformatted one — confirms per-file processing, not directory-only |
| Canonical, timed | `fmt -check -no-color` | `0` | `0.028s` real time — consistent with no network round-trip; not conclusive on its own, but corroborates the source-level claim rather than resting on it alone |

This is what backs `pass_filenames` being left at its pre-commit default (`true`) in `.pre-commit-config.yaml`: pre-commit handing `fmt -check` a list of matched `.tf` paths is genuinely correct behavior for this command, not an untested assumption carried over from the shellcheck hook by resemblance.

## D-007/05 — The mandatory reformatting pass (plan Implementation step 1) found a zero diff across all 13 existing `.tf` files

`ci/checks.sh`'s `hooks` stage runs `pre-commit run --all-files`, which resolves to `git ls-files` — so the moment this hook is added, it checks every `.tf` file T007–T010 already landed under `infra/terraform/{00-crds,10-storage,20-platform}/`, not only newly-touched ones. Nothing in this repo's history shows a real `terraform fmt -check` had ever been run against them; `docs/decisions/006-t007-t010-integration.md` never actually confirmed it, and the files were only hand-formatted to *look* canonical.

Measured directly, twice — once during initial implementation and re-confirmed here before writing this document:

```
$ terraform fmt -check -recursive -no-color infra/terraform/
$ echo $?
0
```

Zero diff, 13 of 13 files, both times. No separate reformatting commit was needed — this section exists to record that the check was actually run and passed, not merely assumed to.

## D-007/06 — Open question for `spec-guardian` (resolved 2026-08-22 — ruling recorded below): does this hook need its own FR, or is it covered by `stage_hooks`'s existing mandate?

Stated honestly rather than pre-decided: the operator originated this specific scope (land `terraform fmt -check` via pre-commit) after being shown the Phase 0 plan's rev-1 review findings — it is not inferred from FR-011, and CLAUDE.md requires unknowns to be surfaced, not improvised past.

Two readings of existing precedent point different directions:

- **For "no new FR needed":** `stage_hooks` already runs the full `.pre-commit-config.yaml` in CI (T001's own scope: "Constitution VII requires gitleaks as pre-commit hook **and** CI gate, which a config nobody executes does not satisfy"). Adding one more hook to an existing, already-conformant stage could be read as within that stage's standing mandate, the same way a new ruff rule or a new mypy strict flag would not need its own FR.
- **Against it, and the reasoning this document defers to:** `docs/decisions/001-coverage-gate.md` D-01 draws the line differently — `hooks` was judged conformant *specifically* because it enforced an *existing* Constitution VII requirement (gitleaks), not because "the hooks stage runs whatever is in the config" is itself a standing blanket mandate. Nothing in the constitution or `spec.md` names Terraform formatting the way Constitution VII names secrets scanning. Under that reading, this hook is closer to T001a's coverage gate (FR-011a, added explicitly on operator request) than to a T001-scope extension.

(Historical, pre-ruling:) This document does not resolve it. `spec-guardian` is asked to rule on it in review; whichever way it goes, `specs/001-orbital-drift-ct/tasks.md`'s T001b entry and this section will be updated to record the ruling, not silently left inconsistent with it.

**RULING (spec-guardian, 2026-08-22, T001b-closure PR): own FR — FR-011b.** The gate gets its own FR line, **FR-011b**, following the FR-011a precedent (`docs/decisions/001-coverage-gate.md` D-01). The "covered by `stage_hooks`'s existing mandate" reading is **rejected**: nothing in the constitution or `spec.md` names Terraform formatting the way Constitution VII names secrets scanning, and this gate's provenance — operator-originated scope, added by explicit request after plan review — is exactly FR-011a's shape. The shellcheck hook's own missing FR line is a legacy inconsistency, not a precedent, and is explicitly NOT fixed in this PR; it is flagged for operator triage as item 3 of "Not resolved here" below. `specs/001-orbital-drift-ct/spec.md` (FR-011b) and tasks.md's T001b entry record the outcome per this section's own commitment.

---

## Not resolved here, flagged for whoever reviews next

1. ~~D-007/06 above — the FR-provenance ruling itself.~~ **CLOSED 2026-08-22** by the spec-guardian ruling in the T001b-closure PR (own FR — FR-011b; ruling recorded in D-007/06 above).
2. ~~This pin was resolved without ever running the actual `hashicorp/terraform` Docker image (no daemon in the authoring environment) — D-007/01's registry-API method and D-007/04's static-binary measurements are both faithful proxies (same released artifact HashiCorp ships inside the image), not a substitute for CI's first real `docker run` of it.~~ **CLOSED 2026-08-22** per the spec-guardian's closure condition: GitHub Actions run **32574454828** (workflow `ci`, attempt 1, head `dcc5cb1`, conclusion success — the T001b-closure PR's first CI run) executed `require_pinned_image`'s terraform check in `stage_hooks` and the three container positive controls in `tests/unit/test_terraform_fmt_positive_control.py` against the pinned image (`CI=true` makes `_tool()` raise rather than skip when docker is unavailable, and the zero-skip conftest guard forbids silent skips — a green suite therefore proves execution). Zero of the budgeted 2-3 red runs were consumed; the registry-API digest resolution (D-007/01) was correct on the first pull.
3. **OPEN — operator triage.** The shellcheck hook has no FR line of its own either (no FR in `spec.md`, no dedicated traceability row). The D-007/06 ruling classifies this as a legacy inconsistency predating the D-001/D-01 discipline (shellcheck was named inside T001's original operator-approved task text), NOT a precedent, and deliberately does not fix it here — closing it in the PR that cites it would look like retro-justification. The operator decides: add an FR line for the shellcheck gate in a separate change, or record an explicit N/A-by-design rationale. Flagged 2026-08-22 (spec-guardian finding 7, T001b-closure PR).
