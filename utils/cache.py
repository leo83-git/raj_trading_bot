# ═══════════════════════════════════════════════════════════════
#  Cache Utilities for Performance Optimization
# ═══════════════════════════════════════════════════════════════
from typing import Any

from cachetools import TTLCache

# Global caches with TTL (Time To Live)
option_chain_cache = TTLCache(maxsize=100, ttl=300)  # 5 minutes TTL for option chains
quote_cache = TTLCache(maxsize=500, ttl=60)  # 1 minute TTL for quotes
fno_contracts_cache = TTLCache(maxsize=4, ttl=24 * 60 * 60)


def get_cached_option_chain(symbol: str) -> dict[str, Any]:
    """Get cached option chain if available."""
    return option_chain_cache.get(symbol)


def set_cached_option_chain(symbol: str, data: dict[str, Any]) -> None:
    """Cache option chain data."""
    option_chain_cache[symbol] = data


def get_cached_quote(symbol: str) -> dict[str, Any]:
    """Get cached quote if available."""
    return quote_cache.get(symbol)


def set_cached_quote(symbol: str, data: dict[str, Any]) -> None:
    """Cache quote data."""
    quote_cache[symbol] = data


def get_cached_fno_contracts(key: str = "default") -> dict[str, Any] | None:
    return fno_contracts_cache.get(key)


def set_cached_fno_contracts(key: str, data: dict[str, Any]) -> None:
    fno_contracts_cache[key] = data


def clear_caches() -> None:
    """Clear all caches (useful for forced refresh)."""
    option_chain_cache.clear()
    quote_cache.clear()
    fno_contracts_cache.clear()


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics for monitoring."""
    return {
        "option_chain": {
            "size": len(option_chain_cache),
            "maxsize": option_chain_cache.maxsize,
            "ttl": 300,
        },
        "quote": {"size": len(quote_cache), "maxsize": quote_cache.maxsize, "ttl": 60},
        "fno_contracts": {
            "size": len(fno_contracts_cache),
            "maxsize": fno_contracts_cache.maxsize,
            "ttl": 24 * 60 * 60,
        },
    }
