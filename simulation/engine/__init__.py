# ═══════════════════════════════════════════════════════════════
#  Simulation Engine — Paper trading & backtesting
# ═══════════════════════════════════════════════════════════════
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from execution.cost_model import BrokerFees
from quant_utils.logger import get_logger

log = get_logger("simulation.engine")


@dataclass
class SimPosition:
    symbol: str
    action: str
    entry: float
    quantity: int
    entry_time: datetime.datetime


class SimulationEngine:
    """Paper trading simulation engine"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.capital = self.config.get("capital", 300000)
        self.initial_capital = self.capital

        self.positions = {}
        self.trades = []

        self.daily_pnl = 0
        self.total_pnl = 0

        # Broker fees model for simulation (used when closing positions)
        self.broker_fees = BrokerFees(
            brokerage_per_trade=self.config.get("brokerage", 30),
            stt=self.config.get("stt", 0.001),
        )

        log.info(f"Simulation engine initialized with capital: {self.capital}")

    def mark_to_market(self, get_quote_fn=None) -> dict:
        """
        Mark positions to market using current quotes.
        Updates PnL based on live prices between ticks.
        """
        if not get_quote_fn:
            return {"status": "error", "message": "No quote function provided"}

        total_mtm = 0
        position_values = {}

        for symbol, pos in list(self.positions.items()):
            try:
                quote = get_quote_fn(symbol)
                if not quote or quote.get("ltp") is None:
                    continue

                current_price = quote.get("ltp")
                entry_price = pos.get("entry")
                quantity = pos.get("quantity")
                direction = pos.get("action")

                if direction == "BUY":
                    pnl = (current_price - entry_price) * quantity
                else:  # SELL
                    pnl = (entry_price - current_price) * quantity

                pos["current_price"] = current_price
                pos["unrealized_pnl"] = pnl
                total_mtm += pnl
                position_values[symbol] = {
                    "entry": entry_price,
                    "current": current_price,
                    "pnl": pnl,
                }

            except Exception as e:
                log.debug(f"Mark to market error for {symbol}: {e}")

        self.total_pnl = total_mtm

        return {
            "status": "success",
            "total_unrealized_pnl": round(total_mtm, 2),
            "positions": position_values,
            "capital": round(self.capital, 2),
        }

    def buy(
        self, symbol: str, price: float, quantity: int = 1, metadata: dict = None
    ) -> dict:
        """Simulate buy order"""
        # Reject invalid prices
        if price is None or price <= 0:
            log.warning(f"Attempted to buy with invalid price for {symbol}: {price}")
            return {"status": "error", "message": "Invalid price"}

        cost = price * quantity

        # Dynamic quantity adjustment if insufficient capital
        if cost > self.capital and self.capital > 0:
            max_qty = int(self.capital / price)
            quantity = max_qty
            cost = price * quantity
            log.info(f"Adjusted quantity for {symbol}: {quantity}")

        # Enforce minimum quantity of 1
        if quantity < 1:
            quantity = 1
            cost = price * quantity
            if cost > self.capital:
                log.warning(
                    f"Cannot afford minimum quantity 1 for {symbol}: cost {cost} > {self.capital}"
                )
                return {
                    "status": "error",
                    "message": "Insufficient capital for minimum quantity",
                }
            log.info(f"Set minimum quantity to 1 for {symbol}")

        self.capital -= cost

        # Skip if already has position in this symbol (FIFO - one position at a time)
        if symbol in self.positions and self.positions[symbol].get("quantity", 0) > 0:
            log.warning(f"Already have position in {symbol}, skipping new buy")
            self.capital += cost  # Refund the capital
            return {"status": "error", "message": "Position already exists"}
        else:
            self.positions[symbol] = {
                "action": "BUY",
                "entry": price,
                "quantity": quantity,
                "entry_time": datetime.datetime.now(),
                "metadata": metadata or {},
            }

        self.trades.append(
            {
                "symbol": symbol,
                "action": "BUY",
                "entry": price,
                "exit": None,
                "quantity": quantity,
                "pnl": 0,
                "time": datetime.datetime.now(),
                "metadata": metadata or {},
            }
        )

        log.info(
            f"[PAPER] BUY {quantity} {symbol} @ {price} | Strategy: {metadata.get('strategy', 'N/A')} | Reason: {metadata.get('reason', 'N/A')}"
        )

        return {
            "status": "success",
            "symbol": symbol,
            "action": "BUY",
            "price": price,
            "quantity": quantity,
        }

    def sell(
        self, symbol: str, price: float, quantity: int = 1, metadata: dict = None
    ) -> dict:
        """Simulate sell order"""
        if symbol not in self.positions:
            log.warning(f"No position to sell: {symbol}")
            return {"status": "error", "message": "No position"}

        pos = self.positions[symbol]

        # Skip if quantity is 0 (position already closed)
        if pos.get("quantity", 0) <= 0:
            log.warning(f"Position already closed for: {symbol}")
            del self.positions[symbol]
            return {"status": "error", "message": "Position already closed"}

        pos_action = pos.get("action", "BUY")

        if pos_action == "SELL":
            pnl = (pos["entry"] - price) * quantity
            self.capital -= price * quantity
        else:
            pnl = (price - pos["entry"]) * quantity
            self.capital += price * quantity

        # Estimate broker fees for both entry and exit and deduct from PnL and capital
        try:
            entry_fees = self.broker_fees.calculate_total(
                pos["entry"], quantity, pos_action
            )
            # Exit action is opposite of opening action
            exit_action = "SELL" if pos_action == "BUY" else "BUY"
            exit_fees = self.broker_fees.calculate_total(price, quantity, exit_action)
            fees_total = entry_fees.get("total", 0) + exit_fees.get("total", 0)
        except Exception:
            fees_total = 80  # conservative default if fee calc fails

        pnl_after_fees = pnl - fees_total
        # Subtract fees from capital as they are paid on execution
        self.capital -= fees_total

        log.info(
            f">>> CLOSING POSITION: {symbol} | Entry: {pos['entry']} | Exit: {price} | Qty: {quantity} | GrossPnL: {pnl:.2f} | Fees: {fees_total:.2f} | NetPnL: {pnl_after_fees:.2f}"
        )
        self.total_pnl += pnl_after_fees
        self.daily_pnl += pnl_after_fees

        self.trades.append(
            {
                "symbol": symbol,
                "action": pos_action,
                "entry": pos["entry"],
                "exit": price,
                "quantity": quantity,
                "pnl": pnl_after_fees,
                "fees": fees_total,
                "time": datetime.datetime.now(),
                "metadata": metadata or pos.get("metadata", {}),
            }
        )

        if pos["quantity"] > quantity:
            self.positions[symbol]["quantity"] -= quantity
        else:
            del self.positions[symbol]

        log.info(
            f"[PAPER] SELL {quantity} {symbol} @ {price} | Net PnL: {pnl_after_fees:.2f} | Strategy: {metadata.get('strategy', pos.get('metadata', {}).get('strategy', 'N/A'))} | Reason: {metadata.get('reason', pos.get('metadata', {}).get('reason', 'N/A'))}"
        )

        return {
            "status": "success",
            "symbol": symbol,
            "action": "SELL",
            "price": price,
            "quantity": quantity,
            "pnl": pnl_after_fees,
        }

    def sell_to_open(
        self, symbol: str, price: float, quantity: int = 1, metadata: dict = None
    ) -> dict:
        """Open a short position (sell to open)"""
        if price is None or price <= 0:
            log.warning(
                f"Attempted to open short with invalid price for {symbol}: {price}"
            )
            return {"status": "error", "message": "Invalid price"}

        if symbol in self.positions:
            log.warning(f"Position already exists for {symbol}, cannot open short")
            return {"status": "error", "message": "Position already exists"}

        credit = price * quantity
        self.capital += credit

        self.positions[symbol] = {
            "action": "SELL",
            "entry": price,
            "quantity": quantity,
            "entry_time": datetime.datetime.now(),
            "metadata": metadata or {},
        }

        self.trades.append(
            {
                "symbol": symbol,
                "action": "SELL",
                "entry": price,
                "exit": None,
                "quantity": quantity,
                "pnl": 0,
                "time": datetime.datetime.now(),
                "metadata": metadata or {},
            }
        )

        log.info(
            f"[PAPER] SELL TO OPEN {quantity} {symbol} @ {price:.2f} | Strategy: {metadata.get('strategy', 'N/A')} | Reason: {metadata.get('reason', 'N/A')}"
        )
        return {
            "status": "success",
            "symbol": symbol,
            "action": "SELL",
            "price": price,
            "quantity": quantity,
        }

    def get_positions(self) -> list[dict]:
        """Get current positions as list"""
        return [{"symbol": s, **p} for s, p in self.positions.items()]

    def get_trade_history(self) -> list[dict]:
        """Get all trades (closed and open)"""
        return self.trades

    def get_closed_trades(self) -> list[dict]:
        """Get only closed trades"""
        return [t for t in self.trades if t.get("exit") is not None]

    def update_position_metadata(self, symbol: str, updates: dict) -> bool:
        """Update metadata for an open position"""
        if symbol in self.positions:
            if "metadata" not in self.positions[symbol]:
                self.positions[symbol]["metadata"] = {}
            self.positions[symbol]["metadata"].update(updates)
            return True
        return False

    def get_unrealized_pnl(self, current_prices: dict[str, float]) -> float:
        """Calculate unrealized PnL"""
        unrealized = 0

        for symbol, pos in self.positions.items():
            current = current_prices.get(symbol, pos["entry"])

            if pos["action"] == "BUY":
                pnl = (current - pos["entry"]) * pos["quantity"]
            else:
                pnl = (pos["entry"] - current) * pos["quantity"]

            unrealized += pnl

        return unrealized

    def get_stats(self) -> dict:
        """Get simulation stats"""
        total_trades = len(self.trades)

        closed_trades = [t for t in self.trades if t.get("exit") is not None]

        if len(closed_trades) > 0:
            wins = sum(1 for t in closed_trades if t["pnl"] > 0)
            win_rate = wins / len(closed_trades)

            avg_win = sum(t["pnl"] for t in closed_trades if t["pnl"] > 0) / max(
                1, wins
            )
            avg_loss = abs(
                sum(t["pnl"] for t in closed_trades if t["pnl"] < 0)
                / max(1, len(closed_trades) - wins)
            )

            profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

            log.info("=== Trade Analysis ===")
            log.info(
                f"Closed: {len(closed_trades)}, Wins: {wins}, Losses: {len(closed_trades) - wins}"
            )
            log.info(f"Avg Win: {avg_win:.2f}, Avg Loss: {avg_loss:.2f}")
            log.info(f"Profit Factor: {profit_factor:.2f}")

            # Show recent losing trades
            losing = [t for t in closed_trades if t["pnl"] < 0][-5:]
            if losing:
                log.info("Recent losses:")
                for t in losing:
                    log.info(
                        f"  {t['symbol']}: entry={t['entry']} exit={t['exit']} pnl={t['pnl']:.2f} reason={t.get('metadata', {}).get('reason', 'unknown')}"
                    )
        else:
            wins = 0
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            profit_factor = 0

        return {
            "capital": round(self.capital, 2),
            "initial_capital": self.initial_capital,
            "pnl": round(self.total_pnl, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "total_trades": total_trades,
            "closed_trades": len(closed_trades),
            "wins": wins,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "positions": len(self.positions),
        }

    def reset_daily(self):
        """Reset daily PnL"""
        self.daily_pnl = 0

    def reset_all(self):
        """Reset all simulation state"""
        self.capital = self.initial_capital
        self.positions = {}
        self.trades = []
        self.daily_pnl = 0
        self.total_pnl = 0
        log.info("Simulation reset")


class BacktestEngine:
    """Historical backtesting engine"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.results = []

    def run(self, data: list[dict], strategy_func) -> dict:
        """Run backtest on historical data"""
        log.info(f"Starting backtest with {len(data)} data points")

        capital = self.config.get("capital", 100000)
        trades = []
        positions = {}

        for i, bar in enumerate(data):
            signal = strategy_func(bar)

            if not signal:
                continue

            action = signal.get("action")
            symbol = signal.get("symbol", "TEST")

            if action == "BUY" and symbol not in positions:
                positions[symbol] = {
                    "entry": bar.get("close", 0),
                    "quantity": signal.get("quantity", 1),
                }

            elif action == "SELL" and symbol in positions:
                pnl = (bar.get("close", 0) - positions[symbol]["entry"]) * positions[
                    symbol
                ]["quantity"]

                trades.append(
                    {
                        "entry": positions[symbol]["entry"],
                        "exit": bar.get("close", 0),
                        "pnl": pnl,
                    }
                )

                del positions[symbol]

        total_pnl = sum(t["pnl"] for t in trades)

        return {
            "total_trades": len(trades),
            "total_pnl": round(total_pnl, 2),
            "returns": round((total_pnl / capital) * 100, 2),
            "trades": trades,
        }

    def optimize_parameters(
        self, data: list[dict], strategy_func, param_ranges: dict
    ) -> dict:
        """Optimize strategy parameters"""
        log.info("Parameter optimization not yet implemented")
        return {"status": "not_implemented"}


# Singleton removed to prevent duplicate initialization
