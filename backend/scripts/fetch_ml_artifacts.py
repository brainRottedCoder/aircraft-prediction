"""Download the trained artifact set into the directory the loader reads.

    python -m scripts.fetch_ml_artifacts
    python -m scripts.fetch_ml_artifacts --variant all --base-url https://.../ml

Why this exists
---------------
`scripts/fetch-cmapss.sh` fetches the NASA telemetry from a public mirror, so a fresh
checkout can replay real C-MAPSS cycles with one command. The boosters had no equivalent:
`stage_ml_artifacts.py` only *copies* from a `--source` directory (the notebook's Google
Drive path, which nothing outside that Drive can reach), and `backend/data/ml/` is
gitignored. A fresh clone therefore booted with an empty artifact directory, the loader
found nothing, and every prediction came from `rul = 125 - cycle` — silently, because
`FDT_ML_FALLBACK` defaults to true and the health endpoint reported a healthy app.

This closes that gap: it fetches, then hands the result to `stage_ml_artifacts.stage`,
so the booster/contract feature-count validation is the same one the manual path runs.

Layout
------
Artifacts are addressed as `<base-url>/<variant>/<file>`, one directory per variant. The
file list is not hardcoded: the contract is fetched first and names its own model,
metrics and baselines, exactly as `model_store` reads them.

Exit codes
----------
0  staged, or already staged, or intentionally skipped (no URL configured and the
   directory is already populated)
1  the URL is unset with nothing staged, or a download failed, or the fetched set failed
   validation. Never a silent partial stage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from scripts.stage_ml_artifacts import (  # noqa: E402
    VARIANTS,
    artifact_names,
    derived_metrics_name,
    stage,
)

ENV_URL = "FDT_ML_ARTIFACTS_URL"
DEFAULT_TIMEOUT = 60


def log(msg: str) -> None:
    print(f"[ml-artifacts] {msg}", flush=True)


def download(base_url: str, name: str, dest: Path, timeout: int = DEFAULT_TIMEOUT) -> None:
    """Fetch one file to `dest` via a temp file, so an interrupted run leaves no
    half-written artifact that the loader would then try to parse."""
    url = f"{base_url.rstrip('/')}/{name}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if resp.status != 200:
                raise urllib.error.HTTPError(url, resp.status, "unexpected status", resp.headers, None)
            data = resp.read()
    except Exception as exc:  # noqa: BLE001 — report every transport failure the same way
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"could not fetch {url}: {exc}") from exc

    if not data:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{url} returned an empty body")

    tmp.write_bytes(data)
    tmp.replace(dest)
    log(f"  {name:<44} {len(data) / 1e3:8.1f} KB")


def contract_name(variant: str, settings: Settings) -> str:
    return Path(settings.ml_contract_path_resolved).name


def already_staged(settings: Settings, variant: str) -> bool:
    model = Path(settings.ml_model_path_resolved)
    contract = Path(settings.ml_contract_path_resolved)
    if not (model.is_file() and model.stat().st_size and contract.is_file()):
        return False
    try:
        c = json.loads(contract.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    # The contract names its own baselines file; without it the loader would z-score
    # against nothing, so its absence is not a complete stage.
    for name in artifact_names(c):
        if not (Path(settings.ml_contract_path_resolved).parent / name).is_file():
            return False
    return True


def fetch(variant: str, base_url: str | None, *, force: bool = False,
          timeout: int = DEFAULT_TIMEOUT) -> int:
    settings = Settings(ml_variant=variant)
    dest = settings.ml_dir

    if not force and already_staged(settings, variant):
        log(f"variant {variant!r} already staged in {dest}")
        return 0

    if not base_url:
        log(f"variant {variant!r} is not staged and {ENV_URL} is unset.")
        log("")
        log("The API will serve the deterministic `rul = 125 - cycle` curve for every")
        log("prediction. To fix, either point the artifacts at a reachable location:")
        log("")
        log(f"  {ENV_URL}=https://<host>/<path>   # <base>/{variant}/<file> per artifact")
        log("")
        log("or copy the notebook's output across by hand:")
        log(f"  python -m scripts.stage_ml_artifacts --source <dir> --variant {variant}")
        return 1

    base = f"{base_url.rstrip('/')}/{variant}"
    log(f"variant {variant!r} -> {base}")

    with tempfile.TemporaryDirectory(prefix="ml-artifacts-") as tmpdir:
        tmp = Path(tmpdir)

        # The contract decides which files exist, so it has to land first.
        cname = contract_name(variant, settings)
        try:
            download(base, cname, tmp / cname, timeout)
        except RuntimeError as exc:
            log(f"FATAL {exc}")
            log("")
            log(f"Check that {base}/{cname} exists and is reachable from this container.")
            log("A 404 here usually means the base URL is missing the per-variant directory.")
            return 1

        try:
            contract = json.loads((tmp / cname).read_text())
        except json.JSONDecodeError as exc:
            log(f"FATAL {cname} is not valid JSON: {exc}")
            return 1

        names = artifact_names(contract) + [cname]
        derived = derived_metrics_name(contract)
        if derived and derived not in names:
            names.append(derived)   # best-effort: the model loads without it

        for name in dict.fromkeys(names):
            try:
                download(base, name, tmp / name, timeout)
            except RuntimeError as exc:
                # A missing metrics file is survivable (mae reports null); a missing
                # booster, contract or baselines file is not.
                if name == derived:
                    log(f"  note: {name} unavailable — mae will report null")
                    (tmp / name).unlink(missing_ok=True)
                    continue
                log(f"FATAL {exc}")
                log("")
                log("The contract requires this file. Re-run the training notebook, or point")
                log(f"{ENV_URL} at a directory holding the complete set.")
                return 1

        log("staging:")
        try:
            stage(tmp, variant)
        except SystemExit as exc:
            log(f"FATAL validation refused the artifact set: {exc}")
            return 1

    log(f"variant {variant!r} staged in {dest}")
    log("the loader reads it at API startup; POST /api/v1/ml/reload picks it up without a restart")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the trained artifacts the loader reads.")
    parser.add_argument("--variant", default=os.environ.get("FDT_ML_VARIANT", "all"), choices=VARIANTS)
    parser.add_argument("--base-url", default=os.environ.get(ENV_URL),
                        help=f"artifacts at <base-url>/<variant>/<file> (default: ${ENV_URL})")
    parser.add_argument("--force", action="store_true", help="re-download even if already staged")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    try:
        return fetch(args.variant, args.base_url, force=args.force, timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001 — a bootstrap step must fail loudly, not traceback
        log(f"FATAL {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
