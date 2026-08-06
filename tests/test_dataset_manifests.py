from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from utils.dataset_manifests import DatasetManifest, atomic_write_json, load_manifest, manifest_path_for, summarize_rows, write_manifest


def test_manifest_round_trip(tmp_path: Path):
    path = manifest_path_for(tmp_path / "historical_data.duckdb")
    manifest = DatasetManifest(
        dataset="historical_candles",
        source="test",
        status="completed",
        summary={"rows": 2},
        repair_mode=False,
    )

    write_manifest(path, manifest)
    loaded = load_manifest(path)

    assert loaded["dataset"] == "historical_candles"
    assert loaded["status"] == "completed"
    assert loaded["summary"]["rows"] == 2


def test_summarize_rows_detects_duplicates_gaps_and_ohlc_issues():
    rows = [
        {"symbol": "ABC", "ts": datetime(2026, 8, 6, 9, 15), "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10},
        {"symbol": "ABC", "ts": datetime(2026, 8, 6, 9, 15), "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10},
        {"symbol": "ABC", "ts": datetime(2026, 8, 6, 9, 20), "open": 120, "high": 110, "low": 90, "close": 105, "volume": 10},
        {"symbol": "ABC", "ts": datetime(2026, 8, 6, 9, 35), "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10},
    ]

    summary = summarize_rows(rows, expected_step=timedelta(minutes=5))

    assert summary["duplicate_keys"] == 1
    assert summary["gap_count"] >= 1
    assert summary["ohlc_issues"] >= 1

