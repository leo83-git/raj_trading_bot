# ═══════════════════════════════════════════════════════════════
#  Performance Analytics — Statistical Performance Metrics
# ═══════════════════════════════════════════════════════════════
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from quant_utils.logger import get_logger

log = get_logger("analytics.performance")


@dataclass(frozen=True, slots=True)
class DailyOperationalMetrics:
    """Compact P7 trading and reliability metrics for one session."""

    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    drawdown_percent: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    scheduler_cycles: int = 0
    data_quality_failures: int = 0
    screened: int = 0
    signals: int = 0
    risk_rejections: int = 0
    executions: int = 0
    recoveries: int = 0


def compact_daily_report(metrics: DailyOperationalMetrics | Mapping[str, float]) -> str:
    """Render a stable single-line report suitable for logs and alerts."""
    values = (
        asdict(metrics) if isinstance(metrics, DailyOperationalMetrics) else metrics
    )
    return (
        "Daily P7 | "
        f"PnL={float(values.get('realized_pnl', 0)):.2f}/"
        f"{float(values.get('unrealized_pnl', 0)):.2f} "
        f"DD={float(values.get('drawdown_percent', 0)):.2f}% "
        f"Greeks Δ={float(values.get('delta', 0)):.3f} "
        f"Γ={float(values.get('gamma', 0)):.3f} "
        f"Θ={float(values.get('theta', 0)):.3f} "
        f"V={float(values.get('vega', 0)):.3f} "
        f"cycles={int(values.get('scheduler_cycles', 0))} "
        f"dq_fail={int(values.get('data_quality_failures', 0))} "
        f"screened={int(values.get('screened', 0))} "
        f"signals={int(values.get('signals', 0))} "
        f"risk_reject={int(values.get('risk_rejections', 0))} "
        f"exec={int(values.get('executions', 0))} "
        f"recovery={int(values.get('recoveries', 0))}"
    )


class PerformanceAnalytics:
    """Calculate statistical performance metrics for trading strategies"""

    def calculate_sharpe_ratio(
        self, returns: list, risk_free_rate: float = 0.03
    ) -> float:
        """Calculate Sharpe ratio"""
        if not returns or len(returns) < 2:
            return 0.0

        try:
            # Convert to numpy for better performance if available
            try:
                import numpy as np

                returns_array = np.array(returns)
                excess_returns = (
                    returns_array - risk_free_rate / 252
                )  # Daily risk-free rate
                if np.std(excess_returns) == 0:
                    return 0.0
                sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
            except ImportError:
                # Fallback without numpy
                excess_returns = [r - risk_free_rate / 252 for r in returns]
                mean_excess = sum(excess_returns) / len(excess_returns)
                std_excess = math.sqrt(
                    sum((r - mean_excess) ** 2 for r in excess_returns)
                    / len(excess_returns)
                )
                if std_excess == 0:
                    return 0.0
                sharpe = mean_excess / std_excess * math.sqrt(252)

            return round(sharpe, 4)
        except Exception as e:
            log.warning(f"Error calculating Sharpe ratio: {e}")
            return 0.0

    def calculate_profit_factor(self, trades: list) -> float:
        """Calculate profit factor (gross profit / gross loss)"""
        if not trades:
            return 1.0

        gross_profit = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0))

        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 1.0

        profit_factor = gross_profit / gross_loss
        return round(profit_factor, 4)

    def calculate_win_rate(self, trades: list) -> float:
        """Calculate win rate percentage"""
        if not trades:
            return 0.0

        winning_trades = sum(1 for t in trades if t.get("pnl", 0) > 0)
        win_rate = winning_trades / len(trades) * 100

        return round(win_rate, 2)

    def calculate_max_drawdown(self, equity_curve: list) -> dict:
        """Calculate maximum drawdown"""
        if not equity_curve or len(equity_curve) < 2:
            return {"max_drawdown": 0, "peak": 0, "trough": 0, "recovery_period": 0}

        try:
            peak = equity_curve[0]
            max_drawdown = 0
            peak_idx = 0
            trough_idx = 0

            for i, value in enumerate(equity_curve):
                if value > peak:
                    peak = value
                    peak_idx = i

                drawdown = (peak - value) / peak * 100 if peak > 0 else 0
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                    trough_idx = i

            recovery_period = trough_idx - peak_idx if trough_idx > peak_idx else 0

            return {
                "max_drawdown": round(max_drawdown, 2),
                "peak": round(peak, 2),
                "trough": round(equity_curve[trough_idx], 2),
                "peak_idx": peak_idx,
                "trough_idx": trough_idx,
                "recovery_period": recovery_period,
            }
        except Exception as e:
            log.warning(f"Error calculating max drawdown: {e}")
            return {"max_drawdown": 0, "peak": 0, "trough": 0, "recovery_period": 0}

    def calculate_calmar_ratio(self, returns: list, max_drawdown: float) -> float:
        """Calculate Calmar ratio (annual return / max drawdown)"""
        if not returns or max_drawdown <= 0:
            return 0.0

        try:
            # Annualized return
            total_return = 1.0
            for r in returns:
                total_return *= 1 + r

            if len(returns) >= 252:  # Daily returns
                annualized_return = total_return ** (252 / len(returns)) - 1
            else:
                annualized_return = total_return - 1  # Simple total return

            calmar = (
                annualized_return / (max_drawdown / 100) if max_drawdown > 0 else 0.0
            )

            return round(calmar, 4)
        except Exception as e:
            log.warning(f"Error calculating Calmar ratio: {e}")
            return 0.0

    def calculate_sortino_ratio(
        self, returns: list, risk_free_rate: float = 0.03
    ) -> float:
        """Calculate Sortino ratio (downside deviation only)"""
        if not returns or len(returns) < 2:
            return 0.0

        try:
            # Calculate downside returns only
            downside_returns = [r for r in returns if r < risk_free_rate / 252]

            if not downside_returns:
                return float("inf")  # No downside risk

            try:
                import numpy as np

                excess_returns = np.array(returns) - risk_free_rate / 252
                downside_std = np.std(downside_returns) if downside_returns else 0

                if downside_std == 0:
                    return float("inf")

                sortino = np.mean(excess_returns) / downside_std * np.sqrt(252)
            except ImportError:
                excess_returns = [r - risk_free_rate / 252 for r in returns]
                mean_excess = sum(excess_returns) / len(excess_returns)
                downside_std = (
                    math.sqrt(
                        sum(r**2 for r in downside_returns) / len(downside_returns)
                    )
                    if downside_returns
                    else 0
                )

                if downside_std == 0:
                    return float("inf")

                sortino = mean_excess / downside_std * math.sqrt(252)

            return round(sortino, 4)
        except Exception as e:
            log.warning(f"Error calculating Sortino ratio: {e}")
            return 0.0

    def calculate_alpha_beta(
        self, strategy_returns: list, benchmark_returns: list
    ) -> dict:
        """Calculate alpha and beta vs benchmark"""
        if (
            not strategy_returns
            or not benchmark_returns
            or len(strategy_returns) != len(benchmark_returns)
        ):
            return {"alpha": 0, "beta": 1, "r_squared": 0}

        try:
            try:
                import numpy as np

                strategy_array = np.array(strategy_returns)
                benchmark_array = np.array(benchmark_returns)

                covariance = np.cov(strategy_array, benchmark_array)[0, 1]
                benchmark_variance = np.var(benchmark_array)

                beta = covariance / benchmark_variance if benchmark_variance > 0 else 1
                alpha = np.mean(strategy_array) - beta * np.mean(benchmark_array)

                # R-squared
                correlation_matrix = np.corrcoef(strategy_array, benchmark_array)
                r_squared = correlation_matrix[0, 1] ** 2

            except ImportError:
                # Simple beta calculation
                mean_strategy = sum(strategy_returns) / len(strategy_returns)
                mean_benchmark = sum(benchmark_returns) / len(benchmark_returns)

                covariance = sum(
                    (s - mean_strategy) * (b - mean_benchmark)
                    for s, b in zip(strategy_returns, benchmark_returns)
                ) / len(strategy_returns)
                benchmark_variance = sum(
                    (b - mean_benchmark) ** 2 for b in benchmark_returns
                ) / len(benchmark_returns)

                beta = covariance / benchmark_variance if benchmark_variance > 0 else 1
                alpha = mean_strategy - beta * mean_benchmark
                r_squared = 0  # Simplified, would need correlation calculation

            return {
                "alpha": round(alpha, 6),
                "beta": round(beta, 4),
                "r_squared": round(r_squared, 4),
            }
        except Exception as e:
            log.warning(f"Error calculating alpha/beta: {e}")
            return {"alpha": 0, "beta": 1, "r_squared": 0}

    def generate_performance_report(
        self, trades: list, equity_curve: list | None = None
    ) -> dict:
        """Generate comprehensive performance report"""
        if not trades:
            return {"error": "no_trades"}

        try:
            # Extract returns from trades
            returns = [
                t.get("pnl", 0) / t.get("entry_price", 1)
                for t in trades
                if t.get("entry_price", 0) > 0
            ]

            # Calculate metrics
            sharpe_ratio = self.calculate_sharpe_ratio(returns)
            profit_factor = self.calculate_profit_factor(trades)
            win_rate = self.calculate_win_rate(trades)
            sortino_ratio = self.calculate_sortino_ratio(returns)

            # Max drawdown
            if equity_curve:
                drawdown_analysis = self.calculate_max_drawdown(equity_curve)
                max_drawdown = drawdown_analysis["max_drawdown"]
                calmar_ratio = self.calculate_calmar_ratio(returns, max_drawdown)
            else:
                drawdown_analysis = {"max_drawdown": 0}
                max_drawdown = 0
                calmar_ratio = 0

            # Trade statistics
            total_trades = len(trades)
            winning_trades = sum(1 for t in trades if t.get("pnl", 0) > 0)
            losing_trades = total_trades - winning_trades

            total_pnl = sum(t.get("pnl", 0) for t in trades)
            avg_win = (
                sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
                / winning_trades
                if winning_trades > 0
                else 0
            )
            avg_loss = (
                abs(
                    sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0)
                    / losing_trades
                )
                if losing_trades > 0
                else 0
            )

            return {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": win_rate,
                "total_pnl": round(total_pnl, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": profit_factor,
                "sharpe_ratio": sharpe_ratio,
                "sortino_ratio": sortino_ratio,
                "max_drawdown": max_drawdown,
                "calmar_ratio": calmar_ratio,
                "drawdown_analysis": drawdown_analysis,
            }
        except Exception as e:
            log.error(f"Error generating performance report: {e}")
            return {"error": str(e)}


# Global instance for easy access
_performance_analytics = None


def get_performance_analytics() -> PerformanceAnalytics:
    """Get global performance analytics instance"""
    global _performance_analytics
    if _performance_analytics is None:
        _performance_analytics = PerformanceAnalytics()
    return _performance_analytics
