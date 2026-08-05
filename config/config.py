"""Compatibility shim for tests expecting ``config.config``.

The test suite imports ``load_config`` via ``from config.config import load_config``.
Historically the implementation lived in ``config/config.py``. The project now
exposes ``load_config`` in ``config.__init__`` (which re‑exports the function from
``raj_trading_bot.main``). To maintain backward compatibility we provide a
thin wrapper that implements ``load_config`` directly, propagating any file
errors as the tests expect.
"""

from __future__ import annotations

import os
from typing import Any

import yaml


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load a YAML configuration file.

    This implementation mirrors the original loader but **does not** suppress
    ``FileNotFoundError`` or YAML parsing errors – the test suite asserts that
    those exceptions propagate.

    Parameters
    ----------
    config_path: Optional[str]
        Path to the YAML file. If ``None`` the default location
        ``<package_dir>/config/config.yaml`` is used.
    """
    if config_path is None:
        # Resolve the default config relative to this file's directory.
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")

    # Let underlying file operations raise naturally.
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Ensure we always return a dict (safe_load may return None).
    return cfg if isinstance(cfg, dict) else {}
