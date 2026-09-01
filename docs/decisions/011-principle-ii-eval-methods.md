# D-011: Off-the-shelf replacements for `eval/bootstrap.py`'s spatial block bootstrap and `eval/superiority.py`'s paired promotion gate — Principle II options memo (RB-010 Part 2)

**Status:** PROPOSED — awaiting operator decision. Nothing in this document is implemented. `src/orbital_drift/eval/bootstrap.py` and `src/orbital_drift/eval/superiority.py` are unchanged by this PR; per RB-010's own sequencing (`docs/decision-log.md`, 2026-09-01), Part 3 (implementation) may not start until the operator reads this memo and logs the method-plus-interpretation decision as its own `docs/decision-log.md` entry.
**Audience:** the operator (the decision below is theirs to make) and whichever agent executes RB-010 Part 3 once it is logged.
**Provenance, stated precisely:** this entire document is an **agent research conclusion the operator has not yet seen or approved** — including D-011/08's recommendation, which is explicitly labeled as agent judgment the operator may override in whole or in part. RB-010 itself (the program that authorizes this memo) was operator-approved in-session before work started; the *method choice* it defers to this memo was explicitly carved out as NOT authorized by that approval ("the Principle II method choice is tracked separately via Part 2's memo and is explicitly NOT authorized by this entry").
**Decision-ID namespace:** this file's `D-011/D-nn` series is independent of every other `docs/decisions/*.md` file's series, of `plan.md`'s own `D-01…D-05`, and — per `docs/decision-log.md`'s own namespace rule (rule 3 / design D7: "This file owns `DEC-`/`RB-`/`G-` IDs only. ADR-style rationale documents keep their `D-nn` ids under `docs/decisions/`... A log line decides; an ADR explains.") — of the `DEC-`/`RB-`/`G-` namespace that the operator's eventual decision will actually be logged under. Cross-references to this file are written `D-011/D-nn`.
**Why this exists:** RB-010's six-lens review found a NON-NEGOTIABLE Principle II violation — `eval/bootstrap.py` and `eval/superiority.py` hand-roll statistical resampling and promotion-gate logic with no `scipy`/`arch`/`statsmodels` import — and authorized this memo (RB-010 Part 2) specifically so the operator chooses a remediation path with real alternatives in front of them, rather than an agent picking silently. Retrieval date for every external source (PyPI JSON metadata, package source distributions) cited below: **2026-09-01.**

---

## D-011/01 — What the two files actually compute today

Read in full before writing anything else below. Both files' only non-stdlib imports are `numpy` (`numpy.random.{PCG64,Generator}`, `numpy.typing.NDArray`) and this repo's own `orbital_drift.observability.logging.get_logger` — confirmed by reading every import line: `bootstrap.py:18-28`, `superiority.py:11-21`. No `scipy`, `arch`, `statsmodels`, or `sklearn` import anywhere in either file.

### `eval/bootstrap.py` — a 2-D spatial moving-block bootstrap

Module docstring cites Hall (1985) (`doi.org/10.1016/0304-4149(85)90212-1`, spatial tile/block resampling) and Künsch (1989) (`doi.org/10.1214/aos/1176347265`, the 1-D moving-block reference), and explicitly distinguishes itself from blocked spatial *cross-validation* (Roberts et al. 2017) — this is uncertainty quantification on an already-defined evaluation set, not a train/test-split methodology.

- `BlockSize(rows: int, columns: int)` (`bootstrap.py:34-39`) — a genuinely **2-D rectangular window shape**, not a scalar length.
- `SpatialBlockBootstrapConfig(block_size, confidence_level, replicates, seed)` (`bootstrap.py:42-49`); `BootstrapResult(observed_statistic, lower_bound, upper_bound, confidence_level, replicates, seed)` (`bootstrap.py:60-69`).
- `_moving_block_indices(grid, block_size, rng)` (`bootstrap.py:103-123`): draws `blocks_needed = ceil(grid.size / (block_size.rows * block_size.columns))` random, **contiguous, non-wrapping** `rows×columns` windows — `row_start` drawn uniformly from `[0, grid.shape[0] - block_size.rows]`, `column_start` from `[0, grid.shape[1] - block_size.columns]` — concatenates their flattened contents and truncates to exactly `grid.size` indices.
- `resample_spatial_blocks(values, *, block_size, rng) -> SpatialResample` (`bootstrap.py:126-136`): one public-API draw; the caller-supplied `Generator` is mandatory ("so experiment evidence can reproduce the exact selected windows without relying on process-global random state").
- `spatial_block_bootstrap(values, *, statistic, config) -> BootstrapResult` (`bootstrap.py:147-186`): seeds `Generator(PCG64(config.seed))`; computes the observed statistic once; draws `config.replicates` **independent** moving-block resamples and the statistic on each; forms a **percentile** interval (`bootstrap.py:176-178`):
  ```python
  tail_probability = (1.0 - config.confidence_level) / 2.0
  lower = min(float(np.quantile(replicates, tail_probability)), observed)
  upper = max(float(np.quantile(replicates, 1.0 - tail_probability)), observed)
  ```
  Percentile is the **only** interval-construction rule implemented — no basic/BCa/studentized alternative — and the `min`/`max` against `observed` is a project-authored rule ("makes the result a conservative promotion-gate summary... the reported interval brackets the statistic it qualifies", `bootstrap.py:152-155`) with no off-the-shelf equivalent checked for in this memo.

### `eval/superiority.py` — a paired difference bootstrap with a minimum-effect gate

Module docstring: "A confidence interval for one model's metric only shows whether that metric exceeds zero; it cannot establish superiority over a champion... This paired bootstrap is applicable to complex performance metrics as described by Berg-Kirkpatrick, Burkett, and Klein (EMNLP 2012)" (`aclanthology.org/D12-1091/`). The module's own docstring title is literally "Paired spatial block-bootstrap **promotion gate** for candidate superiority" (`superiority.py:1`) — this code exists specifically to decide whether a retrained candidate ships.

- `SuperiorityConfig(block_size, confidence_level, minimum_effect, replicates, seed)` (`superiority.py:27-35`) — adds `minimum_effect: float` (validated finite and `>= 0.0`, `superiority.py:69-72`) over `bootstrap.py`'s config shape. `SuperiorityResult(..., passes: bool)` (`superiority.py:38-49`).
- `superiority_gate(candidate_values, champion_values, *, metric, config) -> SuperiorityResult` (`superiority.py:75-145`): validates both grids share shape; seeds **one** `Generator(PCG64(config.seed))`; computes `observed = metric(candidate_flat) - metric(champion_flat)` (`superiority.py:113`); for each of `config.replicates` iterations, draws **one** block-index set via `_moving_block_indices(candidate_grid, config.block_size, rng)` (`superiority.py:116` — reusing `bootstrap.py`'s private, underscore-prefixed helper directly, imported at `superiority.py:20`) and applies the **same** indices to both `candidate_flat[indices]` and `champion_flat[indices]` — this identical-indices-for-both-systems draw is what makes the test "paired." Forms the same percentile-bracketing interval as `bootstrap.py` (`superiority.py:121-123`), then:
  ```python
  passes = lower > config.minimum_effect  # superiority.py:124
  ```
  — a one-sided test: the gate passes only if the entire lower bound of the paired-difference distribution clears a minimum-effect floor. This is a non-inferiority/superiority test, not a plain two-sided significance test.

---

## D-011/02 — Candidate 1: `scipy.stats.bootstrap` — MEASURED: i.i.d. resampling only, zero block/spatial capability

`scipy==1.18.1` is already pinned (`pyproject.toml:84,94,169`) and used elsewhere in this exact package for a Principle-II-motivated purpose — `pyproject.toml:81`'s own comment on the `drift` extra reads **"PSI / KS drift metrics. scipy supplies ks_2samp; nothing bespoke (Principle II)."**, and `drift/metrics.py:98` calls `stats.ks_2samp(ref_sample, tgt_sample)`. It is therefore a zero-new-dependency candidate with a direct precedent already in this codebase for "delegate the standard method to scipy instead of writing it."

Read directly from the installed package, `.venv/lib/python3.12/site-packages/scipy/stats/_resampling.py`:

- `bootstrap(data, statistic, *, n_resamples=9999, ..., paired=False, ..., method='BCa', rng=None)` (`_resampling.py:300-303`). Its own docstring states the resampling step in plain language (`_resampling.py:311-313`): "for each sample in `data` and for each of `n_resamples`, take a random sample of the original sample (**with replacement**) of the same size as the original sample" — classic i.i.d. case resampling, one observation at a time.
- The actual draw, `_bootstrap_resample()` (`_resampling.py:75-83`):
  ```python
  def _bootstrap_resample(sample, n_resamples=None, rng=None, *, xp):
      n = sample.shape[-1]
      i = rng_integers(rng, 0, n, (n_resamples, n))  # every element drawn i.i.d.
      ...
  ```
  Every one of the `n` elements of every resample is drawn **independently and uniformly** from `[0, n)`. No block, window, or contiguity concept exists anywhere in this function or file.
- Exhaustive check: zero occurrences of `moving.?block`, `stationary.?bootstrap`, or `circular.?block` (case-insensitive regex) anywhere under the installed `scipy/` tree.
- `method=` (`'percentile'`/`'basic'`/`'bca'`) selects only the **interval-construction** rule, never the resampling scheme. `paired=True` (`_resampling.py:363-367`, "If True, `bootstrap` resamples an array of *indices* and uses the same indices for all arrays in `data`") does give the architecturally-right primitive for a candidate-vs-champion paired difference — shared per-replicate indices across two arrays — but those shared indices are still drawn i.i.d., not as contiguous blocks.

**Verdict.** `scipy.stats.bootstrap` cannot replace `_moving_block_indices` without silently discarding the reason this module exists: Hall/Künsch block resampling is specifically about *not* treating spatially-adjacent pixels as independent. Using it as-is (flatten the grid, call `bootstrap(..., paired=True)` on `(candidate_flat, champion_flat)`) would compute a real, standard, off-the-shelf CI — the **wrong** one, because a plain i.i.d. bootstrap over spatially autocorrelated pixels understates variance and overstates confidence, exactly the failure mode block bootstrapping exists to avoid. Its only safe role here is as an **interval-construction shell** wrapped around block-resampling done elsewhere (D-011/08's fallback), not as a substitute for the resampling call itself.

## D-011/03 — Candidate 2: `arch.bootstrap` — MEASURED from a checksum-verified source distribution (package not installed)

`arch` is **not installed** in `.venv/` (confirmed: no `arch`/`arch-*` entry under `.venv/lib/python3.12/site-packages/`). Assessed the way `docs/decisions/007-terraform-fmt-hook.md` D-007/01 resolved a pin with no local binary available — against the real, checksum-verified published artifact — rather than from training-data memory:

```
$ curl -s https://pypi.org/pypi/arch/json -o arch_pypi.json
# info.version: 8.0.0; releases["8.0.0"][0].upload_time_iso_8601: 2025-10-21T08:42:37Z
$ curl -s -o arch-8.0.0.tar.gz \
    https://files.pythonhosted.org/packages/61/50/f8be4b21db5eb0490aef82b592d105baac957f601805ee7fe5b9182405b2/arch-8.0.0.tar.gz
$ sha256sum arch-8.0.0.tar.gz
5e9895c2354b9475aff50797ff2191dc64dc5f79602baf0c9321310fb864b637  arch-8.0.0.tar.gz
```
This digest is byte-identical to the one PyPI's own JSON metadata publishes for this file. `tar xzf` extracted cleanly; everything below is read from that extracted tree.

**License**, read directly from the extracted `LICENSE.md`: University of Illinois/NCSA Open Source License — copyright Kevin Sheppard (University of Oxford, Dept. of Economics), 2017. Confirms PyPI's `license_expression: "NCSA"` field against the actual bundled text, not just the metadata tag. Permissive, OSI-approved, MIT/BSD-family (grants use/copy/modify/merge/publish/distribute/sublicense; only added restriction is a no-endorsement clause) — compatible with this repo's existing BSD-3-Clause dependency set (scipy, scikit-learn — confirmed via `scikit_learn-1.9.0.dist-info/METADATA`'s `License-Expression: BSD-3-Clause`).

**Maintenance signal**, from PyPI metadata: `Development Status :: 5 - Production/Stable`; measured release cadence 6.3.0 (2024-01-05) → 7.0.0 (2024-04-16) → 7.1.0 (2024-09-24) → 7.2.0 (2024-11-04) → 8.0.0 (2025-10-21) — actively released; wheels published for CPython 3.10-3.13 (3.12, this repo's pin, is covered).

**API, read directly from `arch/bootstrap/__init__.py` and `arch/bootstrap/base.py` in the verified sdist.** `arch.bootstrap` exports `IIDBootstrap`, `IndependentSamplesBootstrap`, `CircularBlockBootstrap`, `StationaryBootstrap`, `MovingBlockBootstrap`, `optimal_block_length` (`__init__.py:6-13`).

- `MovingBlockBootstrap(block_size: int, *args, seed=None, **kwargs)` (`base.py:1686-1707`, subclass of `CircularBlockBootstrap(IIDBootstrap)`). Its `update_indices()` (`base.py:1695-1707`):
  ```python
  num_blocks = self._num_items // self.block_size
  if num_blocks * self.block_size < self._num_items:
      num_blocks += 1
  max_index = self._num_items - self.block_size + 1
  indices = _get_random_integers(self._generator, max_index, size=num_blocks)
  indices = indices[:, None] + np.arange(self.block_size)
  indices = indices.flatten()
  # (then truncated to exactly self._num_items if it overshoots)
  ```
  This is, algorithmically, **the exact 1-D specialization of `orbital_drift.eval.bootstrap._moving_block_indices`**: identical `ceil(n/block_size)` block count, identical non-wrapping start bound (`n - block_size + 1`), identical flatten-and-truncate-to-`n` finish — differing only in dimensionality. Of `arch`'s three block classes, `MovingBlockBootstrap` is the one whose semantics match today's code: `CircularBlockBootstrap` wraps indices modulo `_num_items` instead (a real semantic difference — this repo's code never wraps across grid edges), and `StationaryBootstrap` draws **geometrically-distributed** block lengths (`self._p = 1.0 / block_size`, Politis & Romano's variable-length scheme) rather than the fixed length this repo uses.
  - **But `block_size` is a scalar `int` in every one of the three classes**, not a `(rows, columns)` pair. `update_indices()` operates along one axis only (`self._num_items = len(args[0])`, `base.py:415`) — proven directly by the class's own docstring example (`base.py:1666-1671`): `x = standard_normal((500, 2))`, `bs = MovingBlockBootstrap(7, x, ...)` treats `x` as 500 items of dimension 2, blocking over the 500-axis only, identical in spirit to `scipy.stats.bootstrap`'s `axis` parameter. **There is no native 2-D rectangular block-window concept anywhere in this module.**
- **Paired multi-array resampling exists natively and matches `superiority_gate`'s exact need.** `_common_size_required = True` on `IIDBootstrap` (`base.py:386`, inherited by every block subclass) enforces that every positional/keyword array shares length along axis 0 (`base.py:415,421-423`), and `bootstrap()`/`conf_int()` draw **one** shared index set per replicate and apply it to every passed array together — the class's own worked example (`base.py:359-368`): `bs = IIDBootstrap(x, y=y, z=z)` then iterating `bs.bootstrap(100)` yields one shared resample of `x`, `y`, and `z` together per iteration. So `MovingBlockBootstrap(block_size, candidate_flat, champion_flat, seed=seed)` gives exactly the "same block, both systems" pairing `superiority.py` currently hand-writes as an explicit loop calling `_moving_block_indices` once and applying it twice. (`IndependentSamplesBootstrap` sets `_common_size_required = False`, `base.py:1264` — it is the *unpaired*, independent-two-samples class, the wrong tool for this job; named here only so a future reader does not reach for it by name-similarity.)
- **Interval construction is a public, off-the-shelf, multi-method API.** `conf_int(func, reps=1000, method='basic'|'percentile'|'studentized'|'norm'|'bc'|'bca', size=0.95, tail='two'|'upper'|'lower', ...)` (`base.py:575-591`) returns a 2-row array — row 0 lower bounds, row 1 upper bounds (`base.py`'s Returns section, same block). `method='percentile'` reproduces the shape of the current interval rule; `'bca'`/`'studentized'` are a strict upgrade path this repo's hand-rolled code never implemented.

**Dependency cost — the concrete, measured number.** `arch==8.0.0`'s `requires_dist` (same PyPI JSON) is `numpy<3,>=1.22.3`, `pandas>=1.4.0`, `scipy>=1.8`, `statsmodels>=0.13.0`, `packaging`. `numpy`/`scipy` are already repo dependencies; **`pandas` is not** (confirmed: zero matches for `pandas` anywhere in `pyproject.toml`). Adopting `arch` therefore also transitively adopts `statsmodels` (D-011/04) and adds `pandas` as a wholly new, currently-unused dependency. Scale: `statsmodels-0.15.0-cp312-manylinux_x86_64.whl` alone is `12,042,643` bytes (~11.5 MiB, measured from the same PyPI JSON `releases` array) — `arch` ships a Cython extension (`_samplers.pyx`) with a documented pure-Python fallback (`_samplers_python.py`, gated by `COMPILED_SAMPLERS` in `__init__.py:16-20`, so it degrades gracefully if no wheel matches a given platform), but the transitive `pandas` + `statsmodels` weight is real and is measured in the tens of MB.

**Migration cost, concretely.** `SpatialBlockBootstrapConfig`/`BootstrapResult` and `SuperiorityConfig`/`SuperiorityResult` shapes survive unchanged — `conf_int()`'s 2-row output maps onto `lower_bound`/`upper_bound` in one line. What does **not** survive unchanged is `_moving_block_indices` itself: correct adoption needs a small, project-authored subclass of `IIDBootstrap` (modeled directly on `MovingBlockBootstrap.update_indices()` above) whose `update_indices()` generates 2-D `(rows, columns)` window indices against a `BlockSize`-shaped grid instead of a 1-D run, then defers to the parent class for the paired-resampling harness and `conf_int()`'s interval math. This is materially less project-owned statistical code than today (the replicate loop, the quantile math, and the observed-statistic-bracketing rule all move into a tested off-the-shelf library) but it is **not a zero-line swap** — see D-011/06.

## D-011/04 — Candidate 3: `statsmodels` — MEASURED: no comparable utility exists in the package at all

Not installed (confirmed: no `statsmodels` entry under `.venv/lib/python3.12/site-packages/`). Verified the same way as `arch`:

```
$ curl -s https://pypi.org/pypi/statsmodels/json -o statsmodels_pypi.json
# latest, by upload_time_iso_8601 (not lexical version sort -- pre-release tags like
# "0.6.0-rc2" sort out of order lexically): 0.15.0, uploaded 2026-08-27T10:34:19Z
$ curl -s -o statsmodels-0.15.0.tar.gz \
    https://files.pythonhosted.org/packages/a8/1f/a3cbf6ed7cf6286afec694bfef561f3e51483197716717fb8476f1f0c50e/statsmodels-0.15.0.tar.gz
$ sha256sum statsmodels-0.15.0.tar.gz
5d257fe58d0772bc46a557880ca78e2a8e07fec7bfd9d11074aef8e33e1aecbc  statsmodels-0.15.0.tar.gz     # matches PyPI's published digest
$ tar tzf statsmodels-0.15.0.tar.gz | grep -i bootstrap
                                                                                                   # zero matches
$ tar tzf statsmodels-0.15.0.tar.gz | grep -iE "block|resample"
                                                                                                   # zero matches for "resample"; 8 matches for "block", ALL unrelated
                                                                                                   # Dynamic-Factor-Model nowcasting test fixtures, e.g.
                                                                                                   # tsa/statespace/tests/results/frbny_nowcast/test_dfm_blocks_111.mat
                                                                                                   # ("blocks" of model variables, nothing to do with resampling)
```

Zero of the 2,275 entries in the source tree are named `*bootstrap*` or `*resample*`. License: BSD-3-Clause (`license_expression`, matching this repo's existing dependency license family). It is already a required transitive dependency of `arch` (D-011/03) regardless of whether it is evaluated standalone. It does supply HAC/Newey-West standard errors (`statsmodels.regression.linear_model.RegressionResults.get_robustcov_results`) — a variance-*estimation* technique for regression coefficients, not a resampling-based CI or a paired superiority test, and not applicable to an arbitrary user-supplied metric function the way `spatial_block_bootstrap`'s `Statistic` callable is.

**Verdict.** Not a standalone candidate. `statsmodels` has no block-bootstrap or general-purpose resampling module to adopt; its only relevance here is as `arch`'s already-counted transitive dependency.

## D-011/05 — Candidate 4: `sklearn.utils.resample` — MEASURED: i.i.d. only, but genuinely zero marginal dependency cost

`scikit-learn==1.9.0` is already pinned (`pyproject.toml:95,170`, `License-Expression: BSD-3-Clause`) and already imported twice in this exact package: `eval/calibration.py:26` (`sklearn.calibration.calibration_curve`, the off-the-shelf ECE this repo's calibration code already relies on) and `eval/ranking.py:17` (`sklearn.metrics.average_precision_score`). `pyproject.toml:87-91`'s comment on the `evaluation` extra states the standing rule directly: **"Principle II requires that drift and eval use standard, widely recognised methods from off-the-shelf libraries -- so these are dependencies, not code to be written."** Like scipy, this is a zero-new-dependency candidate with in-repo precedent.

Read directly from the installed package, `.venv/lib/python3.12/site-packages/sklearn/utils/_indexing.py:428-567`:

```python
def resample(
    *arrays, replace=True, n_samples=None, random_state=None, stratify=None, sample_weight=None
):
    """... The default strategy implements one step of the bootstrapping procedure. ..."""
    ...
    indices = random_state.choice(n_samples, ...)  # _indexing.py:566-567
```

The draw is `random_state.choice(n_samples, ...)` over the first-dimension length of the input arrays — i.i.d. resampling of whole rows, with the same multi-array index-consistency behavior as `scipy.stats.bootstrap(paired=True)` (`resample(X, y, ...)` returns index-consistent resamples of both), but **no block, window, or contiguity parameter anywhere in its signature or implementation** — confirmed by reading the full function body.

**Verdict.** Confirms the suspicion this task named up front: `sklearn.utils.resample` is not block-aware and has no path to become so. It is architecturally the same "one resample = one random draw of row indices" primitive as `scipy.stats.bootstrap`, minus scipy's built-in percentile/basic/BCa interval math — a caller would have to hand-write that layer, which is closer to, not further from, today's hand-rolled code. It is real, free, and already approved, but it does not solve the problem at hand and would not even reduce the amount of interval-construction code relative to scipy.

## D-011/06 — Side-by-side, and the fact the table exists to make legible

| Candidate | Already a dependency? | Block/spatial resampling? | Paired multi-array resampling? | Interval methods off-the-shelf? | New transitive deps | Migration cost |
|---|---|---|---|---|---|---|
| `scipy.stats.bootstrap` | Yes (1.18.1, pinned) | No — i.i.d. only (measured, `_resampling.py:75-83`) | Yes, `paired=True` (i.i.d. shared indices only) | percentile / basic / BCa | none | 2-D block logic fully project-authored either way; would also need to reverse-engineer scipy's vectorized-batch `statistic(sample, axis=...)` contract to accept pre-blocked data |
| `arch.bootstrap.MovingBlockBootstrap` | No (verified via checksummed sdist) | 1-D only, scalar `block_size` (measured, `base.py:1695-1707`) — same algorithm as this repo's, one axis short | Yes, native, matches `superiority_gate` almost exactly (`_common_size_required=True`) | basic / percentile / studentized / norm / bc / bca | `pandas`, `statsmodels` (measured `requires_dist`) | subclass `update_indices()` for the 2-D case; parent class supplies the paired harness + `conf_int()` |
| `statsmodels` | No | No comparable utility at all (measured: 0 of 2,275 files named `*bootstrap*`/`*resample*`) | N/A | N/A | already implied by `arch` | not a standalone candidate |
| `sklearn.utils.resample` | Yes (1.9.0, pinned) | No — i.i.d. only (measured, `_indexing.py:566-567`) | Yes, index-consistent multi-array | none built in | none | same 2-D-block gap as scipy, with less reusable machinery around it |

The blunt fact this table exists to make legible: **no candidate evaluated here — including `arch`, whose whole reason for existing is block bootstrapping — natively supports a 2-D rectangular spatial block over a grid.** Every candidate needs `BlockSize(rows, columns)`-shaped window generation to stay project-authored. What differs between them is how much of the *surrounding* machinery (paired multi-array resampling, percentile/BCa/studentized interval construction, degenerate-distribution handling) becomes off-the-shelf instead of hand-rolled.

**Also checked, not a fifth full candidate:** `pyproject.toml:87-98`'s `evaluation` extra already pins `esda==2.10.0` and `libpysal==4.15.0` (PySAL) for "Promotion-gate statistics... esda supplies Moran's I and its permutation inference" — used today in `eval/spatial.py:22-23` (`from esda import Moran`, `from libpysal.weights import W`). This is topically adjacent (already-installed, already-approved, spatial, permutation-based) and worth naming so a future reader does not have to re-derive that it was considered. It solves a **different problem**: `esda.Moran` (`.venv/lib/python3.12/site-packages/esda/moran.py:59`) computes a permutation-based **null distribution for spatial-autocorrelation significance** — repeatedly permuting *values* across a *fixed* spatial-weights structure to test whether the observed Moran's I is more clustered than chance — not a percentile CI for an arbitrary user-supplied metric, and not a paired candidate-vs-champion difference test. Nothing in `esda`/`libpysal` generates block-resampled replicates of a metric function; it is not a viable replacement for either file.

## D-011/07 — The interpretive question this memo does NOT resolve

Two textual readings of Principle II are both defensible; the choice is the operator's.

**Reading A — swapping the resampling call is sufficient.** Principle II's operative clause: "Drift and eval use standard, widely recognized methods only (PSI, KS, ECE only via off-the-shelf libs, prediction-distribution shift, IoU/F1 for segmentation)." Everything in `bootstrap.py`/`superiority.py` other than the core block-index draw and interval math is already the same *shape* of code Principle II tolerates elsewhere in this package: dataclass validation (`_validate_config`, `_validate_block_size`), finite-value checks (`_as_finite_grid`, `_statistic_value`, `_metric_value`), and structured logging — none of it is "resampling logic" in the sense Principle II's fuller sentence targets ("MUST NOT... reimplement **code, metrics, or evaluation-harness logic**"). Under this reading, once the block-index draw and interval construction are delegated to a tested off-the-shelf implementation (D-011/03's `arch` subclass, or D-011/02's scipy shell), what remains project-authored is orchestration and validation — the same category Principle II's own v1.1.0 amendment already exempted for "governance and process artifacts" (`.specify/memory/constitution.md`, "Amended 2026-08-20"), extended here by analogy to code-layer glue.

**Reading B — the enumerated list is exhaustive, and this needs a formal amendment.** Principle II names five things: PSI, KS, ECE (via off-the-shelf libs), prediction-distribution shift, and IoU/F1 for segmentation. "Block-bootstrap confidence intervals" and "paired superiority testing with a minimum-effect gate" are on that list under no reading, whatever library computes them. The constitution has already been amended once, precisely because its own text needed to say something it didn't originally say: v1.1.0 (`docs/decision-log.md` DEC-001, 2026-08-21) added the "governance and process artifacts... are explicitly outside this ban" carve-out, with its own dated ratification and its own rationale paragraph in the constitution text itself. Under this reading, `eval/bootstrap.py` and `eval/superiority.py`'s *category* of method — not just today's hand-rolled implementation of it — needs the same treatment: a v1.2.0 amendment adding "block-bootstrap / paired superiority testing via off-the-shelf libs" as a sixth enumerated method, ratified with DEC-001's rigor, before any off-the-shelf swap is *compliant* rather than merely *improved*.

**The mechanical fork this creates for logging the choice**, stated but not resolved: `docs/decision-log.md`'s own rule 2 defines `DEC-` IDs narrowly — "a CONFIRM-FIRST decision from **charter §5** is confirmed (or overridden)" — and charter §5's CONFIRM-FIRST list is closed at exactly four items (`charter/PROJECT-CHARTER.md` §5): `DEC-001` (governance kit adoption), `DEC-002` (milestone budgets), `DEC-003` (remote allowlist), `DEC-004` (coverage floor) — all four already logged and closed. There is no open `DEC-005` slot for this question today. That leaves two mechanically distinct paths, not a free choice of label:

1. **An `RB-` entry** (a "process/re-baseline decision" per decision-log.md rule 2) that borrows CONFIRM-FIRST's discipline — present this memo, log the operator's choice — without first adding a line to charter §5. Direct precedent: `RB-003`, `RB-008`, and `RB-010` itself all authorized substantive technical/process decisions via `RB-` entries with no pre-existing charter §5 line naming them.
2. **Amend charter §5 first** (add a fifth CONFIRM-FIRST line, mirroring how `DEC-001`–`004` were originally seeded per `PROJECT-CHARTER.md` §5's own format), **then** log the resulting `DEC-005`.

If Reading B is chosen, the constitution amendment itself (v1.2.0 text change) is a **third**, independent artifact from whichever of the two paths above logs the *method* choice — the same way `DEC-001` (a `docs/decision-log.md` line) and the v1.1.0 constitution-text amendment were two artifacts recording one decision, not one artifact wearing two names.

## D-011/08 — Recommendation (agent judgment; the operator may override any part of this)

**Recommend `arch.bootstrap`**, specifically: keep `BlockSize`/`SpatialBlockBootstrapConfig`/`BootstrapResult`/`SuperiorityConfig`/`SuperiorityResult` unchanged, and replace `_moving_block_indices` plus the hand-rolled replicate loop plus the hand-rolled percentile-bracketing rule with a small, project-authored subclass of `arch.bootstrap.IIDBootstrap` (modeled directly on `MovingBlockBootstrap.update_indices()`, D-011/03) that generates 2-D `(rows, columns)` window indices instead of 1-D ones, then calls the **parent** class's `conf_int(..., method="percentile")` for interval construction and its native multi-array paired resampling (`IIDBootstrap(candidate_flat, champion_flat, ...)`) for `superiority_gate`'s paired-difference need.

**Why this over `scipy.stats.bootstrap`**, which is free and already pinned: `arch` is the only evaluated candidate whose block-resampling classes are the *same algorithm family* as the code being replaced — D-011/03's line-by-line comparison of `MovingBlockBootstrap.update_indices()` against `_moving_block_indices` shows identical block-count arithmetic, identical non-wrapping start bound, identical flatten-and-truncate finish, differing only in dimensionality. The subclass this migration needs is therefore a narrow, mechanical 1-D-to-2-D generalization of a method `arch`'s own maintainers already wrote, documented, and ship tests for — not new statistical design. `scipy.stats.bootstrap` offers no comparable foothold: its resampling core has no contiguity concept at all (D-011/02), so using it correctly would mean writing the *entire* 2-D block-catalog logic from scratch **and** separately reverse-engineering scipy's vectorized-batch `statistic(sample, axis=...)` calling convention to accept it — more new surface area, not less, for a library never built with this use case in mind.

`arch`'s real cost — two new transitive dependencies (`pandas`, `statsmodels`), tens of MB, one more supply-chain surface to pin and gitleaks-scan — is real and the operator should weigh it explicitly. It buys a dependency whose published purpose is this exact statistical technique (moving/circular/stationary block bootstraps; `Development Status :: 5 - Production/Stable`; actively released through 2025-10-21), maintained by an academic domain specialist, rather than repurposing a general-purpose stats library or ML utility library outside what either was designed for.

**Fallback, if the two new dependencies are disqualifying regardless of fit:** `scipy.stats.bootstrap` used *only* as the outer percentile/BCa interval-construction shell, wrapped around block-resampled replicate statistics this repo continues to compute itself. Not a full replacement, but it moves "which interval-construction rule, and is its edge-case handling (degenerate distributions, etc.) trustworthy" onto an off-the-shelf, community-tested implementation — a smaller, more defensible slice of Principle II's concern than today's fully hand-rolled percentile rule. This fallback does not change D-011/07's interpretive question at all: the 2-D resampling core stays exactly as project-authored as it is today, either way.

---

## Ratification checklist for the operator

1. Read D-011/01–07 at minimum the D-011/06 table and D-011/07's two readings.
2. Choose a candidate: D-011/03 (`arch`) is recommended; D-011/02 (`scipy`-as-interval-shell-only) is the named fallback. Choosing a different candidate, or declining to change the implementation at all, are both legitimate outcomes of reading this memo.
3. Choose a Principle-II interpretation — Reading A or Reading B (D-011/07) — since this determines whether logging the method choice alone is sufficient, or whether a constitution v1.2.0 amendment must land first.
4. Choose the logging mechanism for #2/#3: an `RB-` entry (precedent: `RB-003`/`RB-008`/`RB-010`), or a charter §5 amendment followed by `DEC-005` (D-011/07).
5. Log the decision in `docs/decision-log.md`; update this file's Status header from PROPOSED to "decided `<date>`", citing the log line.
6. Only then does RB-010 Part 3 (implementation) unlock, per RB-010's own explicit sequencing.

## Follow-ups found during this review, NOT fixed here

Each is unscheduled and needs operator triage before it becomes a task — listing here is not agreement to do them.

| # | Finding |
|---|---|
| 1 | No `traceability/REQUIREMENT-TRACEABILITY.md` row exists for `eval/bootstrap.py` or `eval/superiority.py` today (confirmed: zero matches searching that file for "bootstrap"/"superiority"/"promotion.gate"). RB-010 Part 1 (governance/docs reconciliation) is the more natural owner of adding one, once Part 3 lands a concrete implementation to trace against. |
| 2 | `superiority.py` imports three names from `bootstrap.py`, two of them underscore-prefixed/private (`_as_finite_grid`, `_moving_block_indices`, plus the public `BlockSize`, `superiority.py:20`). Whichever candidate is chosen, Part 3 should decide whether the new shared resampling primitive gets a real public boundary between the two modules instead of a private-name cross-import — a library swap is a natural moment to fix this, but fixing it is out of this memo's scope. |
| 3 | If `arch` is chosen, `pandas` becomes a new, currently-unused transitive dependency. Part 3 should check whether it needs its own `ci/versions.env`-style pin even though it would arrive indirectly via `arch`'s own dependency resolution rather than a direct top-level extra, matching how this repo pins every other Python package by exact version (e.g. `scipy==1.18.1`). |

## Verified correct — no action

- **`superiority.py` reusing `bootstrap.py`'s `_moving_block_indices` rather than re-implementing block-index generation is correct, single-homed design**, not a duplication to fix. Both files draw from exactly one block-index generator today; whichever candidate is chosen should preserve that single-homing (whether via a shared subclass/helper, or whatever shape the chosen candidate's own paired-resampling API takes).
- **Both files' validation code** (`_validate_config`, `_validate_block_size`, `_as_finite_grid`, `_statistic_value`/`_metric_value`) **is standard defensive coding, not hand-rolled statistics** — it rejects malformed input before any resampling happens and computes no resampling scheme or test statistic itself, so it does not independently raise a Principle II question under either reading in D-011/07.
- **`esda`/`libpysal` (D-011/06) are not a missed off-the-shelf fifth candidate** — checked directly against `esda.Moran`'s source (`esda/moran.py:59`) and found to solve spatial-autocorrelation *significance testing*, a different statistical question from block-bootstrap CI construction or paired superiority testing.
