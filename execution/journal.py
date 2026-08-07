"""Crash-safe JSON journal for multi-leg execution state."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from execution.models import TERMINAL_STATES, StrategyExecution

log = logging.getLogger("execution_journal")


class JournalCorruptionError(RuntimeError):
    """Raised when recovery cannot safely interpret an existing journal."""


class ExecutionJournal:
    """Persist the latest state using fsync plus atomic replacement."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("execution_journal_read_failed path=%s", self.path)
            raise JournalCorruptionError(
                f"Cannot safely recover execution journal: {self.path}"
            ) from exc
        if not isinstance(value, dict):
            raise JournalCorruptionError(
                f"Execution journal root must be an object: {self.path}"
            )
        return value

    def save(self, execution: StrategyExecution) -> None:
        with self._lock:
            records = self._read()
            records[execution.strategy_id] = self._safe_payload(execution.to_dict())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(records, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    def get(self, strategy_id: str) -> StrategyExecution | None:
        with self._lock:
            value = self._read().get(strategy_id)
        return StrategyExecution.from_dict(value) if value else None

    def incomplete(self) -> list[StrategyExecution]:
        with self._lock:
            values = self._read().values()
            executions = [StrategyExecution.from_dict(value) for value in values]
        return [item for item in executions if item.state not in TERMINAL_STATES]

    @classmethod
    def _safe_payload(cls, value):
        """Redact credentials and coerce opaque broker values for JSON storage."""
        if isinstance(value, dict):
            return {
                str(key): (
                    "[REDACTED]"
                    if any(
                        secret in str(key).lower()
                        for secret in ("token", "secret", "password", "api_key")
                    )
                    else cls._safe_payload(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._safe_payload(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return repr(value)
