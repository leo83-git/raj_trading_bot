"""Deterministic expiry, strike, and liquidity-aware contract selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from core.options.models import OptionChain, OptionContract, OptionType


@dataclass(frozen=True)
class SelectionCriteria:
    option_type: OptionType
    target_delta: float | None = None
    target_moneyness: float = 0.0
    min_dte: int = 1
    max_dte: int = 45
    min_volume: int = 0
    min_open_interest: int = 0
    max_spread_pct: float | None = None
    require_price: bool = True


@dataclass(frozen=True)
class ContractSelection:
    accepted: bool
    contract: OptionContract | None = None
    expiry: date | None = None
    method: str = "none"
    reasons: tuple[str, ...] = ()


class ContractSelector:
    def select_expiry(self, chain: OptionChain, criteria: SelectionCriteria, today: date | None = None) -> tuple[date | None, tuple[str, ...]]:
        current = today or date.today()
        eligible = [expiry for expiry in chain.expiries if criteria.min_dte <= (expiry - current).days <= criteria.max_dte]
        if not eligible:
            return None, ("no_expiry_in_dte_range",)
        return min(eligible), ()

    def select(self, chain: OptionChain, criteria: SelectionCriteria, *, today: date | None = None) -> ContractSelection:
        expiry, reasons = self.select_expiry(chain, criteria, today)
        if expiry is None:
            return ContractSelection(False, reasons=reasons)
        contracts = [c for c in chain.for_expiry(expiry) if c.option_type == criteria.option_type]
        liquid, rejected = self._liquid(contracts, criteria)
        if not liquid:
            return ContractSelection(False, expiry=expiry, reasons=tuple(sorted(rejected)) or ("no_contracts_for_type",))
        delta_candidates = [c for c in liquid if c.delta is not None]
        if criteria.target_delta is not None and delta_candidates:
            target = abs(criteria.target_delta)
            chosen = min(delta_candidates, key=lambda c: (abs(abs(c.delta or 0) - target), abs(c.strike - chain.spot), c.symbol))
            return ContractSelection(True, chosen, expiry, "delta")
        if chain.spot > 0:
            target_strike = chain.spot * (1 + criteria.target_moneyness)
            chosen = min(liquid, key=lambda c: (abs(c.strike - target_strike), c.strike, c.symbol))
            method = "moneyness" if criteria.target_moneyness else "atm_fallback"
            return ContractSelection(True, chosen, expiry, method)
        return ContractSelection(False, expiry=expiry, reasons=("missing_spot_for_strike_selection",))

    @staticmethod
    def _liquid(contracts: Iterable[OptionContract], criteria: SelectionCriteria) -> tuple[list[OptionContract], set[str]]:
        accepted: list[OptionContract] = []
        reasons: set[str] = set()
        for contract in contracts:
            failures = []
            if criteria.require_price and contract.last_price <= 0:
                failures.append("non_positive_premium")
            if contract.volume < criteria.min_volume:
                failures.append("volume_below_minimum")
            if contract.open_interest < criteria.min_open_interest:
                failures.append("open_interest_below_minimum")
            spread = contract.spread_pct
            if criteria.max_spread_pct is not None and spread is not None and spread > criteria.max_spread_pct:
                failures.append("spread_above_maximum")
            if failures:
                reasons.update(failures)
            else:
                accepted.append(contract)
        return accepted, reasons
