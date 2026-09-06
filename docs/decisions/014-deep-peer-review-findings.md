# Deep Peer Review Findings — Codebase Reflection

**Audience:** Operator, spec-guardian, adversarial-reviewer
**Status:** FINDINGS DOCUMENTED — awaiting operator triage
**Date:** 2026-09-06
**Reviewer:** Orchestrating agent (self-reflection task)
**Scope:** Critical technical debt identified in the reflection plan, verified by direct code review

---

## Executive Summary

This document records findings from a deep peer review of the 13 critical/high-priority issues identified in the codebase reflection plan. Each finding includes root cause analysis with file:line evidence, impact assessment, and concrete fix recommendations.

**Severity Legend:**
- **CRITICAL:** Blocks production deployment or creates data integrity risk
- **MAJOR:** Significant functional gap or security concern
- **MINOR:** Code quality or maintainability issue

---

## Finding 1: Container Cannot Become Healthy (CRITICAL)

**Task:** T053
**Files:** `src/orbital_drift/serve/app.py`, `Dockerfile`

### Root Cause

```text
# serve/app.py:200-202 — Module-level singleton created at import time
dev = _resolve_serve_device()
container = ModelContainer(device=dev)

# serve/app.py:126-127 — Models are None by default
self.production_model: nn.Module | None = None
self.staging_model: nn.Module | None = None

# serve/app.py:217-224 — /healthz returns 503 when no model loaded
if container.production_model is None:
    response.status_code = 503
    return {"status": "degraded", ...}
```

### Evidence Chain

1. `set_models()` (lines 140-167) is the ONLY way to load a model
2. Grep for `set_models` in `src/`: **zero calls** outside tests
3. Dockerfile line 87-88: `HEALTHCHECK ... /healthz || exit 1`
4. Kubernetes would see perpetual 503s → **CrashLoopBackOff**

### Additional Port Configuration Defect

Three inconsistent port sources discovered:

| Source | Variable | Value |
|--------|----------|-------|
| Dockerfile ENV (line 46) | `ORBITAL_DRIFT_SERVE_PORT` | 8000 |
| Dockerfile ENTRYPOINT (line 90) | hardcoded | `--port 8000` |
| config.py | `ORBITAL_DRIFT_SERVING_PORT` | 8080 (default) |

The ENTRYPOINT **ignores** the env var it defines. The config reads a **different** env var name.

### Fix Recommendation

1. Create a composition root that loads config and calls `set_models()` at startup
2. Make lakeFS credentials conditionally required (only when `storage_backend=="lakefs"`)
3. Fix port variable name consistency: `ORBITAL_DRIFT_SERVING_PORT` everywhere
4. Update ENTRYPOINT: `["uvicorn", ..., "--port", "${ORBITAL_DRIFT_SERVING_PORT:-8000}"]`

---

## Finding 2: ECE Can Exceed 1.0 (CRITICAL)

**Task:** T063
**File:** `src/orbital_drift/eval/calibration.py`

### Root Cause

```text
# calibration.py:57-78 — _bin_weights derives occupancy with its own rule
bin_indices = np.clip(
    np.searchsorted(boundaries, probabilities, side="right") - 1,
    0,
    bin_count - 1,
)
counts = np.bincount(bin_indices, minlength=bin_count)
return counts[counts > 0] / probabilities.size

# calibration.py:107-114 — sklearn uses DIFFERENT bin derivation
fraction_of_positives, mean_predicted_value = calibration_curve(...)
weights = _bin_weights(...)  # May return different length!
deviations = np.abs(fraction_of_positives - mean_predicted_value)
ece = float(np.sum(weights * deviations))  # numpy BROADCASTS!
```

### Reproduction

```text
labels = [False, False, False]
probabilities = [1.0, 0.5, 0.5]
bin_count = 2
strategy = "quantile"
# Result: ECE = 1.5 (should be in [0, 1])
```

When `_bin_weights` returns 1 element and `deviations` has 2 elements, numpy broadcasts rather than raising. The "weights sum to 1.0" sanity check **passes** because 1 weight can still sum to 1.0.

### Fix Recommendation

```text
# Add shape assertion before multiplication
if weights.shape != deviations.shape:
    raise ValueError(
        f"Shape mismatch: weights {weights.shape} vs deviations {deviations.shape}. "
        "This indicates sklearn and _bin_weights disagree on populated bins."
    )
```

### CI Impact

The Hypothesis test (`max_examples=20`) fails ~2 in 20 runs due to this bug, causing intermittent CI failures on unrelated PRs.

---

## Finding 3: Import-Linter Contract Hole (MAJOR)

**Task:** T058
**File:** `.importlinter`, `tests/architecture/test_import_boundaries.py`

### Current Contract Coverage

| Contract | What it Forbids |
|----------|-----------------|
| `layers` | `domain` importing `ports` (2 layers only) |
| `forbidden` | domain/ports importing numpy/torch/etc. |
| `independence` | geometry importing temporal or vice versa |

### What Slips Through (Verified by Analysis)

1. `ports/dataversion.py` → `data/lakefs_ops` (port importing adapter)
2. `ingest` → `serve` (cross-cutting adapter dependency)
3. `ports/compute` → `drift/trigger` (port importing unrelated module)

The `layers` contract lists only `ports` and `domain`, so it forbids **exactly one edge** (domain→ports).

### Missing Positive Control

`test_import_boundaries.py:58-71` asserts `lint-imports` returns 0. There is **no test** asserting it returns non-zero on a known violation. This means:
- A contract that silently allows everything would pass
- A misconfigured `.importlinter` would not be caught

### Fix Recommendation

1. Expand layers contract:
```ini
[importlinter:contract:layers]
layers =
    orbital_drift.serve
    orbital_drift.quality
    orbital_drift.observability
    orbital_drift.registry
    orbital_drift.train
    orbital_drift.drift
    orbital_drift.ingest
    orbital_drift.data
    orbital_drift.config
    orbital_drift.ports
    orbital_drift.domain
```

2. Add positive control test with a fixture violation

---

## Finding 4: Logging Redaction Never Runs in Production (MAJOR)

**Task:** T054
**Files:** `src/orbital_drift/observability/logging.py`, all pipeline modules

### Evidence

**Production modules using stdlib logger (NO redaction):**
```
src/orbital_drift/registry/ops.py:15     logging.getLogger(__name__)
src/orbital_drift/train/baseline.py:21   logging.getLogger(__name__)
src/orbital_drift/serve/app.py:23        logging.getLogger(__name__)
src/orbital_drift/data/lakefs_ops.py:14  logging.getLogger(__name__)
src/orbital_drift/ingest/tile_store.py:29 logging.getLogger(__name__)
src/orbital_drift/ingest/stac_client.py:40 logging.getLogger(__name__)
src/orbital_drift/drift/trigger.py:33    logging.getLogger(__name__)
```

**Only eval modules using structured logger (WITH redaction):**
```
src/orbital_drift/eval/superiority.py:21 get_logger("eval.superiority")
src/orbital_drift/eval/spatial.py:27     get_logger("eval.spatial")
src/orbital_drift/eval/bootstrap.py:28   get_logger("eval.bootstrap")
```

### Sensitive Data Leak Risk

```text
# stac_client.py:190-196 — logs response.text verbatim
logger.warning(
    "STAC query failed with status %d: %s (attempt %d/%d)",
    response.status_code,
    response.text,  # Could contain auth tokens, error details
    ...,
)
```

### Redaction Gap

The `JsonFormatter` only redacts `extra={}` fields. Pipeline modules use `%`-interpolation into the message string, which bypasses redaction entirely.

### Fix Recommendation

1. Add `configure_logging()` call to every production entrypoint (serve startup, future CLI)
2. Migrate all 7 pipeline modules from `logging.getLogger(__name__)` to `get_logger(...)`
3. Use structured `extra={...}` logging instead of `%`-interpolation

---

## Finding 5: Request-Size Bound is Ineffective (MAJOR)

**Task:** T055
**File:** `src/orbital_drift/serve/app.py:79-91`

### Root Cause

```text
@field_validator("image_array")
@classmethod
def _bound_image_array_size(cls, value: list[list[list[float]]]) -> ...:
    """Rejects oversized payloads..."""
    total_elements = sum(len(row) for channel in value for row in channel)
    if total_elements > _MAX_IMAGE_ELEMENTS:
        raise ValueError(...)
```

This is a pydantic **after-validator**. By the time it runs:
1. The entire JSON body has been parsed
2. The nested list has been materialized in memory
3. The allocation the bound exists to prevent has **already happened**

### Fix Recommendation

Implement at ASGI/uvicorn layer:

```text
# Option 1: Starlette middleware
from starlette.middleware import Middleware
from starlette.requests import Request

class BodySizeLimitMiddleware:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            content_length = dict(scope.get("headers", [])).get(b"content-length")
            if content_length and int(content_length) > MAX_BODY_SIZE:
                # Reject before reading body
```

---

## Finding 6: Registry Rollback Not Thread-Safe (MAJOR)

**Task:** T062
**File:** `src/orbital_drift/registry/ops.py:185-206`

### Evidence

```text
def rollback_production(self, model_name: str) -> int | None:
    """Rolls back Production stage to the most recent Archived version.

    Not guarded by self._lock. RB-010 Part 10 scopes locking to the two
    confirmed races in register_model_version and transition_stage; this
    method has the same read-then-write shape against self._mock_registry
    but concurrent-safety here is out of scope for that fix and is not
    claimed.
    """
```

The comment explicitly acknowledges the race condition. Two concurrent rollbacks could:
1. Both read the same current Production version
2. Both archive it
3. Both promote different Archived versions to Production
4. Leave multiple versions in Production state

### Fix Recommendation

```text
def rollback_production(self, model_name: str) -> int | None:
    with self._lock:  # Add locking
        curr_prod = self.get_stage_version(model_name, "Production")
        # ... rest of method
```

---

## Finding 7: Drift Trigger Config Unwired (MINOR)

**Task:** T061 (D-012 F3)
**File:** `src/orbital_drift/drift/trigger.py:50-51`

### Evidence

```text
def __init__(
    self,
    hysteresis_window: int = 3,  # pin: follow-up D-012 F3 (config field exists, unwired)
    cooldown_scenes: int = 5,  # pin: follow-up D-012 F3 (config field exists, unwired)
    ...
```

Config fields `OrbitalDriftConfig.drift_hysteresis_window` and `drift_cooldown_scenes` exist but are never consulted.

---

## Summary Table

| ID | Finding | Severity | Task | Fix Complexity |
|----|---------|----------|------|----------------|
| F1 | Container cannot become healthy | CRITICAL | T053 | Medium |
| F2 | ECE can exceed 1.0 | CRITICAL | T063 | Low |
| F3 | Import-linter contract hole | MAJOR | T058 | Low |
| F4 | Logging redaction unwired | MAJOR | T054 | Medium |
| F5 | Request-size bound ineffective | MAJOR | T055 | Medium |
| F6 | Registry rollback not thread-safe | MAJOR | T062 | Low |
| F7 | Drift trigger config unwired | MINOR | T061 | Low |

---

## Recommended Fix Order

1. **T063** (ECE bug) — 6 lines, no production change, stops intermittent CI failures
2. **T058** (import-linter) — config change + test, prevents architectural decay
3. **T062** (rollback lock) — 2 lines, fixes race in SC-004 critical path
4. **T053** (serve startup) — larger change, blocks any real deployment
5. **T054** (logging rollout) — systematic but straightforward migration
6. **T055** (request-size) — requires middleware design decision
7. **T061** (config wiring) — straightforward but low urgency

---

## Review Protocol Compliance

This review follows CLAUDE.md's collaboration protocol:
- Findings are numbered and severity-ranked
- Each has file:line evidence
- Concrete fixes are proposed
- Impact/risk is assessed

Next step per protocol: `spec-guardian` reviews this document for constitution conformance, then `adversarial-reviewer` validates the technical accuracy of each finding.
