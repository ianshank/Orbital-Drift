---
name: canary-rollback-drill
description: Use when conducting periodic continuous training rollback drills or executing an emergency model demotion.
---

# Canary Rollback Drill Runbook & Workflow

Use this skill when conducting periodic continuous training rollback drills or executing an emergency rollback on `node A`.

## Prerequisites
- Operator or agent has access to MLflow Model Registry (`ModelRegistryOps`).
- FastAPI serving container is active and emitting metrics at `/metrics`.

## Step 1: Detect Degradation Trigger
A rollback drill is triggered under any of the following conditions:
1. **Canary IoU Regression**: Candidate Staging model displays IoU < Production baseline in shadow eval.
2. **Inference Latency Spike**: P99 serving latency on `/predict` exceeds `max_p99_latency_ms` (default 50.0ms).
3. **Severe Distribution Shift**: Sudden drift detection with PSI > 0.40 on primary spectral bands.

## Step 2: Instant Model Demotion
Demote current active candidate and promote previous stable snapshot:
```python
from orbital_drift.registry.ops import ModelRegistryOps

registry = ModelRegistryOps()
# Rollback: transition candidate back to Archived and reinstate prior Production version
registry.rollback_production_model(model_name="orbital-drift-unet", prior_stable_version=1)
```

## Step 3: Canary Traffic Neutralization
Set canary routing traffic to 0% to route all inference exclusively to the stable Production model:
```python
from orbital_drift.serve.app import container

container.update_canary_ratio(0.0)
```

## Step 4: Verification Gate
Query the serving `/healthz` and `/metrics` endpoints to confirm:
- `orbital_drift_requests_staging` stops incrementing.
- `orbital_drift_requests_production` handles 100% of live traffic.
- Average inference latency recovers within SLA (< 50ms).
