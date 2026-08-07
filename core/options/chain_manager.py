"""Central option-chain parsing, cache, freshness, and validation boundary."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.options.models import ChainValidation, OptionChain, normalize_option_chain

log = logging.getLogger(__name__)


class OptionChainManager:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, OptionChain] = {}
        self._lock = RLock()

    @staticmethod
    def cache_key(symbol: str, expiry: str | None = None) -> str:
        return f"{symbol.strip().upper()}:{expiry or 'nearest'}"

    def normalize(self, payload: Any, symbol: str, *, fetched_at: datetime | None = None) -> OptionChain:
        chain = normalize_option_chain(payload, symbol, fetched_at=fetched_at)
        log.debug(
            "option_chain_normalized symbol=%s contracts=%d source=%s synthetic=%s",
            symbol, len(chain.contracts), chain.source.value, chain.is_synthetic,
        )
        return chain

    def put(self, chain: OptionChain, expiry: str | None = None) -> None:
        with self._lock:
            self._cache[self.cache_key(chain.underlying, expiry)] = chain

    def get(self, symbol: str, expiry: str | None = None, *, now: datetime | None = None) -> OptionChain | None:
        with self._lock:
            chain = self._cache.get(self.cache_key(symbol, expiry))
        if chain and chain.is_fresh(now or datetime.now(timezone.utc), self.ttl_seconds):
            return chain
        return None

    def ingest(self, payload: Any, symbol: str, expiry: str | None = None) -> OptionChain:
        chain = self.normalize(payload, symbol)
        self.put(chain, expiry)
        return chain

    def validate(
        self,
        chain: OptionChain,
        *,
        live_order: bool = False,
        now: datetime | None = None,
        require_contracts: bool = True,
    ) -> ChainValidation:
        reasons: list[str] = []
        current = now or datetime.now(timezone.utc)
        if require_contracts and not chain.contracts:
            reasons.append("empty_option_chain")
        if not chain.is_fresh(current, self.ttl_seconds):
            reasons.append("stale_option_chain")
        if live_order and not chain.live_order_authorized:
            reasons.append("synthetic_or_untrusted_chain_for_live_order")
        if not chain.underlying:
            reasons.append("missing_underlying")
        return ChainValidation(not reasons, tuple(reasons), chain)


option_chain_manager = OptionChainManager()
