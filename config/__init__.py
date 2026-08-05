"""Configuration package for Quant Trading System.

The test suite expects a ``config.config`` module exposing a ``load_config``
function.  The original implementation lives in ``raj_trading_bot.main``
as ``load_config``.  To keep a single source of truth we simply re‑export that
function here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

# Import the canonical implementation from the main module.
try:
    try:
        from raj_trading_bot.main import load_config as _load_config  # type: ignore
    except ImportError:
        from main import load_config as _load_config  # type: ignore
except Exception as exc:  # pragma: no cover – defensive fallback
    logging.getLogger(__name__).warning(
        "Failed to import load_config from main: %s", exc
    )
    _load_config = None  # type: ignore


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration using the implementation from ``main``.

    Parameters
    ----------
    config_path: Optional[str]
        Path to a YAML configuration file.  If ``None`` the default location
        used by ``raj_trading_bot.main.load_config`` is applied.

    Returns
    -------
    dict
        Parsed configuration dictionary (may be empty on failure).
    """
    if _load_config is None:
        # Gracefully return an empty config if the import failed.
        logging.getLogger(__name__).error("load_config implementation unavailable")
        return {}
    return _load_config(config_path)
