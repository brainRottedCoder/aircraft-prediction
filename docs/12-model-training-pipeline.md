# 12 — Model Training Pipeline (`Model_training_249.ipynb`)

What the notebook does, why each decision was taken, and what came out of it.
Companion to [08 — ML Service](08-ml-service.md), which describes how the backend is
expected to *consume* these artifacts.

---

## 1. What this notebook is

A self-contained, reproducible training pipeline for a **Remaining Useful Life (RUL)**
regressor on NASA's C-MAPSS turbofan degradation dataset. It downloads the raw data,
validates it, cleans it, engineers features, trains an XGBoost model, scores it against
NASA's official benchmark, and persists every artifact to Google Drive.

It is the **only** place the model is produced. Everything downstream (API, replay
engine, dashboard) consumes the artifacts it writes — nothing retrains at serving time.

| Property | Value |
|---|---|
| Dataset | NASA C-MAPSS, subset **FD001** (modelled) + FD002/003/004 (prepared) |
| Task | Regression — predict cycles remaining until failure |
| Model | `XGBRegressor`, `reg:squarederror`, early stopping |
| Features | 29 (17 raw + 12 rolling) |
| Runtime | ~25 s end to end on a Colab CPU runtime, ~0.8 s of which is training |
| Outputs | 4 model artifacts + 8 cleaned CSVs + `manifest.json`, all on Drive |

---

## 2. How to run it

**Interactive (Colab):** open the notebook, Runtime → Run all. Colab prompts for Drive
authorisation on the first run of the session.

**Headless (`colab` CLI):** Drive mount is an interactive OAuth flow, so it must be
authorised *once per session* before the notebook runs:

```bash
colab new -s mysession                                  # create a runtime
colab drivemount -s mysession                           # approve in the browser when prompted
colab exec -f Model_training_249.ipynb --timeout 300 -s mysession
```

`--timeout 300` matters: the default per-cell budget is 30 s, which is not enough for
the Drive mount plus the download on a cold runtime.

---

## 3. Storage layout on Drive

```
/content/drive/MyDrive/Project2/cmapss/
├── raw/                        13 untouched NASA .txt files (cached, idempotent)
├── clean/                      train_/test_FD00x.csv.gz — cleaned, gzipped
├── models/
│   ├── xgboost_fd001_rul.json      the trained booster
│   ├── scaler_fd001.pkl            StandardScaler + feature order
│   ├── metrics_fd001.json          validation + official-test metrics
│   └── feature_contract.json       sensor order, feature order, exclusions
└── manifest.json               per-subset row/engine counts, dropped columns, config
```

**Why Drive and not the VM disk.** A Colab runtime is ephemeral — anything on `/content`
dies with the session. Drive is the only durable store, and it also makes the run
idempotent: on a second run the 13 raw files are already cached, so the download is
skipped entirely and the notebook costs ~0 s for that stage.

The archive itself is deliberately staged in `/tmp` and never written to Drive; Drive
writes are slow and the 12 MB zip is deleted a few lines later anyway.

---

## 4. Stage by stage

### Stage 1 — Setup

Imports, plus every tunable in one place: `RUL_CAP = 125`, `ROLL_WINDOW = 5`,
`ROLL_SENSORS`, `SEPARATOR = r"\s+"`, `N_BOOST = 1000`, `RANDOM_STATE = 42`,
`VAL_FRACTION = 0.20`, and the download budget.

The whitespace separator deserves a note: the raw files are space-separated **with
trailing whitespace**. Using `sep=" "` produces two extra all-NaN columns and a
silently wrong shape, so `r"\s+"` is used and the 26-column count is asserted later.

### Stage 2 — Google Drive

```python
from google.colab import drive
drive.mount('/content/drive')
```

Then the storage tree is created and the mount is verified. There is no local-disk
fallback: if Drive is not mounted the notebook stops and says how to fix it, rather
than quietly writing artifacts somewhere that will not survive.

### Stage 3 — Extract

Downloads `CMAPSSData.zip` (12.4 MB), streaming to disk with a live progress bar
(percentage, MB, speed, ETA), then extracts the 13 data files and copies them to Drive.

Three things this stage had to get right, each of which was a real failure first:

1. **Mirror fallback.** `data.nasa.gov` is authoritative but intermittently stalls for
   >60 s on a cold runtime. A CDN-backed mirror is tried first, with NASA as fallback.
2. **Nested archives.** The mirrors do not agree on layout — NASA serves the `.txt` files
   at the archive root, while the mirror wraps the same `CMAPSSData.zip` inside an outer
   archive. Hard-coding one layout made extraction fail with
   `KeyError: train_FD001.txt` whenever the other mirror won. The extractor now walks
   the archive breadth-first and descends into nested zips.
3. **A hard wall-clock budget.** A headless runner kills a cell that exceeds its
   timeout, mid-download, with no explanation. The download therefore either succeeds or
   raises an actionable error within `DOWNLOAD_BUDGET` (default 25 s). An archive that
   downloads but does not contain the data files is rejected *inside* the mirror loop so
   the next mirror gets a turn.

### Stage 4 — Load

Parses all four subsets with `header=None` and explicit column names:
`unit, cycle, op1, op2, op3, s1..s21` = 26 columns.

| Subset | Train rows | Train engines | Test rows | Conditions | Fault modes |
|---|---:|---:|---:|---|---|
| FD001 | 20,631 | 100 | 13,096 | 1 (sea level) | HPC |
| FD002 | 53,759 | 260 | 33,991 | 6 | HPC |
| FD003 | 24,720 | 100 | 16,596 | 1 | HPC + Fan |
| FD004 | 61,249 | 249 | 41,214 | 6 | HPC + Fan |

All four are parsed and cleaned, but **only FD001 is modelled** — see §9.

### Stage 5 — Validate

Hard assertions, collected into a single error rather than a warning per issue:
26 columns per frame, no NaN, no infinities, no duplicate rows, rows ordered by engine,
every engine starts at cycle 1 with contiguous cycles, train/test column parity, RUL row
count equal to the number of test engines, no negative or missing RUL.

The point is that a silently bad row would otherwise propagate into every downstream
metric and be indistinguishable from a modelling problem.

One non-issue is called out explicitly: unit IDs are reused between `train` and `test`
(both are numbered `1..N` as independent fleets), so overlap there is expected and is
**not** leakage. Leakage only matters *within* the training file, which Stage 9 handles.

### Stage 6 — Clean

**Constant-column removal**, per subset. A column is dropped when it holds a single
value — it cannot discriminate anything.

| Subset | Dropped |
|---|---|
| FD001 | `op3, s1, s5, s10, s16, s18, s19` (7) |
| FD002 | none (multi-regime: every column varies) |
| FD003 | `op3, s1, s5, s16, s18, s19` (6) |
| FD004 | none |

**Near-constant columns are reported, not dropped.** Sensors `s2, s6, s8, s13` have a
coefficient of variation below `1e-3` in FD001 — they barely move at a single operating
condition, but they still shift with degradation. Dropping them on a CV threshold would
discard real signal, so `DROP_NEAR_CONSTANT = False` and the advisory list is printed
for review instead.

**RUL target construction — the piecewise-linear cap.**

```
RUL = (last cycle of this engine − current cycle + 1), capped at 125
```

The `+1` makes the final cycle RUL 1 rather than 0. The cap is the standard trick from
the C-MAPSS literature: an engine shows almost no degradation over its first ~100 cycles,
so without a cap the model would spend its capacity learning "how old is this engine"
rather than "how close to failure". Measured effect on FD001:

| | min | max | mean |
|---|---:|---:|---:|
| Uncapped RUL | 1 | 362 | 108.8 |
| Capped at 125 | 1 | 125 | 87.4 |

8,231 rows (39.9%) sit on the 125 plateau.

Note that RUL is **computed, not given**, for the training set — you can only know when
an engine failed because its full trajectory to failure is available. The test set is
truncated, which is why its labels ship separately in `RUL_FD001.txt`.

### Stage 7 — Persist

Writes the 8 cleaned CSVs (gzipped, `compresslevel=1` for speed) plus `manifest.json`
recording per-subset row/engine counts, dropped and near-constant columns, kept sensors,
and the config constants used. The manifest is what makes a later run interpretable
without re-reading the code.

### Stage 8 — Feature engineering (FD001)

For 6 sensors (`s4, s7, s9, s11, s12, s14`), a **rolling mean and standard deviation over
a 5-cycle window, computed within each engine** — `groupby("unit").rolling(...)` means a
window can never straddle an engine boundary. `min_periods=1` keeps the first cycles
usable, so no rows are lost to NaN.

Result: 17 raw + 12 rolling = **29 features**.

**Two columns are deliberately excluded:**

- `unit` — an identifier, not a measurement.
- `cycle` — excluded on purpose. It correlates strongly with RUL, and because the
  official test set is truncated early, a model allowed to use `cycle` scores well and
  then fails on real data. An earlier iteration of this notebook had `cycle` as the
  single most important feature (0.36) precisely because of this artefact. Dropping it
  costs a little accuracy and buys an honest model.

**Why rolling features dominate.** Raw sensor values for a healthy and a mid-life engine
overlap heavily; what carries the signal is the *trend*. The importance table confirms it
— raw `s4` scores 0.02 while `s4_rollmean5` scores 0.47.

### Stage 9 — Grouped split and scaling

`GroupShuffleSplit(test_size=0.20, random_state=42)` → 80 engines train (16,561 rows) /
20 engines validation (4,070 rows). The notebook asserts the engine sets are disjoint.

A row-level split would place the same engine's degradation curve on both sides, letting
the model recognise an engine it has already seen; the resulting score would not survive
contact with new engines. The assertion exists so this can never silently regress.

`StandardScaler` is fitted on the training split only and applied to validation —
fitting on the full set leaks validation statistics into training.

### Stage 10 — Train

```python
XGBRegressor(
    objective="reg:squarederror",
    n_estimators=1000, learning_rate=0.05,
    max_depth=6, min_child_weight=3,
    subsample=0.8, colsample_bytree=0.8,
    reg_lambda=1.0,
    tree_method="hist",
    early_stopping_rounds=50,
    random_state=42,
)
```

`n_estimators=1000` is a ceiling, not a target. Early stopping halts boosting once
validation RMSE has not improved for 50 rounds:

| Round | Validation RMSE |
|---:|---:|
| 0 | 39.65 |
| 25 | 19.33 |
| 50 | 16.41 |
| 75 | 16.08 |
| 100 | 16.06 |
| **92** | **early stop** |

Training took 0.8 s. The remaining 908 trees would only have overfit — that the model
converges and stops is itself a useful signal.

### Stage 11 — Evaluate

Two plots (predicted vs actual, and error vs actual RUL) plus the importance table:

| Feature | Gain |
|---|---:|
| `s4_rollmean5` | 0.473 |
| `s11_rollmean5` | 0.219 |
| `s9_rollmean5` | 0.078 |
| `s7_rollmean5` | 0.041 |
| `s9` | 0.023 |

The top 3 features carry 77.1% of total importance. These are exactly the sensors the
C-MAPSS literature identifies as degradation indicators — `s4` (T50, LPT outlet
temperature) responds to HPC degradation, `s11` (Ps30) and `s9` (Nc, HPC speed) to core
condition. **The model learned engine physics, not a cycle-count shortcut.**

**Official test-set scoring.** The 100 test engines are labelled from `RUL_FD001.txt`, and
the model predicts at each engine's final cycle. Two views are reported:

| View | RMSE | MAE | Asymmetric score |
|---|---:|---:|---:|
| Pointwise (final cycle) | 18.365 | 13.197 | 0.4636 |
| Last-30-cycle mean | 22.879 | 17.049 | 0.3932 |

NASA's asymmetric score penalises late predictions (saying "30 cycles left" when it is 5
is far worse than the reverse), which is the right incentive for maintenance. The
last-30-cycle mean is a diagnostic built from reconstructed targets, **not** an official
benchmark — read it as "what an operator experiences over the final half-minute", not as
a score to optimise.

---

## 5. Results

**Grouped validation** (20 engines never seen in training):

| Metric | Value |
|---|---:|
| MAE | 11.41 cycles |
| RMSE | 16.05 cycles |
| R² | 0.849 |

**Official FD001 test set** (100 engines never seen in training):

| Metric | Value |
|---|---:|
| RMSE | 18.36 cycles |
| MAE | 13.20 cycles |
| Asymmetric score | 0.4636 |

**How to read these.** The gap between validation (16.05) and test (18.36) is small and
in the right direction. The pathological pattern — test far *better* than validation — is
the signature of leakage; we see the opposite, which means the split held.

For context, published FD001 results for this class of model sit around RMSE 12–20, with
strong deep models near 12–13. Our 18.36 is inside the normal band and slightly toward
the high end. It is also computed on the **final cycle only**, which is harsher and
noisier than the standard "score every test cycle" protocol, so it is not directly
comparable to published tables.

---

## 6. Engineering issues hit along the way

| Symptom | Root cause | Fix |
|---|---|---|
| Cell 2 hung, whole run aborted | `drive.mount()` waits on an interactive OAuth prompt; nothing answers it under `colab exec`, so the cell was killed at the 30 s timeout and cells 3–13 never ran | Authorise with `colab drivemount` first; the notebook now verifies the mount and fails fast with instructions |
| `KeyError: train_FD001.txt` after a "successful" download | The primary mirror serves a wrapper archive containing a nested `CMAPSSData.zip` | Extractor walks nested archives; incomplete archives are rejected inside the mirror loop |
| Two cells produced no output at all | Notebook `source` lines had lost their trailing `\n`, so the code ran as one mangled line | Repaired the notebook JSON; all cells verified to compile |

---

## 7. Design decisions and their rationale

| Decision | Rationale | Cost |
|---|---|---|
| Cap RUL at 125 | Standard C-MAPSS practice; stops the model learning engine age instead of health | Cannot express RUL > 125 |
| Exclude `cycle` | Truncated test set makes it a leakage vector; it was the top feature (0.36) when included | ~1–2 cycles RMSE |
| Split by engine, not by row | Row-level splits leak each engine's curve across the split | Fewer validation rows |
| Keep near-constant sensors | They carry degradation signal even at a single operating condition | 4 low-importance features |
| `n_estimators=1000` + early stopping | Ceiling, not target — the model decides when to stop | None; saves 0.8 s → would have overfit |
| FD001 only | Single operating condition removes regime-normalisation risk from the critical path | Multi-regime robustness untested |
| Scale before XGBoost | Trees are scale-invariant, but a standardised matrix keeps the saved scaler meaningful for the backend contract | None |
| Drive, no local fallback | Ephemeral VM disk loses artifacts; silent fallback hides that | Run requires Drive access |

---

## 8. Artifacts and how to use them

| File | Contents |
|---|---|
| `xgboost_fd001_rul.json` | The booster. Load with `XGBRegressor().load_model(...)` |
| `scaler_fd001.pkl` | `{"scaler": StandardScaler, "features": [...]}`. **Required** — the model was trained on standardised inputs |
| `metrics_fd001.json` | Validation and official-test metrics, config, best iteration |
| `feature_contract.json` | Feature order, sensor order, excluded columns, roll window, RUL cap |

Inference must reproduce the contract exactly:

```python
feats = build_rolling(window_row)          # same 29 columns, same order
pred  = np.clip(model.predict(scaler.transform(feats)), 1, 125)
```

Order matters and cannot be inferred — that is what `feature_contract.json` is for.

---

## 9. Limitations and next steps

**Known limitations**

1. **FD001 only.** FD002/FD004 have 6 operating conditions and need regime
   normalisation before they can be pooled; FD003 adds fan degradation, which FD001's
   single fault mode does not exercise.
2. **Pointwise scoring on the final cycle** is a harsh reading of the benchmark.
3. **The notebook is not yet a script.** Training is reproducible only by re-running it
   interactively.
4. **No component-health head or sensor attribution.** The notebook produces a RUL
   number; it does not yet explain *why*, which the API contract references.

**Next steps, in order**

1. Port to `backend/scripts/train_rul.py` — CLI-driven, no Colab dependency, so the
   artifacts are regenerable in CI and on a laptop. This is the highest-value move: right
   now the model exists only as notebook output.
2. Emit `feature_manifest.json` and `baseline_stats.json` (per-sensor median/MAD over each
   unit's first 20 cycles), both required by the ML service contract.
3. Implement `ml/features.py`, `ml/inference.py`, `ml/attribution.py`, `ml/fallback.py`
   so the backend can score a live window, explain the prediction, and degrade safely
   when no artifact is present.
4. Replace the single 80/20 split with a grouped k-fold sweep over `max_depth`,
   `learning_rate` and `min_child_weight` — a single split is a noisy basis for tuning.
5. Only then extend to FD002–FD004 with regime normalisation.

**Contract drift — resolved.** [08 §3](08-ml-service.md) used to specify a 22-feature vector
including `cycle`, describing a model that no longer exists. It now specifies the 29-feature
contract this pipeline actually produces. `backend/scripts/train_rul.py` still trains the old
16-feature variant and remains unreconciled — see [08 §12](08-ml-service.md).

---

## 10. Reproducibility

| Knob | Value | Effect |
|---|---|---|
| `RANDOM_STATE` | 42 | Split, subsampling, column sampling |
| `VAL_FRACTION` | 0.20 | 20 of 100 engines held out |
| `RUL_CAP` | 125 | Target ceiling |
| `ROLL_WINDOW` | 5 | Rolling feature width |
| `N_BOOST` + early stopping | 1000 / 50 | Ceiling and patience |

Same notebook + same Drive cache + same `random_state` → identical model. Metrics in this
document were produced by the run recorded in the notebook outputs.
