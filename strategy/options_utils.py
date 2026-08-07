# ═══════════════════════════════════════════════════════════════
#  Options Strategy Utilities
#  Common functions for options strategies (expiry, strikes, symbols)
# ═══════════════════════════════════════════════════════════════
import datetime as dt
from typing import Any

from core.options.contract_selector import ContractSelector, SelectionCriteria
from core.options.models import (
    OptionChain,
    OptionType,
    normalize_option_chain,
    parse_date,
)
from quant_utils.logger import get_logger

log = get_logger("options_utils")


# NSE holidays list (update yearly)
NSE_HOLIDAYS = [
    dt.date(2025, 1, 26),
    dt.date(2025, 3, 14),
    dt.date(2025, 3, 31),
    dt.date(2025, 4, 11),
    dt.date(2025, 4, 14),
    dt.date(2025, 4, 18),
    dt.date(2025, 5, 1),
    dt.date(2025, 8, 15),
    dt.date(2025, 10, 2),
    dt.date(2025, 10, 31),
    dt.date(2025, 11, 15),
    dt.date(2025, 12, 25),
    dt.date(2026, 1, 26),
    dt.date(2026, 3, 14),
    dt.date(2026, 3, 31),
    dt.date(2026, 4, 10),
    dt.date(2026, 4, 14),
    dt.date(2026, 4, 18),
    dt.date(2026, 5, 1),
    dt.date(2026, 8, 15),
    dt.date(2026, 10, 2),
    dt.date(2026, 10, 31),
    dt.date(2026, 11, 15),
    dt.date(2026, 12, 25),
]


def get_next_expiry(holidays: list[dt.date] | None = None) -> dt.date:
    """Calculate next weekly expiry (Thursday for weekly contracts)"""
    if holidays is None:
        holidays = NSE_HOLIDAYS

    current_date = dt.date.today()
    wd = current_date.weekday()  # 0=Mon, 3=Thu, 6=Sun

    # Days to next Thursday
    if wd <= 3:
        days_to_thursday = 3 - wd
    else:
        days_to_thursday = 6

    exp_date = current_date + dt.timedelta(days=days_to_thursday)

    # Adjust for holidays
    while exp_date in holidays:
        exp_date = exp_date - dt.timedelta(days=1)

    return exp_date


def get_atm_strike(spot_price: float, strike_interval: int = 50) -> int:
    """Round spot price to nearest ATM strike (50-point interval for NIFTY/BANKNIFTY)"""
    remainder = spot_price % strike_interval
    if remainder < strike_interval / 2:
        atm = spot_price - remainder
    else:
        atm = spot_price - remainder + strike_interval
    return int(atm)


def get_atm_strike_nifty(spot: float) -> int:
    """NIFTY ATM strike (50-point interval)"""
    return get_atm_strike(spot, 50)


def get_atm_strike_banknifty(spot: float) -> int:
    """BANKNIFTY ATM strike (100-point interval)"""
    return get_atm_strike(spot, 100)


def get_atm_strike_finnifty(spot: float) -> int:
    """FINNIFTY ATM strike (50-point interval)"""
    return get_atm_strike(spot, 50)


def filter_options_by_expiry(
    instruments_df, expiry: dt.date, name: str = "NIFTY"
) -> list[dict]:
    """Filter option instruments for given expiry and underlying"""
    return instruments_df[
        (instruments_df["name"] == name) & (instruments_df["expiry"] == expiry)
    ].to_dict("records")


def find_option_symbol(instruments_df, strike: int, option_type: str) -> str | None:
    """Find trading symbol for given strike and option type"""
    try:
        # Filter for exact strike
        strike_options = [opt for opt in instruments_df if opt.get("strike") == strike]
        if not strike_options:
            return None

        # Find matching option type
        for opt in strike_options:
            if opt.get("instrument_type") == option_type:
                return opt.get("tradingsymbol")

        return None
    except Exception as e:
        log.error(f"Error finding option symbol: {e}")
        return None


def normalize_chain(payload: Any, underlying: str = "") -> OptionChain:
    """Backward-compatible entry point for the canonical typed parser."""
    return normalize_option_chain(payload, underlying)


def select_option_contract(
    payload: Any,
    underlying: str,
    option_type: str,
    *,
    target_delta: float | None = None,
    target_moneyness: float = 0.0,
    min_dte: int = 1,
    max_dte: int = 45,
    min_volume: int = 0,
    min_open_interest: int = 0,
    max_spread_pct: float | None = None,
):
    """Select a validated contract using delta/moneyness with ATM fallback."""
    chain = (
        payload
        if isinstance(payload, OptionChain)
        else normalize_chain(payload, underlying)
    )
    criteria = SelectionCriteria(
        option_type=OptionType(str(option_type).upper()),
        target_delta=target_delta,
        target_moneyness=target_moneyness,
        min_dte=min_dte,
        max_dte=max_dte,
        min_volume=min_volume,
        min_open_interest=min_open_interest,
        max_spread_pct=max_spread_pct,
    )
    return ContractSelector().select(chain, criteria)


def parse_expiry(value: Any) -> dt.date | None:
    """Preserved parser wrapper backed by the canonical date parser."""
    return parse_date(value)


def calculate_lot_size(symbol: str) -> int:
    """Get standard lot size for Indian indices"""
    lot_sizes = {
        "NIFTY": 65,
        "BANKNIFTY": 30,
        "FINNIFTY": 60,
        "MIDCPNIFTY": 120,
        "NIFTYNXT50": 75,
        "SENSEX": 20,
        "BANKEX": 30,
        "SENSEX50": 75,
    }
    return lot_sizes.get(symbol.upper(), 25)


def format_option_symbol(
    symbol: str, strike: int, opt_type: str, expiry: dt.date
) -> str:
    """Format option symbol in standard format (e.g., NIFTY23OCT24C24300)"""
    # Different brokers use different formats; this is simplified
    expiry_str = expiry.strftime("%d%b%y").upper()
    return f"{symbol}{expiry_str}{opt_type}{strike}"
