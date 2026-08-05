# ═══════════════════════════════════════════════════════════════
#  Signal Validation — Centralized signal quality & safety checks
# ═══════════════════════════════════════════════════════════════
from dataclasses import dataclass, field

from quant_utils.logger import get_logger

log = get_logger("validation.signal")


@dataclass
class ValidationResult:
    """Result of signal validation"""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence_boost: float = 0.0  # Positive adjustment to signal confidence

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)


class SignalValidator:
    """Centralized validator for all trading signals"""

    # Configurable thresholds
    MIN_ENTRY_PRICE = 1.0  # Minimum price (e.g., ₹1 for equity)
    MAX_ENTRY_PRICE = 1_000_000  # Prevent typos (₹10 lakh cap)
    MIN_RISK_REWARD = 1.0  # Minimum reward:risk ratio (1:1 acceptable)
    MAX_STOP_LOSS_PCT = 0.10  # Max stop loss distance (10%)
    MIN_CONFIDENCE = 0.1  # Minimum confidence score
    MAX_LEVERAGE = 3.0  # Max position leverage
    PRICE_TOLERANCE = 0.01  # 1% deviation warning threshold
    BROKER_FEE_PER_SIDE = 20  # Broker fee: ₹20 per side (entry + exit = ₹40 total)
    MIN_PROFIT_AFTER_FEES = 0  # Minimum profit after broker fees (₹0)

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        # Override defaults from config
        for key, value in self.config.items():
            if hasattr(self, key.upper()):
                setattr(self, key.upper(), value)

    # ═══════════════════════════════════════════════════════════════
    #  Entry point — validate complete signal
    # ═══════════════════════════════════════════════════════════════
    def validate(self, signal: dict, context: dict | None = None) -> ValidationResult:
        """
        Full signal validation pipeline

        Args:
            signal: Signal dictionary with all fields
            context: Optional market/portfolio context (positions, capital, etc.)

        Returns:
            ValidationResult with is_valid flag and detailed errors/warnings
        """
        result = ValidationResult(is_valid=True)
        context = context or {}

        # Pipeline: structure → data sanity → risk checks → optional chain
        self._validate_structure(signal, result)
        if not result.is_valid:
            return result

        self._validate_data_sanity(signal, result)
        self._validate_risk_params(signal, result)
        self._validate_duplicate_position(signal, context, result)

        # Options-specific checks (if applicable)
        if signal.get("type") == "OPTIONS":
            self._validate_options_specific(signal, result, context)

        # Multi-leg validation
        if signal.get("legs"):
            self._validate_multileg(signal, result)

        return result

    # ═══════════════════════════════════════════════════════════════
    #  1. Structure validation — required fields, types, enums
    # ═══════════════════════════════════════════════════════════════
    def _validate_structure(self, signal: dict, result: ValidationResult):
        """Check required fields and data types"""

        # Base required fields for all signals
        required_fields = ["symbol", "action", "strategy"]

        # Conditional required fields:
        # - For options with legs: legs array required, entry optional (filled at execution)
        # - For equity (or non-legs): entry and quantity required
        signal_type = signal.get("type", "").upper()
        has_legs = bool(signal.get("legs"))

        if signal_type == "OPTIONS" and has_legs:
            # Options multi-leg: legs required; entry optional
            required_fields.append("legs")
        else:
            # Equity or single-leg options without legs: need entry and quantity
            required_fields.extend(["entry", "quantity"])

        for field in required_fields:
            if field not in signal:
                result.add_error(f"Missing required field: '{field}'")

        # If critical fields missing, bail early
        if not result.is_valid:
            return

        # Symbol validation
        symbol = signal.get("symbol", "")
        if not isinstance(symbol, str) or len(symbol) < 2:
            result.add_error("Invalid symbol: must be non-empty string")

        # Action enum
        action = signal.get("action", "").upper()
        if action not in ["BUY", "SELL"]:
            result.add_error(f"Invalid action '{action}': must be BUY or SELL")

        # Entry price type and range (if present)
        entry = signal.get("entry")
        if entry is not None:
            if not isinstance(entry, (int, float)):
                result.add_error("Entry price must be numeric")
            elif entry < self.MIN_ENTRY_PRICE:
                result.add_error(
                    f"Entry price ₹{entry:.2f} below minimum ₹{self.MIN_ENTRY_PRICE}"
                )
            elif entry > self.MAX_ENTRY_PRICE:
                result.add_error(
                    f"Entry price ₹{entry:.2f} exceeds maximum ₹{self.MAX_ENTRY_PRICE}"
                )

        # Strategy name
        strategy = signal.get("strategy", "")
        if not isinstance(strategy, str) or len(strategy) < 3:
            result.add_error("Invalid strategy name")

        # Quantity sanity (if present)
        quantity = signal.get("quantity", 0)
        if quantity is not None and not isinstance(quantity, int):
            result.add_error("Quantity must be integer")
        elif quantity <= 0:
            result.add_warning(f"Quantity {quantity} should be positive")

    # ═══════════════════════════════════════════════════════════════
    #  2. Data sanity — NaN, inf, nulls, sanity bounds
    # ═══════════════════════════════════════════════════════════════
    def _validate_data_sanity(self, signal: dict, result: ValidationResult):
        """Detect malformed numeric data and impossible values"""

        # Fields to validate if present
        numeric_fields = ["entry", "target", "stop_loss", "confidence", "quantity"]
        for field in numeric_fields:
            if field not in signal:
                continue
            val = signal[field]
            if not isinstance(val, (int, float)):
                result.add_error(f"Field '{field}' must be numeric, got {type(val)}")
                continue
            # Check for NaN or inf
            if val != val or val in (float("inf"), float("-inf")):
                result.add_error(f"Field '{field}' has invalid numeric value: {val}")

        # Confidence bounds
        confidence = signal.get("confidence", 0.5)
        if confidence < 0 or confidence > 1:
            result.add_error(f"Confidence {confidence:.2f} must be between 0 and 1")
        elif confidence < self.MIN_CONFIDENCE:
            result.add_warning(
                f"Low confidence: {confidence:.2f} < {self.MIN_CONFIDENCE}"
            )

        # Quantity sanity (if present and non-zero)
        quantity = signal.get("quantity", 0)
        if quantity and quantity > 10000:  # Prevent typos like 100000
            result.add_warning(f"Unusually large quantity: {quantity}")

    # ═══════════════════════════════════════════════════════════════
    #  3. Risk parameter validation — reward:risk, stop distances
    # ═══════════════════════════════════════════════════════════════
    def _validate_risk_params(self, signal: dict, result: ValidationResult):
        """Ensure risk parameters are sensible and within limits"""

        # For options strategies, risk parameters are less standardized; skip strict R:R check
        sig_type = signal.get("type", "").upper()
        if sig_type == "OPTIONS":
            # Only basic sanity: stop_loss and target should be > 0 if present
            sl = signal.get("stop_loss", 0)
            tgt = signal.get("target", 0)
            if sl and tgt:
                if sl <= 0 or tgt <= 0:
                    result.add_warning("Options stop_loss/target should be positive")

            # BROKER FEE CHECK: Ensure profit > ₹20 after broker fees (₹40 total for entry + exit)
            entry = signal.get("entry", 0)
            quantity = signal.get("quantity", 1)
            action = signal.get("action", "").upper()

            if entry > 0 and tgt > 0 and quantity > 0:
                total_broker_fees = 40  # ₹20 entry + ₹20 exit

                if action == "BUY":
                    gross_profit = (tgt - entry) * quantity
                else:  # SELL
                    gross_profit = (entry - tgt) * quantity

                net_profit = gross_profit - total_broker_fees

                if net_profit < self.MIN_PROFIT_AFTER_FEES:
                    result.add_error(
                        f"Insufficient profit: Gross ₹{gross_profit:.2f} - Broker fees ₹{total_broker_fees} = "
                        f"Net ₹{net_profit:.2f} < minimum ₹{self.MIN_PROFIT_AFTER_FEES}. "
                        f"Need wider target distance or larger lot size."
                    )
            return

        entry = signal.get("entry", 0)
        stop_loss = signal.get("stop_loss", 0)
        target = signal.get("target", 0)
        action = signal.get("action", "").upper()

        # Skip if fields not provided (some strategies omit them)
        if stop_loss == 0 or target == 0:
            result.add_warning("Missing stop_loss or target — risk cannot be assessed")
            return

        if entry <= 0:
            return  # Already flagged in structure check

        # Calculate risk and reward
        if action == "BUY":
            risk = entry - stop_loss
            reward = target - entry
        else:  # SELL
            risk = stop_loss - entry
            reward = entry - target

        # Avoid division by zero / negative risk
        if risk <= 0:
            result.add_error(
                f"Invalid stop loss for {action}: entry={entry}, sl={stop_loss}"
            )

        if reward <= 0:
            result.add_error(
                f"Invalid target for {action}: target must be {'higher' if action == 'BUY' else 'lower'} than entry"
            )

        if risk > 0 and reward > 0:
            rr_ratio = reward / risk
            if rr_ratio < self.MIN_RISK_REWARD:
                result.add_error(
                    f"Risk-reward {rr_ratio:.2f} below minimum {self.MIN_RISK_REWARD}: "
                    f"reward=₹{reward:.2f}, risk=₹{risk:.2f}"
                )
            elif rr_ratio < 2.0:
                result.add_warning(f"Modest risk-reward ratio: {rr_ratio:.2f}")

            # BROKER FEE CHECK: Ensure profit > ₹20 after broker fees (₹40 total for entry + exit)
            quantity = signal.get("quantity", 1)
            total_broker_fees = 40  # ₹20 entry + ₹20 exit
            gross_profit = reward * quantity
            net_profit = gross_profit - total_broker_fees

            if net_profit < self.MIN_PROFIT_AFTER_FEES:
                result.add_error(
                    f"Insufficient profit: Gross ₹{gross_profit:.2f} - Broker fees ₹{total_broker_fees} = "
                    f"Net ₹{net_profit:.2f} < minimum ₹{self.MIN_PROFIT_AFTER_FEES}. "
                    f"Need wider target distance or larger lot size."
                )

        # Stop loss distance as percentage
        if entry > 0 and risk > 0:
            sl_pct = risk / entry
            if sl_pct > self.MAX_STOP_LOSS_PCT:
                result.add_error(
                    f"Stop loss too wide: {sl_pct:.1%} > {self.MAX_STOP_LOSS_PCT:.1%}"
                )
            elif sl_pct > 0.05:
                result.add_warning(f"Wide stop loss: {sl_pct:.1%}")

        # Additional risk check: Max potential loss should be meaningful vs broker fees
        if risk > 0:
            total_broker_fees = 40
            if risk < total_broker_fees / 2:  # Risk < ₹20
                result.add_warning(
                    f"Small risk distance ₹{risk:.2f} relative to broker fees ₹{total_broker_fees}"
                )

    # ═══════════════════════════════════════════════════════════════
    #  4. Duplicate position check
    # ═══════════════════════════════════════════════════════════
    def _validate_duplicate_position(
        self, signal: dict, context: dict, result: ValidationResult
    ):
        """Detect if we already have an open position in this symbol"""

        symbol = signal.get("symbol", "")
        existing_positions = context.get("positions", [])

        for pos in existing_positions:
            pos_symbol = pos.get("symbol", "")
            # For options, need exact match; for equity, symbol match
            if pos_symbol == symbol:
                result.add_warning(
                    f"Already have position in {symbol} — skipping duplicate"
                )
                # Not an error, but flag it
                break

    # ═══════════════════════════════════════════════════════════════
    #  5. Options-specific validation — strikes, expiry, liquidity
    # ═══════════════════════════════════════════════════════════
    def _validate_options_specific(
        self, signal: dict, result: ValidationResult, context: dict
    ):
        """Extra checks for options signals"""

        # Check option premium data quality BEFORE accepting trade
        entry = signal.get("entry", 0)
        if entry and entry > 0:
            # Reject suspiciously small premiums that indicate stale/corrupt data
            if entry < 0.05:
                result.add_error(
                    f"Invalid option premium: ₹{entry:.3f} is unrealistically small. "
                    f"Indicates stale or corrupted data. Rejecting trade."
                )
            elif entry < 0.20:
                result.add_warning(
                    f"Small option premium: ₹{entry:.3f} — verify data freshness. "
                    f"May lead to rapid expiration to zero."
                )

        # Check metadata for premium lookup status
        metadata = signal.get("metadata", {})
        price_source = metadata.get("price_source", "unknown")

        if price_source == "entry":
            result.add_warning(
                "Option premium unavailable — using entry price as fallback. "
                "Consider rejecting trade and retrying after data refresh."
            )
        elif "entry" in price_source.lower():
            result.add_warning(
                f"Option premium data quality uncertain (source: {price_source}). "
                f"Verify current market price before execution."
            )

        legs = signal.get("legs", [])
        if not legs:
            # Single-leg options may not have a legs array; this is acceptable
            # The execution path will handle single-leg options differently
            return

        # Validate each leg
        for i, leg in enumerate(legs):
            strike = leg.get("strike", 0)
            opt_type = leg.get("opt_type", "")
            action = leg.get("action", "").upper()

            # Strike must be positive integer (round number for indices)
            if not isinstance(strike, (int, float)) or strike <= 0:
                result.add_error(f"Leg {i}: Invalid strike price {strike}")

            # Option type
            if opt_type not in ["CE", "PE"]:
                result.add_error(
                    f"Leg {i}: Invalid opt_type '{opt_type}' — must be CE or PE"
                )

            # Action
            if action not in ["BUY", "SELL"]:
                result.add_error(f"Leg {i}: Invalid action '{action}'")

            # Strike roundness check (for Indian indices, typically 50 or 100)
            if strike % 50 != 0:
                result.add_warning(
                    f"Leg {i}: Strike {strike} not a multiple of 50 — may be illiquid"
                )

        # Net credit/debit sanity check
        total_cost = 0
        for leg in legs:
            premium = leg.get("premium", 0)
            action = leg.get("action", "").upper()
            multiplier = -1 if action == "SELL" else 1
            total_cost += premium * multiplier

        if total_cost > 0:
            result.add_warning(
                f"Net debit of ₹{total_cost:.2f} — paying premium, check max loss"
            )
        else:
            self.add_info(f"Net credit of ₹{abs(total_cost):.2f} — max profit limited")

    # ═══════════════════════════════════════════════════════════════
    #  6. Multi-leg strategy validation
    # ═══════════════════════════════════════════════════════════
    def _validate_multileg(self, signal: dict, result: ValidationResult):
        """Validate complex multi-leg strategies (iron condor, butterfly, etc.)"""

        legs = signal.get("legs", [])
        strategy = signal.get("strategy", "")

        if len(legs) < 2:
            result.add_error("Multi-leg strategy needs at least 2 legs")
            return

        # Check strike ordering
        strikes = [leg.get("strike", 0) for leg in legs]
        if all(s > 0 for s in strikes):
            # For defined-risk strategies, strikes should be ordered
            if "BUTTERFLY" in strategy or "CONDOR" in strategy:
                sorted_strikes = sorted(strikes)
                if strikes != sorted_strikes:
                    result.add_warning(
                        "Strikes not sorted — verify option chain ordering"
                    )

        # Validate max loss calculation is present
        if "max_loss" not in signal:
            result.add_warning("Max loss not specified — compute before execution")

        # Break-even calculation check
        if "break_even_upper" in signal and "break_even_lower" in signal:
            upper = signal["break_even_upper"]
            lower = signal["break_even_lower"]
            if lower >= upper:
                result.add_error("Invalid break-even range: lower must be < upper")

    def add_info(self, msg: str):
        """Add informational message (not error or warning)"""
        # Could log only; ValidationResult doesn't have info level
        log.info(f"[SignalValidator] {msg}")


# ═══════════════════════════════════════════════════════════════
#  Helper functions — quick checks used inline
# ═══════════════════════════════════════════════════════════════
def validate_price(
    price: float, min_val: float = 1.0, max_val: float = 1e6
) -> tuple[bool, str]:
    """Quick price sanity check"""
    if price < min_val:
        return False, f"Price ₹{price:.2f} below minimum ₹{min_val}"
    if price > max_val:
        return False, f"Price ₹{price:.2f} exceeds maximum ₹{max_val}"
    if price != price or price in (float("inf"), float("-inf")):
        return False, f"Price has invalid numeric value: {price}"
    return True, ""


def validate_risk_reward(
    entry: float,
    target: float,
    stop_loss: float,
    min_rr: float = 1.5,
    action: str = "BUY",
) -> tuple[bool, str]:
    """Validate risk-reward ratio"""
    if action.upper() == "BUY":
        risk = entry - stop_loss
        reward = target - entry
    else:
        risk = stop_loss - entry
        reward = entry - target

    if risk <= 0:
        return False, f"Invalid risk direction: entry={entry}, sl={stop_loss}"
    if reward <= 0:
        return False, f"Invalid reward direction: entry={entry}, target={target}"

    rr = reward / risk
    if rr < min_rr:
        return False, f"Risk-reward {rr:.2f} below minimum {min_rr}"
    return True, f"RR={rr:.2f}"


def validate_signal_complete(
    signal: dict, required: list[str] = None
) -> tuple[bool, list[str]]:
    """Quick check for required fields"""
    required = required or ["symbol", "action", "entry", "strategy"]
    missing = [f for f in required if f not in signal or signal[f] is None]
    return (len(missing) == 0, missing)


# Singleton instance for convenience
_default_validator: SignalValidator | None = None


def get_validator(config: dict | None = None) -> SignalValidator:
    """Get singleton or new validator instance"""
    global _default_validator
    if _default_validator is None:
        _default_validator = SignalValidator(config)
    return _default_validator
