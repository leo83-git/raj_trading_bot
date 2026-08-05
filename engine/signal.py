# ═══════════════════════════════════════════════════════════════
#  Strategy Engine — Options & Equity Strategies
# ═══════════════════════════════════════════════════════════════

from quant_utils.logger import get_logger

log = get_logger("engine.strategy")


class StrategyEngine:
    """Multi-strategy trading engine"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def generate_signal(
        self, symbol: str, features: dict, vol_signal: float = 0, ob_signal: float = 0
    ) -> dict | None:
        """Generate trading signal from multiple inputs"""

        strategy_type = self._select_strategy(features)

        if strategy_type == "INTRADAY_BREAKOUT":
            return self._intraday_breakout(symbol, features)
        elif strategy_type == "SWING":
            return self._swing_trade(symbol, features)
        elif strategy_type == "OPTIONS_SHORT_STRANGLE":
            return self._short_strangle(symbol, features)
        elif strategy_type == "OPTIONS_IRON_CONDOR":
            return self._iron_condor(symbol, features)
        elif strategy_type == "OPTIONS_IRON_BUTTERFLY":
            return self._iron_butterfly(symbol, features)
        elif strategy_type == "OPTIONS_LONG_CALL":
            return self._long_call(symbol, features)

        return None

    def _select_strategy(self, features: dict) -> str:
        """Auto-select strategy based on conditions"""
        volatility = features.get("volatility") or 0
        trend = features.get("trend", "SIDEWAYS")
        rsi = features.get("rsi") or 50

        if volatility and volatility > 25:
            return "OPTIONS_SHORT_STRANGLE"
        elif volatility and volatility > 15:
            return "OPTIONS_IRON_CONDOR"
        elif volatility and volatility > 8:
            return "OPTIONS_IRON_BUTTERFLY"
        elif trend == "UPTREND" and rsi < 60:
            return "INTRADAY_BREAKOUT"
        elif trend == "SIDEWAYS":
            return "SWING"

        return "OPTIONS_LONG_CALL"

    def _intraday_breakout(self, symbol: str, features: dict) -> dict | None:
        """Intraday breakout strategy"""
        price = features.get("close") or 0
        high_20d = features.get("high_20d") or price

        if price and high_20d and price >= high_20d * 0.98:
            return {
                "symbol": symbol,
                "type": "EQUITY",
                "action": "BUY",
                "entry": price,
                "strategy": "INTRADAY_BREAKOUT",
                "target": price * 1.02,
                "stop_loss": price * 0.98,
                "confidence": 0.7,
            }
        return None

    def _swing_trade(self, symbol: str, features: dict) -> dict | None:
        """Swing trading strategy"""
        price = features.get("close") or 0
        sma_21 = features.get("sma_21") or price

        if price and sma_21 and price > sma_21:
            return {
                "symbol": symbol,
                "type": "EQUITY",
                "action": "BUY",
                "entry": price,
                "strategy": "SWING",
                "target": price * 1.05,
                "stop_loss": price * 0.97,
                "confidence": 0.6,
            }
        return None

    def _short_strangle(self, symbol: str, features: dict) -> dict | None:
        """Short strangle options strategy"""
        spot = features.get("close") or 0

        if not spot or spot <= 0:
            return None

        atm_strike = round(spot / 50) * 50
        call_strike = atm_strike + 200
        put_strike = atm_strike - 200

        return {
            "symbol": symbol,
            "type": "OPTIONS",
            "strategy": "SHORT_STRANGLE",
            "legs": [
                {
                    "symbol": f"{symbol}{call_strike}CE",
                    "action": "SELL",
                    "strike": call_strike,
                    "opt_type": "CE",
                },
                {
                    "symbol": f"{symbol}{put_strike}PE",
                    "action": "SELL",
                    "strike": put_strike,
                    "opt_type": "PE",
                },
            ],
            "confidence": 0.6,
        }

    def _iron_condor(self, symbol: str, features: dict) -> dict | None:
        """Iron condor options strategy"""
        spot = features.get("close", 0)

        if spot <= 0:
            return None

        atm_strike = round(spot / 50) * 50
        otm_call = atm_strike + 200
        otm_put = atm_strike - 200
        further_call = atm_strike + 400
        further_put = atm_strike - 400

        return {
            "symbol": symbol,
            "type": "OPTIONS",
            "strategy": "IRON_CONDOR",
            "legs": [
                {
                    "symbol": f"{symbol}{further_call}CE",
                    "action": "SELL",
                    "strike": further_call,
                    "opt_type": "CE",
                },
                {
                    "symbol": f"{symbol}{otm_call}CE",
                    "action": "BUY",
                    "strike": otm_call,
                    "opt_type": "CE",
                },
                {
                    "symbol": f"{symbol}{otm_put}PE",
                    "action": "SELL",
                    "strike": otm_put,
                    "opt_type": "PE",
                },
                {
                    "symbol": f"{symbol}{further_put}PE",
                    "action": "BUY",
                    "strike": further_put,
                    "opt_type": "PE",
                },
            ],
            "confidence": 0.65,
        }

    def _iron_butterfly(self, symbol: str, features: dict) -> dict | None:
        """
        Iron Butterfly options strategy
        - Sell 1 ATM Call + Sell 1 ATM Put (short straddle)
        - Buy 1 OTM Call + Buy 1 OTM Put (wings/hedge)
        Best for: Low volatility, sideways market
        """
        spot = features.get("close", 0)

        if spot <= 0:
            return None

        atm_strike = round(spot / 50) * 50

        upper_wing = atm_strike + 300
        lower_wing = atm_strike - 300

        return {
            "symbol": symbol,
            "type": "OPTIONS",
            "strategy": "IRON_BUTTERFLY",
            "legs": [
                {
                    "symbol": f"{symbol}{atm_strike}CE",
                    "action": "SELL",
                    "strike": atm_strike,
                    "opt_type": "CE",
                },
                {
                    "symbol": f"{symbol}{atm_strike}PE",
                    "action": "SELL",
                    "strike": atm_strike,
                    "opt_type": "PE",
                },
                {
                    "symbol": f"{symbol}{upper_wing}CE",
                    "action": "BUY",
                    "strike": upper_wing,
                    "opt_type": "CE",
                },
                {
                    "symbol": f"{symbol}{lower_wing}PE",
                    "action": "BUY",
                    "strike": lower_wing,
                    "opt_type": "PE",
                },
            ],
            "max_profit_at": atm_strike,
            "break_even_upper": atm_strike + 300,
            "break_even_lower": atm_strike - 300,
            "confidence": 0.7,
        }

    def calculate_iron_butterfly_payoff(
        self,
        spot_price: float,
        atm_strike: int,
        upper_wing: int,
        lower_wing: int,
        call_credit: float,
        put_credit: float,
        call_debit: float,
        put_debit: float,
    ) -> dict:
        """
        Calculate Iron Butterfly payoff at expiry
        Returns: profit/loss at given spot price
        """
        net_credit = (call_credit + put_credit) - (call_debit + put_debit)

        if spot_price >= atm_strike and spot_price <= atm_strike:
            pnl = net_credit * 1
        elif spot_price > atm_strike:
            loss = min(spot_price - atm_strike, upper_wing - atm_strike)
            pnl = net_credit - loss
        elif spot_price < atm_strike:
            loss = min(atm_strike - spot_price, atm_strike - lower_wing)
            pnl = net_credit - loss
        else:
            pnl = net_credit

        return {
            "spot_price": spot_price,
            "pnl": pnl,
            "pnl_pct": (pnl / net_credit * 100) if net_credit > 0 else 0,
            "in_profit_zone": lower_wing < spot_price < upper_wing,
        }

    def get_iron_butterfly_premium_estimate(
        self, spot_price: float, iv: float = 15
    ) -> dict:
        """Estimate premiums for Iron Butterfly legs using simplified model"""
        atm_strike = round(spot_price / 50) * 50
        upper_wing = atm_strike + 300
        lower_wing = atm_strike - 300

        moneyness_mult = 0.04
        atm_premium = spot_price * moneyness_mult * (iv / 100) * 0.3
        otm_premium = atm_premium * 0.5

        return {
            "atm_call_credit": atm_premium,
            "atm_put_credit": atm_premium,
            "otm_call_debit": otm_premium,
            "otm_put_debit": otm_premium,
            "net_credit": (atm_premium * 2) - (otm_premium * 2),
            "max_profit": (atm_premium * 2) - (otm_premium * 2),
            "max_loss": upper_wing
            - atm_strike
            - ((atm_premium * 2) - (otm_premium * 2)),
        }

    def _long_call(self, symbol: str, features: dict) -> dict | None:
        """Long call option strategy"""
        spot = features.get("close") or 0
        rsi = features.get("rsi") or 50

        if not spot or spot <= 0:
            return None

        if rsi and rsi < 35:
            atm_strike = round(spot / 50) * 50

            return {
                "symbol": symbol,
                "type": "OPTIONS",
                "strategy": "LONG_CALL",
                "legs": [
                    {
                        "symbol": f"{symbol}{atm_strike}CE",
                        "action": "BUY",
                        "strike": atm_strike,
                        "opt_type": "CE",
                    }
                ],
                "confidence": 0.7,
            }
        return None

    def fuse_signals(self, signals: list[dict]) -> dict | None:
        """Fuse multiple signals into one decision"""
        if not signals:
            return None

        buy_signals = sum(1 for s in signals if s.get("action") == "BUY")
        sell_signals = sum(1 for s in signals if s.get("action") == "SELL")

        total = buy_signals + sell_signals
        if total == 0:
            return None

        confidence = max(buy_signals, sell_signals) / total

        if buy_signals > sell_signals:
            action = "BUY"
        elif sell_signals > buy_signals:
            action = "SELL"
        else:
            return None

        return {"action": action, "confidence": confidence, "signal_count": total}


_engine_instance = None


def get_strategy_engine() -> "SignalGenerator":
    """Get singleton signal generator"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = SignalGenerator()
    return _engine_instance


def generate_signal(
    symbol: str, features: dict, vol_signal: float = 0, ob_signal: float = 0
) -> dict | None:
    """Generate trading signal"""
    return get_strategy_engine().generate_signal(
        symbol, features, vol_signal, ob_signal
    )
