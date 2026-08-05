# ═══════════════════════════════════════════════════════════════
#  Capital Allocator (Dynamic Position Sizing)
# ═══════════════════════════════════════════════════════════════

from quant_utils.logger import get_logger

log = get_logger("capital_allocator")


class CapitalAllocator:
    """
    Dynamic capital allocation based on confidence and volatility
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.base_capital = self.config.get("base_capital", 300000)
        self.max_capital_per_trade = self.config.get("max_capital_per_trade", 50000)
        self.risk_per_trade = self.config.get("risk_per_trade", 0.01)

        self.current_capital = self.base_capital
        self.peak_capital = self.base_capital
        self.trough_capital = self.base_capital

    def size_position(
        self, confidence: float, volatility: float, option_price: float = 100
    ) -> dict:
        """
        Calculate position size based on confidence and volatility

        Returns:
            dict with lots, quantity, capital_used, risk_amount
        """
        risk_factor = confidence / (volatility + 1)

        allocation = self.current_capital * risk_factor * 0.05

        allocation = min(allocation, self.max_capital_per_trade)

        lots = max(1, int(allocation / (option_price * 25)))

        quantity = lots * 25

        capital_used = quantity * option_price

        risk_amount = self.current_capital * self.risk_per_trade

        result = {
            "lots": lots,
            "quantity": quantity,
            "capital_used": capital_used,
            "risk_amount": risk_amount,
            "risk_pct": (
                risk_amount / self.current_capital if self.current_capital > 0 else 0
            ),
        }

        log.info(
            f"Position sized: {lots} lots | ₹{capital_used:,.0f} | Risk: ₹{risk_amount:,.0f}"
        )

        return result

    def adjust_for_drawdown(self) -> float:
        """
        Adjust capital allocation based on drawdown
        Returns: multiplier (0-1)
        """
        if self.peak_capital == 0:
            return 1.0

        drawdown = (self.peak_capital - self.current_capital) / self.peak_capital

        if drawdown > 0.20:
            log.warning("Drawdown > 20%, reducing position size by 50%")
            return 0.5
        elif drawdown > 0.10:
            log.warning("Drawdown > 10%, reducing position size by 25%")
            return 0.75

        return 1.0

    def adjust_for_profit(self) -> float:
        """
        Adjust capital allocation based on profit
        Returns: multiplier (1+)
        """
        profit_pct = (self.current_capital - self.base_capital) / self.base_capital

        if profit_pct > 0.50:
            log.info("Profit > 50%, increasing position size by 25%")
            return 1.25
        elif profit_pct > 0.25:
            log.info("Profit > 25%, increasing position size by 15%")
            return 1.15

        return 1.0

    def update_capital(self, pnl: float):
        """Update capital after trade"""
        self.current_capital += pnl

        self.peak_capital = max(self.peak_capital, self.current_capital)

        self.trough_capital = min(self.trough_capital, self.current_capital)

    def get_available_capital(self) -> float:
        """Get available capital for trading"""
        return self.current_capital * 0.95

    def get_capital_stats(self) -> dict:
        """Get capital statistics"""
        return {
            "current_capital": self.current_capital,
            "peak_capital": self.peak_capital,
            "trough_capital": self.trough_capital,
            "profit": self.current_capital - self.base_capital,
            "profit_pct": (
                (self.current_capital - self.base_capital) / self.base_capital
                if self.base_capital > 0
                else 0
            ),
        }

    def calculate_kelly_position_size(
        self, win_rate: float, risk_reward_ratio: float, current_capital: float
    ) -> dict:
        """
        Calculate position size using Kelly Criterion formula.

        Kelly Criterion: f* = (win_rate - (1-win_rate)/risk_reward_ratio)
        where f* is the optimal fraction of capital to risk.

        Args:
            win_rate: Fraction of winning trades (0 < win_rate < 1)
            risk_reward_ratio: Ratio of average win to average loss (> 1)
            current_capital: Current available capital for trading

        Returns:
            Dict with kelly_fraction, position_size, max_position, validation_passed

        Raises:
            ValueError: If any parameter validation fails
        """
        # Validation
        if not (0 < win_rate < 1):
            log.error(
                f"Invalid win_rate: {win_rate}. Must be between 0 and 1 (exclusive)"
            )
            raise ValueError(f"win_rate must be > 0 and < 1, got {win_rate}")

        if risk_reward_ratio <= 1:
            log.error(f"Invalid risk_reward_ratio: {risk_reward_ratio}. Must be > 1")
            raise ValueError(f"risk_reward_ratio must be > 1, got {risk_reward_ratio}")

        if current_capital <= 0:
            log.error(f"Invalid current_capital: {current_capital}. Must be > 0")
            raise ValueError(f"current_capital must be > 0, got {current_capital}")

        try:
            # Calculate Kelly fraction
            kelly_fraction = win_rate - ((1 - win_rate) / risk_reward_ratio)

            # Cap between 1% and 10% for safety (fractional Kelly)
            kelly_fraction_capped = min(0.1, max(0.01, kelly_fraction))

            # Calculate position size
            position_size = kelly_fraction_capped * current_capital

            # Ensure position size doesn't exceed max_capital_per_trade
            max_position = min(position_size, self.max_capital_per_trade)

            log.info(
                f"Kelly sizing: win_rate={win_rate:.2%}, rr_ratio={risk_reward_ratio:.2f}, "
                f"kelly_fraction={kelly_fraction:.4f}, capped={kelly_fraction_capped:.4f}, "
                f"position_size=₹{max_position:,.0f} / ₹{current_capital:,.0f}"
            )

            return {
                "kelly_fraction": kelly_fraction,
                "kelly_fraction_capped": kelly_fraction_capped,
                "position_size": max_position,
                "max_position": self.max_capital_per_trade,
                "capital_available": current_capital,
                "validation_passed": True,
            }

        except Exception as e:
            log.error(f"Kelly calculation failed: {e}")
            raise

    def _calculate_risk_reward_ratio(self, strategy_trades: list) -> float:
        """
        Calculate risk/reward ratio from strategy trade history.

        Args:
            strategy_trades: List of trade dicts with 'pnl' and 'won' keys

        Returns:
            float: Average winning trade / average losing trade ratio
        """
        if not strategy_trades:
            return 1.5  # Default conservative ratio

        winning_trades = [
            t["pnl"] for t in strategy_trades if t.get("won", False) and t["pnl"] > 0
        ]
        losing_trades = [
            t["pnl"]
            for t in strategy_trades
            if not t.get("won", False) and t["pnl"] < 0
        ]

        if not winning_trades or not losing_trades:
            return 1.5  # Default if insufficient data

        avg_win = sum(winning_trades) / len(winning_trades)
        avg_loss = abs(sum(losing_trades) / len(losing_trades))

        if avg_loss == 0:
            return 1.5

        rr_ratio = avg_win / avg_loss
        return max(1.1, min(5.0, rr_ratio))  # Cap between 1.1 and 5.0

    def allocate_capital(
        self, strategy_name: str, strategy_tracker=None, use_kelly: bool = False
    ) -> dict:
        """
        Allocate capital for a strategy, optionally using Kelly Criterion.

        Args:
            strategy_name: Name of the strategy
            strategy_tracker: StrategyPerformanceTracker instance (optional)
            use_kelly: If True, use Kelly sizing; otherwise use default sizing

        Returns:
            Dict with allocation_result, kelly_used, available_capital
        """
        available_capital = self.get_available_capital()

        if available_capital <= 0:
            log.warning(
                f"Insufficient capital for {strategy_name}: ₹{available_capital:,.0f}"
            )
            return {
                "allocation_result": None,
                "kelly_used": False,
                "available_capital": available_capital,
                "error": "Insufficient available capital",
            }

        # Try Kelly sizing if enabled and tracker available
        if use_kelly and strategy_tracker:
            try:
                win_rate = strategy_tracker.get_win_rate(strategy_name)

                # Only use Kelly if we have enough data (at least 5 trades)
                if strategy_name in strategy_tracker.strategy_trades:
                    trades = strategy_tracker.strategy_trades[strategy_name]
                    if len(trades) >= 5:
                        # Calculate risk/reward from actual trades
                        rr_ratio = self._calculate_risk_reward_ratio(trades)

                        kelly_result = self.calculate_kelly_position_size(
                            win_rate=win_rate,
                            risk_reward_ratio=rr_ratio,
                            current_capital=self.current_capital,
                        )

                        # Validate against available funds
                        if kelly_result["position_size"] <= available_capital:
                            log.info(
                                f"Allocated ₹{kelly_result['position_size']:,.0f} to {strategy_name} "
                                f"using Kelly (win_rate={win_rate:.2%}, rr={rr_ratio:.2f})"
                            )
                            return {
                                "allocation_result": kelly_result,
                                "kelly_used": True,
                                "available_capital": available_capital,
                                "strategy_name": strategy_name,
                            }
                        else:
                            log.warning(
                                f"Kelly allocation exceeds available funds. "
                                f"Requested: ₹{kelly_result['position_size']:,.0f}, "
                                f"Available: ₹{available_capital:,.0f}. Using default sizing."
                            )

            except (ValueError, ZeroDivisionError) as e:
                log.warning(
                    f"Kelly sizing failed for {strategy_name}: {e}. Using default sizing."
                )
            except Exception as e:
                log.error(
                    f"Unexpected error in Kelly allocation for {strategy_name}: {e}"
                )

        # Fall back to default sizing
        allocation = min(available_capital, self.max_capital_per_trade)

        log.info(
            f"Allocated ₹{allocation:,.0f} to {strategy_name} "
            f"(Kelly: {use_kelly}, Tracker: {strategy_tracker is not None})"
        )

        return {
            "allocation_result": {
                "position_size": allocation,
                "max_position": self.max_capital_per_trade,
                "capital_available": available_capital,
            },
            "kelly_used": False,
            "available_capital": available_capital,
            "strategy_name": strategy_name,
        }
