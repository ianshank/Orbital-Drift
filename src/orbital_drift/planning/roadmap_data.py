"""THE single data source for the Orbital-Drift backlog projections.

Both projections — ``planning/roadmap.md`` and ``planning/jira-import.csv`` —
are emitted from this module by ``orbital_drift.projections``. Neither may be
hand-edited (charter source-of-truth rule; skill "Source of truth"): change the
records here and regenerate with::

    python -m orbital_drift.projections --write

CI runs the drift check as the ``projections`` stage::

    python -m orbital_drift.projections --check --json

which fails if either committed projection does not BYTE-MATCH what this module
emits — so the backlog literally cannot disagree with the plan (design D9).

Scope stays owned by ``specs/001-orbital-drift-ct/tasks.md``: every story's
``trace`` names its task id, and ``tests/unit/test_projections.py`` asserts
scope parity in BOTH directions — every cited task exists in that file, and
every task there has a story.

CORRECTED 2026-09-05 (RB-012). This paragraph previously claimed the tests also
assert "that story status matches the checkbox state". They do not, and cannot:
:class:`Story` has no status field and no test in the suite asserts anything
about status. The headline property below ("cannot disagree with the plan") was
half-mechanized and documented as fully mechanized — the exact over-claim this
repo removes guards for. What IS mechanized is set parity of task ids; what is
not is whether a story's acceptance text still describes what its task says.

Design rules (donor template, kept):
1. Pure data plus small pure helpers; stdlib only; no I/O.
2. CSV-projected fields are plain ASCII with no markdown (Jira renders emphasis
   as literal asterisks; non-ASCII risks legacy importers).
3. Every story carries id, title, epic, acceptance (a testable AC), labels,
   points (None for owner stories — a BLANK cell, never 0), priority, and a
   ``trace`` naming its tasks.md task ids.
4. Convention rules live as guard functions at the bottom, machine-checkable,
   run by the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

#: Jira accepts exactly these; a typo used to pass mypy, every guard and the
#: byte-check, and fail only at import time in Jira.
Priority = Literal["Highest", "High", "Medium", "Low"]

#: Closed label vocabulary. Open strings drift into near-duplicates
#: ("runbook"/"runbooks") that split a Jira board silently.
LABELS: Final[frozenset[str]] = frozenset(
    {
        "orbital-drift",
        "owner",
        "runbook",
        "infra",
        "tdd",
        "config",
        "ingest",
        "data",
        "pipeline",
        "train",
        "registry",
        "research",
        "drift",
        "serve",
        "dashboards",
        # Added 2026-09-05 (RB-012): the governance/review track had no label, so
        # T057 (RB-010's retroactive review of T013-T052) had nowhere honest to
        # sit. Every other candidate ("infra", "tdd") mis-groups it on a board.
        "governance",
    }
)

#: The Jira import template carries exactly this many Labels columns.
#:
#: SINGLE HOME, and it lives HERE rather than in :mod:`orbital_drift.projections`
#: (which is where it used to be, with this module keeping a private
#: ``limit = 3`` copy). The stated reason for that copy — "duplicated to keep
#: this module I/O-free" — was simply wrong: importing an ``int`` performs no
#: I/O. The real constraint is direction. ``projections`` imports this module,
#: so this module importing ``projections`` back would be a cycle. Putting the
#: constant in the leaf and re-exporting it from ``projections`` satisfies both
#: the acyclic import graph and Constitution III.
#:
#: Both readers must move together: ``projections._label_cells`` pads to this
#: width and raises above it, and :func:`labels_fit_the_csv_projection` rejects
#: the same overflow before the emitter ever runs. Two literals meant widening
#: the CSV in one place restored the exact silent-data-loss bug those two exist
#: to prevent.
LABEL_COLUMNS: Final = 3


@dataclass(frozen=True)
class Epic:
    key: str
    name: str
    summary: str
    priority: Priority
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Story:
    key: str
    title: str
    epic: str
    acceptance: str
    labels: tuple[str, ...]
    points: int | None
    priority: Priority


_L: Final = "orbital-drift"

EPICS: tuple[Epic, ...] = (
    Epic(
        "E0",
        "E0 Substrate",
        "Phase 0: repo+CI scaffold, host prep, k3s, GPU operator, platform charts, bring-up. Exit: plan.md Phase-0 gate.",
        "Highest",
    ),
    Epic(
        "E1",
        "E1 Ingestion and Data Lifecycle",
        "Phase 1 (US1, US2): STAC client, tile store, catalog, lakeFS flow, ingest DAG. Exit: plan.md Phase-1 gate.",
        "High",
    ),
    Epic(
        "E2",
        "E2 Training and Registry",
        "Phase 2 (US3): labels, dataset, baseline+finetune training, MLflow registry. Exit: plan.md Phase-2 gate.",
        "High",
    ),
    Epic(
        "E3",
        "E3 CT Loop",
        "Phase 3 (US4, US5): drift metrics, trigger, drift/retrain DAGs, shadow eval, promotion. Exit: plan.md Phase-3 gate.",
        "High",
    ),
    Epic(
        "E4",
        "E4 Serving and Canary",
        "Phase 4 (US6): FastAPI stage-loader, canary split, per-version metrics. Exit: plan.md Phase-4 gate.",
        "Medium",
    ),
    Epic(
        "E5",
        "E5 Observability and Soak",
        "Phase 5 (US7, US8): dashboards, alerts, runbooks, rebuild drill, 6-week soak. Exit: plan.md Phase-5 gate, operator sign-off only.",
        "High",
    ),
    Epic(
        "E6",
        "E6 Reconciliation and Integration Hardening",
        "Phase 6 (RB-012): defects found in already-remediated code, RB-010 findings assigned to no part, adapter convergence, gate integrity. Exit: T057 complete and roadmap Tracks A-E closed.",
        "High",
    ),
    Epic(
        "E7",
        "E7 Operator and Decision Gates",
        "Operator-side critical path: HUMAN tasks, phase gates G-x, DEC sign-offs (Constitution I/VI).",
        "Highest",
    ),
)

# fmt: off
STORIES: tuple[Story, ...] = (
    # E0 — Substrate (T002-T012)
    Story("S0.2", "S0.2 Host-prep runbook (driver+CUDA, Blackwell)", "E0", "AC: docs/runbooks/00-host-prep.md merged with pinned driver/operator versions and verification block. Trace: T002.", (_L, "runbook"), 3, "Highest"),
    Story("S0.1a", "S0.1a Coverage gate plus CI defect fixes", "E0", "AC: the coverage stage enforces FR-011a as one combined statement+branch rate, threshold pinned in ci/versions.env, with positive controls proving it fails a run whose tests all pass. Trace: T001a.", (_L, "infra"), 5, "High"),
    Story("S0.1b", "S0.1b terraform fmt pre-commit hook", "E0", "AC: FR-011b canonical Terraform formatting runs as a pre-commit hook and in the hooks CI stage against a digest-pinned image, with positive, negative and guard-the-guard controls. Trace: T001b.", (_L, "infra"), 3, "Medium"),
    Story("S0.3", "S0.3 Execute host prep on node A", "E7", "AC: runbook verification block filled with measured driver/CUDA versions; G-1 logged. Owner story. Trace: T003.", (_L, "owner"), None, "Highest"),
    Story("S0.4", "S0.4 k3s install runbook", "E0", "AC: docs/runbooks/01-k3s-install.md merged (single node, GPU labels, nvidia runtime). Trace: T004.", (_L, "runbook"), 3, "High"),
    Story("S0.4a", "S0.4a containerd config-v3 template", "E0", "AC: infra/k3s/config-v3.toml.tmpl encodes the nvidia containerd runtime stanza and keeps the NRI plugin off, per D-000/D-02b. Trace: T004a.", (_L, "infra"), 3, "High"),
    Story("S0.5", "S0.5 Install k3s on node A", "E7", "AC: k3s up per runbook; kubeconfig location note committed; G-2 logged. Owner story. Trace: T005.", (_L, "owner"), None, "Highest"),
    Story("S0.6", "S0.6 GPU Operator values + Terraform", "E0", "AC: pinned infra/helm-values/gpu-operator.yaml + infra/terraform/gpu_operator.tf; AUTHORED-PROVISIONAL until re-reviewed against T003/T005 verification blocks. Trace: T006.", (_L, "infra"), 5, "High"),
    Story("S0.7", "S0.7 SeaweedFS + lakeFS + CloudNativePG infra", "E0", "AC: pinned values + Terraform releases under infra/ per D-000/D-04+D-05. Trace: T007.", (_L, "infra"), 5, "High"),
    Story("S0.8", "S0.8 MLflow infra", "E0", "AC: pinned values + Terraform; community chart 1.11.4 per D-000/D-05b; S3 artifact store on SeaweedFS. Trace: T008.", (_L, "infra"), 3, "High"),
    Story("S0.9", "S0.9 Airflow infra", "E0", "AC: official chart, KubernetesExecutor, git-sync DAG deployment; pinned values + Terraform. Trace: T009.", (_L, "infra"), 3, "High"),
    Story("S0.10", "S0.10 Argo Workflows infra", "E0", "AC: pinned values + Terraform; GPU RBAC + training-namespace service account. Trace: T010.", (_L, "infra"), 3, "High"),
    Story("S0.11", "S0.11 Platform bring-up runbook", "E0", "AC: docs/runbooks/02-platform-bringup.md with per-component validation; requires T006-T010 review-APPROVED. Trace: T011.", (_L, "runbook"), 3, "High"),
    Story("S0.12", "S0.12 Apply platform on cluster", "E7", "AC: plan.md Phase-0 gate demonstrated (nvidia-smi in pod, Airflow UI, Argo GPU hello-world, lakeFS repo, MLflow UI); G-3 logged. Owner story. Trace: T012.", (_L, "owner"), None, "Highest"),
    # E1 — Ingestion (T013-T022)
    Story("S1.1", "S1.1 STAC client contract tests (failing)", "E1", "AC: AOI query, pagination, band resolution tests exist against recorded fixtures and fail before implementation. Trace: T013.", (_L, "tdd"), 3, "High"),
    Story("S1.2", "S1.2 Tile store + lakeFS contract tests (failing)", "E1", "AC: tile store I/O + lakeFS commit flow tests exist and fail before implementation. Trace: T014.", (_L, "tdd"), 3, "High"),
    Story("S1.3", "S1.3 config.py via pydantic-settings", "E1", "AC: AOI, bands, thresholds, cadence, endpoints all config-sourced (Constitution III); no magic numbers. Trace: T015.", (_L, "config"), 3, "High"),
    Story("S1.4", "S1.4 STAC client implementation", "E1", "AC: Earth Search sentinel-2-l2a queries with retry/backoff budget; contract tests green. Trace: T016.", (_L, "ingest"), 5, "High"),
    Story("S1.5", "S1.5 Tile store + cloud mask", "E1", "AC: SCL mask, per-scene cloud fraction, windowed COG reads, read-throughput micro-benchmark logged. Trace: T017.", (_L, "ingest"), 5, "High"),
    Story("S1.6", "S1.6 Local STAC catalog", "E1", "AC: catalog writer + query API green against contract tests. Trace: T018.", (_L, "ingest"), 3, "Medium"),
    Story("S1.7", "S1.7 lakeFS ops module", "E1", "AC: commit-per-ingest, branch-per-experiment, snapshot pinning; contract tests green. Trace: T019.", (_L, "data"), 3, "High"),
    Story("S1.8", "S1.8 Ingest DAG", "E1", "AC: scheduled, idempotent, bounded backfill; smoke test in tests/smoke/. Trace: T020.", (_L, "pipeline"), 5, "High"),
    Story("S1.9", "S1.9 Ingest operations runbook", "E1", "AC: docs/runbooks/03-ingest-ops.md incl. STAC outage response. Trace: T021.", (_L, "runbook"), 2, "Medium"),
    Story("S1.10", "S1.10 Observe two unattended ingests", "E7", "AC: plan.md Phase-1 gate (two scheduled real-scene ingests) observed; G-4 logged. Owner story. Trace: T022.", (_L, "owner"), None, "High"),
    # E2 — Training (T023-T032)
    Story("S2.1", "S2.1 Training contract tests (failing)", "E2", "AC: entrypoint interface, MLflow logging contract, registry transitions tested and failing first. Trace: T023.", (_L, "tdd"), 3, "High"),
    Story("S2.2", "S2.2 Label bootstrap", "E2", "AC: public land-cover weak labels for AOI with documented caveats (D-04). Trace: T024.", (_L, "data"), 3, "Medium"),
    Story("S2.3", "S2.3 Dataset assembly", "E2", "AC: pinned lakeFS snapshot to torchgeo patches. Trace: T025.", (_L, "data"), 3, "High"),
    Story("S2.4", "S2.4 Baseline training entrypoint", "E2", "AC: U-Net/ResNet50 with AMP + grad-accum, IoU/F1 eval, MLflow logs {lakeFS commit, git SHA, config hash}. Trace: T026.", (_L, "train"), 5, "High"),
    Story("S2.5", "S2.5 Argo training workflow", "E2", "AC: preprocess-train-eval-register(Staging) with GPU requests for 5060 Ti. Trace: T027.", (_L, "infra"), 3, "High"),
    Story("S2.6", "S2.6 Registry ops", "E2", "AC: promote/archive/rollback as MLflow stage transitions; unit tests green. Trace: T028.", (_L, "registry"), 3, "High"),
    Story("S2.7", "S2.7 Baseline workflow run + reproducibility", "E7", "AC: US2 reproducibility acceptance verified; wall-clock + GPU util recorded. Owner story. Trace: T029.", (_L, "owner"), None, "High"),
    Story("S2.8", "S2.8 Foundation-model spike (doc only)", "E2", "AC: docs/decisions/fm-selection.md recommends Clay vs Prithvi-EO with fine-tune config for 16GB. Trace: T030.", (_L, "research"), 2, "Medium"),
    Story("S2.9", "S2.9 Fine-tune entrypoint", "E2", "AC: per T030 recommendation; baseline-beats gate encoded in eval. Trace: T031.", (_L, "train"), 5, "Medium"),
    Story("S2.10", "S2.10 Fine-tune run + gate demo", "E7", "AC: plan.md Phase-2 gate; baseline-beats gate shown in both directions. Owner story. Trace: T032.", (_L, "owner"), None, "High"),
    # E3 — CT loop (T033-T040)
    Story("S3.1", "S3.1 Drift contract tests (failing)", "E3", "AC: drift API, trigger idempotency, hysteresis on synthetic sequences tested and failing first. Trace: T033.", (_L, "tdd"), 3, "High"),
    Story("S3.2", "S3.2 Reference-stats builder", "E3", "AC: reference stats from training snapshot. Trace: T034.", (_L, "drift"), 3, "High"),
    Story("S3.3", "S3.3 Drift metrics via standard libs", "E3", "AC: PSI/KS per band + prediction-class shift via off-the-shelf libs only (Constitution II); Prometheus export. Trace: T035.", (_L, "drift"), 3, "High"),
    Story("S3.4", "S3.4 Trigger emitter", "E3", "AC: hysteresis window + cooldown + queue-depth-1 coalescing. Trace: T036.", (_L, "drift"), 3, "High"),
    Story("S3.5", "S3.5 Drift DAG", "E3", "AC: post-ingest sensor to compute/export/maybe-trigger; starvation vs shift distinguished. Trace: T037.", (_L, "pipeline"), 3, "High"),
    Story("S3.6", "S3.6 Retrain DAG + shadow eval", "E3", "AC: trigger-snapshot-train-shadow-eval-gated-promotion; auto vs operator-approve from config. Trace: T038.", (_L, "pipeline"), 5, "High"),
    Story("S3.7", "S3.7 CT ops + rollback runbooks", "E3", "AC: docs/runbooks/04-ct-ops.md + 05-rollback.md. Trace: T039.", (_L, "runbook"), 2, "Medium"),
    Story("S3.8", "S3.8 Forced-drift E2E + rollback drill", "E7", "AC: plan.md Phase-3 gate; rollback drill under 10 min (SC-004). Owner story. Trace: T040.", (_L, "owner"), None, "High"),
    # E4 — Serving (T041-T045)
    Story("S4.1", "S4.1 Serving contract tests (failing)", "E4", "AC: serving API, stage-loader, canary split tested and failing first. Trace: T041.", (_L, "tdd"), 3, "Medium"),
    Story("S4.2", "S4.2 FastAPI serving app", "E4", "AC: loads by registry stage; canary ratio from config; per-version Prometheus metrics. Trace: T042.", (_L, "serve"), 5, "Medium"),
    Story("S4.3", "S4.3 Serving deployment manifests", "E4", "AC: 8GB-GPU deployment with readiness/liveness and single-config revert. Trace: T043.", (_L, "infra"), 3, "Medium"),
    Story("S4.4", "S4.4 Canary operations runbook", "E4", "AC: docs/runbooks/06-canary.md incl. regression response. Trace: T044.", (_L, "runbook"), 2, "Medium"),
    Story("S4.5", "S4.5 Canary regression demo", "E7", "AC: plan.md Phase-4 gate (canary regression alert + revert). Owner story. Trace: T045.", (_L, "owner"), None, "Medium"),
    # E5 — Observability & soak (T046-T052)
    Story("S5.1", "S5.1 kube-prometheus-stack + alert routes", "E5", "AC: pinned values + Terraform; DAG-failure, drift-trigger, canary-regression routes. Trace: T046.", (_L, "infra"), 3, "High"),
    Story("S5.2", "S5.2 Grafana dashboards as code", "E5", "AC: DAG health, Argo states, GPU util/mem/temp, drift series, serving per-version under dashboards/. Trace: T047.", (_L, "dashboards"), 3, "High"),
    Story("S5.3", "S5.3 Remaining runbooks + templates", "E5", "AC: rebuild, GPU-operator recovery, scheduler failure, storage recovery runbooks; postmortem + soak-log templates. Trace: T048.", (_L, "runbook"), 3, "High"),
    Story("S5.4", "S5.4 Deploy observability + alert drills", "E7", "AC: all three alert classes fire in drills. Owner story. Trace: T049.", (_L, "owner"), None, "High"),
    Story("S5.5", "S5.5 P40 node join (optional lesson)", "E7", "AC: training job scheduled to node B; heterogeneous-GPU pain documented as incident (R-05). Owner story. Trace: T050.", (_L, "owner"), None, "Low"),
    Story("S5.6", "S5.6 Rebuild-runbook verification", "E7", "AC: platform torn down and rebuilt once from docs (SC-006). Owner story. Trace: T051.", (_L, "owner"), None, "High"),
    Story("S5.7", "S5.7 Six-week soak", "E7", "AC: Constitution VI definition of done - 6 weeks operated, 1 organic drift retrain, 3 incident postmortems, 1 rollback drill; only the operator marks done. Owner story. Trace: T052.", (_L, "owner"), None, "Highest"),
    # E6 - Reconciliation and integration hardening (T053-T062, RB-012).
    # Declaring these does NOT unlock them; RB-012 authorizes execution of none.
    Story("S6.1", "S6.1 Serving startup wiring and a healthy container", "E6", "AC: a production model is loaded outside tests, /healthz reports ok in the shipped image, and the Dockerfile port env names match the config fields they claim to set. Trace: T053.", (_L, "serve"), 5, "High"),
    Story("S6.2", "S6.2 Structured-logging rollout and message redaction", "E6", "AC: configure_logging runs at every production entrypoint and credential redaction covers the message path, not only extra= fields. Trace: T054.", (_L, "infra"), 5, "High"),
    Story("S6.3", "S6.3 Real request-body size limit", "E6", "AC: an oversized /predict body is rejected before it is read and parsed, proven by a test that measures the allocation rather than the comparison operator. Trace: T055.", (_L, "serve"), 3, "Medium"),
    Story("S6.4", "S6.4 Honest lakeFS simulation", "E6", "AC: commit ids are deterministic for a given scene, and every log line naming a lakeFS object says SIMULATED until a real client exists. Trace: T056.", (_L, "data"), 3, "High"),
    Story("S6.5", "S6.5 Retroactive review of T013-T052", "E6", "AC: every T013-T052 task carries a recorded spec-guardian and adversarial-reviewer outcome, and the tasks that pass are checked off. Required by RB-010 and owned by no part until RB-012. Trace: T057.", (_L, "governance"), 8, "Highest"),
    Story("S6.6", "S6.6 Close the import-linter contract hole", "E6", "AC: a port importing its own concrete counterpart breaks a contract, and a positive control proves lint-imports exits non-zero on a planted violation. Trace: T058.", (_L, "infra"), 3, "High"),
    Story("S6.7", "S6.7 Real MLflow adapter behind ModelRegistryPort", "E6", "AC: gated on the operator adapter-disposition decision; one port-conformance suite pins both the in-memory fake and the adapter. Trace: T059.", (_L, "registry"), 5, "Medium"),
    Story("S6.8", "S6.8 Real lakeFS adapter behind DataVersionPort", "E6", "AC: gated on the same operator decision; depends on T056 and on a composition root existing. Trace: T060.", (_L, "data"), 5, "Medium"),
    Story("S6.9", "S6.9 Close the D-012 config-wiring gaps", "E6", "AC: F1 through F5 of docs/decisions/012 are wired, drift/trigger.py first since its config fields already exist and name that file in their own descriptions. Trace: T061.", (_L, "config"), 3, "Medium"),
    Story("S6.10", "S6.10 Lock the registry rollback path", "E6", "AC: rollback_production either takes the lock or transition_stage stops promising an invariant it cannot hold. Trace: T062.", (_L, "registry"), 2, "Medium"),
    Story("S6.11", "S6.11 Fix the ECE weight shape mismatch", "E6", "AC: calibration_error asserts its weights and deviations are the same shape instead of letting numpy broadcast, expected calibration error is bounded in zero to one for every input, and the Hypothesis property test stops reddening CI intermittently. Trace: T063.", (_L, "train"), 3, "High"),
)
# fmt: on


# ── Guard functions (run by tests/unit/test_projections.py) ─────────────────


def csv_projected_fields_are_plain_ascii() -> list[str]:
    """Return violations: CSV-bound text containing markdown or non-ASCII.

    Covers EPIC fields too — `Epic.name`/`Epic.summary` are CSV-projected by
    `projections.render_csv`, and the first version checked only stories, so a
    non-ASCII em-dash in an epic summary shipped straight through.
    """
    bad: list[str] = []
    for epic in EPICS:
        for text in (epic.name, epic.summary):
            if any(ord(character) > 127 for character in text) or "**" in text:
                bad.append(f"{epic.key}: {text[:60]!r}")
    for story in STORIES:
        for text in (story.title, story.acceptance):
            if any(ord(character) > 127 for character in text) or "**" in text:
                bad.append(f"{story.key}: {text[:60]!r}")
    return bad


def every_story_traces_to_a_task() -> list[str]:
    """Return story keys whose acceptance text names no 'Trace:' anchor."""
    return [story.key for story in STORIES if "Trace:" not in story.acceptance]


def owner_stories_carry_no_points() -> list[str]:
    """Owner stories render a BLANK points cell — never 0, and never a number.

    The first version returned only `points == 0`, so a story labelled `owner`
    carrying `points=5` passed a guard whose name and docstring both promised
    it could not. Both halves are checked now.
    """
    zero_points = [story.key for story in STORIES if story.points == 0]
    owner_with_points = [
        story.key for story in STORIES if "owner" in story.labels and story.points is not None
    ]
    return sorted(set(zero_points) | set(owner_with_points))


def every_story_epic_exists() -> list[str]:
    """Return story keys pointing at an undeclared epic."""
    epic_keys = {epic.key for epic in EPICS}
    return [story.key for story in STORIES if story.epic not in epic_keys]


def labels_are_declared() -> list[str]:
    """Return items using a label outside the closed :data:`LABELS` set."""
    bad: list[str] = []
    for epic in EPICS:
        bad += [f"{epic.key}: {label}" for label in epic.labels if label not in LABELS]
    for story in STORIES:
        bad += [f"{story.key}: {label}" for label in story.labels if label not in LABELS]
    return bad


def labels_fit_the_csv_projection() -> list[str]:
    """Return items carrying more labels than the CSV has Labels columns.

    Without this the emitter silently dropped the fourth label (it sliced to
    three), which is data loss inside the module whose premise is that the
    backlog cannot disagree with the plan.
    """
    over: list[str] = [epic.key for epic in EPICS if len(epic.labels) > LABEL_COLUMNS]
    over += [story.key for story in STORIES if len(story.labels) > LABEL_COLUMNS]
    return over


def keys_are_unique() -> list[str]:
    """Return any epic or story key declared more than once."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for key in [epic.key for epic in EPICS] + [story.key for story in STORIES]:
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return sorted(duplicates)


#: Every convention guard, so callers cannot forget to run a new one.
GUARDS: Final = (
    csv_projected_fields_are_plain_ascii,
    every_story_traces_to_a_task,
    owner_stories_carry_no_points,
    every_story_epic_exists,
    labels_are_declared,
    labels_fit_the_csv_projection,
    keys_are_unique,
)
