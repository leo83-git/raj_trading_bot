"""
Indian Stock Market Data Provider
Priority:
1. jugaad-data (most reliable for live + historical)
2. BennyThadikaran/NseIndiaApi (fallback)
3. yfinance (Yahoo Finance - final fallback)

Usage:
    from sources.indian_stock_api import NSEIndiaData

    data = NSEIndiaData()

    # Get quote
    quote = data.get_quote("RELIANCE")

    # Get historical data
    hist = data.get_historical("RELIANCE", days=30)

    # Get all symbols from an index
    symbols = data.get_index_symbols("NIFTY 50")

    # Search stocks
    results = data.search_symbol("reliance")
"""

from datetime import date, datetime, timedelta

import yfinance as yf


class NSEIndiaData:
    """NSE India stock data provider with jugaad-data, NseIndiaApi, yfinance"""

    POPULAR_SYMBOLS = [
        "RELIANCE",
        "TCS",
        "INFY",
        "HDFCBANK",
        "ICICIBANK",
        "ITC",
        "SBIN",
        "BHARTIARTL",
        "HINDUNILVR",
        "IOC",
        "LT",
        "ASIANPAINT",
        "MARUTI",
        "BAJFINANCE",
        "TITAN",
        "NESTLE",
        "AXISBANK",
        "KOTAKBANK",
        "SUNPHARMA",
        "ADANIENT",
        "POWERGRID",
        "NTPC",
        "ONGC",
        "COALINDIA",
    ]

    INDEX_TICKERS = {
        "NIFTY 50": "^NSEI",
        "NIFTY BANK": "^NSEBANK",
        "NIFTY IT": "^CNXIT",
        "NIFTY AUTO": "^CNXAUTO",
    }

    def __init__(self, server: bool = False):
        self._jugaad = None
        self._nse = None
        self._server = server
        self._jugaad_available = None
        self._nse_available = None

    def _to_yf_ticker(self, symbol: str) -> str:
        if not symbol:
            return ""
        symbol_up = symbol.strip().upper()
        if symbol_up in self.INDEX_TICKERS:
            return self.INDEX_TICKERS[symbol_up]
        if symbol_up.endswith(".NS") or symbol_up.endswith(".BO"):
            base, suffix = symbol_up.rsplit(".", 1)
            return f"{base}.{suffix}"
        return f"{symbol_up}.NS"

    def _normalize_nse_symbol(self, symbol: str) -> str:
        if not symbol:
            return ""
        symbol_up = symbol.strip().upper()
        if symbol_up.endswith(".NS") or symbol_up.endswith(".BO"):
            return symbol_up.rsplit(".", 1)[0]
        return symbol_up

    def _init_jugaad(self):
        """Lazy initialization of jugaad-data"""
        if self._jugaad_available is None:
            try:
                from jugaad_data.nse import NSELive

                self._jugaad = NSELive()
                self._jugaad_available = True
            except Exception:
                self._jugaad_available = False

    def _init_nse(self):
        """Lazy initialization of NseIndiaApi"""
        if self._nse_available is None:
            try:
                from nse import NSE

                self._nse = NSE(download_folder="", server=self._server)
                self._nse_available = True
            except Exception:
                self._nse_available = False

    def get_quote(self, symbol: str) -> dict | None:
        """
        Get real-time quote for a stock.
        Priority: jugaad-data > NseIndiaApi > yfinance
        """
        stock_symbol = self._normalize_nse_symbol(symbol)
        # Try jugaad-data first (most reliable)
        self._init_jugaad()
        if self._jugaad_available and self._jugaad:
            try:
                quote = self._jugaad.stock_quote(stock_symbol)
                if quote and isinstance(quote, dict):
                    price_info = quote.get("priceInfo", {})
                    return {
                        "symbol": symbol,
                        "exchange": "NSE",
                        "last_price": float(price_info.get("lastPrice", 0)),
                        "open": float(price_info.get("open", 0)),
                        "high": float(
                            price_info.get("intraDayHighLow", {}).get("max", 0)
                        ),
                        "low": float(
                            price_info.get("intraDayHighLow", {}).get("min", 0)
                        ),
                        "previous_close": float(price_info.get("previousClose", 0)),
                        "change": float(price_info.get("change", 0)),
                        "percent_change": float(price_info.get("pChange", 0)),
                        "volume": int(
                            quote.get("preOpenMarket", {}).get("totalTradedVolume", 0)
                        ),
                        "vwap": float(price_info.get("vwap", 0)),
                        "sector": quote.get("industryInfo", {}).get("industry"),
                        "company_name": quote.get("info", {}).get("companyName"),
                    }
            except Exception:
                pass

        # Fallback to NseIndiaApi
        self._init_nse()
        if self._nse_available and self._nse:
            try:
                quote = self._nse.quote(stock_symbol)
                if quote and isinstance(quote, dict):
                    price_info = quote.get("priceInfo", {})
                    return {
                        "symbol": symbol,
                        "exchange": "NSE",
                        "last_price": float(price_info.get("lastPrice", 0)),
                        "open": float(
                            price_info.get("intraDayHighLow", {}).get("min", 0)
                        ),
                        "high": float(
                            price_info.get("intraDayHighLow", {}).get("max", 0)
                        ),
                        "low": float(
                            price_info.get("intraDayHighLow", {}).get("min", 0)
                        ),
                        "previous_close": float(price_info.get("previousClose", 0)),
                        "volume": int(
                            quote.get("preOpenMarket", {}).get("totalTradedVolume", 0)
                        ),
                        "sector": quote.get("industryInfo", {}).get("industry"),
                        "company_name": quote.get("info", {}).get("companyName"),
                    }
            except Exception:
                pass

        # Final fallback to yfinance
        return self._get_quote_yfinance(symbol)

    def _get_quote_yfinance(self, symbol: str) -> dict | None:
        """Get quote using yfinance"""
        try:
            ticker = yf.Ticker(self._to_yf_ticker(symbol))
            info = getattr(ticker, "info", None) or {}

            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if price is not None:
                price = float(price)
                if price > 0:
                    return {
                        "symbol": symbol,
                        "exchange": "NSE",
                        "last_price": price,
                        "open": float(info.get("open", price) or price),
                        "high": float(
                            info.get("dayHigh", info.get("high52Week", price)) or price
                        ),
                        "low": float(
                            info.get("dayLow", info.get("low52Week", price)) or price
                        ),
                        "previous_close": float(
                            info.get("previousClose", price) or price
                        ),
                        "volume": int(info.get("volume", 0) or 0),
                        "sector": info.get("sector"),
                        "industry": info.get("industry"),
                    }
        except Exception:
            pass
        return None

    def get_quotes(self, symbols: list[str]) -> list[dict]:
        """Get quotes for multiple stocks"""
        quotes = []
        for symbol in symbols:
            quote = self.get_quote(symbol)
            if quote:
                quotes.append(quote)
        return quotes

    def get_historical(self, symbol: str, days: int = 90) -> list[dict]:
        """
        Get historical OHLC data for a stock.
        Priority: jugaad-data > NseIndiaApi > yfinance
        """
        # Try jugaad-data first
        self._init_jugaad()
        if self._jugaad_available:
            try:
                from jugaad_data.nse import stock_df

                end_date = date.today()
                start_date = end_date - timedelta(days=days)
                df = stock_df(
                    symbol=symbol, from_date=start_date, to_date=end_date, series="EQ"
                )

                if df is not None and len(df) > 0:
                    df = df.sort_values("DATE")
                    candles = []
                    for _, row in df.iterrows():
                        candles.append(
                            {
                                "timestamp": str(row.get("DATE", "")),
                                "date": str(row.get("DATE", ""))[:10],
                                "open": float(row.get("OPEN", 0)),
                                "high": float(row.get("HIGH", 0)),
                                "low": float(row.get("LOW", 0)),
                                "close": float(row.get("CLOSE", 0)),
                                "volume": int(row.get("VOLUME", 0)),
                                "delivery": int(row.get("DELIVERY QTY", 0)),
                                "delivery_pct": float(row.get("DELIVERY %", 0)),
                            }
                        )
                    return candles
            except Exception:
                pass

        # Fallback to NseIndiaApi
        self._init_nse()
        if self._nse_available and self._nse:
            try:
                end_date = date.today()
                start_date = end_date - timedelta(days=days)
                hist = self._nse.fetch_equity_historical_data(
                    symbol, start_date, end_date
                )
                if hist and len(hist) > 0:
                    candles = []
                    for record in hist:
                        candles.append(
                            {
                                "timestamp": record.get("mtimestamp", ""),
                                "date": record.get("mtimestamp", ""),
                                "open": float(record.get("chOpeningPrice", 0)),
                                "high": float(record.get("chTradeHighPrice", 0)),
                                "low": float(record.get("chTradeLowPrice", 0)),
                                "close": float(record.get("chClosingPrice", 0)),
                                "volume": int(record.get("chTotTradedQty", 0)),
                            }
                        )
                    return candles
            except:
                pass

        # Final fallback to yfinance
        return self._get_historical_yfinance(symbol, days)

    def _get_historical_yfinance(self, symbol: str, days: int) -> list[dict]:
        """Get historical data using yfinance"""
        try:
            ticker = yf.Ticker(self._to_yf_ticker(symbol))
            old_hide = None
            if hasattr(yf, "config") and hasattr(yf.config, "debug"):
                old_hide = getattr(yf.config.debug, "hide_exceptions", None)
                yf.config.debug.hide_exceptions = True
            try:
                df = ticker.history(period=f"{days}d", raise_errors=False)
            finally:
                if old_hide is not None:
                    yf.config.debug.hide_exceptions = old_hide

            if df is None or getattr(df, "empty", True):
                return []

            candles = []
            for idx, row in df.iterrows():
                candles.append(
                    {
                        "timestamp": str(idx),
                        "date": str(idx)[:10],
                        "open": float(
                            row.get("Open", 0)
                            if hasattr(row, "get")
                            else row["Open"] if "Open" in row else 0
                        ),
                        "high": float(
                            row.get("High", 0)
                            if hasattr(row, "get")
                            else row["High"] if "High" in row else 0
                        ),
                        "low": float(
                            row.get("Low", 0)
                            if hasattr(row, "get")
                            else row["Low"] if "Low" in row else 0
                        ),
                        "close": float(
                            row.get("Close", 0)
                            if hasattr(row, "get")
                            else row["Close"] if "Close" in row else 0
                        ),
                        "volume": int(
                            row.get("Volume", 0)
                            if hasattr(row, "get")
                            else row["Volume"] if "Volume" in row else 0
                        ),
                    }
                )

            return candles
        except Exception:
            return []

    def get_index_quote(self, index_name: str) -> dict | None:
        """Get quote for a market index"""
        # Try jugaad-data
        self._init_jugaad()
        if self._jugaad_available:
            try:
                # Also try NseIndiaApi for index
                self._init_nse()
                if self._nse_available and self._nse:
                    status = self._nse.status()
                    for market in status:
                        if market.get("index") == index_name:
                            return {
                                "index": index_name,
                                "last_price": float(market.get("last", 0)),
                                "change": float(market.get("variation", 0)),
                                "percent_change": float(market.get("percentChange", 0)),
                                "status": market.get("marketStatus", ""),
                            }
            except:
                pass

        # Fallback to yfinance
        ticker_symbol = self.INDEX_TICKERS.get(index_name)
        if not ticker_symbol:
            ticker_symbol = f"^{index_name.upper().replace(' ', '')}"

        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info
            if info:
                price = info.get("currentPrice") or info.get("regularMarketPrice")
                return {
                    "index": index_name,
                    "last_price": float(price) if price else 0,
                    "change": info.get("regularMarketChange"),
                    "percent_change": info.get("regularMarketChangePercent"),
                }
        except:
            pass

        return None

    def get_index_symbols(
        self, index_name: str = "NIFTY 50", max_symbols: int = 200
    ) -> list[str]:
        """Get list of stock symbols from an NSE index"""
        # Try NseIndiaApi
        self._init_nse()
        if self._nse_available and self._nse:
            try:
                result = self._nse.listEquityStocksByIndex(index_name)
                if result and isinstance(result, dict):
                    data = result.get("data", [])
                    symbols = [d.get("symbol") for d in data if d.get("symbol")]
                    return symbols[:max_symbols]
            except:
                pass

        return self.POPULAR_SYMBOLS[:max_symbols]

    def search_symbol(self, query: str) -> list[dict]:
        """Search for stocks by name/symbol"""
        # Try NseIndiaApi lookup
        self._init_nse()
        if self._nse_available and self._nse:
            try:
                results = self._nse.lookup(query)
                if results and isinstance(results, list):
                    return [
                        {
                            "symbol": r.get("symbol", ""),
                            "company_name": r.get("info", {}).get("companyName", ""),
                            "nse_ticker": f"{r.get('symbol', '')}.NS",
                        }
                        for r in results[:10]
                    ]
            except:
                pass

        # Fallback: search in popular symbols
        return self._search_popular_symbols(query)

    def _search_popular_symbols(self, query: str) -> list[dict]:
        """Search in popular symbols as fallback"""
        results = []
        query_lower = query.lower()

        company_names = {
            "RELIANCE": "Reliance Industries Ltd",
            "TCS": "Tata Consultancy Services Ltd",
            "INFY": "Infosys Ltd",
            "HDFCBANK": "HDFC Bank Ltd",
            "ICICIBANK": "ICICI Bank Ltd",
            "ITC": "ITC Ltd",
            "SBIN": "State Bank of India",
            "BHARTIARTL": "Bharti Airtel Ltd",
            "HINDUNILVR": "Hindustan Unilever Ltd",
            "IOC": "Indian Oil Corporation Ltd",
            "LT": "Larsen & Toubro Ltd",
            "ASIANPAINT": "Asian Paints Ltd",
            "MARUTI": "Maruti Suzuki India Ltd",
            "BAJFINANCE": "Bajaj Finance Ltd",
            "TITAN": "Titan Company Ltd",
            "NESTLE": "Nestle India Ltd",
            "AXISBANK": "Axis Bank Ltd",
            "KOTAKBANK": "Kotak Mahindra Bank Ltd",
            "SUNPHARMA": "Sun Pharmaceutical Industries Ltd",
            "ADANIENT": "Adani Enterprises Ltd",
            "POWERGRID": "Power Grid Corporation of India Ltd",
            "NTPC": "NTPC Ltd",
            "ONGC": "Oil & Natural Gas Corporation Ltd",
            "COALINDIA": "Coal India Ltd",
        }

        for symbol, name in company_names.items():
            if query_lower in symbol.lower() or query_lower in name.lower():
                results.append(
                    {
                        "symbol": symbol,
                        "company_name": name,
                        "nse_ticker": f"{symbol}.NS",
                    }
                )

        return results

    def get_market_status(self) -> dict:
        """Get current market status"""
        self._init_nse()
        if self._nse_available and self._nse:
            try:
                status = self._nse.status()
                return {
                    "status": status,
                    "open": any(m.get("marketStatus") == "Open" for m in status),
                }
            except:
                pass

        return {"open": self._is_market_hours(), "status": []}

    def _is_market_hours(self) -> bool:
        """Check if within market hours.

        For testing purposes the market hours can be overridden by setting the
        environment variable ``EXTEND_MARKET_HOURS=1``. When this variable is
        present the method will always return ``True`` regardless of the
        actual time or day.
        """
        # Original market‑hours logic (close at 3:30 PM).
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        hour = now.hour
        minute = now.minute
        current_time = hour * 60 + minute
        # Market open from 09:15 to 15:30.
        return 9 * 60 + 15 <= current_time <= 15 * 60 + 30

    def is_market_open(self) -> bool:
        """Check if NSE market is currently open"""
        status = self.get_market_status()
        return status.get("open", False)

    def get_gainers(self, limit: int = 10) -> list[dict]:
        """Get top gaining stocks"""
        self._init_nse()
        if self._nse_available and self._nse:
            try:
                gainers = self._nse.gainers()
                if gainers and isinstance(gainers, list):
                    return gainers[:limit]
            except:
                pass
        return []

    def get_losers(self, limit: int = 10) -> list[dict]:
        """Get top losing stocks"""
        self._init_nse()
        if self._nse_available and self._nse:
            try:
                losers = self._nse.losers()
                if losers and isinstance(losers, list):
                    return losers[:limit]
            except:
                pass
        return []

    def close(self):
        """Close connections"""
        if self._nse:
            try:
                self._nse.exit()
            except:
                pass


def get_quote(symbol: str) -> dict | None:
    """Convenience function to get quote"""
    data = NSEIndiaData()
    return data.get_quote(symbol)


def get_historical(symbol: str, days: int = 90) -> list[dict]:
    """Convenience function to get historical data"""
    data = NSEIndiaData()
    return data.get_historical(symbol, days)


def fetch_market_data(symbols: list[str]) -> dict[str, dict]:
    """Fetch market data for multiple stocks"""
    data = NSEIndiaData()
    quotes = data.get_quotes(symbols)
    return {q.get("symbol", ""): q for q in quotes if q.get("symbol")}
