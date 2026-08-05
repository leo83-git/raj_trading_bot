# ═══════════════════════════════════════════════════════════════
#  NSE Data Enrichment — Fundamental & Market Microstructure Data
#  Adapted from nselib (RuchiTanmay/nselib)
#  Provides: financial results, corporate actions, events, FII/DII flows,
#            index constituents, market breadth, India VIX
# ═══════════════════════════════════════════════════════════════
import logging
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from quant_utils.logger import get_logger

logging.getLogger("urllib3").setLevel(logging.CRITICAL)

log = get_logger("sources.nse_enrichment")


# ─── HTTP Session Management ──────────────────────────────────────────────
_default_header = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

_nse_header = {
    "referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Sec-Fetch-User": "?1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
}

_nse_session = None


def _get_nse_session() -> requests.Session:
    global _nse_session
    if _nse_session is None:
        _nse_session = requests.Session()
        # Increase pool size to prevent "Connection pool is full" warnings
        # No urllib3 Retry - let our own retry logic handle 500/503 errors with proper jitter
        adapter = HTTPAdapter(pool_connections=30, pool_maxsize=30, pool_block=False)
        _nse_session.mount("https://", adapter)
        _nse_session.mount("http://", adapter)
        _nse_session.headers.update(_default_header)
    return _nse_session


def _nse_urlfetch(
    url: str, origin_url: str = "https://www.nseindia.com/"
) -> requests.Response:
    """Fetch NSE URL with proper session/cookie handling and retry for 500/503 errors"""
    session = _get_nse_session()
    max_retries = 3

    for attempt in range(max_retries):
        try:
            if not session.cookies or not session.cookies.get_dict():
                session.get(origin_url, headers=_default_header, timeout=10)
            response = session.get(url, headers=_nse_header, timeout=15)

            if response.status_code in (500, 502, 503, 504):
                if attempt < max_retries - 1:
                    wait_time = 2**attempt + random.uniform(0, 1)
                    log.debug(
                        f"NSE URL returned {response.status_code}, retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                    continue
            return response
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2**attempt + random.uniform(0, 1)
                log.debug(f"NSE URL fetch failed: {e}, retrying in {wait_time:.1f}s")
                time.sleep(wait_time)
            else:
                raise
    # Final attempt without retry loop
    if not session.cookies or not session.cookies.get_dict():
        session.get(origin_url, headers=_default_header, timeout=10)
    return session.get(url, headers=_nse_header, timeout=15)


# ─── Date Helpers ─────────────────────────────────────────────────────────
_dd_mm_yyyy = "%d-%m-%Y"
_ddmmyyyy = "%d%m%Y"

# VIX module-level cache to prevent repeated rate-limited requests
_vix_module_cache = {"data": None, "time": None, "last_attempt": 0}


def _validate_date_param(
    from_date: str | None, to_date: str | None, period: str | None
):
    if not period and (not from_date or not to_date):
        raise ValueError("Either from_date/to_date or period must be provided")
    if period and period.upper() not in ["1D", "1W", "1M", "3M", "6M", "1Y"]:
        raise ValueError(f"Invalid period: {period}")


def _derive_from_and_to_date(
    from_date: str | None, to_date: str | None, period: str | None
):
    """Convert period shorthand to actual dates"""
    if not period:
        return from_date, to_date

    today = date.today()
    period_map = {
        "1D": today - timedelta(days=1),
        "1W": today - timedelta(weeks=1),
        "1M": today - timedelta(days=30),
        "3M": today - timedelta(days=90),
        "6M": today - timedelta(days=180),
        "1Y": today - timedelta(days=365),
    }

    from_dt = period_map.get(period.upper(), today - timedelta(days=1))
    # Adjust to most recent trading day
    while from_dt.weekday() >= 5:  # Skip weekends
        from_dt += timedelta(days=1)

    return from_dt.strftime(_dd_mm_yyyy), today.strftime(_dd_mm_yyyy)


def _clean_nse_symbol(symbol: str) -> str:
    """URL-encode NSE symbol (handles &, spaces)"""
    return symbol.replace("&", "%26").upper()


# ─── NSE Enrichment Provider ──────────────────────────────────────────────
@dataclass
class FinancialResult:
    """Simplified financial result record"""

    symbol: str
    date: str
    quarter: str
    revenue: float | None
    net_profit: float | None
    eps: float | None
    revenue_growth: float | None = None
    profit_growth: float | None = None


@dataclass
class CorporateAction:
    """Corporate action record"""

    symbol: str
    action_type: str  # DIVIDEND, BONUS, SPLIT
    date: str
    details: dict[str, Any]


@dataclass
class EventCalendar:
    """Upcoming corporate event"""

    symbol: str
    event_type: str
    date: str
    description: str


class NSEEnrichmentProvider:
    """
    Enriches market data with NSE-specific fundamental and microstructure data

    Data sources (via direct NSE API/scraping):
    - Financial results (XBRL)
    - Corporate actions (dividends, bonuses, splits)
    - Event calendar (AGM, earnings)
    - FII/DII flow (participant-wise OI/trading volume)
    - Index constituents
    - Market breadth (gainers/losers, most active)
    - India VIX (with broker fallback)
    """

    def __init__(self, config: dict = None, data_provider=None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.data_provider = data_provider

        # Only enable features if NSE enrichment is enabled
        if self.enabled:
            self.enable_fundamentals = self.config.get("enable_fundamentals", True)
            self.enable_corporate_actions = self.config.get(
                "enable_corporate_actions", True
            )
            self.enable_fii_dii = self.config.get("enable_fii_dii", True)
            self.enable_index_data = self.config.get("enable_index_data", True)
        else:
            self.enable_fundamentals = False
            self.enable_corporate_actions = False
            self.enable_fii_dii = False
            self.enable_index_data = False

        # Caches for rate-limiting protection
        self._holiday_cache = None
        self._holiday_cache_date = None

        status = "enabled" if self.enabled else "disabled"
        log.info(f"NSE Enrichment provider initialized ({status})")

    # ═══════════════════════════════════════════════════════════════════════
    # Fundamentals: Financial Results
    # ═══════════════════════════════════════════════════════════════════════
    def get_financial_results(
        self, symbol: str, period: str = "6M"
    ) -> list[FinancialResult]:
        """
        Fetch quarterly financial results for a stock.

        Args:
            symbol: Stock symbol (e.g., 'RELIANCE')
            period: Lookback period (1D, 1W, 1M, 3M, 6M, 1Y)

        Returns:
            List of FinancialResult objects with revenue, net profit, EPS
        """
        if not self.enable_fundamentals:
            return []

        from_date, to_date = _derive_from_and_to_date(None, None, period)
        symbol = _clean_nse_symbol(symbol)

        try:
            url = f"https://www.nseindia.com/api/companies/announcements?index=equities&from_date={from_date}&to_date={to_date}&fo_sec=true"
            # Note: This endpoint requires authentication cookies. Fallback implementation
            # would use the nselib pattern with multiple attempts.
            # For now, return empty (placeholder for real implementation)
            log.debug(
                f"Financial results for {symbol}: endpoint requires full auth implementation"
            )
            return []
        except Exception as e:
            log.warning(f"Failed to fetch financial results for {symbol}: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # Corporate Actions
    # ═══════════════════════════════════════════════════════════════════════
    def get_corporate_actions(
        self, symbol: str, period: str = "3M"
    ) -> list[CorporateAction]:
        """
        Get corporate actions (dividends, bonuses, splits) for a stock.
        """
        if not self.enable_corporate_actions:
            return []

        from_date, to_date = _derive_from_and_to_date(None, None, period)
        symbol = _clean_nse_symbol(symbol)

        try:
            url = f"https://www.nseindia.com/api/corporates-corporateactions?index=equities&from_date={from_date}&to_date={to_date}"
            response = _nse_urlfetch(
                url,
                "https://www.nseindia.com/companies-listing/corporate-filings-actions",
            )
            if response.status_code != 200:
                return []

            data = response.json()
            actions = []
            for item in data.get("data", []):
                actions.append(
                    CorporateAction(
                        symbol=item.get("symbol", ""),
                        action_type=item.get("subject", ""),
                        date=item.get("announcementDate", ""),
                        details=item,
                    )
                )
            return actions
        except Exception as e:
            log.warning(f"Failed to fetch corporate actions for {symbol}: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # Event Calendar (Earnings, AGMs, etc.)
    # ═══════════════════════════════════════════════════════════════════════
    def get_event_calendar(
        self, symbol: str, period: str = "1M"
    ) -> list[EventCalendar]:
        """
        Get upcoming corporate events (earnings, AGM, results declaration).
        """
        if not self.enable_corporate_actions:
            return []

        from_date, to_date = _derive_from_and_to_date(None, None, period)

        try:
            url = f"https://www.nseindia.com/api/companies/announcements?index=equities&from_date={from_date}&to_date={to_date}"
            response = _nse_urlfetch(
                url,
                "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar",
            )
            if response.status_code != 200:
                return []

            data = response.json()
            events = []
            for item in data.get("data", []):
                events.append(
                    EventCalendar(
                        symbol=item.get("symbol", ""),
                        event_type=item.get("announcement_type", ""),
                        date=item.get("announcement_date", ""),
                        description=item.get("subject", ""),
                    )
                )
            return events
        except Exception as e:
            log.warning(f"Failed to fetch event calendar: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # FII/DII Flow (from derivatives participant data)
    # ═══════════════════════════════════════════════════════════════════════
    def get_participant_wise_oi(
        self, from_date: str, to_date: str
    ) -> pd.DataFrame | None:
        """
        Get participant-wise open interest (FII/DII/Proprietary/client data).

        Returns:
            DataFrame with columns: [type, symbol, expiry, option_type, oi, change_oi]
        """
        if not self.enable_fii_dii:
            return None

        try:
            url = f"https://www.nseindia.com/api/option-chain-indices?from={from_date}&to={to_date}"
            # Note: This requires deeper integration with NSE's API. Placeholder.
            return None
        except Exception as e:
            log.warning(f"Failed to fetch participant OI: {e}")
            return None

    def get_fii_dii_flow(self) -> dict[str, float]:
        """
        Get net FII/DII flows (cash market) for the latest trading day.

        Returns:
            Dict with keys: fii_net, dii_net, fii_buy, fii_sell, dii_buy, dii_sell
        """
        try:
            # Use NSE's daily market turnover API
            url = "https://www.nseindia.com/api/fiidii-trade-data"
            response = _nse_urlfetch(url, "https://www.nseindia.com/")
            if response.status_code != 200:
                return {"fii_net": 0.0, "dii_net": 0.0, "date": ""}

            data = response.json()
            # Extract most recent entry
            latest = data[-1] if data else {}
            return {
                "fii_net": float(latest.get("fii_net", 0) or 0),
                "dii_net": float(latest.get("dii_net", 0) or 0),
                "date": latest.get("date", ""),
                "fii_buy": float(latest.get("fii_gross_buy", 0) or 0),
                "fii_sell": float(latest.get("fii_gross_sell", 0) or 0),
                "dii_buy": float(latest.get("dii_gross_buy", 0) or 0),
                "dii_sell": float(latest.get("dii_gross_sell", 0) or 0),
            }
        except Exception as e:
            log.warning(f"Failed to fetch FII/DII flow: {e}")
            return {"fii_net": 0.0, "dii_net": 0.0, "date": ""}

    # ═══════════════════════════════════════════════════════════════════════
    # Index Constituents & Market Breadth
    # ═══════════════════════════════════════════════════════════════════════
    def get_index_constituents(self, index_name: str) -> list[str]:
        """
        Get list of stock symbols in a given NSE index.

        Args:
            index_name: e.g., 'NIFTY 50', 'NIFTY BANK', 'NIFTY IT'

        Returns:
            List of stock symbols
        """
        if not self.enable_index_data:
            return []

        try:
            index_code = index_name.upper().replace(" ", "%20")
            url = f"https://www.nseindia.com/api/equity-stockIndices?index={index_code}"
            response = _nse_urlfetch(
                url, "https://www.nseindia.com/market-data/live-equity-market"
            )
            if response.status_code != 200:
                return []

            data = response.json()
            symbols = []
            for item in data.get("data", []):
                symbols.append(item.get("symbol", ""))
            return symbols
        except Exception as e:
            log.warning(f"Failed to fetch constituents for {index_name}: {e}")
            return []

    def get_market_breadth(self) -> dict[str, Any]:
        """
        Get market breadth (advances/declines, top gainers/losers, most active).
        """
        try:
            url = "https://www.nseindia.com/api/market-data-preview?category=equities"
            response = _nse_urlfetch(url, "https://www.nseindia.com/")
            if response.status_code != 200:
                return {}

            data = response.json()
            # Parse breadth data from response
            # This is a simplified version; actual response structure may vary
            return {
                "advances": data.get("advances", 0),
                "declines": data.get("declines", 0),
                "unchanged": data.get("unchanged", 0),
                "top_gainers": [],
                "top_losers": [],
                "most_active": [],
            }
        except Exception as e:
            log.warning(f"Failed to fetch market breadth: {e}")
            return {}

    # ═══════════════════════════════════════════════════════════════════════
    # India VIX
    # ═══════════════════════════════════════════════════════════════════════
    def get_india_vix(self, period: str = "1W") -> pd.DataFrame | None:
        """
        Get India VIX with broker fallback to avoid rate limiting.

        Priority: data_provider (Zerodha/other brokers) -> NSE direct API (rate-limited)

        Args:
            period: Lookback period (typically 1W for recent VIX)

        Returns:
            DataFrame with columns: TIMESTAMP, CLOSE_INDEX_VAL, etc.
        """
        import pandas as pd

        # Try data_provider first (uses Zerodha/other brokers)
        if self.data_provider is not None:
            try:
                quote = self.data_provider.get_quote("INDIAVIX")
                if quote:
                    vix_value = quote.get("last_price") or quote.get("ltp") or 0
                    if vix_value:
                        now = datetime.now()
                        log.debug(f"India VIX fetched via data_provider: {vix_value}")
                        return pd.DataFrame(
                            {"TIMESTAMP": [now], "CLOSE_INDEX_VAL": [float(vix_value)]}
                        )
            except Exception as e:
                log.debug(f"data_provider VIX fetch failed: {e}")

        global _vix_module_cache
        now = datetime.now()

        # Check module-level cache first to prevent repeated rate-limited requests
        if _vix_module_cache["data"] is not None:
            if (
                now - _vix_module_cache["time"]
            ).total_seconds() < 300:  # 5 minute cache
                log.debug("Returning cached India VIX data")
                return _vix_module_cache["data"].copy()
            # If last attempt was within 5 minutes and failed, don't retry
            if (now - _vix_module_cache["last_attempt"]).total_seconds() < 300:
                log.warning(
                    "India VIX cache miss but still in cooldown - returning None"
                )
                return None

        max_retries = 5
        backoff_times = [5, 10, 20, 40, 80]

        for attempt in range(max_retries):
            try:
                from_date, to_date = _derive_from_and_to_date(None, None, period)
                url = f"https://www.nseindia.com/api/historical/indices?indexSymbol=INDIAVIX&from={from_date}&to={to_date}"

                response = _nse_urlfetch(url, "https://www.nseindia.com/")

                if response.status_code == 503:
                    _vix_module_cache["last_attempt"] = now
                    if attempt < max_retries - 1:
                        wait_time = backoff_times[attempt] + random.uniform(0, 3)
                        log.warning(
                            f"India VIX fetch returned 503 (too many requests). Retrying in {wait_time:.1f}s... (attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        log.warning(
                            "India VIX fetch failed after max retries (503 error)"
                        )
                        return None

                if response.status_code != 200:
                    log.warning(
                        f"India VIX fetch returned status {response.status_code}"
                    )
                    return None

                data = response.json()
                df = pd.DataFrame(data.get("data", []))
                if not df.empty:
                    df["TIMESTAMP"] = pd.to_datetime(df["TIMESTAMP"])
                    df["CLOSE_INDEX_VAL"] = pd.to_numeric(
                        df["CLOSE_INDEX_VAL"], errors="coerce"
                    )
                    log.debug(
                        f"Successfully fetched India VIX data (attempt {attempt + 1})"
                    )
                    _vix_module_cache["data"] = df.copy()
                    _vix_module_cache["time"] = datetime.now()
                return df

            except Exception as e:
                _vix_module_cache["last_attempt"] = now
                if attempt < max_retries - 1:
                    wait_time = backoff_times[attempt] + random.uniform(0, 3)
                    log.warning(
                        f"Failed to fetch India VIX: {e}. Retrying in {wait_time:.1f}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                else:
                    log.warning(
                        f"Failed to fetch India VIX after {max_retries} attempts: {e}"
                    )
                    return None

        return None

    # ═══════════════════════════════════════════════════════════════════════
    # Deliverable Positions (Institutional vs Retail participation)
    # ═══════════════════════════════════════════════════════════════════════
    def get_deliverable_data(
        self, symbol: str, period: str = "1M"
    ) -> pd.DataFrame | None:
        """
        Get daily deliverable position data (institutional flow indicator).

        High deliverable % (>50%) suggests strong institutional participation.
        """
        try:
            from_date, to_date = _derive_from_and_to_date(None, None, period)
            symbol = _clean_nse_symbol(symbol)

            # Use NSE's delivery data API
            url = f"https://www.nseindia.com/api/stock-delivery?symbol={symbol}&from={from_date}&to={to_date}"
            response = _nse_urlfetch(url, "https://www.nseindia.com/")
            if response.status_code != 200:
                return None

            data = response.json()
            df = pd.DataFrame(data.get("data", []))
            if not df.empty:
                df["Date"] = pd.to_datetime(df["Date"], format=_dd_mm_yyyy)
                df["DeliverableQty"] = pd.to_numeric(
                    df["DeliverableQty"], errors="coerce"
                )
                df["%DlyQttoTradedQty"] = pd.to_numeric(
                    df["%DlyQttoTradedQty"], errors="coerce"
                )
            return df
        except Exception as e:
            log.warning(f"Failed to fetch deliverable data for {symbol}: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════════
    # Bulk/Block Deals (Insider/institutional activity)
    # ═══════════════════════════════════════════════════════════════════════
    def get_bulk_deals(
        self, from_date: str = None, to_date: str = None, period: str = "1W"
    ) -> list[dict]:
        """
        Get bulk deal data (large transactions > 0.5% of equity).
        Useful for tracking smart money.
        """
        try:
            from_date, to_date = _derive_from_and_to_date(from_date, to_date, period)
            url = (
                f"https://www.nseindia.com/api/bulk-deals?from={from_date}&to={to_date}"
            )
            response = _nse_urlfetch(url, "https://www.nseindia.com/")
            if response.status_code != 200:
                return []

            data = response.json()
            return data.get("data", [])
        except Exception as e:
            log.warning(f"Failed to fetch bulk deals: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # Short Selling Data
    # ═══════════════════════════════════════════════════════════════════════
    def get_short_selling_data(
        self, symbol: str = None, period: str = "1W"
    ) -> list[dict]:
        """
        Get short selling data (quantity traded, % of total volume).
        High short interest can indicate bearish sentiment.
        """
        try:
            from_date, to_date = _derive_from_and_to_date(None, None, period)
            url = f"https://www.nseindia.com/api/short-selling?from={from_date}&to={to_date}"
            if symbol:
                url += f"&symbol={_clean_nse_symbol(symbol)}"

            response = _nse_urlfetch(url, "https://www.nseindia.com/")
            if response.status_code != 200:
                return []

            data = response.json()
            return data.get("data", [])
        except Exception as e:
            log.warning(f"Failed to fetch short selling data: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # Index Constituents & Performance
    # ═══════════════════════════════════════════════════════════════════════
    def get_index_components(self, index_name: str) -> list[dict]:
        """
        Get detailed constituents of an index with weights and changes.

        Returns:
            List of dicts with: symbol, name, weight, change, industry
        """
        if not self.enable_index_data:
            return []

        try:
            index_code = index_name.upper().replace(" ", "%20")
            url = f"https://www.nseindia.com/api/equity-stockIndices?index={index_code}"
            response = _nse_urlfetch(
                url, "https://www.nseindia.com/market-data/live-equity-market"
            )
            if response.status_code != 200:
                return []

            data = response.json()
            components = []
            for item in data.get("data", []):
                components.append(
                    {
                        "symbol": item.get("symbol", ""),
                        "company_name": item.get("companyName", ""),
                        "weight": float(item.get("weight", 0) or 0),
                        "change": float(item.get("change", 0) or 0),
                        "percent_change": float(item.get("pChange", 0) or 0),
                        "industry": item.get("industry", ""),
                    }
                )
            return components
        except Exception as e:
            log.warning(f"Failed to fetch index constituents for {index_name}: {e}")
            return []

    def get_index_performance(
        self, index_name: str, period: str = "1M"
    ) -> pd.DataFrame | None:
        """
        Get historical performance of an index.
        """
        try:
            from_date, to_date = _derive_from_and_to_date(None, None, period)
            index_code = index_name.upper().replace(" ", "%20")
            url = f"https://www.nseindia.com/api/historical/indices?indexName={index_code}&from={from_date}&to={to_date}"
            response = _nse_urlfetch(url, "https://www.nseindia.com/")
            if response.status_code != 200:
                return None

            data = response.json()
            df = pd.DataFrame(data.get("data", []))
            if not df.empty:
                df["TIMESTAMP"] = pd.to_datetime(df["TIMESTAMP"])
                numeric_cols = [
                    "OPEN_INDEX_VAL",
                    "HIGH_INDEX_VAL",
                    "LOW_INDEX_VAL",
                    "CLOSE_INDEX_VAL",
                ]
                for col in numeric_cols:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        except Exception as e:
            log.warning(f"Failed to fetch index performance for {index_name}: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════════
    # Trading Holiday Calendar
    # ═══════════════════════════════════════════════════════════════════════
    def get_trading_holidays(self, year: int = None) -> list[dict]:
        """
        Get NSE trading holidays for the specified year.
        """
        if self._holiday_cache and self._holiday_cache_date == (
            year or date.today().year
        ):
            return self._holiday_cache

        try:
            year = year or date.today().year
            url = "https://www.nseindia.com/api/holiday-master?type=trading"
            response = _nse_urlfetch(url, "https://www.nseindia.com/")
            if response.status_code != 200:
                return []

            data = response.json()
            holidays = []
            for segment, dates in data.items():
                for h in dates:
                    holidays.append(
                        {
                            "date": h.get("tradingDate"),
                            "day": h.get("weekDay"),
                            "description": h.get("description", ""),
                            "segment": segment,
                        }
                    )

            self._holiday_cache = holidays
            self._holiday_cache_date = year
            return holidays
        except Exception as e:
            log.warning(f"Failed to fetch trading holidays: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # Option Chain Data (F&O)
    # ═══════════════════════════════════════════════════════════════════════
    def get_option_chain(self, symbol: str, expiry: str = None) -> dict | None:
        """
        Get option chain data for indices/stocks with F&O.

        Args:
            symbol: Index symbol (NIFTY, BANKNIFTY) or stock symbol
            expiry: Specific expiry date (DD-MMM-YYYY format), None for all

        Returns:
            Option chain data with strikes, prices, and expiry dates
        """
        try:
            symbol = symbol.upper()
            if symbol in ["NIFTY", "NIFTY50"]:
                lookup_symbol = "NIFTY"
            elif symbol in {"NIFTY BANK", "BANKNIFTY"}:
                lookup_symbol = "BANKNIFTY"
            else:
                lookup_symbol = symbol

            # Use data_provider first
            if self.data_provider is not None:
                try:
                    result = self.data_provider.get_option_chain(symbol, expiry)
                    if result and isinstance(result, dict) and result.get("data"):
                        log.debug(
                            f"NSE Enrichment: Fetched option chain for {symbol} via data provider"
                        )
                        return result
                except Exception as e:
                    log.debug(f"NSE Enrichment: Data provider failed for {symbol}: {e}")

            return None

        except Exception as e:
            log.warning(f"Failed to fetch option chain for {symbol}: {e}")
            return None

    def _fetch_option_chain_nse_direct(
        self, symbol: str, expiry: str = None
    ) -> dict | None:
        """
        Fetch option chain directly from NSE API as final fallback.
        """
        try:
            # Use NSE's option chain API
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol.upper()}"
            if expiry:
                url += f"&expiry={expiry}"

            response = _nse_urlfetch(url, "https://www.nseindia.com/option-chain")

            if response.status_code != 200:
                log.warning(
                    f"Direct NSE API returned status {response.status_code} for {symbol}"
                )
                return None

            data = response.json()

            # Validate response structure
            if not isinstance(data, dict) or "records" not in data:
                log.debug(f"Invalid NSE API response structure for {symbol}")
                return None

            records = data.get("records", {})
            if not records.get("data"):
                log.debug(f"No option chain data in NSE API response for {symbol}")
                return None

            result = {
                "data": data,
                "symbol": symbol,
                "is_index": symbol.upper()
                in ["NIFTY", "BANKNIFTY", "NIFTY50", "FINNIFTY"],
                "source": "nse_direct",
            }

            log.debug(
                f"NSE Enrichment: Fetched option chain for {symbol} via direct NSE API"
            )
            return result

        except Exception as e:
            log.warning(f"Direct NSE API failed for {symbol}: {e}")
            return None


# ─── Singleton instance ────────────────────────────────────────────────────
nse_enrichment = NSEEnrichmentProvider()
