# 14 — Final Status Report

Consolidated state of the C-MAPSS RUL work: what was built, what the numbers are, what is
broken, and what to do next. Supersedes nothing — [12](12-model-training-pipeline.md)
documents Phase 1 in depth, [13](13-multi-regime-training-report.md) documents Phase 2 —
but this is the one page to read for status.

Date of record: notebook run on Colab, Drive authorised. Backend work verified locally.

---

## 1. Headline

| Question | Answer |
|---|---|
| Does a trained model exist? | ✅ On Google Drive, `models/multi/` |
| Is it trained on all the data? | ✅ FD001: 20,631 rows / 100 engines |
| Is it good? | ✅ Official test RMSE **18.30**, asymmetric score 0.464 |
| Can the backend serve it? | ✅ `make fetch-model` stages it from `FDT_ML_ARTIFACTS_URL`; `POST /api/v1/ml/reload` picks up a new set without a restart |
| Is training reproducible outside the notebook? | ❌ `train_rul.py` still trains a different model |
| Do the other subsets help? | ❌ No accuracy gain, and they are handicapped (see §5) |

---

## 2. The serving model

`MyDrive/Project2/cmapss/models/multi/xgboost_fd001_full.json` — FD001, all 100 engines,
83 rounds, 29 features.

| Metric | Value |
|---|---:|
| CV RMSE (5-fold, grouped by engine) | **17.988 ± 1.487** |
| Official test RMSE | **18.30** |
| Phase 1 holdout RMSE / MAE / R² | 16.049 / 11.407 / 0.8494 |
| Official test asymmetric score | 0.4636 |

**CV RMSE is the performance record.** The full-data refit has no holdout by construction;
its official-test number is a sanity check, not a claim.

**Why FD001 and nothing else:** single operating condition, single fault mode, 100 engines
covering the 8-aircraft fleet 1:1 — and it is data-saturated. Training on 100 engines
instead of 80 moved test RMSE from 18.365 to 18.30. Twenty percent more data bought 0.07
cycles; there is no headroom there.

---

## 3. All six candidates

| Candidate | Engines | Rows | Features | CV RMSE | Official test RMSE (sanity only) |
|---|---:|---:|---:|---:|---|
| FD003 | 100 | 24,720 | 29 | 16.489 ± 0.545 | FD003 20.64 |
| FD004 | 249 | 61,249 | 30 | 16.881 ± 0.356 | FD004 29.96 |
| FD001+FD003 | 200 | 45,351 | 30 | 16.948 ± 0.746 | FD001 18.42 · FD003 20.40 |
| ALL | 709 | 160,359 | 30 | 17.361 ± 0.962 | FD001 17.61 · FD002 28.31 · FD003 19.94 · FD004 30.15 |
| **FD001** | 100 | 20,631 | 29 | **17.988 ± 1.487** | FD001 **18.30** |
| FD002 | 260 | 53,759 | 30 | 18.384 ± 0.888 | FD002 28.57 |

**Read the CV column with care.** Each candidate is cross-validated on a *different* row
set, so the ranking above largely reflects which rows were averaged, not model quality.
FD003 appearing to "win" is an artifact: its rows are easier.

The only apples-to-apples comparison is FD001's test set: FD001-only **18.30**,
FD001+FD003 18.42, ALL 17.61. Pooling FD003 did not help. `ALL` scoring 17.61 is within
FD001's own fold spread (±1.487) and was observed on the test set, so it is not a basis
for choosing.

---

## 4. Features

Identical for all four subsets — 2 settings + 15 sensors + 12 rolling:

```
op1, op2
s2, s3, s4, s6, s7, s8, s9, s11, s12, s13, s14, s15, s17, s20, s21
s{11,4,9,12,14,7}_rollmean5, s{11,4,9,12,14,7}_rollstd5      (+ regime_global = 30)
```

`unit` (identifier) and `cycle` (leakage vector) are excluded. Top importance:
`s4_rollmean5` 0.473, `s11_rollmean5` 0.219, `s9_rollmean5` 0.078.

---

## 5. The most important open finding

**FD002 and FD004 are handicapped by FD001's feature set.**

The pipeline selects sensors with a union rule — drop a sensor only if constant in *every*
subset — which keeps all models comparable. But:

| Subset | Constant columns dropped |
|---|---|
| FD001 | 7 — `op3, s1, s5, s10, s16, s18, s19` |
| FD002 | **0** — all 21 sensors vary |
| FD004 | **0** — all 21 sensors vary |

So FD002/FD004 fly six operating conditions with **six sensors that genuinely carry
information being withheld** (`s1, s5, s10, s16, s18, s19`, plus `op3`), because they are
constant in FD001.

**This is the most likely cause of their ~29 RMSE** — not a failure of regime
normalisation, but a single-regime sensor set applied to multi-regime data. The earlier
recommendation to "tune FD002/FD004" was incomplete: tuning hyper-parameters on a
handicapped feature set would not have fixed it.

**Fix:** per-subset sensor selection in notebook cell 15 (one-line change). FD002/FD004 get
21 sensors, FD001/FD003 keep 15. Cost: models stop being directly comparable, and a pooled
model needs the union anyway.

---

## 6. Backend status

### Fixed this session

The ML module could not have loaded the real model. `app/ml/features.py` hardcoded the
obsolete **22-feature** contract, so the loader would have rejected the 29-feature artifact
and silently served `rul = 125 − cycle`.

| File | Change |
|---|---|
| `app/ml/features.py` | 29-column contract, contract-driven, per-regime z-scoring applied *before* rolling statistics |
| `app/ml/model_store.py` | Carries the preprocessing variant; `transform()`, `predict()`, gain keys mapped to sensor names |
| `app/ml/inference.py` | Uses the contract's feature order, applies the transform, keeps request validation ahead of the artifact check |
| `app/core/config.py` | `FDT_ML_VARIANT=full\|holdout` resolves four artifact paths |
| `tests/unit/test_features.py` | Rewritten — 24 tests |
| `tests/unit/test_model_store.py` | New — 14 tests |

### Three silent bugs found

All three returned plausible responses instead of failing:

1. **Native `Booster.predict()` requires a `DMatrix`**, not a numpy array. Every prediction
   was falling back. `docs/08 §6.2` documented the numpy call, so the doc was wrong too.
2. **`get_score` returned `f0..f28`** for a nameless booster, so `top_sensors` was always
   empty.
3. **`attribution.z_scores` did not understand the Phase 2 `baselines[<n>]` layout**, so
   component health read **1.0 everywhere** — perfectly healthy, and entirely wrong.

A fourth surfaced while cleaning up: an empty config override meant "use the variant
default", so there was no way to express "no stats file". Now `None` derives and `""` means
none.

### Verification

- **160 unit tests pass.**
- A real 29-feature XGBoost was trained, written as notebook-shaped artifacts, and loaded
  through `app.ml`: prediction returned, both preprocessing variants load, `top_sensors`
  populated, steady latency **1.2 ms median** (budget 100 ms).
- **Integration and performance suites were not run** — Postgres is unreachable in this
  environment (228 errors, all `sqlalchemy`). Unverified.

### Not done

- **The model is not in the repo.** `backend/data/ml/full/` is empty by design; the app
  correctly reports `version: "fallback"`. Synthetic test artifacts created during
  verification were deleted so they cannot be mistaken for the real model.
- **`scripts/train_rul.py` is unreconciled** — 16 features, `cycle` included, RUL uncapped,
  MAE 23.94 against the notebook's 11.41. Training is reproducible only by opening a
  notebook.

---

## 7. Documentation status

`docs/01, 02, 03, 06, 08, 10, 12, 13` and `backend/models_artifacts/README.md` were
corrected. They had described a model that no longer exists: 22 features including `cycle`,
MAE 23.94 / RMSE 31.59 / R² 0.768, "44 cells", "500 trees", and artifacts that were never
produced. The quality gate was tightened from MAE ≤ 25 / RMSE ≤ 35 / R² ≥ 0.70 to
**≤ 14 / ≤ 20 / ≥ 0.80**, because the model now clears the old gate by half.

One drift remains, in code rather than prose: `scripts/train_rul.py`.

---

## 8. What the model actually monitors

**One subsystem: the engine.** A single model, one RUL number, no per-subsystem models.

The fan/hpc/hpt/lpt split in `COMPONENT_SENSOR_MAP` is a *reporting* layer that converts
per-sensor deviations into four health readouts. It is not four models.

| Subsystem | Modelled | Evidence |
|---|---|---|
| Engine | ✅ the only one | FD001, single model |
| HPC | ⚠️ partial | FD001's fault mode, but see below |
| Fan, HPT, LPT | ❌ unvalidated | Never degraded in FD001 |
| Radar, gear, hyd, fuel | ❌ no sensor source | Derived from maintenance records, `simulated: true` |

**Calibration caveat.** The dominant feature `s4` maps to **LPT**, yet FD001's documented
fault mode is **HPC**. `s4` (T50 at the LPT outlet) sits downstream of the HPC, so it acts
as a whole-engine degradation proxy. The LPT health readout will therefore be the most
responsive of the four, on data where the LPT was never the failing part. Separately, `s9`
carries 7.8% of the gain and is **unassigned** to any component.

---

## 9. Next steps, in priority order

| # | Action | Why | Effort |
|---|---|---|---|
| 1 | **Per-subset sensor selection** (notebook cell 15) | FD002/FD004 are handicapped; their ~29 RMSE may be a feature-set artifact, not a regime failure | ~15 min + one CV run |
| 2 | **Stage the artifact** — copy 4 FD001-full files into `backend/data/ml/full/`, gitignored | Otherwise the app serves the fallback and the loader work is unused | 10 min |
| 3 | **Reconcile `scripts/train_rul.py`** with the 29-feature contract | Training is not reproducible outside a notebook; blocks CI and deploy | 2–3 h |
| 4 | **Common-yardstick evaluation** — score every candidate on one fixed set | Makes the candidate ranking trustworthy; currently the largest methodological gap | ~1 h |
| 5 | Fix component-health calibration against real baselines; consider adding `s9` to HPC | LPT dominates for the wrong reason; 7.8% of gain is unassigned | ~1 h |
| 6 | Run integration + performance suites against a live Postgres | 228 errors unverified | env-dependent |

Steps 1 and 2 together are roughly half an hour and close the two gaps that matter most.

---

## 10. Honest limitations of this report

- The CV numbers rank candidates measured on different row sets. §3 says so, but the table
  invites exactly the wrong reading.
- FD002/FD004 have never been tuned and are currently handicapped (§5). Their numbers bound
  the current approach, not its ceiling.
- The backend's integration and performance behaviour is unverified.
- All Phase 2 logic was validated on synthetic C-MAPSS-shaped data before the real run;
  the real run then confirmed the refit mechanics and the FD001 numbers.
