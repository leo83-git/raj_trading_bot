"""Utility functions for retrieving option chain data from local cache / SQLite.

The implementation is intentionally lightweight: it loads the cached F&O
contracts from :class:`screener.fno_contract_loader.FnoContractLoader` and
filters them for the requested underlying symbol. The returned structure mimics
the format produced by the NSE HTTP endpoint so that the existing pipeline
code can operate unchanged.
"""

import logging
from datetime import date, datetime

from screener.fno_contract_loader import FnoContractLoader
from utils.cache import get_cached_option_chain, set_cached_option_chain

log = logging.getLogger(__name__)

_loader = None


def _load_contracts() -> list[dict]:
    """Load contracts using the shared ``FnoContractLoader`` singleton.

    The loader maintains an in‑memory list refreshed periodically. This helper
    simply returns that list, falling back to an empty list on error.
    """
    global _loader
    try:
        if _loader is None:
            _loader = FnoContractLoader()
        return list(_loader.contracts)
    except Exception as exc:  # pragma: no cover – defensive
        log.debug(f"Failed to instantiate FnoContractLoader: {exc}")
        return []


def get_local_option_chain(symbol: str) -> dict | None:
    """Return a locally‑generated option chain for *symbol*.

    The function first checks the in‑memory cache. If a cached entry exists and
    is still valid, it is returned immediately. Otherwise it builds a minimal
    chain from the contracts database.

    The returned dictionary follows the shape expected by ``extract_option_chain_data``
    in ``main.py``:

    ::

        {
            "data": {
                "records": {
                    "data": [<contract dicts>],
                    "underlyingValue": <price if known>
                }
            },
            "symbol": <symbol>,
            "source": "local_db"
        }
    """
    # Check cache first
    cached = get_cached_option_chain(symbol)
    if cached:
        cached_data = cached.get("data", {}) if isinstance(cached, dict) else {}
        cached_records = (
            cached_data.get("records", {}) if isinstance(cached_data, dict) else {}
        )
        cached_rows = (
            cached_records.get("data", []) if isinstance(cached_records, dict) else []
        )
        has_symbols = any(
            isinstance(row, dict)
            and any(
                isinstance(row.get(side), dict)
                and (row[side].get("tradingSymbol") or row[side].get("symbol"))
                for side in ("CE", "PE")
            )
            for row in cached_rows
        )
        if has_symbols:
            log.debug(f"Option chain cache hit for {symbol}")
            return cached

    if not symbol:
        return None

    underlying = str(symbol).strip().upper()
    contracts = _load_contracts()

    def parse_expiry(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not value:
            return None
        text = str(value).strip().upper()
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d%b%Y", "%d%b%y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    # Futures and expired contracts cannot supply option legs.
    filtered = []
    for contract in contracts:
        contract_symbol = str(contract.get("symbol") or "").strip().upper()
        if not contract_symbol.startswith(underlying):
            continue
        option_type = str(contract.get("option_type") or "").upper()
        if option_type not in {"CE", "PE"}:
            if contract_symbol.endswith("CE"):
                option_type = "CE"
            elif contract_symbol.endswith("PE"):
                option_type = "PE"
        if option_type not in {"CE", "PE"}:
            continue
        expiry = parse_expiry(contract.get("expiry"))
        if expiry is None or expiry < date.today():
            continue
        filtered.append({**contract, "option_type": option_type, "expiry_date": expiry})

    if not filtered:
        log.debug(f"No local contracts found for {symbol}")
        return None

    # Group CE and PE contracts into the NSE-style rows expected downstream.
    grouped = {}
    for contract in filtered:
        try:
            strike = float(contract.get("strike"))
        except (TypeError, ValueError):
            continue
        key = (contract["expiry_date"], strike)
        row = grouped.setdefault(
            key,
            {
                "strikePrice": strike,
                "expiryDate": contract.get("expiry"),
            },
        )
        side = contract["option_type"]
        row[side] = {
            "tradingSymbol": contract.get("symbol"),
            "symbol": contract.get("symbol"),
            "instrument_token": contract.get("instrument_token"),
        }

    records = [grouped[key] for key in sorted(grouped)]
    expiry_dates = sorted(
        {row["expiryDate"] for row in records if row.get("expiryDate")}
    )

    chain = {
        "data": {
            "records": {
                "data": records,
                "expiryDates": expiry_dates,
            }
        },
        "symbol": symbol,
        "source": "local_db",
    }

    # Cache the result for future calls
    set_cached_option_chain(symbol, chain)
    log.debug(
        f"Generated local option chain with {len(records)} contracts for {symbol}"
    )
    return chain
