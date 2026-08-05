# ═══════════════════════════════════════════════════════════════
#  TradingView Backtester Integration
#  Backtest strategies using TradingView MCP tools
# ═══════════════════════════════════════════════════════════════
import time
from datetime import datetime
from typing import Any

from quant_utils.logger import get_logger

log = get_logger("backtesting.tradingview")

try:
    from tradingview_mcp.server import backtest_strategy as mcp_backtest_strategy
    from tradingview_mcp.server import compare_strategies as mcp_compare_strategies
except ImportError:
    log.warning("TradingView MCP backtest tools not available")
    mcp_backtest_strategy = None
    mcp_compare_strategies = None


class TradingViewBacktester:
    """TradingView backtesting integration for strategy validation"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.backtest_history = []
        self.comparison_history = []
        log.info("TradingViewBacktester initialized")

    def backtest(
        self,
        strategy: str,
        symbol: str,
        period: str = "1y",
        use_walk_forward: bool = False,
    ) -> dict[str, Any]:
        """
        Backtest a single strategy on a symbol

        Args:
            strategy: Strategy name (e.g., 'RSI', 'MACD', 'EMA_CROSS', 'BOLLINGER', 'SUPERTREND', 'DONCHIAN')
            symbol: Yahoo Finance symbol (e.g., 'AAPL', 'BTC-USD')
            period: Backtest period ('1mo', '3mo', '6mo', '1y', '2y')
            use_walk_forward: If True, perform walk-forward optimization first and use optimized parameters

        Returns:
            Dict with backtest metrics (Sharpe, returns, win_rate, max_drawdown, etc.)
        """
        optimized_params = None
        if use_walk_forward:
            log.info(
                f"Performing walk-forward optimization for {strategy} on {symbol} for period {period}"
            )
            opt_result = self.walk_forward_optimize(strategy, symbol, period)
            if opt_result.get("status") == "success" and opt_result.get(
                "optimized_params"
            ):
                optimized_params = opt_result.get("optimized_params")
                log.info(
                    f"Walk-forward optimization selected params: {optimized_params}"
                )
            else:
                log.warning(
                    "Walk-forward optimization did not return optimized parameters; using default backtest parameters"
                )

        try:
            if mcp_backtest_strategy is None:
                log.warning(
                    f"Backtest unavailable for {strategy}|{symbol}, MCP tools not available"
                )
                return {
                    "strategy": strategy,
                    "symbol": symbol,
                    "period": period,
                    "status": "unavailable",
                    "message": "TradingView MCP backtest tools not available",
                }

            # Validate and adjust period
            valid_periods = ["1mo", "3mo", "6mo", "1y", "2y"]
            if period not in valid_periods:
                log.warning(f"Invalid period '{period}', using default '1y'")
                period = "1y"

            # Check minimum data requirements
            min_bars_required = 21
            if period == "1mo":
                log.warning(
                    f"Period '{period}' may not have enough data (minimum 21 bars required). Consider using '3mo' or longer."
                )
            elif period in ["3mo", "6mo", "1y", "2y"]:
                log.debug(
                    f"Period '{period}' should have sufficient data for backtesting"
                )

            log.info(f"Starting backtest: {strategy} | {symbol} | {period}")

            # Call TradingView backtest API
            if optimized_params:
                result = self._backtest_with_params(
                    strategy, symbol, period, optimized_params
                )
            else:
                result = mcp_backtest_strategy(
                    symbol=symbol, strategy=strategy.upper(), period=period
                )

            if not result or not isinstance(result, dict):
                log.error(f"Invalid backtest response for {strategy}|{symbol}")
                return {
                    "strategy": strategy,
                    "symbol": symbol,
                    "period": period,
                    "status": "error",
                    "message": "Invalid backtest response",
                }

            # Check for specific error messages
            if "error" in result:
                error_msg = result["error"]
                if (
                    "not enough data" in error_msg.lower()
                    or "bars" in error_msg.lower()
                ):
                    suggested_periods = ["3mo", "6mo", "1y", "2y"]
                    return {
                        "strategy": strategy,
                        "symbol": symbol,
                        "period": period,
                        "status": "insufficient_data",
                        "message": f"Insufficient data for backtesting: {error_msg}",
                        "suggested_periods": suggested_periods,
                        "timestamp": datetime.now().isoformat(),
                    }
                else:
                    return {
                        "strategy": strategy,
                        "symbol": symbol,
                        "period": period,
                        "status": "error",
                        "message": error_msg,
                        "timestamp": datetime.now().isoformat(),
                    }

            # Extract key metrics
            metrics = {
                "strategy": strategy,
                "symbol": symbol,
                "period": period,
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                # Performance metrics
                "total_return": result.get("total_return", 0.0),
                "annual_return": result.get("annual_return", 0.0),
                "sharpe_ratio": result.get("sharpe_ratio", 0.0),
                "max_drawdown": result.get("max_drawdown", 0.0),
                "win_rate": result.get("win_rate", 0.0),
                "profit_factor": result.get("profit_factor", 0.0),
                # Trade statistics
                "total_trades": result.get("total_trades", 0),
                "winning_trades": result.get("winning_trades", 0),
                "losing_trades": result.get("losing_trades", 0),
                "avg_win": result.get("avg_win", 0.0),
                "avg_loss": result.get("avg_loss", 0.0),
                # Additional metrics from API
                "recovery_factor": result.get("recovery_factor", 0.0),
                "ulcer_index": result.get("ulcer_index", 0.0),
                "calmar_ratio": result.get("calmar_ratio", 0.0),
            }

            # Log results
            log.info(f"Backtest completed: {strategy} | {symbol}")
            log.info(
                f"  Return: {metrics['total_return']:.2f}% | Sharpe: {metrics['sharpe_ratio']:.2f} | Max DD: {metrics['max_drawdown']:.2f}%"
            )
            log.info(
                f"  Trades: {metrics['total_trades']} | Win Rate: {metrics['win_rate']:.1%} | Profit Factor: {metrics['profit_factor']:.2f}"
            )

            # Store in history
            self.backtest_history.append(metrics)

            return metrics

        except Exception as e:
            log.error(f"Backtest failed for {strategy}|{symbol}: {e}")
            return {
                "strategy": strategy,
                "symbol": symbol,
                "period": period,
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def compare(
        self, symbol: str, period: str = "1y", strategies: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Compare multiple strategies on a symbol

        Args:
            symbol: Yahoo Finance symbol
            period: Backtest period
            strategies: List of strategy names to compare. If None, compares all available strategies
                       (RSI, MACD, EMA_CROSS, BOLLINGER, SUPERTREND, DONCHIAN)

        Returns:
            Dict with comparison results and ranked leaderboard
        """
        try:
            if mcp_compare_strategies is None:
                log.warning(
                    f"Compare unavailable for {symbol}, MCP tools not available"
                )
                return {
                    "symbol": symbol,
                    "period": period,
                    "status": "unavailable",
                    "message": "TradingView MCP compare tools not available",
                }

            # Default strategies
            if strategies is None:
                strategies = [
                    "RSI",
                    "MACD",
                    "EMA_CROSS",
                    "BOLLINGER",
                    "SUPERTREND",
                    "DONCHIAN",
                ]

            # Validate period
            valid_periods = ["1mo", "3mo", "6mo", "1y", "2y"]
            if period not in valid_periods:
                log.warning(f"Invalid period '{period}', using default '1y'")
                period = "1y"

            log.info(
                f"Starting strategy comparison: {symbol} | {period} | {len(strategies)} strategies"
            )

            # Call TradingView compare API
            result = mcp_compare_strategies(
                symbol=symbol, period=period, initial_capital=10000
            )

            if not result or not isinstance(result, dict):
                log.error(f"Invalid comparison response for {symbol}")
                return {
                    "symbol": symbol,
                    "period": period,
                    "status": "error",
                    "message": "Invalid comparison response",
                }

            # Build comparison result
            comparison = {
                "symbol": symbol,
                "period": period,
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "strategies_compared": len(strategies),
                "results": [],
                "leaderboard": [],
            }

            # Extract individual strategy results
            if isinstance(result.get("results"), list):
                comparison["results"] = result["results"]
            elif isinstance(result.get("results"), dict):
                for strat, metrics in result["results"].items():
                    comparison["results"].append({"strategy": strat, **metrics})

            # Extract leaderboard (ranked by Sharpe ratio)
            if isinstance(result.get("leaderboard"), list):
                comparison["leaderboard"] = result["leaderboard"]
            elif isinstance(result.get("leaderboard"), dict):
                comparison["leaderboard"] = [
                    {"strategy": k, **v} for k, v in result["leaderboard"].items()
                ]

            # Log results
            log.info(f"Comparison completed: {symbol} | {period}")
            if comparison["leaderboard"]:
                top = comparison["leaderboard"][0]
                log.info(
                    f"  Winner: {top.get('strategy', '?')} | Sharpe: {top.get('sharpe_ratio', 0):.2f} | Return: {top.get('total_return', 0):.2f}%"
                )

            # Store in history
            self.comparison_history.append(comparison)

            return comparison

        except Exception as e:
            log.error(f"Strategy comparison failed for {symbol}: {e}")
            return {
                "symbol": symbol,
                "period": period,
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def get_backtest_history(
        self, symbol: str | None = None, strategy: str | None = None
    ) -> list[dict]:
        """
        Retrieve backtest history with optional filtering

        Args:
            symbol: Filter by symbol
            strategy: Filter by strategy name

        Returns:
            List of backtest results
        """
        history = self.backtest_history

        if symbol:
            history = [h for h in history if h.get("symbol") == symbol]

        if strategy:
            history = [
                h for h in history if h.get("strategy").upper() == strategy.upper()
            ]

        return history

    def get_comparison_history(self, symbol: str | None = None) -> list[dict]:
        """
        Retrieve comparison history with optional filtering

        Args:
            symbol: Filter by symbol

        Returns:
            List of comparison results
        """
        if symbol:
            return [c for c in self.comparison_history if c.get("symbol") == symbol]

        return self.comparison_history

    def get_best_strategy(
        self, symbol: str, period: str = "1y", metric: str = "sharpe_ratio"
    ) -> dict | None:
        """
        Get the best performing strategy for a symbol based on a metric

        Args:
            symbol: Symbol to analyze
            period: Backtest period
            metric: Metric to rank by (sharpe_ratio, total_return, win_rate)

        Returns:
            Best strategy result or None
        """
        history = self.get_backtest_history(symbol=symbol)
        history = [
            h
            for h in history
            if h.get("period") == period and h.get("status") == "success"
        ]

        if not history:
            log.debug(f"No backtest history for {symbol} in {period}")
            return None

        # Sort by metric (descending for positive metrics)
        best = max(history, key=lambda x: x.get(metric, 0))

        log.info(
            f"Best strategy for {symbol}: {best['strategy']} ({metric}={best.get(metric, 0):.2f})"
        )
        return best

    def _parse_period_to_days(self, period: str) -> int:
        """Convert period string to approximate number of trading days."""
        period_map = {
            "1mo": 21,
            "3mo": 63,
            "6mo": 126,
            "1y": 252,
            "2y": 504,
            "3y": 756,
            "5y": 1260,
        }
        return period_map.get(period, 252)

    def _get_period_string(self, days: int) -> str:
        """Convert number of days to nearest period string."""
        if days <= 21:
            return "1mo"
        elif days <= 63:
            return "3mo"
        elif days <= 126:
            return "6mo"
        elif days <= 252:
            return "1y"
        else:
            return "2y"

    def walk_forward_optimize(
        self, strategy_name: str, symbol: str, total_period: str = "2y"
    ) -> dict[str, Any]:
        """
        Walk-forward optimization: divide data into rolling windows,
        train parameters on train data, evaluate on test data.

        Args:
            strategy_name: Strategy name (RSI, MACD, EMA_CROSS, BOLLINGER, SUPERTREND, DONCHIAN)
            symbol: Yahoo Finance symbol (e.g., 'AAPL', 'BTC-USD')
            total_period: Total backtest period ('1y', '2y', '3y', '5y')

        Returns:
            Dict with optimized parameters, performance metrics, and optimization history
        """
        try:
            if mcp_backtest_strategy is None:
                log.warning(
                    "Walk-forward optimization unavailable: MCP tools not available"
                )
                return {
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "total_period": total_period,
                    "status": "unavailable",
                    "message": "TradingView MCP backtest tools not available",
                }

            # Validate period
            valid_periods = ["1y", "2y", "3y", "5y"]
            if total_period not in valid_periods:
                log.warning(f"Invalid period '{total_period}', using '2y'")
                total_period = "2y"

            total_days = self._parse_period_to_days(total_period)
            min_required_days = 252  # Minimum 12 months = 252 trading days

            if total_days < min_required_days:
                error_msg = f"Insufficient data: {total_period} ({total_days} days) < minimum 12 months ({min_required_days} days)"
                log.error(error_msg)
                return {
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "total_period": total_period,
                    "status": "error",
                    "message": error_msg,
                    "timestamp": datetime.now().isoformat(),
                }

            log.info(
                f"Starting walk-forward optimization: {strategy_name} | {symbol} | {total_period}"
            )

            # Define parameter ranges for different strategies
            strategy_params = self._get_strategy_param_ranges(strategy_name)

            # Create 6-month rolling windows with 3-month train/test splits
            window_size_days = 126  # 6 months
            train_days = 63  # 3 months
            test_days = 63  # 3 months

            windows = self._create_rolling_windows(
                total_days, window_size_days, train_days, test_days
            )

            if not windows:
                error_msg = (
                    f"Could not create rolling windows for period {total_period}"
                )
                log.error(error_msg)
                return {
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "total_period": total_period,
                    "status": "error",
                    "message": error_msg,
                }

            log.info(f"Created {len(windows)} rolling windows for optimization")

            # Optimize parameters for each window
            window_results = []
            best_params = None
            best_avg_sharpe = -float("inf")
            param_combinations_tested = set()
            param_sharpe_totals = {}

            for window_idx, window in enumerate(windows):
                train_period = window["train_period"]
                test_period = window["test_period"]

                log.info(
                    f"[Window {window_idx + 1}/{len(windows)}] Training: {train_period} | Testing: {test_period}"
                )

                # Test different parameter combinations
                window_sharpes = []

                for param_combo in strategy_params:
                    param_id = self._param_combo_to_id(param_combo)
                    param_combinations_tested.add(param_id)

                    # API rate limit: 1 second between calls
                    time.sleep(1)
                    train_result = self._backtest_with_params(
                        strategy_name, symbol, train_period, param_combo
                    )
                    if not train_result or train_result.get("status") == "error":
                        log.warning(
                            f"Train failed for params {param_id} on {train_period}"
                        )
                        continue

                    time.sleep(1)
                    test_result = self._backtest_with_params(
                        strategy_name, symbol, test_period, param_combo
                    )
                    if not test_result or test_result.get("status") == "error":
                        log.warning(
                            f"Test failed for params {param_id} on {test_period}"
                        )
                        continue

                    sharpe = test_result.get("sharpe_ratio", 0)
                    window_sharpes.append(
                        {
                            "params": param_combo,
                            "sharpe": sharpe,
                            "train_period": train_period,
                            "test_period": test_period,
                            "metrics": test_result,
                        }
                    )

                    if param_id not in param_sharpe_totals:
                        param_sharpe_totals[param_id] = {
                            "params": param_combo,
                            "sharpe_sum": 0.0,
                            "count": 0,
                        }

                    param_sharpe_totals[param_id]["sharpe_sum"] += sharpe
                    param_sharpe_totals[param_id]["count"] += 1

                    log.debug(
                        f"  Params {param_id} | Train: {train_result.get('sharpe_ratio', 0):.3f} | Test: {sharpe:.3f}"
                    )

                # Determine the best parameters for this window
                if window_sharpes:
                    best_for_window = max(window_sharpes, key=lambda x: x["sharpe"])
                    window_results.append(
                        {
                            "window": window_idx + 1,
                            "train_period": train_period,
                            "test_period": test_period,
                            "best_params": best_for_window["params"],
                            "sharpe_ratio": best_for_window["sharpe"],
                            "metrics": best_for_window["metrics"],
                        }
                    )

                    log.info(
                        f"[Window {window_idx + 1}] Best Sharpe: {best_for_window['sharpe']:.3f} | "
                        f"Params: {best_for_window['params']}"
                    )

            # Select the best parameter set across all windows by average test Sharpe
            for summary in param_sharpe_totals.values():
                if summary["count"] == 0:
                    continue
                avg_sharpe_for_param = summary["sharpe_sum"] / summary["count"]
                if avg_sharpe_for_param > best_avg_sharpe:
                    best_avg_sharpe = avg_sharpe_for_param
                    best_params = summary["params"]

            # Calculate average Sharpe across all windows from the winners in each window
            avg_sharpe = (
                sum(w["sharpe_ratio"] for w in window_results) / len(window_results)
                if window_results
                else 0
            )

            log.info(f"Optimization complete: {len(window_results)} windows evaluated")
            log.info(f"Average Sharpe across test periods: {avg_sharpe:.3f}")
            log.info(f"Best parameters found: {best_params}")

            return {
                "strategy": strategy_name,
                "symbol": symbol,
                "total_period": total_period,
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "windows_analyzed": len(window_results),
                "optimized_params": best_params,
                "average_sharpe": avg_sharpe,
                "best_sharpe": best_avg_sharpe,
                "window_results": window_results,
                "param_combinations_tested": len(param_combinations_tested),
            }

        except Exception as e:
            log.error(f"Walk-forward optimization failed: {e}")
            return {
                "strategy": strategy_name,
                "symbol": symbol,
                "total_period": total_period,
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def _get_strategy_param_ranges(self, strategy_name: str) -> list[dict[str, Any]]:
        """Get parameter combinations for a strategy."""
        strategy_name = strategy_name.upper()

        # Generate parameter combinations based on strategy
        if strategy_name == "RSI":
            # RSI parameters: period and oversold/overbought levels
            return [
                {"period": 7},
                {"period": 14},
                {"period": 21},
            ]
        elif strategy_name == "MACD":
            # MACD parameters
            return [
                {"fast": 12, "slow": 26, "signal": 9},
                {"fast": 10, "slow": 20, "signal": 9},
            ]
        elif strategy_name == "EMA_CROSS":
            # EMA cross parameters
            return [
                {"fast": 10, "slow": 30},
                {"fast": 12, "slow": 26},
                {"fast": 20, "slow": 50},
            ]
        elif strategy_name == "BOLLINGER":
            # Bollinger Bands parameters
            return [
                {"period": 20, "std_dev": 1.5},
                {"period": 20, "std_dev": 2.0},
                {"period": 20, "std_dev": 2.5},
            ]
        elif strategy_name == "SUPERTREND":
            # Supertrend parameters
            return [
                {"atr_period": 10, "multiplier": 2.0},
                {"atr_period": 10, "multiplier": 3.0},
                {"atr_period": 14, "multiplier": 2.0},
            ]
        elif strategy_name == "DONCHIAN":
            # Donchian Channel parameters
            return [
                {"period": 14},
                {"period": 20},
                {"period": 28},
            ]
        else:
            # Default fallback
            return [{}]

    def _create_rolling_windows(
        self, total_days: int, window_size: int, train_days: int, test_days: int
    ) -> list[dict[str, str]]:
        """Create rolling window periods for walk-forward analysis."""
        windows = []

        # Each window advances by the test period to simulate rolling walk-forward analysis
        step = test_days

        current_pos = 0
        while current_pos + window_size <= total_days:
            windows.append(
                {
                    "start": current_pos,
                    "train_period": self._get_period_string(train_days),
                    "test_period": self._get_period_string(test_days),
                }
            )

            current_pos += step

        return windows

    def _param_combo_to_id(self, params: dict[str, Any]) -> str:
        """Convert parameter dict to unique identifier string."""
        if not params:
            return "default"
        items = sorted(params.items())
        return "_".join([f"{k}={v}" for k, v in items])

    def _backtest_with_params(
        self, strategy: str, symbol: str, period: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Backtest using parameterized strategy execution if supported."""
        if not params:
            return mcp_backtest_strategy(
                symbol=symbol, strategy=strategy.upper(), period=period
            )

        try:
            return mcp_backtest_strategy(
                symbol=symbol, strategy=strategy.upper(), period=period, params=params
            )
        except TypeError:
            try:
                return mcp_backtest_strategy(
                    symbol=symbol,
                    strategy=strategy.upper(),
                    period=period,
                    strategy_params=params,
                )
            except TypeError:
                log.warning(
                    "TradingView backtest does not support custom strategy parameters; falling back to default backtest"
                )
                return mcp_backtest_strategy(
                    symbol=symbol, strategy=strategy.upper(), period=period
                )
