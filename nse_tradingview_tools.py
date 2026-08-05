"""
NSE TradingView Tools

Provides TradingView data access for NSE (National Stock Exchange of India)
using the tradingview-scraper library. These tools complement existing
TradingView integrations without breaking them.
"""

import logging
from typing import Any

from tradingview_scraper.symbols.stream import Streamer
from tradingview_scraper.symbols.technicals import Indicators

logger = logging.getLogger(__name__)


async def get_nse_indicators(
    symbol: str,
    timeframe: str = "1d",
    all_indicators: bool = True,
    export_result: bool = False,
) -> dict[str, Any]:
    """
    Retrieve technical indicators for an NSE symbol from TradingView.

    Args:
        symbol: NSE symbol (e.g., "RELIANCE", "TCS")
        timeframe: Timeframe for indicators (e.g., "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M")
        all_indicators: Whether to fetch all indicators
        export_result: Whether to export results to file

    Returns:
        Dictionary containing the technical indicators data
    """
    try:
        logger.info(f"Fetching NSE indicators for {symbol} ({timeframe})")

        # Create indicators scraper instance
        indicators_scraper = Indicators(
            export_result=export_result, export_type="json" if export_result else None
        )

        # Scrape indicators
        indicators = indicators_scraper.scrape(
            symbol=symbol,
            exchange="NSE",
            timeframe=timeframe,
            allIndicators=all_indicators,
        )

        logger.info(f"Successfully fetched NSE indicators for {symbol}")
        return {
            "success": True,
            "symbol": symbol,
            "exchange": "NSE",
            "timeframe": timeframe,
            "indicators": indicators,
        }

    except Exception as e:
        logger.error(f"Error fetching NSE indicators for {symbol}: {e!s}")
        return {
            "success": False,
            "symbol": symbol,
            "exchange": "NSE",
            "timeframe": timeframe,
            "error": str(e),
        }


async def get_nse_specific_indicators(
    symbol: str,
    indicators: list[str],
    timeframe: str = "1d",
    export_result: bool = False,
) -> dict[str, Any]:
    """
    Retrieve specific technical indicators for an NSE symbol.

    Args:
        symbol: NSE symbol (e.g., "RELIANCE")
        indicators: List of specific indicators to fetch
        timeframe: Timeframe for indicators
        export_result: Whether to export results to file

    Returns:
        Dictionary containing the requested indicators
    """
    try:
        logger.info(f"Fetching specific NSE indicators {indicators} for {symbol}")

        # Create indicators scraper instance
        indicators_scraper = Indicators(
            export_result=export_result, export_type="json" if export_result else None
        )

        # Scrape all indicators first
        all_indicators = indicators_scraper.scrape(
            symbol=symbol, exchange="NSE", timeframe=timeframe, allIndicators=True
        )

        # Filter requested indicators
        filtered_indicators = {}
        for key, value in all_indicators.items():
            for requested in indicators:
                if requested.lower() in key.lower():
                    filtered_indicators[key] = value

        logger.info(
            f"Successfully fetched {len(filtered_indicators)} NSE indicators for {symbol}"
        )
        return {
            "success": True,
            "symbol": symbol,
            "exchange": "NSE",
            "timeframe": timeframe,
            "requested_indicators": indicators,
            "indicators": filtered_indicators,
        }

    except Exception as e:
        logger.error(f"Error fetching NSE indicators for {symbol}: {e!s}")
        return {
            "success": False,
            "symbol": symbol,
            "exchange": "NSE",
            "timeframe": timeframe,
            "error": str(e),
        }


async def get_nse_historical_data(
    symbol: str,
    timeframe: str = "1d",
    max_records: int = 100,
    export_result: bool = False,
) -> dict[str, Any]:
    """
    Retrieve OHLCV data for an NSE symbol.

    Args:
        symbol: NSE symbol (e.g., "RELIANCE")
        timeframe: Timeframe for candles
        max_records: Maximum number of records to collect
        export_result: Whether to export to file

    Returns:
        Dictionary containing OHLCV data
    """
    try:
        logger.info(f"Starting NSE OHLCV data collection for {symbol} ({timeframe})")

        # Use export=True to get processed data
        streamer = Streamer(export_result=True, export_type="json")

        # Stream data
        result = streamer.stream(
            exchange="NSE",
            symbol=symbol,
            timeframe=timeframe,
            numb_price_candles=max_records,
        )

        # Extract OHLC data
        ohlcv_data = result.get("ohlc", [])

        logger.info(
            f"Successfully collected {len(ohlcv_data)} NSE {timeframe} candles for {symbol}"
        )

        return {
            "success": True,
            "symbol": symbol,
            "exchange": "NSE",
            "timeframe": timeframe,
            "records_collected": len(ohlcv_data),
            "data": ohlcv_data,
            "export_file": (
                f"export/ohlc_{symbol.lower()}_{timeframe}.json"
                if export_result
                else None
            ),
        }

    except Exception as e:
        logger.error(f"Error collecting NSE OHLCV data for {symbol}: {e!s}")
        return {
            "success": False,
            "symbol": symbol,
            "exchange": "NSE",
            "timeframe": timeframe,
            "error": str(e),
        }


# Example usage
if __name__ == "__main__":
    import asyncio

    async def test():
        # Test NSE indicators
        result = await get_nse_indicators(
            "RELIANCE", timeframe="1d", all_indicators=False
        )
        print("NSE Indicators:", result)

        # Test specific indicators
        result = await get_nse_specific_indicators(
            "RELIANCE", ["RSI", "MACD.macd"], timeframe="1d"
        )
        print("Specific NSE Indicators:", result)

        # Test historical data
        result = await get_nse_historical_data(
            "RELIANCE", timeframe="1d", max_records=10
        )
        print("NSE Historical Data:", result)

    asyncio.run(test())
