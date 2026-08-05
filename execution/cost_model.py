# ═══════════════════════════════════════════════════════════════
#  Execution Cost & Slippage Modeling
#  Tracks fill prices, broker fees, and execution delays
# ═══════════════════════════════════════════════════════════════════════
import math
import random
from dataclasses import dataclass, field
from datetime import datetime

from quant_utils.logger import get_logger

log = get_logger("execution.cost")


@dataclass
class BrokerFees:
    brokerage_per_trade: float = 30.0
    stt: float = 0.001
    sebi_charge: float = 0.00001
    stamp_duty: float = 0.00002
    gst: float = 0.18
    transaction_charge: float = 0.00003
    exchange_fee: float = 0.00001

    def calculate_total(self, price: float, quantity: int, action: str) -> dict:
        turnover = price * quantity
        brokerage = max(self.brokerage_per_trade, turnover * 0.0003)
        brokerage_gst = brokerage * (1 + self.gst)

        sebi = turnover * self.sebi_charge
        stamp = turnover * self.stamp_duty if action == "BUY" else 0
        txn = turnover * self.transaction_charge
        exch = turnover * self.exchange_fee

        stt = turnover * self.stt if action == "SELL" else 0

        total_fees = brokerage_gst + sebi + stamp + txn + exch + stt

        return {
            "brokerage": brokerage,
            "brokerage_gst": brokerage_gst,
            "sebi": sebi,
            "stamp_duty": stamp,
            "transactionCharge": txn,
            "exchange_fee": exch,
            "stt": stt,
            "total": total_fees,
            "net_cost": total_fees,
            "effective_cost_pct": (total_fees / turnover * 100) if turnover > 0 else 0,
        }


@dataclass
class SlippageModel:
    mode: str = "variable"
    base_pips: float = 1.0
    min_pips: float = 0.5
    max_pips: float = 2.0
    volume_impact: float = 0.1
    volatility_impact: float = 0.2
    liquidity_factor: float = 0.5

    def get_slippage(
        self,
        price: float,
        action: str,
        volume: int = 1,
        market_volatility: float = 0.15,
        liquidity_score: float = 0.5,
    ) -> float:

        if self.mode == "fixed":
            pips = self.base_pips
        elif self.mode == "variable":
            pips = random.uniform(self.min_pips, self.max_pips)
        elif self.mode == "volume_based":
            volume_adj = 1 + math.log1p(volume) * self.volume_impact
            pips = self.base_pips * volume_adj
        elif self.mode == "dynamic":
            vol_adj = 1 + math.log1p(volume) * self.volume_impact
            vola_adj = 1 + market_volatility * self.volatility_impact
            liq_penalty = 1 - liquidity_score * self.liquidity_factor
            pips = self.base_pips * vol_adj * vola_adj * liq_penalty
        else:
            pips = 0

        slippage_value = price * (pips / 10000)

        if action == "BUY":
            return price + slippage_value
        return price - slippage_value

    def get_slippage_info(self) -> dict:
        return {
            "mode": self.mode,
            "base_pips": self.base_pips,
            "expected_pct": self.base_pips / 10000,
        }


@dataclass
class ExecutionDelay:
    mode: str = "fixed"
    base_delay_ms: int = 500
    min_delay_ms: int = 200
    max_delay_ms: int = 2000
    network_factor: float = 0.3
    market_factor: float = 0.5

    def get_delay(
        self, market_volatility: float = 0.15, is_market_open: bool = True
    ) -> float:
        if self.mode == "fixed":
            return self.base_delay_ms / 1000

        delay = random.uniform(self.min_delay_ms, self.max_delay_ms)

        if not is_market_open:
            delay *= 1 + self.market_factor

        delay *= 1 + market_volatility * self.market_factor

        return delay / 1000


@dataclass
class FillPrice:
    symbol: str
    action: str
    quote_price: float
    fill_price: float
    slippage_pips: float
    delay_seconds: float
    timestamp: datetime = field(default_factory=datetime.now)
    fills: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "quote_price": self.quote_price,
            "fill_price": self.fill_price,
            "slippage": self.fill_price - self.quote_price,
            "slippage_pips": self.slippage_pips,
            "delay_seconds": self.delay_seconds,
            "timestamp": self.timestamp.isoformat(),
        }


class ExecutionCostTracker:
    def __init__(self, config: dict = None):
        self.config = config or {}

        self.broker_fees = BrokerFees(
            brokerage_per_trade=self.config.get("brokerage", 30),
            stt=self.config.get("stt", 0.001),
        )

        self.slippage_model = SlippageModel(
            mode=self.config.get("slippage_mode", "variable"),
            base_pips=self.config.get("slippage_pips", 1.0),
            min_pips=self.config.get("min_slippage_pips", 0.5),
            max_pips=self.config.get("max_slippage_pips", 2.0),
        )

        self.execution_delay = ExecutionDelay(
            mode=self.config.get("delay_mode", "variable"),
            base_delay_ms=self.config.get("base_delay_ms", 500),
        )

        self.fills_history: list[FillPrice] = []
        self.total_slippage = 0
        self.total_fees = 0
        self.total_delays = 0

    def simulate_fill(
        self,
        symbol: str,
        action: str,
        quote_price: float,
        quantity: int = 1,
        market_volatility: float = 0.15,
        liquidity_score: float = 0.5,
        is_market_open: bool = True,
    ) -> dict:

        delay = self.execution_delay.get_delay(market_volatility, is_market_open)

        fill_price = self.slippage_model.get_slippage(
            quote_price, action, quantity, market_volatility, liquidity_score
        )

        slippage = fill_price - quote_price
        slippage_pips = (slippage / quote_price) * 10000

        fees = self.broker_fees.calculate_total(fill_price, quantity, action)

        fill_record = FillPrice(
            symbol=symbol,
            action=action,
            quote_price=quote_price,
            fill_price=fill_price,
            slippage_pips=slippage_pips,
            delay_seconds=delay,
        )

        self.fills_history.append(fill_record)
        self.total_slippage += abs(slippage) * quantity
        self.total_fees += fees["total"]
        self.total_delays += delay

        return {
            "symbol": symbol,
            "action": action,
            "quote_price": quote_price,
            "fill_price": fill_price,
            "slippage": slippage,
            "slippage_pips": round(slippage_pips, 2),
            "delay_seconds": round(delay, 3),
            "fees": fees,
            "total_cost": fees["total"] + abs(slippage) * quantity,
        }

    def get_fill_statistics(self) -> dict:
        if not self.fills_history:
            return {"no_fills": True}

        total_fills = len(self.fills_history)

        avg_slippage = self.total_slippage / total_fills
        avg_delay = self.total_delays / total_fills
        avg_fees = self.total_fees / total_fills

        slippage_by_action = {"BUY": [], "SELL": []}
        for fill in self.fills_history:
            slippage_by_action[fill.action].append(
                abs(fill.fill_price - fill.quote_price)
            )

        avg_slippage_buy = sum(slippage_by_action["BUY"]) / max(
            1, len(slippage_by_action["BUY"])
        )
        avg_slippage_sell = sum(slippage_by_action["SELL"]) / max(
            1, len(slippage_by_action["SELL"])
        )

        return {
            "total_fills": total_fills,
            "total_slippage": round(self.total_slippage, 2),
            "total_fees": round(self.total_fees, 2),
            "total_delays": round(self.total_delays, 3),
            "avg_slippage": round(avg_slippage, 2),
            "avg_slippage_buy": round(avg_slippage_buy, 2),
            "avg_slippage_sell": round(avg_slippage_sell, 2),
            "avg_delay_seconds": round(avg_delay, 3),
            "avg_fees": round(avg_fees, 2),
            "slippage_model": self.slippage_model.get_slippage_info(),
        }

    def reset(self):
        self.fills_history = []
        self.total_slippage = 0
        self.total_fees = 0
        self.total_delays = 0


def calculate_total_execution_cost(
    price: float, quantity: int, action: str, slippage_pips: float = 1.0
) -> dict:
    """Calculate total execution cost including all fees"""
    fees = BrokerFees()

    slippage_amount = price * slippage_pips / 10000

    fee_details = fees.calculate_total(price, quantity, action)

    return {
        "price": price,
        "slippage": slippage_amount,
        "fees": fee_details,
        "total_cost": fee_details["total"] + slippage_amount * quantity,
        "break_even_pct": (
            (fee_details["total"] + slippage_amount * quantity) / (price * quantity)
        )
        * 100,
    }


def model_fill_price(
    quote_price: float,
    action: str,
    option_type: str = "CE",
    strikes: list[int] = None,
    position_type: str = "single",
    lot_size: int = 50,
) -> dict:
    """Model realistic fill price for different strategy types"""

    if position_type == "single":
        slippage_pips = 1.0
    elif position_type == "iron_butterfly":
        slippage_pips = 1.5
    elif position_type == "iron_condor":
        slippage_pips = 2.0
    else:
        slippage_pips = 1.0

    base_slippage = quote_price * slippage_pips / 10000

    if action == "BUY":
        fill_price = quote_price + base_slippage
    else:
        fill_price = quote_price - base_slippage

    return {
        "quote_price": quote_price,
        "fill_price": round(fill_price, 2),
        "slippage_pips": slippage_pips,
        "slippage_amount": round(base_slippage, 2),
    }
