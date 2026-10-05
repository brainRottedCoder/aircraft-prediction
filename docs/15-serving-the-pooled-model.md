# 15 — Serving the Pooled Model

How the backend loads and serves the multi-subset (`ALL`) RUL model: the 32-column
contract, the two sensors that are constant in FD001, subset-aware baselines, and the
deployment traps that silently degrade to the deterministic fallback.

Companion to [08 — ML Service](08-ml-service.md), which describes the FD001 serving
contract that was in place before this model existed.

## 1. Status

| Item | State |
|---|---|
| `xgboost_all_full.json` — pooled booster, 709 engines, 161 rounds | staged |
| `feature_contract_all_full.json` — 32 features | staged |
| `regime_baselines_all.json` — 14 regimes across 4 subsets | staged |
| `metrics_all_full.json` — CV 17.37 ± 0.95 | staged |
| C-MAPSS FD001–FD004, `train`/`test`/`RUL` | staged |
| Model serves without fallback | **verified** — `/healthz` `status: ok`, live API |
| All four subsets replayable via `FDT_ML_DATASET` | **verified** |
| `scripts/train_rul.py` produces this contract | **no** — see §7 |

Measured on FD001 telemetry against the piecewise-linear target capped at 125, 1801
windows: **RMSE 17.89, MAE 12.87, r = 0.899**. On late-life windows (`RUL ≤ 40`, what the
demo reaches): **RMSE 16.10, MAE 9.92**. The artifact's own 5-fold grouped CV figure is
17.37 ± 0.95 — a different row set, so not directly comparable, but the same
neighbourhood.

## 2. The contract is 32 features

```
op1, op2                                      2   operating settings
s2,s3,s4,s6,s7,s8,s9,s11,s12,s13,s14,s15,s17,s20,s21   15   sensors
s11,s4,s9,s12,s14,s7 × rollmean5 + rollstd5 12   rolling mean AND std
regime_global                                 1   absolute operating-condition id
s10, s16                                      2   constant in FD001, live in FD002/FD004
                                            ---
                                              32
```

Three separate widths exist and mixing them up is the most common failure:

| Contract | Width | Why |
|---|---:|---|
| Phase 1 / `holdout`, and the FD001-only `full` refit | 29 | 15 sensors FD001 measures |
| a pooled contract as originally assumed | 30 | 29 + `regime_global` |
| **the artifact actually on disk** | **32** | + `s10`, `s16` |

`sensor_selection: per_subset` in the contract records this: FD001 and FD003 contribute
15 and 16 measured sensors, FD002 and FD004 contribute 21.

## 3. `s10` and `s16` — the two columns that break naive implementations

They are constant in FD001. That has three separate consequences:

1. **No telemetry.** `app/seed/cmapss.py` keeps 15 sensors for FD001; `'s10' in
   row["sensors"]` is `False`.
2. **No baseline.** FD001's block in `regime_baselines_all.json` has 15 sensors and
   omits both. FD002/FD003/FD004 do carry medians (`s10` → 1.08, `s16` → 0.02).
3. **Still required by the booster.** Dropping the columns shifts every later column
   left by one. The result is a 30-column matrix fed to a 32-feature booster:
   positionally valid, numerically meaningless.

They are also harmless. `mad: 0.0` in **every** subset, and 0.32 % of total gain combined
(`s10` 0.153 %, `s16` 0.170 %, versus `s11_rollmean5` at 44.3 %). A constant has no
deviation, so its z-score is 0.0 by definition.

### How the backend supplies them

`ModelHandle.missing_sensor_defaults(subset)` returns a per-sensor median map:

* sensors the replayed subset's baselines **do** describe are excluded;
* each remaining sensor is looked up in any subset that does describe it;
* anything undescribed anywhere falls back to `0.0`, which the MAD floor zeroes anyway.

For FD001 that yields exactly `{"s10": 1.08, "s16": 0.02}`. For FD004 it is empty —
FD004 measures all 17, so nothing needs supplying.

`build_window` then fills those columns with the median and flags the window
`s6_imputed: true`, keeping the matrix aligned with the booster.

### The bug this exposed

`dict.update()` only *adds* keys; it never clears them. `regime_z_scorer` originally
*skipped* sensors absent from the baseline block, so for `s10` the raw median `1.08`
survived into the matrix — the booster read `1.08` where it was trained to read `0.0`.
Correct only because `1.08` is small. `s16` at `0.02` would have passed unnoticed too.

It now zeroes any wanted sensor the baselines do not describe, rather than skipping it.
Pinned by `test_z_scorer_zeroes_a_sensor_the_baselines_do_not_describe`.

## 4. Sensors are derived from the contract, never hardcoded

`contract_sensors(feature_order)` in `app/ml/features.py` parses the contract's own
feature list: drop `op1`/`op2`, drop `regime_global`, drop anything containing `_roll`,
keep the rest. `ModelHandle.base_sensors` feeds that to the matrix builder and the
z-scorer.

The earlier approach — a module-level `SENSORS` tuple of 15 — produced a *misaligned*
matrix, not merely a short one, because XGBoost consumes a positional matrix.

> A first cut of `contract_sensors` excluded names present in a `derived` set (which holds
> `s11`) rather than names containing `_roll`. `s11_rollmean5` therefore passed the
> filter, and the function reported 23 sensors instead of 17. Caught by asserting on the
> count. Pinned by `test_contract_sensors_are_derived_from_the_feature_order`.

## 5. Subset-aware baselines

The baselines artifact nests `baselines[subset][regime][sensor]`, plus `centroids` and
`offsets` per subset.

**Regime ids are namespaced.** FD001 is offset 0, FD002 1, FD003 7, FD004 8. Without the
offset, FD002's regime 3 would be read as FD001's regime 3. `regime_offset(subset)`
applies it; `regime_global` is what reaches the booster.

**An unknown subset yields nothing.** `regime_block` previously fell through to a
depth-first search and returned the *first* block it found — so an `FD004` request was
scored against FD001's medians, silently. A named-but-absent subset now returns `{}`.

**A missing baseline raises.** `regime_z_scorer` used to return `None`, which
`build_window` interpreted as "no z-scoring" and fed raw sensors to a z-score-trained
booster: a confident, plausible, wrong prediction. It now raises `BusinessRuleError`, and
inference reports `window_rejected: no baseline block for subset='FD002' …` as a distinct
reason from `window_rejected: window must contain at least 5 cycles`. Both are different
bugs; sharing a reason string sent debugging in the wrong direction.

## 6. Deployment traps found by running it

Both of these shipped for a while. Neither raised an error — the service degraded to
`rul = 125 − cycle` while a fully valid model sat on disk, and `/healthz` said so only
in a field nobody was reading.

### 6.1 `FDT_ML_MODEL_PATH` pinned in compose

```yaml
FDT_ML_MODEL_PATH: /app/models_artifacts/rul_xgb.json   # that file does not exist
```

It overrides *only* the model, so the contract still resolved from the variant and the
two disagreed about which directory the artifact set lived in. Compounding it, compose
has no `env_file:` directive, so `.env` never reached the container and
`FDT_ML_VARIANT=all` was ignored too.

Fixed: compose now passes `FDT_ML_VARIANT` and `FDT_ML_DATASET` and pins no model path.
`model_store._warn_on_split_artifact_set` logs an error if a split is ever reintroduced.

### 6.2 `FDT_DATA_DIR` pointed at a directory the image does not have

```dockerfile
ENV FDT_DATA_DIR=/data      # but: COPY data/ data/   →  /app/data
```

Every artifact path was wrong: the model *and* the C-MAPSS telemetry both failed, so
`/healthz` reported `status: degraded`, `cmapss_loaded: false`, and `model.loaded: false`.

Fixed to `/app/data`, plus a build-time assertion:

```dockerfile
RUN python -c "… assert (d/'raw').is_dir(), f'{d}/raw missing' …"
```

A path regression now fails the build instead of shipping a fallback.

### 6.3 Tests that asserted absent data

`test_replay_disabled_when_cmapss_is_absent` passed *because* `train_FD001.txt` was not
in the repo. Once staged, it asserted the opposite of what it meant and failed.
`test_model_quality.py` hardcoded `data/ml/full/` and raised `FileNotFoundError` — not a
skip — whenever a different variant was staged, and skipped silently when `full` was
absent. Both now create the condition they test: `conftest._staged_variant()` probes
`data/ml/<variant>/` and points `FDT_ML_VARIANT` at it.

## 7. Outstanding

**`scripts/train_rul.py` does not produce this contract.** It trains 16 features, includes
`cycle` (a leakage vector — on the truncated official test set it was the single most
important column at 0.3585), takes an uncapped RUL target, and computes rolling means
without standard deviations. Until it is reconciled, the notebook is the only producer
and the artifact cannot be regenerated in CI. This is Phase 0 task 0.1 in [10](10-development-plan.md)
and is unchanged by this work.

**`data/cmapss/` is not gitignored** while `data/ml/` is — 43 MB of externally-sourced
data, against a stated intent not to commit it. Gitignoring it would break
`docker compose build` on a fresh clone unless a fetch step is added. Open decision.

**Only `regime_global` is per-cycle-correct in aggregate.** The classifier assigns one
regime per request from the latest cycle, whereas training assigned per cycle. Within an
engine this is right; across a six-regime window spanning an operating-condition change,
the whole window is scored against one regime. No FD001 evidence either way.

## 8. Reference

```
FD001  regimes [0]        offset 0    15 measured sensors
FD002  regimes [0..5]     offset 1    21
FD003  regimes [0]        offset 7    16
FD004  regimes [0..5]     offset 8    21
```

| Setting | Default | Meaning |
|---|---|---|
| `FDT_ML_VARIANT` | `all` | artifact generation to serve |
| `FDT_ML_DATASET` | `FD001` | subset the demo replays — independent of the variant |
| `FDT_ML_MODEL_PATH` | unset | **do not set**; overrides only the model |

```bash
python -m scripts.stage_ml_artifacts --source <dir> --variant all
curl -s localhost:8000/healthz | jq '.model, .replay'
```