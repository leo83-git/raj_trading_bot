#!/usr/bin/env python3
"""Minimal Support/Resistance (SR) utility – MVP.

Provides a tiny in‑memory cache and a classic pivot‑point calculation.
The functions are deliberately simple so they can be imported anywhere
without heavy dependencies.
"""

import time

# Simple in‑memory cache for SR levels per symbol
_sr_cache = {}
_cache_ttl = 300  # seconds (5 minutes)
_cache_time = {}


def _now():
    """Return the current Unix timestamp as an integer."""
    return int(time.time())


def compute_pivot_points(price_history):
    """Calculate classic pivot points from a price‑history mapping.

    Expected keys: ``high``, ``low``, ``close``. Returns ``None`` if any are
    missing or if ``price_history`` is empty.
    """
    if not price_history:
        return None
    hi = price_history.get("high")
    lo = price_history.get("low")
    cl = price_history.get("close")
    if hi is None or lo is None or cl is None:
        return None
    P = (hi + lo + cl) / 3.0
    R1 = 2 * P - lo
    S1 = 2 * P - hi
    R2 = P + (hi - lo)
    S2 = P - (hi - lo)
    R3 = hi + 2 * (P - lo)
    S3 = lo - 2 * (hi - P)
    return {"P": P, "R1": R1, "S1": S1, "R2": R2, "S2": S2, "R3": R3, "S3": S3}


def get_sr_levels(
    symbol,
    price_history=None,
    method="classic",
    lookback=20,
):
    """Return cached SR levels or compute them on‑the‑fly.

    * ``symbol`` – ticker symbol.
    * ``price_history`` – optional mapping with ``high``, ``low`` and ``close``.
    * ``method`` – placeholder for future extensions (currently only ``"classic"``).
    * ``lookback`` – placeholder for future extensions.
    """
    # Simple cache check
    now = _now()
    if symbol in _sr_cache and now - _cache_time.get(symbol, 0) < _cache_ttl:
        return _sr_cache[symbol]

    levels = {}
    if price_history:
        pts = compute_pivot_points(price_history)
        if pts:
            levels.update(pts)

    _sr_cache[symbol] = levels
    _cache_time[symbol] = now
    return levels
