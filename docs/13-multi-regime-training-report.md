# 13 — Multi-Regime Training Report (Phase 2)

What Phase 2 of `Model_training_249.ipynb` built, what it measured, and what it means for
the serving model. Companion to [12 — Model Training Pipeline](12-model-training-pipeline.md),
which documents Phase 1.

---

## 1. The question Phase 2 was built to answer

Phase 1 produced one model, on FD001, scoring RMSE 16.05 on a held-out engine split.
Three things were left open:

1. Would the other three subsets (FD002, FD003, FD004) improve accuracy if trained on?
2. FD002/FD004 fly six operating conditions each. Can they be used at all without
   teaching the model the regimes instead of the degradation?
3. Phase 1's saved model trains on 80 of 100 engines, because early stopping needs a
   holdout. Can the shipping artifact use all 100?

**Headline answer: no on (1) for the serving path, yes-but-unfinished on (2), yes on (3).**
The reasoning and the caveats are in §6 and §7 — read those before acting on the table.

---

## 2. What was built

Fifteen cells appended to the notebook (Phase 2, cells 14–28). Nothing in Phase 1 changed.

| Component | Design decision | Why |
|---|---|---|
| Regime detection | Seeded k-means over distinct `(op1,op2,op3)` combos, with the **published** condition count per subset (1/6/1/6) | Counting distinct combinations fabricates regimes from incidental settings jitter and would change FD001's feature count |
| Healthy baseline | Median + MAD per (regime, sensor) over each engine's first 20 cycles | MAD, not std: a small healthy population is sensitive to one odd cycle |
| Features | Per-regime z-score → rolling mean/std (window 5) within each engine | Makes "healthy" mean the same thing in every condition |
| Leakage control | Engine ids namespaced `FD002#7` before any split | Every subset numbers engines 1..N; unnamespaced, FD002's engine 7 shares a CV fold with FD004's engine 7 |
| Evaluation | The **same** 5-fold `GroupKFold` for every candidate | A single 80/20 split is too noisy to choose between models |
| Shipping artifact | Full-data refit, `early_stopping_rounds=None`, rounds pinned to the CV mean | CV already answers how many rounds; the holdout is then redundant |

**Parity guarantee, verified:** a single-regime Phase 2 model produces **exactly the same
29 features** as the Phase 1 serving model. Z-scoring against one regime is a per-column
affine transform, and gradient boosting is invariant to that. The multi-regime machinery
is therefore a no-op for FD001 — it only makes FD002/FD004 poolable.

---

## 3. Regime detection on the real data

| Subset | Regimes found | Published | Distinct setting combos | Rows per regime |
|---|---:|---:|---:|---|
| FD001 | 1 | 1 | — | 20,631 (all) |
| FD002 | 6 | 6 | — | spread across 6 clusters |
| FD003 | 1 | 1 | — | 24,720 (all) |
| FD004 | 6 | 6 | — | spread across 6 clusters |

Detected counts matched the published condition counts for all four subsets — no warnings
raised. Per-subset regime row counts and baselines are recorded in
`models/multi/regime_baselines_<tag>.json`.

---

## 4. Results

### 4.1 Cross-validation (5-fold, grouped by engine)

Each candidate is scored on **its own** row set. Read §7.1 before comparing these numbers
across candidates.

| Candidate | Engines | Rows | Features | CV RMSE | ± spread | Rounds (refit) |
|---|---:|---:|---:|---:|---:|---:|
| FD003 | 100 | 24,720 | 29 | 16.489 | ± 0.545 | 100 |
| FD004 | 249 | 61,249 | 30 | 16.881 | ± 0.356 | 159 |
| FD001+FD003 | 200 | 45,351 | 30 | 16.948 | ± 0.746 | 115 |
| ALL | 709 | 160,359 | 30 | 17.361 | ± 0.962 | 186 |
| FD002 | 260 | 53,759 | 30 | 18.384 | ± 0.888 | 115 |
| **FD001** | 100 | 20,631 | 29 | **17.988** | **± 1.487** | 83 |

FD001 per-fold RMSE: `[18.891, 18.693, 16.457, 19.874, 16.026]` — a spread of 3.8 cycles
across folds, which is exactly why one split was not enough to choose on.

Note FD001 has both the **worst mean and the widest spread** (± 1.487, more than four
times FD004's ± 0.356). A 100-engine cross-validation is a noisy instrument; a 0.5-cycle
difference between two candidates is not evidence of anything.

### 4.2 Full-data refits — trained on 100% of engines

| Candidate | Rows | Engines | Rounds | CV RMSE (selection metric) | Official test RMSE (sanity check only) |
|---|---:|---:|---:|---:|---|
| FD001 | 20,631 | 100 | 83 | 17.988 ± 1.487 | FD001 **18.30** |
| FD003 | 24,720 | 100 | 100 | 16.489 ± 0.545 | FD003 20.64 |
| FD001+FD003 | 45,351 | 200 | 115 | 16.948 ± 0.746 | FD001 18.42 · FD003 20.40 |
| FD002 | 53,759 | 260 | 115 | 18.384 ± 0.888 | FD002 28.57 |
| FD004 | 61,249 | 249 | 159 | 16.881 ± 0.356 | FD004 29.96 |
| ALL | 160,359 | 709 | 186 | 17.361 ± 0.962 | FD001 17.61 · FD002 28.31 · FD003 19.94 · FD004 30.15 |

Round counts scale with data volume, as expected: 83 rounds for 100 engines, 186 for 709.
Cell 27 asserts each refit consumed every training row and engine, so the row counts above
are the complete training sets, not a train-minus-holdout.

### 4.3 Phase 1 versus Phase 2, on FD001

| Model | Trained on | Test RMSE | Rounds | Features |
|---|---|---:|---:|---:|
| Phase 1 (cell 10) | 80 engines | 18.365 | 92 | 29 |
| Phase 2 full refit | **100 engines** | **18.30** | 83 | 29 |

**Twenty percent more training data bought 0.07 cycles of RMSE.** FD001 is data-saturated.

For reference, Phase 1's internal holdout was RMSE 16.049 / MAE 11.407 / R² 0.849, and its
official test asymmetric score was 0.4636.

---

## 5. The CV-to-test gap, and why it matters

| Candidate | CV RMSE | Test RMSE | Gap |
|---|---:|---:|---:|
| FD001 | 17.99 | 18.30 | +0.3 |
| FD003 | 16.49 | 20.64 | +4.2 |
| FD001+FD003 | 16.95 | 18.42 (FD001) | +1.5 |
| FD002 | 18.38 | 28.57 | **+10.2** |
| FD004 | 16.88 | 29.96 | **+13.1** |

Two effects compound here:

1. **The protocols measure different things.** CV is row-level across a whole trajectory;
   the official test is a single point — the final cycle — which is the hardest and
   noisiest cycle to predict.
2. **Multi-regime models degrade much more under truncation** (+10 to +13 cycles versus
   +0.3 for FD001). Plausible mechanism: with six regimes the model has less capacity per
   regime, so it is more easily thrown by an unseen condition at the final cycle.

This gap is the strongest evidence in the whole report that the multi-regime models are
**not yet production-ready**, independent of their CV numbers.

---

## 6. Interpretation

**Pooling FD003 did not improve FD001 accuracy.** On the same 100 FD001 test engines:
FD001-only 18.30, FD001+FD003 18.42. The apparent CV improvement (17.988 → 16.948) is an
artifact of the evaluation set changing, not of the model improving: FD003's CV rows are
easier (16.489 on its own) and dilute the pooled mean. The gain is in the metric.

`ALL` scores best on FD001's test set (17.61 versus 18.30). Treat that as a reason for
suspicion, not adoption — it is a 0.69-cycle difference on 100 engines, chosen by looking at
the test set, and FD001's own fold spread is ± 1.487. Acting on it is precisely how a test
set stops being a test set.

**The multi-regime models have not been shown to work.** FD002/FD004 test RMSE near 30 is
the output of a first, untuned pass at regime normalisation. It is a baseline to improve,
not a verdict. Nobody has tuned them.

**An FD001 model is not merely less accurate for multi-regime work — it is invalid.** It
has seen one operating condition and one fault mode. If the fleet ever flies at altitude,
or needs fan-failure coverage, FD001 stops being usable at all.

**FD001+FD003 is the interesting artifact.** Not for accuracy, but for coverage: it is the
only single-regime model that has seen fan degradation, which an FD001-only model has
never encountered.

---

## 7. Threats to validity

Read this section before acting on any number above.

### 7.1 The CV column is not comparable across candidates

Each candidate is cross-validated on a different row set. FD003's 16.49 and FD004's 16.88
are measured on their own data, which is a different difficulty from FD001's. The apparent
ranking (FD003 best, FD001 worst) largely reflects **which rows were averaged**, not model
quality. Cell 25's verdict logic sorts on this column and is therefore unreliable for
cross-subset comparisons; it is sound only between candidates evaluated on the same rows.

### 7.2 The one clean comparison is a single noisy reading

The FD001 test column (18.30 / 18.42 / 17.61) is the only apples-to-apples comparison
available. Differences of 0.7 RMSE across 100 engines are inside the noise. Selecting a
model on its test score is how a test set stops being a test set — which is why the
recommendation in §8 does **not** rest on `ALL` scoring 17.61.

### 7.3 One hyperparameter setting, one seed

No sweep was run. `max_depth=6`, `learning_rate=0.05`, `min_child_weight=3` were carried
over from Phase 1 and applied unchanged to every candidate. FD002/FD004 in particular may
respond substantially to tuning, so their numbers bound the current approach, not the
approach's ceiling.

### 7.4 Fold spread exceeds most of the differences

FD001's own folds span 16.03 to 19.87. Differences of 1–2 cycles between candidates sit
inside that. The ± 1.487 spread is reported throughout for this reason.

---

## 8. Verdict and recommendation

**Ship `models/multi/xgboost_fd001_full.json`.**

- Same recipe and same 29 features as the Phase 1 serving model — drop-in compatible.
- Trained on all 100 engines instead of 80.
- FD001 is the documented serving dataset, and it is data-saturated: more data changed
  nothing.
- Round count (83) comes from cross-validation, not guesswork.

**Keep FD001 as `FDT_ML_DATASET`.** Single-regime scope is a product decision, and it
removes regime normalisation from the critical path.

**Leave `models/multi/` as research artifacts.** Nothing there is serving-ready:
the pooled and multi-regime models show no accuracy gain, and the multi-regime models show
a 10–13 cycle degradation under the truncated final-cycle protocol.

**Use 17.988 ± 1.487 as the performance record for FD001**, not 18.30. The refit has no
holdout by construction; its test number is a sanity check, not a claim.

### What would change this conclusion

| Finding | Would mean |
|---|---|
| FD002/FD004 tuned and reaching test RMSE < 20 | Multi-regime becomes viable; the regime work pays off |
| A common-yardstick evaluation (all candidates on FD001's test set) | The cross-subset ranking becomes trustworthy; `ALL` might genuinely win |
| FD003 fan-degradation required by the product | FD001+FD003 becomes necessary, not optional |
| Fleet operating outside one condition | FD001 is invalid, not just suboptimal |

---

## 8b. Selection discipline

The refit in cell 26b is governed by one rule, recorded in `summary.json` so it is
auditable rather than folklore:

```json
"selection_rule": {
  "metric": "cv_rmse_mean",
  "selected_on": "engine-level cross-validation on training data only",
  "official_test_used_for_selection": false,
  "statement": "CV RMSE is the model-selection/performance metric. After selection, the
                final model is retrained on 100% of the available training engines. The
                official test set is used only for final sanity-check evaluation."
}
```

Mechanically:

- **Round count** comes from CV — the mean best iteration across the 5 folds — with early
  stopping switched off in the refit. Nothing about the test set influences it.
- **Hyperparameters** are frozen from the CV configuration; the refit changes only the
  training data and the booster count.
- **The test set** is read exactly once per candidate, after the model is already built, and
  only to compute a sanity number.
- **Both numbers are recorded per candidate** in `summary.json`: `cv_rmse_mean`,
  `cv_rmse_std`, `cv_rmse_folds` under selection, and `official_test_rmse` per subset
  marked `"sanity check only, not a selection metric"`.

Cell 26b asserts that the refit consumed every training row and every training engine,
comparing against the raw frames rather than trusting the pipeline. A future edit that
reintroduced a holdout would raise instead of quietly shipping an under-trained model.

There is deliberately **no** train/test engine-id overlap assertion. C-MAPSS train and test
files are independent fleets that both number engines `1..N`, so `FD001#1` legitimately
appears in both and refers to different machines — an id-overlap check would false-positive
on correct code.

---

## 9. Recommended next steps

1. **Common-yardstick evaluation** — score every candidate on one fixed evaluation set so
   the comparison is apples-to-apples; keep per-subset CV as a diagnostic. This is a small
   change to cells 25–27 and removes the largest weakness in this report.
2. **Tune FD002/FD004** before judging them — grouped k-fold sweep over `max_depth`,
   `learning_rate`, `min_child_weight`. Until then their numbers are not evidence.
3. **Port Phase 2 to `backend/scripts/train_rul.py`** so the multi-regime candidates are
   regenerable outside a notebook. Note the script currently trains a *different* model
   (16 features, `cycle` included, RUL uncapped) — see §11.
4. ~~**Update `docs/08`**~~ — **done.** It now specifies the real 29-feature contract;
   `docs/01`, `02`, `03`, `06` and `10` were aligned with it in the same pass. Remaining
   drift is `backend/scripts/train_rul.py` alone (item 3).

---

## 10. Engineering issues found and fixed

Phase 2 surfaced three real bugs, all caught by a synthetic C-MAPSS harness before the
notebook ever touched real data.

| Symptom | Root cause | Fix |
|---|---|---|
| `NameError: BASELINE_SENSORS` in the baseline cell | The sensor list was never defined; baselines referenced a name that did not exist | Resolve `Z_SENSORS` in the same cell that builds the baselines, so one definition drives both |
| FD001 reported 4 regimes, FD002 reported 16 | Regime count was inferred by counting distinct setting combinations, and the settings columns carry incidental jitter | Use the documented per-subset condition count; seeded k-means only to place centroids |
| FD001 produced 27 features instead of 29 | Constant-column removal stripped `op1`/`op2`, which Phase 1 feeds to XGBoost regardless | Protect the settings columns, restoring exact parity with the Phase 1 contract |

Harness result: all 28 code cells compile, nbformat validates at 4.5, zero source lines
missing trailing newlines, and the FD001 feature-parity assertion passes.

---

## 11. Artifacts produced

All under `MyDrive/Project2/cmapss/models/multi/`, none overwriting Phase 1's `models/`.

| File pattern | Contents |
|---|---|
| `xgboost_<tag>.json` | 80/20 model — has a holdout, used for measurement |
| `xgboost_<tag>_full.json` | Full-data refit — the shipping candidate |
| `metrics_<tag>.json` | Holdout + CV + official-test metrics |
| `metrics_<tag>_full.json` | Refit provenance, `has_holdout: false`, `cv_rmse` (mean/std/folds + role), `official_test_rmse` per subset, and the 80/20 model's test score for reference |
| `feature_contract_<tag>.json` | Feature order, sensor order, regime column, preprocessing description |
| `feature_contract_<tag>_full.json` | Copy of the above with `model_file` repointed — copied, never rebuilt, so it cannot drift |
| `regime_baselines_<tag>.json` | Centroids, global offsets, per-regime median/MAD |
| `summary.json` | `selection_rule`, all candidates with CV metrics, and a `full_refit` block carrying rows, engines, rounds, model file, CV RMSE (mean/std/folds) and official-test RMSE per candidate |

`<tag>` ∈ `fd001`, `fd003`, `fd001+fd003`, `fd002`, `fd004`, `all`.

**Contract drift — docs resolved, script not.** `docs/08 §3` previously specified a
22-feature vector including `cycle`, describing a model that no longer existed; it now
specifies the real 29-feature contract, and `docs/01`, `02`, `03`, `06` and `10` were aligned
with it. The remaining item is `backend/scripts/train_rul.py`, which still trains a
16-feature model with `cycle` included and RUL uncapped. It must be reconciled before it can
be the source of truth — until then the model is regenerable only by running the notebook.

---

## 12. Reproduction

```bash
colab drivemount -s <session>          # once per session
colab exec -f Model_training_249.ipynb --timeout 900 -s <session>
```

Phase 1 re-runs in ~25 s from the Drive cache. Phase 2 is dominated by the six
cross-validation candidates (cells 19–24); `--timeout 900` is required for the `ALL`
candidate. Rerun cells 19–24 before cell 27 whenever the data or hyperparameters change —
the refit's round count is inherited from them.
