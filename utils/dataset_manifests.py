"""Helpers for incremental dataset downloads and lightweight validation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

DEFAULT_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as handle:
        handle.write(content)
        tmp_name = handle.name
    os.replace(tmp_name, path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, default=str))


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default or {}


def manifest_path_for(dataset_path: Path, suffix: str = ".manifest.json") -> Path:
    if dataset_path.is_dir():
        return dataset_path / suffix.lstrip(".")
    return dataset_path.with_name(dataset_path.name + suffix)


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_interval_delta(interval: str | None) -> timedelta | None:
    if not interval:
        return None
    normalized = interval.strip().lower()
    if normalized in {"day", "1day", "1d"}:
        return timedelta(days=1)
    if normalized.endswith("minute") or normalized.endswith("min"):
        digits = "".join(ch for ch in normalized if ch.isdigit())
        minutes = int(digits) if digits else 1
        return timedelta(minutes=max(1, minutes))
    if normalized.endswith("hour") or normalized.endswith("h"):
        digits = "".join(ch for ch in normalized if ch.isdigit())
        hours = int(digits) if digits else 1
        return timedelta(hours=max(1, hours))
    return None


def summarize_rows(
    rows: list[dict[str, Any]],
    *,
    symbol_key: str = "symbol",
    timestamp_key: str = "ts",
    open_key: str = "open",
    high_key: str = "high",
    low_key: str = "low",
    close_key: str = "close",
    volume_key: str = "volume",
    expected_step: timedelta | None = None,
) -> dict[str, Any]:
    """Return validation summary for OHLCV-like records."""

    summary: dict[str, Any] = {
        "row_count": len(rows),
        "nulls": {},
        "duplicate_keys": 0,
        "gap_count": 0,
        "ohlc_issues": 0,
        "first_ts": None,
        "last_ts": None,
    }
    seen: set[tuple[Any, Any]] = set()
    timestamps: list[datetime] = []
    for row in rows:
        sym = row.get(symbol_key)
        ts = row.get(timestamp_key)
        if sym is None:
            summary["nulls"][symbol_key] = summary["nulls"].get(symbol_key, 0) + 1
        if ts is None:
            summary["nulls"][timestamp_key] = summary["nulls"].get(timestamp_key, 0) + 1
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
        if isinstance(ts, datetime):
            timestamps.append(ts)
            if summary["first_ts"] is None or ts < summary["first_ts"]:
                summary["first_ts"] = ts
            if summary["last_ts"] is None or ts > summary["last_ts"]:
                summary["last_ts"] = ts
        key = (sym, ts)
        if key in seen:
            summary["duplicate_keys"] += 1
        else:
            seen.add(key)

        open_v = row.get(open_key)
        high_v = row.get(high_key)
        low_v = row.get(low_key)
        close_v = row.get(close_key)
        volume_v = row.get(volume_key)
        if (
            open_v is not None
            and high_v is not None
            and low_v is not None
            and close_v is not None
            and not (low_v <= open_v <= high_v and low_v <= close_v <= high_v)
        ):
            summary["ohlc_issues"] += 1
        if volume_v is not None and float(volume_v) < 0:
            summary["ohlc_issues"] += 1

    if expected_step and len(timestamps) > 1:
        timestamps = sorted(set(timestamps))
        for previous, current in zip(timestamps, timestamps[1:]):
            if current - previous > expected_step * 2:
                summary["gap_count"] += 1

    return summary


@dataclass
class DatasetManifest:
    dataset: str
    schema_version: int = DEFAULT_SCHEMA_VERSION
    updated_at: str = ""
    source: str = ""
    status: str = "unknown"
    summary: dict[str, Any] | None = None
    repair_mode: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["summary"] = self.summary or {}
        return payload


def load_manifest(path: Path) -> dict[str, Any]:
    return load_json(path, default={})


def write_manifest(path: Path, manifest: DatasetManifest | dict[str, Any]) -> None:
    payload = manifest.to_dict() if isinstance(manifest, DatasetManifest) else manifest
    atomic_write_json(path, payload)
