# D-012: Config-wiring gaps found while triaging `hardcode_scan` findings (RB-010 Part 6)

**Status:** RECORDED — informational. Nothing in this document is implemented; no `OrbitalDriftConfig` field is added or changed by it.
**Audience:** whichever agent picks up the follow-up config-wiring work these findings describe.
**Why this exists:** RB-010 Part 6 wires `orbital_drift.quality.hardcode_scan` into `ci/checks.sh` as a new `hardcode` stage. Per the part's own scope (`docs/decision-log.md`, 2026-09-01 RB-010 entry: "(6) hardcode_scan CI-wiring, depends on (4)+(5)"), doing NEW `OrbitalDriftConfig` wiring is out of scope for this part — that is a larger change than wiring a scanner into CI, and is exactly the kind of work Parts 4/5 already did for other modules. Every finding the scanner reported against the current tree was triaged to a resolution (a `# pin:` comment) so the gate lands green; the items below are the subset judged genuinely config-shaped rather than legitimate constants, each pinned with a comment that references this file so the gap stays visible instead of silently resolved by a pin comment alone.

---

## F1 — `train/baseline.py`: `SimpleUNet.__init__`'s `in_channels`/`init_features`

`in_channels: int = 4` (spectral band count) and `init_features: int = 32` (U-Net base channel width) are plain constructor defaults with no `OrbitalDriftConfig` field behind them at all, unlike `num_classes` in the same constructor (which Part 5 already wired via `_resolve_num_classes`). `in_channels` in particular tracks `OrbitalDriftConfig.bands` (the configured spectral band tuple) only by coincidence of both defaulting to 4 today — a caller who reconfigures `bands` to a different length gets no corresponding change here.

**Suggested fix:** add `OrbitalDriftConfig` fields (e.g. `model_in_channels`, `model_init_features`) and extend `SimpleUNet.__init__` with the same explicit-argument > config > literal-default precedence already used for `num_classes`.

## F2 — `data/dataset.py`: `Sentinel2PatchDataset.__init__`'s `patch_size`/`stride`

`OrbitalDriftConfig.patch_size` already exists (`config.py`, default `256`) but `Sentinel2PatchDataset.__init__`'s own `patch_size: int = 256` parameter does not consult it — only `normalize_max` was wired here in Part 5a. `stride: int = 256` has no matching config field at all.

**Suggested fix:** wire `patch_size` through the same `_resolve`-style precedence used for `normalize_max` in this module; decide (operator call) whether `stride` gets its own field or is defined as derived from `patch_size`.

## F3 — `drift/trigger.py`: `DriftTriggerManager.__init__`'s `hysteresis_window`/`cooldown_scenes`

`OrbitalDriftConfig.drift_hysteresis_window` (default `3`) and `OrbitalDriftConfig.drift_cooldown_scenes` (default `5`) already exist and their doc comments explicitly name `drift/trigger.py`'s hardcoded `3`/`5` as the literals they mirror — but `DriftTriggerManager.__init__` accepts no `config` parameter at all. Part 5c's commit (`c578a0a`) wired `drift/metrics.py` only; `drift/trigger.py` was never touched. This is the highest-priority item here since the config fields already exist and are simply unconsulted.

**Suggested fix:** add an optional `config: OrbitalDriftConfig | None = None` parameter to `DriftTriggerManager.__init__` and resolve `hysteresis_window`/`cooldown_scenes` through it, mirroring `drift/metrics.py`'s `_resolve_threshold` pattern.

## F4 — `data/lakefs_ops.py`: `LakeFSOps.__init__`'s `endpoint_url`/`repository`/`main_branch`

`OrbitalDriftConfig.lakefs_endpoint`, `lakefs_repository`, and `lakefs_main_branch` already exist, but `LakeFSOps` (distinct from `data/lakefs_ops.py`'s sibling client code touched elsewhere in RB-010) accepts no `config` parameter and was never wired by any Part 5 sub-part.

**Suggested fix:** same `config`-parameter pattern as F1-F3.

## F5 — `ingest/stac_client.py`: `STACClient.search_scenes`'s `limit` parameter

`limit: int = 10` is the one `search_scenes` parameter Part 5a's otherwise-thorough wiring of this class (`endpoint_url`, `collection`, `retry_budget`, `backoff_factor`, `timeout`, `max_cloud_cover`) left untouched. Minor — a default page size, not a drift/training-critical threshold — but the same class as the others above.

**Suggested fix:** either add an `OrbitalDriftConfig` field or explicitly record that a page-size default is deliberately left as a plain constant (unlike this memo's other items, this one may resolve as "not config-shaped" on closer look).

---

## Judged NOT to need this treatment (pinned as legitimate constants instead)

* `drift/metrics.py`'s `calculate_psi(num_bins: int = 10)` — ten quantile bins is a standard PSI-binning convention (see the function's own docstring), not a drift-decision threshold the way `psi_threshold`/`ks_alpha`/`psi_moderate_threshold` are; those three were wired in Part 5c precisely because they gate the drift verdict, `num_bins` does not.
* `drift/metrics.py`'s `max_samples = 5000` KS-test subsampling cap — a computational-cost bound (keeps `scipy.stats.ks_2samp` fast on large arrays), not a statistical threshold.

Both are pinned in-line with a comment saying so rather than listed as follow-ups above; an operator who disagrees with that judgment should re-open them here.
