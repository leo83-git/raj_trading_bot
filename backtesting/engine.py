# ═══════════════════════════════════════════════════════════════
#  Real Backtesting Framework — VectorBT Integration
#  Features: Walk-forward analysis, slippage, commissions, realistic fills
# ═══════════════════════════════════════════════════════════════
import datetime
import random
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

try:
    import vectorbt as vbt

    VECTORBT_AVAILABLE = True
except ImportError:
    VECTORBT_AVAILABLE = False
    print("Warning: VectorBT not installed. Install with: pip install vectorbt")

from quant_utils.logger import get_logger

log = get_logger("backtesting")


@dataclass
class BacktestConfig:
    initial_capital: float = 300000
    commission: float = 30.0
    slippage_pips: float = 1.0
    slippage_mode: str = "fixed"
    fill_mode: str = "close"
    position_sizing: str = "fixed"
    max_position_size: float = 0.2
    allow_partial_fills: bool = True
    realistic_fills: bool = True


@dataclass
class TradeResult:
    symbol: str
    entry_time: datetime.datetime
    exit_time: datetime.datetime
    action: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    commission: float
    slippage: float
    reason: str


@dataclass
class BacktestResults:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_pnl_pct: float
    avg_win: float
    avg_loss: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    avg_trade_duration: float
    trades: list[TradeResult]
    equity_curve: pd.DataFrame
    metrics: dict


class SlippageModel:
    def __init__(self, config: BacktestConfig):
        self.pips = config.slippage_pips
        self.mode = config.slippage_mode

    def apply(self, price: float, action: str, volume: int = 1) -> float:
        if self.mode == "fixed":
            slippage = self.pips
        elif self.mode == "variable":
            slippage = random.uniform(0.5, 2.0)
        elif self.mode == "volume_based":
            slippage = self.pips * (1 + np.log1p(volume) * 0.1)
        else:
            slippage = 0

        if action == "BUY":
            return price + slippage
        else:
            return price - slippage


class CommissionModel:
    def __init__(self, config: BacktestConfig):
        self.base_commission = config.commission
        self.gst_rate = 0.18
        self.sebi_charge = 0.00001
        self.stt = 0.001

    def calculate(self, price: float, quantity: int, action: str) -> dict[str, float]:
        turnover = price * quantity

        broker_commission = max(self.base_commission, turnover * 0.0003)
        broker_commission_with_gst = broker_commission * (1 + self.gst_rate)

        sebi_charge = turnover * self.sebi_charge

        stt = turnover * self.stt if action == "SELL" else 0

        stamp_duty = turnover * 0.00002 if action == "BUY" else 0

        total = broker_commission_with_gst + sebi_charge + stt + stamp_duty

        return {
            "broker": broker_commission,
            "gst": broker_commission * self.gst_rate,
            "sebi": sebi_charge,
            "stt": stt,
            "stamp_duty": stamp_duty,
            "total": total,
        }


class FillModel:
    def __init__(self, config: BacktestConfig):
        self.mode = config.fill_mode
        self.allow_partial = config.allow_partial_fills
        self.realistic = config.realistic_fills

    def get_fill_price(
        self, df: pd.DataFrame, idx: int, action: str
    ) -> tuple[float, bool]:
        if self.mode == "close":
            return df.iloc[idx]["close"], True

        elif self.mode == "ohlc":
            if action == "BUY":
                price = df.iloc[idx]["high"]
            else:
                price = df.iloc[idx]["low"]

            if self.realistic:
                fill_prob = 0.85
                if random.random() > fill_prob:
                    if action == "BUY":
                        price = df.iloc[idx]["close"] + random.uniform(0, 0.01)
                    else:
                        price = df.iloc[idx]["close"] - random.uniform(0, 0.01)
            return price, True

        elif self.mode == "vwap":
            typical = (
                df.iloc[idx]["high"] + df.iloc[idx]["low"] + df.iloc[idx]["close"]
            ) / 3
            return typical, True

        return df.iloc[idx]["close"], True


class RealBacktestEngine:
    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()

        self.slippage_model = SlippageModel(self.config)
        self.commission_model = CommissionModel(self.config)
        self.fill_model = FillModel(self.config)

        self.capital = self.config.initial_capital
        self.initial_capital = self.config.initial_capital

        self.positions = {}
        self.trades = []
        self.equity_history = []

        log.info(
            f"RealBacktestEngine initialized | Capital: {self.capital} | Commission: {self.config.commission}"
        )

    def reset(self):
        self.capital = self.initial_capital
        self.positions = {}
        self.trades = []
        self.equity_history = []

    def run_vectorbt(
        self, data: pd.DataFrame, signals: pd.Series, price_col: str = "close"
    ) -> BacktestResults:
        if not VECTORBT_AVAILABLE:
            log.error("VectorBT not available")
            return None

        self.reset()

        close = data[price_col]

        entries = signals == "BUY"
        exits = signals == "SELL"

        pf = vbt.Portfolio.from_signals(
            close,
            entries=entries,
            exits=exits,
            init_cash=self.config.initial_capital,
            fees=self.config.commission / 100,
            slippage=self.config.slippage_pips / 100,
            size=np.inf,
            size_type="value",
            call_seq="auto",
        )

        stats = pf.stats()

        trades_list = []
        closed_trades = pf.closed_trades()

        if closed_trades is not None and len(closed_trades) > 0:
            for i, (idx, trade) in enumerate(closed_trades.iterrows()):
                trades_list.append(
                    TradeResult(
                        symbol="BACKTEST",
                        entry_time=trade["entry_date"],
                        exit_time=trade["exit_date"],
                        action="BUY",
                        entry_price=trade["entry_price"],
                        exit_price=trade["exit_price"],
                        quantity=1,
                        pnl=trade["pnl"],
                        pnl_pct=trade["return"] * 100,
                        commission=trade.get("fees", 0),
                        slippage=0,
                        reason="signal",
                    )
                )

        equity_curve = pd.DataFrame({"timestamp": data.index, "equity": pf.value()})

        winning = len([t for t in trades_list if t.pnl > 0])
        losing = len([t for t in trades_list if t.pnl <= 0])

        return BacktestResults(
            total_trades=len(trades_list),
            winning_trades=winning,
            losing_trades=losing,
            win_rate=winning / len(trades_list) if trades_list else 0,
            total_pnl=stats.get("total_return", 0) * self.initial_capital,
            total_pnl_pct=stats.get("total_return", 0) * 100,
            avg_win=stats.get("avg_win", 0),
            avg_loss=abs(stats.get("avg_loss", 0)),
            max_drawdown=abs(stats.get("max_drawdown", 0)),
            sharpe_ratio=stats.get("sharpe_ratio", 0),
            sortino_ratio=stats.get("sortino_ratio", 0),
            profit_factor=stats.get("profit_factor", 0),
            avg_trade_duration=0,
            trades=trades_list,
            equity_curve=equity_curve,
            metrics=stats.to_dict(),
        )

    def run_bar_by_bar(
        self, data: pd.DataFrame, strategy_func: Callable, price_col: str = "close"
    ) -> BacktestResults:
        self.reset()

        for idx in range(len(data)):
            row = data.iloc[idx]
            timestamp = data.index[idx]

            signal = strategy_func(data, idx)

            if signal and "action" in signal:
                action = signal["action"]
                price, filled = self.fill_model.get_fill_price(data, idx, action)

                if action == "BUY":
                    self._execute_buy(
                        symbol=signal.get("symbol", "TEST"),
                        price=price,
                        quantity=signal.get("quantity", 1),
                        timestamp=timestamp,
                        metadata=signal,
                    )
                elif action == "SELL":
                    self._execute_sell(
                        symbol=signal.get("symbol", "TEST"),
                        price=price,
                        quantity=signal.get("quantity", 1),
                        timestamp=timestamp,
                        metadata=signal,
                    )

            self._check_exits(data, idx, timestamp)

            unrealized = self._calculate_unrealized(data, idx)
            self.equity_history.append(
                {
                    "timestamp": timestamp,
                    "capital": self.capital,
                    "unrealized": unrealized,
                    "total": self.capital + unrealized,
                }
            )

        return self._generate_results()

    def _execute_buy(
        self,
        symbol: str,
        price: float,
        quantity: int,
        timestamp: datetime.datetime,
        metadata: dict = None,
    ):
        price_with_slippage = self.slippage_model.apply(price, "BUY", quantity)

        comm = self.commission_model.calculate(price_with_slippage, quantity, "BUY")
        total_cost = price_with_slippage * quantity + comm["total"]

        if total_cost > self.capital:
            if self.config.allow_partial_fills:
                max_qty = int(
                    self.capital / (price_with_slippage + comm["total"] / quantity)
                )
                if max_qty < 1:
                    log.warning(f"Insufficient capital for {symbol}")
                    return
                quantity = max_qty
                total_cost = price_with_slippage * quantity + comm["total"]
            else:
                log.warning(f"Insufficient capital for {symbol}")
                return

        self.capital -= total_cost

        if symbol in self.positions:
            existing = self.positions[symbol]
            total_qty = existing["quantity"] + quantity
            avg_price = (
                existing["entry"] * existing["quantity"]
                + price_with_slippage * quantity
            ) / total_qty
            self.positions[symbol] = {
                "entry": avg_price,
                "quantity": total_qty,
                "entry_time": timestamp,
                "metadata": metadata or {},
            }
        else:
            self.positions[symbol] = {
                "entry": price_with_slippage,
                "quantity": quantity,
                "entry_time": timestamp,
                "metadata": metadata or {},
            }

        log.debug(f"BUY {symbol} @ {price_with_slippage:.2f} qty={quantity}")

    def _execute_sell(
        self,
        symbol: str,
        price: float,
        quantity: int,
        timestamp: datetime.datetime,
        metadata: dict = None,
    ):
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        quantity = min(quantity, pos["quantity"])

        price_with_slippage = self.slippage_model.apply(price, "SELL", quantity)

        comm = self.commission_model.calculate(price_with_slippage, quantity, "SELL")
        proceeds = price_with_slippage * quantity - comm["total"]

        pnl = (price_with_slippage - pos["entry"]) * quantity - comm["total"]

        self.capital += proceeds

        self.trades.append(
            TradeResult(
                symbol=symbol,
                entry_time=pos["entry_time"],
                exit_time=timestamp,
                action="BUY",
                entry_price=pos["entry"],
                exit_price=price_with_slippage,
                quantity=quantity,
                pnl=pnl,
                pnl_pct=(pnl / (pos["entry"] * quantity)) * 100,
                commission=comm["total"],
                slippage=abs(price_with_slippage - price),
                reason=metadata.get("reason", "signal") if metadata else "signal",
            )
        )

        if pos["quantity"] > quantity:
            self.positions[symbol]["quantity"] -= quantity
        else:
            del self.positions[symbol]

        log.debug(
            f"SELL {symbol} @ {price_with_slippage:.2f} qty={quantity} PnL={pnl:.2f}"
        )

    def _check_exits(self, data: pd.DataFrame, idx: int, timestamp: datetime.datetime):
        for symbol, pos in list(self.positions.items()):
            metadata = pos.get("metadata", {})

            target = metadata.get("target")
            stop_loss = metadata.get("stop_loss")
            exit_time = metadata.get("exit_time")

            current_price = data.iloc[idx][data.columns[0]]
            if "close" in data.columns:
                current_price = data.iloc[idx]["close"]

            should_exit = False
            reason = ""

            if exit_time and timestamp >= exit_time:
                should_exit = True
                reason = "time_exit"
            elif target and current_price >= target:
                should_exit = True
                reason = "target_hit"
            elif stop_loss and current_price <= stop_loss:
                should_exit = True
                reason = "stop_loss"

            if should_exit:
                self._execute_sell(
                    symbol,
                    current_price,
                    pos["quantity"],
                    timestamp,
                    {"reason": reason},
                )

    def _calculate_unrealized(self, data: pd.DataFrame, idx: int) -> float:
        unrealized = 0
        for symbol, pos in self.positions.items():
            current_price = (
                data.iloc[idx]["close"]
                if "close" in data.columns
                else data.iloc[idx][0]
            )
            unrealized += (current_price - pos["entry"]) * pos["quantity"]
        return unrealized

    def _generate_results(self) -> BacktestResults:
        closed_trades = [t for t in self.trades]

        winning = [t for t in closed_trades if t.pnl > 0]
        losing = [t for t in closed_trades if t.pnl <= 0]

        total_pnl = sum(t.pnl for t in closed_trades)

        avg_win = sum(t.pnl for t in winning) / len(winning) if winning else 0
        avg_loss = abs(sum(t.pnl for t in losing) / len(losing)) if losing else 0

        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

        equity_df = pd.DataFrame(self.equity_history)

        max_drawdown = 0
        if len(equity_df) > 0:
            equity_df["peak"] = equity_df["total"].cummax()
            equity_df["drawdown"] = (
                equity_df["total"] - equity_df["peak"]
            ) / equity_df["peak"]
            max_drawdown = abs(equity_df["drawdown"].min())

        returns = equity_df["total"].pct_change().dropna()
        sharpe = (
            returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
        )

        downside_returns = returns[returns < 0]
        sortino = (
            returns.mean() / downside_returns.std() * np.sqrt(252)
            if len(downside_returns) > 0 and downside_returns.std() > 0
            else 0
        )

        return BacktestResults(
            total_trades=len(closed_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=len(winning) / len(closed_trades) if closed_trades else 0,
            total_pnl=total_pnl,
            total_pnl_pct=(total_pnl / self.initial_capital) * 100,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            profit_factor=profit_factor,
            avg_trade_duration=0,
            trades=closed_trades,
            equity_curve=equity_df,
            metrics={},
        )


class WalkForwardAnalyzer:
    def __init__(self, config: BacktestConfig = None, train_ratio: float = 0.7):
        self.config = config or BacktestConfig()
        self.train_ratio = train_ratio
        self.results = []

    def run(
        self,
        data: pd.DataFrame,
        strategy_func: Callable,
        n_splits: int = 5,
        step_size: int = None,
    ) -> list[BacktestResults]:
        log.info(f"Running walk-forward analysis with {n_splits} splits")

        n = len(data)
        train_size = int(n * self.train_ratio)

        if step_size is None:
            step_size = (n - train_size) // n_splits

        all_results = []

        for i in range(n_splits):
            train_end = train_size + (i * step_size)
            test_end = min(train_end + (n - train_size), n)

            if test_end > n:
                break

            train_data = data.iloc[:train_end]
            test_data = data.iloc[train_end:test_end]

            log.info(
                f"Split {i + 1}: Train [{0}-{train_end}], Test [{train_end}-{test_end}]"
            )

            engine = RealBacktestEngine(self.config)

            trained_strategy = self._train_strategy(strategy_func, train_data)

            result = engine.run_bar_by_bar(test_data, trained_strategy)

            all_results.append(
                {
                    "split": i + 1,
                    "train_period": f"{train_data.index[0]} to {train_data.index[-1]}",
                    "test_period": f"{test_data.index[0]} to {test_data.index[-1]}",
                    "result": result,
                }
            )

            log.info(
                f"Split {i + 1} Results: PnL={result.total_pnl:.2f}, WinRate={result.win_rate:.2%}"
            )

        self.results = all_results
        return all_results

    def _train_strategy(
        self, base_strategy: Callable, train_data: pd.DataFrame
    ) -> Callable:
        def trained_strategy(data: pd.DataFrame, idx: int) -> dict | None:
            return base_strategy(data, idx, train_data)

        return trained_strategy

    def get_summary(self) -> dict:
        if not self.results:
            return {}

        total_pnl = sum(r["result"].total_pnl for r in self.results)
        avg_win_rate = sum(r["result"].win_rate for r in self.results) / len(
            self.results
        )
        avg_drawdown = sum(r["result"].max_drawdown for r in self.results) / len(
            self.results
        )

        return {
            "total_splits": len(self.results),
            "total_pnl": total_pnl,
            "avg_win_rate": avg_win_rate,
            "avg_drawdown": avg_drawdown,
            "results": self.results,
        }


def create_backtest_config(config_dict: dict = None) -> BacktestConfig:
    if config_dict is None:
        config_dict = {}

    return BacktestConfig(
        initial_capital=config_dict.get("initial_capital", 300000),
        commission=config_dict.get("commission", 30.0),
        slippage_pips=config_dict.get("slippage_pips", 1.0),
        slippage_mode=config_dict.get("slippage_mode", "fixed"),
        fill_mode=config_dict.get("fill_mode", "close"),
        position_sizing=config_dict.get("position_sizing", "fixed"),
        max_position_size=config_dict.get("max_position_size", 0.2),
        allow_partial_fills=config_dict.get("allow_partial_fills", True),
        realistic_fills=config_dict.get("realistic_fills", True),
    )
