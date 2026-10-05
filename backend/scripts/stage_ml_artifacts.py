"""Stage the trained artifacts into the directory the loader reads (docs/08 §4).

    python -m scripts.stage_ml_artifacts --source /content/drive/MyDrive/Project2/cmapss/models/multi
    python -m scripts.stage_ml_artifacts --source ~/cmapss-models --variant holdout

Model_training_249.ipynb writes its artifacts to Google Drive, which is not something the
API can reach. This copies them into `data/ml/<variant>/`, which is gitignored, and is the
step docs/14 §9 lists as outstanding. Without it the loader finds nothing and every request
is answered by the deterministic fallback.

Which files are needed is NOT hardcoded. `data/ml/<variant>/` is resolved from
`ml_variant` in app/core/config.py, and the contract names its own baselines file, so the
set is discovered from the contract rather than guessed — the same way model_store reads it.
Copying an extra file is harmless; copying a mismatched pair is not, which is why
`_validate` refuses to stage a booster whose feature count disagrees with the contract.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402

# Derived from the loader's own table so a new variant cannot be added to
# app/core/config.py and left unstageable here.
VARIANTS = tuple(Settings._ML_FILES)


def artifact_names(contract: dict) -> list[str]:
    """Model, contract, metrics and baselines for one variant, per the contract.

    `metrics` is best-effort: model_store derives it from the model filename
    (`xgboost_X.json` -> `metrics_X.json`) when the contract names no `metrics_file`,
    so a missing metrics file still loads but reports `mae: null`. It is therefore
    copied when present and reported when absent, rather than being required.
    """
    names = []
    for key in ("model_file", "metrics_file"):
        if contract.get(key):
            names.append(str(contract[key]))
    named = contract.get("regime_baselines_file") or contract.get("stats_file")
    if named:
        names.append(str(named))
    return names


def derived_metrics_name(contract: dict) -> str | None:
    """The metrics filename model_store will look for, if the contract omits it."""
    if contract.get("metrics_file"):
        return None
    model = str(contract.get("model_file") or "")
    return model.replace("xgboost_", "metrics_") if model.startswith("xgboost_") else None


def _find_contract(source: Path, variant: str, settings: Settings) -> Path:
    """Locate the variant's contract in `source`, by configured name or by suffix."""
    configured = Path(settings.ml_contract_path_resolved).name
    candidate = source / configured
    if candidate.is_file():
        return candidate
    matches = sorted(source.glob(f"*{variant}*.json"))
    contracts = [p for p in matches if "contract" in p.name]
    if not contracts:
        raise SystemExit(
            f"no feature contract for variant {variant!r} under {source}\n"
            f"  looked for {configured} and any *{variant}*contract*.json\n"
            f"  files present: {sorted(p.name for p in source.glob('*.json'))}"
        )
    if len(contracts) > 1:
        raise SystemExit(
            f"ambiguous contracts for variant {variant!r}: {[p.name for p in contracts]}\n"
            "  narrow it with --contract <name>"
        )
    return contracts[0]


def _validate(booster_path: Path, contract: dict) -> int:
    """Fail before staging if the booster and contract disagree on the feature count.

    XGBoost consumes a positional matrix, so a contract that lists a different number of
    columns than the booster expects yields plausible-looking wrong predictions rather than
    an error. This is the one check worth doing at staging time.
    """
    import xgboost as xgb

    booster = xgb.Booster()
    booster.load_model(str(booster_path))
    declared = int(contract.get("n_features", len(contract.get("feature_order", []))))
    actual = booster.num_features()
    if actual != declared:
        raise SystemExit(
            f"refusing to stage: {booster_path.name} has {actual} features but "
            f"{contract.get('model_file')} declares {declared}"
        )
    order = list(contract.get("feature_order") or [])
    if order and len(order) != declared:
        raise SystemExit(
            f"refusing to stage: feature_order lists {len(order)} names, "
            f"n_features says {declared}"
        )
    return actual


def stage(source: Path, variant: str, *, dry_run: bool = False) -> list[Path]:
    settings = Settings(ml_variant=variant)
    if not source.is_dir():
        raise SystemExit(f"source directory not found: {source}")

    contract_path = _find_contract(source, variant, settings)
    contract = json.loads(contract_path.read_text())

    names = artifact_names(contract)
    if not names:
        raise SystemExit(f"{contract_path.name} names no model_file; cannot stage")
    names.append(contract_path.name)

    missing = [n for n in names if not (source / n).is_file()]
    if missing:
        raise SystemExit(
            f"missing artifacts in {source}: {missing}\n"
            f"  the contract requires them; re-run Model_training_249.ipynb"
        )

    derived = derived_metrics_name(contract)
    if derived:
        if (source / derived).is_file():
            names.append(derived)
        else:
            print(f"  note: {derived} not found — the model will load but report "
                  "mae: null until the notebook is re-run")

    dest = settings.ml_dir
    staged: list[Path] = []
    for name in dict.fromkeys(names):
        src_file = (source / name).resolve()
        target = dest / name
        staged.append(target)
        print(f"  {name:<44} {src_file.stat().st_size / 1e3:8.1f} KB")
        if dry_run or src_file == target.resolve():
            continue        # already staged here; re-running must be a no-op, not a crash
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, target)

    if not dry_run:
        n = _validate(dest / str(contract["model_file"]), contract)
        print(f"  validated: booster and contract agree on {n} features")

    print(f"{'would stage' if dry_run else 'staged'} {len(staged)} files -> {dest}")
    return staged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="directory holding the notebook's artifacts, e.g. the "
                             "Drive path .../Project2/cmapss/models/multi")
    parser.add_argument("--variant", default="full", choices=VARIANTS,
                        help="which ml_variant the files are for (default: full)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be copied without writing")
    args = parser.parse_args()

    settings = Settings(ml_variant=args.variant)
    print(f"variant {args.variant!r} resolves to {settings.ml_model_path_resolved}")
    stage(args.source.expanduser(), args.variant, dry_run=args.dry_run)
    print(f"set FDT_ML_VARIANT={args.variant} and leave FDT_ML_MODEL_PATH unset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
