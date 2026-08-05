#!/usr/bin/env python3
"""Strategy Engine MVP: premium_pct option mapping + SR hook"""

import logging
from typing import Dict, Optional

log = logging.getLogger("strategy.engine")

try:
    from strategy.sr_levels import get_sr_levels
except Exception:
    get_sr_levels = None  # type: ignore


class StrategyEngine:
    def __init__(self, config: dict | None = None, data_provider=None):
        self.config = config or {}
        self.data_provider = data_provider
        # Allow min_confidence to be provided either at top-level or under thresholds
        self.min_confidence = self.config.get(
            "min_confidence",
            self.config.get("thresholds", {}).get("min_confidence", 0.08),
        )
        self.risk_reward_ratio = self.config.get("risk_reward_ratio", 1.0)
        self.sr_config = self.config.get("sr_levels", {})
        self.sr_enabled = bool(self.sr_config.get("enabled", False))
        self.sr_method = self.sr_config.get("method", "classic")
        self.sr_lookback = int(self.sr_config.get("lookback", 20))

    def generate_signal(
        self, symbol: str, stock_data: dict, ml_signal: float, dl_signal: float = 0
    ) -> dict | None:
        price = stock_data.get(
            "close", stock_data.get("price", stock_data.get("last_price", 0))
        )
        category = stock_data.get("category", "intraday")
        if not price or price <= 0:
            log.warning(f"No valid price for {symbol}")
            return None
        ensemble_score = ml_signal
        if abs(ensemble_score) < self.min_confidence:
            log.info(
                f"Signal too weak for {symbol}: {ensemble_score:.3f} < {self.min_confidence}"
            )
            return None
        action = "BUY" if ensemble_score > 0 else "SELL"
        atr = stock_data.get("atr", price * 0.015)
        sl_distance = atr * 1.5
        stop_loss = price - sl_distance if action == "BUY" else price + sl_distance
        target_distance = sl_distance * self.risk_reward_ratio
        target = price + target_distance if action == "BUY" else price - target_distance
        metadata = {}
        if self.sr_enabled and get_sr_levels is not None:
            price_history = stock_data.get("price_history")
            try:
                sr_levels = get_sr_levels(
                    symbol,
                    price_history,
                    method=self.sr_method,
                    lookback=self.sr_lookback,
                )
                metadata["sr_levels"] = sr_levels
            except Exception:
                pass

        # Position sizing
        base_capital = float(self.config.get("base_capital", 300000))
        risk_per_trade = float(self.config.get("risk_per_trade", 0.02))
        max_capital_per_trade = float(self.config.get("max_capital_per_trade", 100000))

        quantity = 1
        if category == "fno":
            # Use fixed lot sizes for known indices
            lot_map = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60}
            quantity = int(lot_map.get(symbol, 1))
        else:
            # Risk-based sizing for equity/intraday
            risk_amount = base_capital * risk_per_trade
            risk_per_share = abs(price - stop_loss)
            if risk_per_share <= 0:
                risk_per_share = max(1.0, price * 0.01)

            try:
                raw_qty = int(risk_amount / float(risk_per_share))
            except Exception:
                raw_qty = 1

            raw_qty = max(1, raw_qty)

            # Caps
            cap_by_capital = (
                int(max_capital_per_trade / price) if price > 0 else raw_qty
            )
            cap_by_maxpos = int(base_capital * 0.30 / price) if price > 0 else raw_qty
            quantity = max(1, min(raw_qty, cap_by_capital, cap_by_maxpos))

        return {
            "action": action,
            "symbol": symbol,
            "entry": round(price, 2),
            "stop_loss": round(stop_loss, 2),
            "target": round(target, 2),
            "quantity": int(quantity),
            "confidence": 0.5,
            "reason": "model",
            "metadata": metadata,
            "type": "EQUITY",
            "strategy": "ML_ENSEMBLE",
        }

    def generate_options_signal(
        self, symbol: str, stock_data: dict, model_signal: float
    ) -> dict | None:
        equity_signal = self.generate_signal(symbol, stock_data, model_signal)
        if not equity_signal:
            return None
        spot = equity_signal["entry"]
        strike = round(spot / 100) * 100
        expiry_days = 7
        volatility = stock_data.get("volatility", 20) / 100
        leg_type = "CE" if equity_signal["action"] == "BUY" else "PE"

        premium = self._get_option_premium(symbol, strike, leg_type)
        if premium <= 0:
            premium = max(spot * 0.25, 1.0)
            log.debug(
                f"Using fallback premium proxy for {symbol} {strike}{leg_type}: ₹{premium:.2f}"
            )
        else:
            log.debug(
                f"Using live option premium for {symbol} {strike}{leg_type}: ₹{premium:.2f}"
            )

        target_pct = 0.04
        stop_pct = 0.02
        metadata = equity_signal.get("metadata", {})
        if isinstance(metadata, dict) and self.sr_enabled:
            metadata.setdefault("sr_levels", {})
        if equity_signal.get("metadata"):
            metadata = equity_signal["metadata"]
        if equity_signal["action"] == "BUY":
            target = premium * (1 + target_pct)
            stop_loss = premium * (1 - stop_pct)
        else:
            target = premium * (1 - target_pct)
            stop_loss = premium * (1 + stop_pct)
        return {
            "action": equity_signal["action"],
            "symbol": symbol,
            "entry": spot,
            "stop_loss": stop_loss,
            "target": target,
            "quantity": 1,
            "confidence": equity_signal["confidence"],
            "reason": equity_signal["reason"],
            "type": "OPTIONS",
            "strategy": "OPTIONS_ML",
            "legs": [
                {
                    "symbol": f"{symbol}{int(strike)}{leg_type}",
                    "strike": int(strike),
                    "opt_type": leg_type,
                    "action": equity_signal["action"],
                    "expiry_days": expiry_days,
                    "volatility": volatility,
                    "premium": premium,
                }
            ],
            "metadata": metadata,
        }

    def validate_signal(self, signal: dict, portfolio_state: dict) -> bool:
        return True

    def _get_option_premium(
        self, underlying: str, strike: float, opt_type: str
    ) -> float:
        """Get option premium from data provider"""
        try:
            if self.data_provider is None:
                return 0.0

            chain = self.data_provider.get_option_chain(underlying)
            if not chain:
                return 0.0

            data = chain.get("data", {})
            records = data.get("records", {}) if isinstance(data, dict) else {}
            options_list = records.get("data", []) if isinstance(records, dict) else []

            if not isinstance(options_list, list):
                return 0.0

            target_strike = float(strike)
            for opt in options_list:
                if not isinstance(opt, dict):
                    continue
                strike_price = (
                    opt.get("strikePrice")
                    or opt.get("strike_price")
                    or opt.get("strike")
                )
                try:
                    if (
                        strike_price is not None
                        and abs(float(strike_price) - target_strike) < 1
                    ):
                        side = (
                            opt.get(opt_type)
                            or opt.get(opt_type.lower())
                            or opt.get(opt_type.upper())
                        )
                        if isinstance(side, dict):
                            premium = (
                                side.get("lastPrice")
                                or side.get("ltp")
                                or side.get("price")
                            )
                            if premium is not None:
                                premium = float(premium)
                                if premium > 0:
                                    return premium
                        premium = (
                            opt.get("lastPrice") or opt.get("ltp") or opt.get("price")
                        )
                        if premium is not None:
                            premium = float(premium)
                            if premium > 0:
                                return premium
                except (TypeError, ValueError):
                    continue
        except Exception as e:
            log.debug(
                f"Failed to get option premium for {underlying} {strike}{opt_type}: {e}"
            )

        return 0.0
