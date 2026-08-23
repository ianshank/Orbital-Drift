# Orbital-Drift: Next Steps & Operational Roadmap

## 1. Multi-Phase Deployment Plan

### Phase 1: Local & Dual-GPU Verification (Completed)
- [x] Implement STAC client with rate limiting and exponential backoff.
- [x] Implement Sentinel-2 SCL cloud masking and windowed tile store.
- [x] Build multi-spectral PyTorch dataset with 4-band normalization.
- [x] Configure lakeFS branch management and immutable commit tracking.
- [x] Implement Population Stability Index (PSI) and KS 2-sample statistical drift sensor.
- [x] Build U-Net spatial segmentation baseline with AMP fp16 autocast and gradient accumulation on GPU 0 (NVIDIA RTX 5060 Ti 16GB).
- [x] Build FastAPI canary serving service on GPU 1 (NVIDIA RTX 5060 8GB).
- [x] Author multi-tier test suite (Unit, Contract, Integration, Sanity, E2E Journey, Governance).
- [x] Build multi-stage Docker container and Docker Compose topology.
- [x] Author specialized agent definitions (`mlops-ct-agent`, `gpu-qa-agent`) and skills (`canary-rollback-drill`, `gpu-profiler`).

### Phase 2: Staging Soak & Continuous Training Validation
- [ ] Deploy Airflow DAG orchestrators for scheduled STAC polling (every 6 hours).
- [ ] Connect lakeFS client to live staging lakeFS repository.
- [ ] Execute continuous training loop on simulated seasonal drift scenarios.
- [ ] Validate MLflow Model Registry automated staging promotion triggers.
- [ ] Run Prometheus + Grafana telemetry pipeline and track canary routing splits.

### Phase 3: Production Hardening & 6-Week Soak Operations
- [ ] Execute the 6-Week Soak Test with live Sentinel-2 L2A tile ingest.
- [ ] Conduct mandatory weekly rollback drills (< 10 minutes from alert to 0% canary traffic).
- [ ] Profile GPU memory fragmentation and verify zero memory leak across 100,000 requests.
- [ ] Validate Foundation Model (Prithvi / SatMAE) fine-tuning pipeline against baseline-beats gate.

---

## 2. Operational Drill Runbook

### Rollback Drill Execution (< 10 Minutes SLA)
1. **Trigger Alert**: Emulate canary model degradation or synthetic drift event.
2. **Execute Demotion**: Call `ModelRegistryOps.rollback_production_model()` to archive candidate.
3. **Traffic Neutralization**: Call `container.update_canary_ratio(0.0)` to divert all traffic to stable production.
4. **Verification**: Query `/metrics` to confirm `orbital_drift_requests_production` handles 100% of traffic and latency returns within SLA (< 50ms).
