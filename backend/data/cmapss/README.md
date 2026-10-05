# C-MAPSS

Raw NASA C-MAPSS files, whitespace-delimited with no header:

```
unit_id  cycle  setting_1  setting_2  setting_3  sensor_1 … sensor_21
```

Source: https://www.nasa.gov/content/prognostics-center-of-excellence-data-set-repository

## What is staged

All four subsets are present, `train_*` / `test_*` / `RUL_*` for FD001–FD004 (43 MB).

| File | Read at startup? | Role |
|---|---|---|
| `train_FD001.txt` | yes | the replayed subset — the fleet reads 100 engine units from it |
| `train_FD002/3/4.txt` | yes | staged so the four-subset baselines artifact resolves; not replayed |
| `test_*.txt`, `RUL_*.txt` | no | ground truth; labels are parsed into memory, test sets are not used at runtime |

The replay subset is selected by `FDT_ML_DATASET` (default `FD001`). It is deliberately
independent of `FDT_ML_VARIANT`: the served model may be trained on all four subsets
while every aircraft still binds 1:1 to an FD001 engine. Training breadth ≠ replay
breadth. Setting `FDT_ML_DATASET=FD004` genuinely works — regime classification and the
per-subset offsets are exercised — but the fleet binding in `app/seed/derived.py` is
fixed to FD001 units, so only the first few cycles per unit are meaningful.

## Unit id namespacing

Every subset numbers its engines from 1, so rows are keyed `subset → unit → cycle` and
units are namespaced as `FD001#7`. Without the prefix, FD002#7 and FD001#7 collide in one
dict and the fleet silently replays the wrong engine. Bare integers are accepted and mean
the replayed subset.

## Sensors

`SENSOR_KEYS` in `app/seed/cmapss.py` keeps 15 sensors — the FD001 informative set plus
`s6`, which the trained model uses although the spec's item-45 list omits it. 7 columns
are constant in FD001 and dropped at training time:
`setting_3, sensor_1, sensor_5, sensor_10, sensor_16, sensor_18, sensor_19`.

**`s10` and `s16` are the exception.** They are constant in FD001 and so are absent from
FD001 telemetry, but they vary in FD002/FD004 and the pooled 32-feature contract still
expects them. `s10` → `s10` and `s16` → `s16` are *not* carried in the FD001 rows; the
backend substitutes their healthy medians. See `docs/15` §3.

## Not committed

43 MB. Regenerate from the NASA source above and drop the 12 files in this directory.
Note that `data/ml/` is gitignored but `data/cmapss/` currently is not, so committing
this repository as-is would add 43 MB of externally-sourced data. Deciding between
"gitignore and fetch on demand" and "commit" is an open call — the first breaks
`docker compose build` on a fresh clone unless a fetch step is added.

## Replay

Until `train_FD001.txt` exists the replay engine logs `replay: false` on `/healthz` and
the REST endpoints still serve seeded state. `readme.txt` (the NASA description file) is
harmless — the loader only opens `train_*` / `test_*` / `RUL_*`.