"""Typed option-chain domain models and provider-payload normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping


class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


class ChainSource(str, Enum):
    LIVE = "live"
    CACHE = "cache"
    LOCAL_DB = "local_db"
    RECORDED = "recorded"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: float = 0.0
    delta: float | None = None
    theta: float | None = None
    instrument_token: str | None = None

    @property
    def spread_pct(self) -> float | None:
        midpoint = (self.bid + self.ask) / 2
        if self.bid <= 0 or self.ask <= 0 or midpoint <= 0:
            return None
        return ((self.ask - self.bid) / midpoint) * 100


@dataclass(frozen=True)
class OptionChain:
    underlying: str
    spot: float
    contracts: tuple[OptionContract, ...]
    fetched_at: datetime
    source: ChainSource = ChainSource.UNKNOWN
    is_synthetic: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def expiries(self) -> tuple[date, ...]:
        return tuple(sorted({contract.expiry for contract in self.contracts}))

    def for_expiry(self, expiry: date) -> tuple[OptionContract, ...]:
        return tuple(c for c in self.contracts if c.expiry == expiry)

    def is_fresh(self, now: datetime, max_age_seconds: float) -> bool:
        fetched = self.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return 0 <= (now - fetched).total_seconds() <= max_age_seconds

    @property
    def live_order_authorized(self) -> bool:
        return bool(self.contracts) and not self.is_synthetic and self.source in {
            ChainSource.LIVE,
            ChainSource.CACHE,
            ChainSource.LOCAL_DB,
        }


@dataclass(frozen=True)
class ChainValidation:
    accepted: bool
    reasons: tuple[str, ...] = ()
    chain: OptionChain | None = None


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip().upper()
    for fmt in (
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d%b%Y",
        "%d%b%y",
        "%d/%m/%Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _number(mapping: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return default


def _integer(mapping: Mapping[str, Any], *keys: str) -> int:
    return int(_number(mapping, *keys))


def _rows_and_metadata(payload: Any) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)], {}
    if not isinstance(payload, Mapping):
        return [], {}
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)], payload
    if not isinstance(data, Mapping):
        return [], payload
    for key in ("records", "filtered"):
        container = data.get(key)
        if isinstance(container, Mapping) and isinstance(container.get("data"), list):
            return [r for r in container["data"] if isinstance(r, Mapping)], container
    rows = data.get("option_chain") or payload.get("option_chain") or []
    return [row for row in rows if isinstance(row, Mapping)], data


def _source(payload: Any) -> tuple[ChainSource, bool]:
    raw = str(payload.get("source", "unknown") if isinstance(payload, Mapping) else "unknown").lower()
    synthetic = bool(isinstance(payload, Mapping) and payload.get("is_synthetic"))
    if raw in {"websocket", "synthetic", "fallback", "test"}:
        return ChainSource.SYNTHETIC, True
    if raw in {"local_db", "database"}:
        return ChainSource.LOCAL_DB, synthetic
    if raw in {"recorded", "fixture"}:
        return ChainSource.RECORDED, synthetic
    if raw in {"cache", "cached"}:
        return ChainSource.CACHE, synthetic
    if raw not in {"", "unknown"}:
        return ChainSource.LIVE, synthetic
    return ChainSource.UNKNOWN, synthetic


def normalize_option_chain(
    payload: Any,
    underlying: str = "",
    *,
    fetched_at: datetime | None = None,
) -> OptionChain:
    """Normalize NSE, broker, local-cache, and flat-list payloads."""
    rows, metadata = _rows_and_metadata(payload)
    root = payload if isinstance(payload, Mapping) else {}
    name = str(underlying or root.get("symbol") or metadata.get("underlying") or "").upper()
    spot = _number(metadata, "underlyingValue", "underlying_price", "spot")
    source, synthetic = _source(payload)
    default_expiry = parse_date(metadata.get("expiryDate") or root.get("expiry"))
    contracts: list[OptionContract] = []

    def append_contract(row: Mapping[str, Any], side: Mapping[str, Any], kind: OptionType) -> None:
        strike = _number(row, "strikePrice", "strike_price", "strike") or _number(side, "strikePrice", "strike")
        expiry = parse_date(
            side.get("expiryDate") or side.get("expiry") or row.get("expiryDate")
            or row.get("expiry_date") or row.get("Expiry_Date")
        ) or default_expiry
        symbol = str(
            side.get("tradingSymbol") or side.get("tradingsymbol") or side.get("symbol")
            or side.get("identifier") or row.get("tradingSymbol") or row.get("symbol") or ""
        )
        if not strike or expiry is None or not symbol:
            return
        contracts.append(
            OptionContract(
                symbol=symbol,
                underlying=name,
                expiry=expiry,
                strike=strike,
                option_type=kind,
                last_price=_number(side, "lastPrice", "LTP", "ltp", "price", "last_price"),
                bid=_number(side, "bidprice", "bidPrice", "bid", "best_bid"),
                ask=_number(side, "askPrice", "askprice", "ask", "best_ask"),
                volume=_integer(side, "totalTradedVolume", "volume", "traded_volume"),
                open_interest=_integer(side, "openInterest", "open_interest", "oi"),
                implied_volatility=_number(side, "impliedVolatility", "iv", "implied_volatility"),
                delta=_number(side, "delta", default=float("nan")),
                theta=_number(side, "theta", default=float("nan")),
                instrument_token=str(side.get("instrument_token") or side.get("instrumentToken") or "") or None,
            )
        )

    for row in rows:
        found_side = False
        for key, kind in (("CE", OptionType.CALL), ("PE", OptionType.PUT)):
            side = row.get(key) or row.get(key.lower())
            if isinstance(side, Mapping):
                append_contract(row, side, kind)
                found_side = True
        if not found_side:
            raw_kind = str(row.get("optionType") or row.get("option_type") or row.get("instrument_type") or "").upper()
            if raw_kind.endswith("CE") or raw_kind in {"CALL", "C"}:
                append_contract(row, row, OptionType.CALL)
            elif raw_kind.endswith("PE") or raw_kind in {"PUT", "P"}:
                append_contract(row, row, OptionType.PUT)

    def clean_optional(value: float | None) -> float | None:
        return None if value is None or value != value else value

    contracts = [
        OptionContract(**{**c.__dict__, "delta": clean_optional(c.delta), "theta": clean_optional(c.theta)})
        for c in contracts
    ]
    unique = {(c.expiry, c.strike, c.option_type, c.symbol): c for c in contracts}
    return OptionChain(
        underlying=name,
        spot=spot,
        contracts=tuple(sorted(unique.values(), key=lambda c: (c.expiry, c.strike, c.option_type.value))),
        fetched_at=fetched_at or datetime.now(timezone.utc),
        source=source,
        is_synthetic=synthetic,
        metadata=dict(metadata),
    )


def contracts_to_legacy_rows(contracts: Iterable[OptionContract]) -> list[dict[str, Any]]:
    """Serialize typed contracts to the established NSE-style row shape."""
    grouped: dict[tuple[date, float], dict[str, Any]] = {}
    for contract in contracts:
        row = grouped.setdefault(
            (contract.expiry, contract.strike),
            {"strikePrice": contract.strike, "expiryDate": contract.expiry.strftime("%d-%b-%Y")},
        )
        row[contract.option_type.value] = {
            "tradingSymbol": contract.symbol,
            "symbol": contract.symbol,
            "lastPrice": contract.last_price,
            "bidprice": contract.bid,
            "askPrice": contract.ask,
            "totalTradedVolume": contract.volume,
            "openInterest": contract.open_interest,
            "impliedVolatility": contract.implied_volatility,
            "delta": contract.delta,
            "theta": contract.theta,
            "instrument_token": contract.instrument_token,
        }
    return [grouped[key] for key in sorted(grouped)]
