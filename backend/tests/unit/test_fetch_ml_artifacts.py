"""Staged-artifact detection for scripts/fetch_ml_artifacts.py.

This is the function that decides whether the fetch runs at all. Getting it wrong in
either direction is what made the original problem possible: a checkout whose artifact
directory exists but is incomplete reported itself as staged, so the bootstrap skipped
the fetch and the API served `rul = 125 - cycle` for every prediction.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from scripts import fetch_ml_artifacts as fetch_mod

CONTRACT = {
    "model_file": "xgboost_all_full.json",
    "regime_baselines_file": "regime_baselines_all.json",
    "n_features": 32,
    "feature_order": [f"f{i}" for i in range(32)],
}


def _stage(tmp_path: Path, files: dict[str, str], *, variant: str = "all") -> Path:
    """Write a fake artifact set where `Settings(data_dir=tmp_path/'data')` will look."""
    dest = tmp_path / "data" / "ml" / variant
    dest.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (dest / name).write_text(body)
    return dest


def _settings(tmp_path: Path, variant: str = "all") -> Settings:
    return Settings(data_dir=tmp_path / "data", ml_variant=variant, ml_fallback=False)


def test_a_complete_set_counts_as_staged(tmp_path):
    dest = _stage(tmp_path, {
        "feature_contract_all_full.json": json.dumps(CONTRACT),
        "xgboost_all_full.json": "{}",
        "regime_baselines_all.json": "{}",
    })
    assert fetch_mod.already_staged(_settings(tmp_path), "all") is True
    assert dest.is_dir()


def test_an_empty_directory_is_not_staged(tmp_path):
    (tmp_path / "data" / "ml" / "all").mkdir(parents=True)
    assert fetch_mod.already_staged(_settings(tmp_path), "all") is False


def test_a_missing_baselines_file_is_not_staged(tmp_path):
    """The loader z-scores against the baselines; without them every prediction would be
    scored against nothing, so this is not a usable stage even with a booster present."""
    _stage(tmp_path, {
        "feature_contract_all_full.json": json.dumps(CONTRACT),
        "xgboost_all_full.json": "{}",
    })
    assert fetch_mod.already_staged(_settings(tmp_path), "all") is False


def test_a_missing_contract_is_not_staged(tmp_path):
    _stage(tmp_path, {"xgboost_all_full.json": "{}"})
    assert fetch_mod.already_staged(_settings(tmp_path), "all") is False


def test_an_unparseable_contract_is_not_staged(tmp_path):
    """A truncated download must not read as a complete stage."""
    _stage(tmp_path, {
        "feature_contract_all_full.json": '{"model_file": "x.json"',
        "xgboost_all_full.json": "{}",
        "regime_baselines_all.json": "{}",
    })
    assert fetch_mod.already_staged(_settings(tmp_path), "all") is False


def test_a_zero_byte_booster_is_not_staged(tmp_path):
    _stage(tmp_path, {
        "feature_contract_all_full.json": json.dumps(CONTRACT),
        "xgboost_all_full.json": "",
        "regime_baselines_all.json": "{}",
    })
    assert fetch_mod.already_staged(_settings(tmp_path), "all") is False


def test_a_variant_with_nothing_staged_reports_not_staged(tmp_path):
    _stage(tmp_path, {
        "feature_contract_all_full.json": json.dumps(CONTRACT),
        "xgboost_all_full.json": "{}",
        "regime_baselines_all.json": "{}",
    }, variant="all")
    assert fetch_mod.already_staged(_settings(tmp_path, variant="full"), "full") is False


def test_no_url_with_nothing_staged_fails_loudly(tmp_path, capsys, monkeypatch):
    """The whole point: a fresh checkout must not proceed silently on the fallback."""
    # fetch() resolves paths from the environment, so point it at an empty tree. Without
    # this the test silently passed by finding the real artifacts staged in /app/data/ml.
    monkeypatch.setenv("FDT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(fetch_mod.ENV_URL, raising=False)

    rc = fetch_mod.fetch("all", None)
    out = capsys.readouterr().out
    assert rc == 1
    assert fetch_mod.ENV_URL in out
    assert "125 - cycle" in out


def test_no_url_with_a_complete_set_is_a_clean_skip(tmp_path, capsys, monkeypatch):
    _stage(tmp_path, {
        "feature_contract_all_full.json": json.dumps(CONTRACT),
        "xgboost_all_full.json": "{}",
        "regime_baselines_all.json": "{}",
    })
    monkeypatch.setenv("FDT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(fetch_mod.ENV_URL, raising=False)
    assert fetch_mod.fetch("all", None) == 0
    assert "already staged" in capsys.readouterr().out


class _EmptyResponse:
    """Minimal urlopen() result returning an empty body."""

    status = 200

    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_download_writes_via_a_temp_file_and_rejects_an_empty_body(tmp_path, monkeypatch):
    """A failed fetch must leave no half-written artifact for the loader to parse."""
    dest = tmp_path / "xgboost_all_full.json"
    monkeypatch.setattr(fetch_mod.urllib.request, "urlopen", lambda *_a, **_k: _EmptyResponse())
    with pytest.raises(RuntimeError, match="empty body"):
        fetch_mod.download("http://x/all", "xgboost_all_full.json", dest)
    assert not dest.exists()
    assert not dest.with_suffix(".json.part").exists()


def test_download_leaves_no_partial_file_when_the_transport_fails(tmp_path, monkeypatch):
    dest = tmp_path / "xgboost_all_full.json"

    def boom(*_a, **_k):
        raise OSError("connection reset")

    monkeypatch.setattr(fetch_mod.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="could not fetch"):
        fetch_mod.download("http://x/all", "xgboost_all_full.json", dest)
    assert not dest.exists()
    assert not dest.with_suffix(".json.part").exists()
