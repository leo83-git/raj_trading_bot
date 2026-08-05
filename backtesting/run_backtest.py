# ═══════════════════════════════════════════════════════════════
#  Backtest Runner Script
#  Usage: python run_backtest.py --symbol NIFTY --days 365 --capital 300000
# ═══════════════════════════════════════════════════════════════
import argparse
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting import (
    BacktestConfig,
    RealBacktestEngine,
    WalkForwardAnalyzer,
    create_backtest_config,
)
from quant_utils.logger import get_logger

log = get_logger("backtest_runner")


def fetch_historical_data(
    symbol: str, days: int = 365, interval: str = "5m"
) -> pd.DataFrame:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    log.info(f"Fetching {symbol} data from {start_date.date()} to {end_date.date()}")

    df = yf.download(
        symbol, start=start_date, end=end_date, interval=interval, progress=False
    )

    if df.empty:
        log.error(f"No data fetched for {symbol}")
        return pd.DataFrame()

    df.columns = [c.lower() for c in df.columns]

    if "adj close" in df.columns:
        df = df.rename(columns={"adj close": "close"})

    log.info(f"Fetched {len(df)} candles")
    return df


def simple_ma_crossover_strategy(
    data: pd.DataFrame, idx: int, train_data: pd.DataFrame = None
) -> dict:
    if idx < 20:
        return None

    window_fast = 10
    window_slow = 20

    close_series = data["close"].iloc[: idx + 1]

    if len(close_series) < window_slow:
        return None

    fast_ma = close_series.rolling(window_fast).mean().iloc[-1]
    slow_ma = close_series.rolling(window_slow).mean().iloc[-1]

    prev_fast = close_series.rolling(window_fast).mean().iloc[-2]
    prev_slow = close_series.rolling(window_slow).mean().iloc[-2]

    current_price = close_series.iloc[-1]

    if prev_fast <= prev_slow and fast_ma > slow_ma:
        return {
            "action": "BUY",
            "symbol": "BACKTEST",
            "quantity": 1,
            "target": current_price * 1.05,
            "stop_loss": current_price * 0.97,
        }
    elif prev_fast >= prev_slow and fast_ma < slow_ma:
        return {
            "action": "SELL",
            "symbol": "BACKTEST",
            "quantity": 1,
            "target": current_price * 0.95,
            "stop_loss": current_price * 1.03,
        }

    return None


def run_basic_backtest(symbol: str, days: int, capital: float, config: BacktestConfig):
    data = fetch_historical_data(symbol, days, "5m")

    if data.empty:
        log.error("No data available for backtest")
        return

    engine = RealBacktestEngine(config)

    log.info(f"Running backtest: {symbol} | Capital: INR{capital:,.0f}")
    log.info(
        f"Config: Commission=INR{config.commission}, Slippage={config.slippage_pips} pips"
    )

    results = engine.run_bar_by_bar(data, simple_ma_crossover_strategy)

    log.info("=" * 60)
    log.info("BACKTEST RESULTS")
    log.info("=" * 60)
    log.info(f"Symbol: {symbol}")
    log.info(f"Period: {days} days")
    log.info(f"Total Trades: {results.total_trades}")
    log.info(f"Win Rate: {results.win_rate:.2%}")
    log.info(f"Total PnL: INR{results.total_pnl:,.2f} ({results.total_pnl_pct:.2f}%)")
    log.info(f"Max Drawdown: {results.max_drawdown:.2%}")
    log.info(f"Sharpe Ratio: {results.sharpe_ratio:.2f}")
    log.info(f"Profit Factor: {results.profit_factor:.2f}")
    log.info(f"Avg Win: INR{results.avg_win:,.2f}")
    log.info(f"Avg Loss: INR{results.avg_loss:,.2f}")

    if results.equity_curve is not None and len(results.equity_curve) > 0:
        output_file = (
            f"backtest_results_{symbol}_{datetime.now().strftime('%Y%m%d')}.csv"
        )
        results.equity_curve.to_csv(output_file, index=False)
        log.info(f"Equity curve saved to {output_file}")

    return results


def run_walk_forward(
    symbol: str, days: int, capital: float, config: BacktestConfig, n_splits: int = 5
):
    data = fetch_historical_data(symbol, days, "15m")

    if data.empty:
        log.error("No data available for walk-forward")
        return

    log.info(f"Running walk-forward analysis: {symbol} | {n_splits} splits")

    analyzer = WalkForwardAnalyzer(config, train_ratio=0.7)
    results = analyzer.run(data, simple_ma_crossover_strategy, n_splits=n_splits)

    summary = analyzer.get_summary()

    log.info("=" * 60)
    log.info("WALK-FORWARD RESULTS")
    log.info("=" * 60)
    log.info(f"Splits: {summary.get('total_splits', 0)}")
    log.info(f"Total PnL: INR{summary.get('total_pnl', 0):,.2f}")
    log.info(f"Avg Win Rate: {summary.get('avg_win_rate', 0):.2%}")
    log.info(f"Avg Drawdown: {summary.get('avg_drawdown', 0):.2%}")

    for r in results:
        log.info(
            f"  Split {r['split']}: {r['test_period']} | PnL: INR{r['result'].total_pnl:,.2f}"
        )

    return summary


def main():
    parser = argparse.ArgumentParser(description="Run backtests on historical data")

    parser.add_argument(
        "--symbol", type=str, default="^NSEI", help="Symbol to backtest"
    )
    parser.add_argument("--days", type=int, default=90, help="Number of days of data")
    parser.add_argument("--capital", type=float, default=300000, help="Initial capital")
    parser.add_argument(
        "--commission", type=float, default=30, help="Commission per trade (INR)"
    )
    parser.add_argument("--slippage", type=float, default=1.0, help="Slippage in pips")
    parser.add_argument(
        "--walk-forward", action="store_true", help="Run walk-forward analysis"
    )
    parser.add_argument(
        "--splits", type=int, default=5, help="Number of walk-forward splits"
    )

    args = parser.parse_args()

    config = create_backtest_config(
        {
            "initial_capital": args.capital,
            "commission": args.commission,
            "slippage_pips": args.slippage,
            "slippage_mode": "variable",
            "fill_mode": "ohlc",
            "realistic_fills": True,
        }
    )

    if args.walk_forward:
        run_walk_forward(args.symbol, args.days, args.capital, config, args.splits)
    else:
        run_basic_backtest(args.symbol, args.days, args.capital, config)


if __name__ == "__main__":
    main()
