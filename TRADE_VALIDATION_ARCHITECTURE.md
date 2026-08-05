# Trading System - Trade Validation & Entry Architecture

Complete map of where trades are validated, sized, executed, and P&L is calculated.

---

## 1. SIGNAL VALIDATION & ENTRY DECISION

### A. Signal Validator (Central Entry Point)
**File:** [risk/signal_validator.py](risk/signal_validator.py)

#### Main Validation Class
- **Class:** `SignalValidator` (Line 27)
- **Entry Method:** `validate(signal: Dict, context: Dict)` → ValidationResult (Line 49)

#### Validation Pipeline (Lines 49-80):
1. **Structure Validation** (Line 74) - Check required fields, data types, enums
   - Required fields: `symbol`, `action`, `strategy`
   - Conditional: `entry`, `quantity` OR `legs` (for multi-leg options)
   - Validates: Action is BUY/SELL, symbol non-empty, entry price in range

2. **Data Sanity Check** (Line 75) - Detect NaN, inf, malformed data
   - Checks: `entry`, `target`, `stop_loss`, `confidence`, `quantity`
   - Confidence must be 0-1
   - Validates numeric bounds and prevents typos

3. **Risk Parameter Validation** (Line 76) - Reward:risk ratio, stop distances
   - **File:** [risk/signal_validator.py](risk/signal_validator.py#L212-L265)
   - **Key Logic:**
   ```
   For BUY: risk = entry - stop_loss, reward = target - entry
   For SELL: risk = stop_loss - entry, reward = entry - target
   
   Checks:
   - Min Risk-Reward Ratio: 1.0 (default in MIN_RISK_REWARD at Line 34)
   - Max Stop Loss Distance: 10% of entry (MAX_STOP_LOSS_PCT at Line 36)
   - Warns if R:R < 2.0 (Line 254)
   - Errors if R:R < 1.0 (Line 250)
   - Errors if S/L too wide: > 10% (Line 259)
   ```

4. **Duplicate Position Check** (Line 77) - Prevent re-entering same symbol
   - **File:** [risk/signal_validator.py](risk/signal_validator.py#L269-L285)
   - Scans existing positions from context
   - Warns if position already open in symbol

5. **Options-Specific Validation** (Line 78-81)
   - Strike price validation
   - Expiry date validation
   - Liquidity checks
   - Multi-leg structure validation

#### Risk-Reward Calculation Functions
**File:** [risk/signal_validator.py](risk/signal_validator.py#L355-L375)

```python
def validate_risk_reward(entry: float, target: float, stop_loss: float, 
                        min_rr: float = 1.5, action: str = "BUY") -> Tuple[bool, str]:
    """Calculates R:R ratio and validates against minimum threshold"""
    
    For BUY:
    - risk = entry - stop_loss
    - reward = target - entry
    
    For SELL:
    - risk = stop_loss - entry
    - reward = entry - target
    
    Returns: (is_valid, message)
```

---

## 2. RISK MANAGEMENT GATES

### A. Daily Loss/Profit Limits
**File:** [risk/risk_manager.py](risk/risk_manager.py#L1-100)

**Daily PnL Tracking:**
- `check_daily_loss_limit(current_pnl)` (Line 40-44)
  - Max Daily Loss: `-₹15,000` (configurable)
  - Triggers KILL_SWITCH if breached
  
- `check_daily_profit_limit(current_pnl)` (Line 46-50)
  - Max Daily Profit: `+₹50,000` (configurable)
  - Stops trading at profit target

### B. Drawdown Control
**File:** [risk/risk_manager.py](risk/risk_manager.py#L52-58)

- `check_drawdown(peak_capital, current_capital)`
- Max Drawdown: 20% (configurable)
- Triggers KILL_SWITCH if exceeded

### C. Position Limits
**File:** [risk/risk_manager.py](risk/risk_manager.py#L60-70)

- `check_position_limit(current_positions)` (Line 60-64)
  - Max Open Positions: 20 (configurable)
  - Rejects new trade if limit reached

- `check_exposure_limit(exposure_pct)` (Line 72-76)
  - Max Exposure: 30% of capital (configurable)
  - Rejects high exposure positions

### D. Kill Switch (Consecutive Loss Limits)
**File:** [risk/controls/__init__.py](risk/controls/__init__.py#L122-180)

**KillSwitch Class:**
- `record_trade(pnl)` (Line 122-129)
  - Tracks consecutive losses
  - Increments counter on losing trade
  - Resets on winning trade

- `check()` (Line 131-138)
  - Max Consecutive Losses: 5 (configurable)
  - Triggers stop if exceeded

---

## 3. POSITION SIZING & KELLY CRITERION

### A. Capital Allocator (Position Sizing)
**File:** [portfolio/capital_allocator.py](portfolio/capital_allocator.py)

#### Size Position Method
- **Method:** `size_position(confidence, volatility, option_price)` (Line 26-50)
- **Returns:** dict with `lots`, `quantity`, `capital_used`, `risk_amount`
- **Logic:**
  ```
  risk_factor = confidence / (volatility + 1)
  allocation = current_capital * risk_factor * 0.05
  allocation = min(allocation, max_capital_per_trade)
  lots = int(allocation / (option_price * 25))
  quantity = lots * 25
  ```

#### Kelly Criterion Position Sizing
- **Method:** `calculate_kelly_position_size(win_rate, risk_reward_ratio, current_capital)` 
  (Line 119-177)
  
- **Kelly Formula:** `f* = win_rate - ((1-win_rate)/risk_reward_ratio)`
  
- **Constraints:**
  - Kelly fraction capped: 1% to 10% (fractional Kelly for safety)
  - Position size = capped_kelly_fraction × current_capital
  - Max position limited to `max_capital_per_trade` (₹50,000 default)
  
- **Validation Returns:**
  ```python
  {
      "kelly_fraction": raw_kelly_value,
      "kelly_fraction_capped": 0.01 - 0.10,
      "position_size": actual_capital_to_risk,
      "max_position": hard_limit,
      "validation_passed": bool,
  }
  ```

#### Drawdown Adjustment
- **Method:** `adjust_for_drawdown()` (Line 52-63)
  - Drawdown > 20%: Reduce position size by 50%
  - Drawdown > 10%: Reduce position size by 25%
  - Normal: 1.0× multiplier

#### Profit Adjustment
- **Method:** `adjust_for_profit()` (Line 65-75)
  - Profit > 50%: Increase position size by 25%
  - Profit > 25%: Increase position size by 15%
  - Normal: 1.0× multiplier

---

## 4. EXECUTION ENGINE

### A. Order Placement with Failover
**File:** [execution/execution_engine.py](execution/execution_engine.py)

**ExecutionEngine Class:** (Line 11)

- **Single Leg Order:** `place_order(symbol, exchange, token, transaction_type, quantity, order_type, price)` 
  (Line 31-62)
  - Attempts to place on primary broker
  - Falls back to backup brokers if primary fails
  - Logs order to history

- **Multi-Leg Order:** `place_multi_leg_order(legs, producttype)` (Line 64-86)
  - For options spreads, straddles, butterflies
  - Places each leg with 0.2s delay between legs

---

## 5. PORTFOLIO MANAGEMENT & P&L CALCULATION

### A. Position Tracking
**File:** [portfolio/portfolio_manager.py](portfolio/portfolio_manager.py)

**Position Class:**
- **Constructor:** Lines 15-42
- **Key Fields:**
  - `entry_price`: Entry price at which position was opened
  - `quantity`: Number of contracts
  - `sl`: Stop loss level
  - `target`: Profit target level
  - `direction`: "BULLISH" or "BEARISH"
  - `strategy`: Strategy name that generated signal
  - `confidence`: Signal confidence score

#### Stop Loss & Target Hit Detection
- **Method:** `is_sl_hit(ltp)` (Line 111-115)
  ```python
  For BULLISH: return ltp <= self.sl
  For BEARISH: return ltp >= self.sl
  ```
  
- **Method:** `is_target_hit(ltp)` (Line 117-121)
  ```python
  For BULLISH: return ltp >= self.target
  For BEARISH: return ltp <= self.target
  ```

### B. P&L Calculation
**File:** [portfolio/portfolio_manager.py](portfolio/portfolio_manager.py)

#### Current P&L (Unrealized)
- **Method:** `current_pnl(ltp)` (Line 109)
  ```python
  return (ltp - self.entry_price) * self.quantity
  ```
  - For LONG: profit if ltp > entry_price
  - For SHORT: profit if ltp < entry_price

#### Position Close & Realized P&L
- **Method:** `close_position(position, exit_price, reason)` (Line 159-177)
  ```python
  position.exit_price = exit_price
  position.exit_time = datetime.now()
  position.exit_reason = reason
  position.status = "CLOSED"
  position.pnl = position.current_pnl(exit_price)  # REALIZED P&L
  
  self.daily_pnl += position.pnl
  
  if position.pnl > 0:
      self.win_count += 1
  else:
      self.loss_count += 1
  ```
  - **Exit Reasons:** "SL_HIT", "TARGET_HIT", "TIME_EXIT", "MANUAL"

#### Portfolio P&L Summary
- **Method:** `get_pnl_summary()` (Line 211-222)
  ```python
  Returns:
  {
    "daily_pnl": daily_accumulated,
    "total_pnl": all_trades,
    "total_trades": count,
    "win_count": winning_trades,
    "loss_count": losing_trades,
    "win_rate": win_count/total_trades
  }
  ```

---

## 6. RISK PARAMETER CALCULATIONS

### A. Risk-Reward Ratio Function
**File:** [risk/risk_engine.py](risk/risk_engine.py#L425-428)

```python
def calculate_risk_reward(entry: float, target: float, stop_loss: float) -> float:
    risk = entry - stop_loss
    reward = target - entry
    return reward / risk if risk > 0 else 0
```

### B. Stop Loss Calculation
**File:** [risk/risk_engine.py](risk/risk_engine.py#L421-423)

```python
def calculate_stop_loss(entry: float, risk_pct: float = 0.02) -> float:
    return entry * (1 - risk_pct)
    # Default: 2% below entry
```

### C. Position Size Based on Risk
**File:** [risk/risk_engine.py](risk/risk_engine.py#L418-420)

```python
def calculate_position_size(
    capital: float, price: float, leverage: float = 1.0, risk_pct: float = 0.02
) -> int:
    max_position_value = capital * leverage * risk_pct
    return int(max_position_value / price)
```

---

## 7. CORRELATION & LEVERAGE RISK

### A. Leverage Limits
**File:** [risk/risk_engine.py](risk/risk_engine.py#L207-305)

**LeverageManager Class:**
- **Max Leverage:** 3.0× (configurable)
- **Warning Leverage:** 2.5× (80% of max)

- **Method:** `can_add_position(positions, new_position, total_capital)` (Line 254-268)
  ```python
  current_leverage = total_exposure / total_capital
  new_leverage = new_position_value / total_capital
  total_leverage = current_leverage + new_leverage
  
  REJECT if: total_leverage > 3.0×
  WARN if: current_leverage > 2.5×
  ```

### B. Correlation Risk
**File:** [risk/risk_engine.py](risk/risk_engine.py#L160-206)

**CorrelationManager Class:**
- **Max Correlation:** 0.7 (configurable)
- **Max Sector Concentration:** 30% (configurable)

- **Method:** `recommend_diversification(positions, new_symbol)` (Line 193-206)
  ```python
  For each existing position:
    - Get correlation to new_symbol
    - Average all correlations
  
  RECOMMEND ADD if: avg_correlation <= 0.7
  REJECT if: avg_correlation > 0.7
  ```

### C. VIX-Based Hedging
**File:** [risk/risk_engine.py](risk/risk_engine.py#L307-370)

**VIXHedgeManager Class:**
- **Hedge Threshold:** VIX > 20.0
- **Max Hedge Ratio:** 0.25 (max 25% portfolio hedged)

- **Method:** `should_hedge()` (Line 345-346)
  ```python
  if current_vix > hedge_threshold:
      hedge_needed = (vix_excess / 100) × portfolio_beta
      self.hedge_ratio = min(hedge_needed, 0.25)
      return True
  ```

---

## 8. COMPLETE TRADE ENTRY DECISION FLOW

```
┌─ Signal Generated (e.g., from strategy engine)
│
├─ 1. SIGNAL VALIDATION [signal_validator.py:49]
│   ├─ Structure Check: Symbol, Action, Entry, Quantity
│   ├─ Data Sanity: NaN/Inf detection, type checking
│   ├─ Risk Parameters: R:R ratio >= 1.0, S/L distance <= 10%
│   ├─ Duplicate Check: No existing position in symbol
│   └─ OPTIONS: Multi-leg validation if applicable
│
├─ 2. DAILY LIMITS CHECK [risk_manager.py]
│   ├─ Daily Loss: current_pnl > -15,000?
│   ├─ Daily Profit: current_pnl < +50,000?
│   ├─ Drawdown: (peak - current)/peak < 20%?
│   └─ Time Check: Within 9:15-15:00?
│
├─ 3. POSITION LIMITS CHECK [risk_manager.py]
│   ├─ Position Count: open_positions < 20?
│   ├─ Exposure: portfolio_exposure < 30%?
│   └─ Kill Switch: consecutive_losses < 5?
│
├─ 4. KILL SWITCH CHECK [controls/__init__.py:131]
│   └─ Consecutive Losses: loss_count < 5?
│
├─ 5. LEVERAGE CHECK [risk_engine.py:254]
│   └─ Total Leverage: (current + new) < 3.0×?
│
├─ 6. CORRELATION CHECK [risk_engine.py:193]
│   └─ Avg Correlation: to existing positions <= 0.7?
│
├─ 7. POSITION SIZE [capital_allocator.py:119]
│   ├─ Kelly Criterion: f* = win_rate - ((1-win_rate)/R:R)
│   ├─ Capped: 1% to 10%
│   ├─ Drawdown Adjustment: multiply by 0.5-1.0
│   └─ Profit Adjustment: multiply by 1.0-1.25
│
└─ 8. EXECUTION [execution_engine.py:31]
    └─ Place Order on Broker(s)

IF ANY CHECK FAILS → REJECT & LOG REASON
```

---

## 9. KEY CONFIGURATION PARAMETERS

**File:** [config/config.yaml](config/config.yaml)

```yaml
risk_management:
  paper_trading: true
  max_daily_loss: 15000           # Stop-loss limit
  max_daily_profit: 50000         # Take-profit limit
  max_drawdown: 0.20              # 20% max drawdown
  max_positions: 20               # Max open positions
  max_exposure: 0.30              # 30% max portfolio exposure
  max_latency_ms: 500             # Max latency tolerance

position_sizing:
  base_capital: 300000
  max_capital_per_trade: 50000    # Max capital per position
  risk_per_trade: 0.01            # 1% risk per trade

risk_reward:
  min_risk_reward_ratio: 1.5      # Minimum R:R ratio
  max_stop_loss_pct: 0.10         # 10% max stop distance
  min_confidence: 0.1             # 10% min signal confidence

correlation:
  max_correlation: 0.7            # 70% max correlation
  max_sector_size: 0.30           # 30% max sector concentration
  
leverage:
  max_leverage: 3.0               # 3× max leverage
  
vix_hedging:
  vix_threshold: 20.0             # Start hedging when VIX > 20
```

---

## 10. TEST COVERAGE

**Test Files:**
- [tests/test_risk_calculations.py](tests/test_risk_calculations.py) - Signal validation, R:R tests
- [tests/test_position_sizing.py](tests/test_position_sizing.py) - Position sizing, Kelly tests

**Key Test Cases:**
- Valid/Invalid R:R ratios (Lines 176-202 in test_risk_calculations.py)
- Stop loss validation (Lines 194-202)
- Position size calculations (Lines 72-140 in test_position_sizing.py)
- Kelly criterion bounds (Lines 117-140)

---

## Summary

| Component | Location | Decision Logic |
|-----------|----------|---|
| **Signal Validation** | signal_validator.py:49 | R:R >= 1.0, S/L <= 10%, structure checks |
| **Daily Limits** | risk_manager.py:40-76 | Loss <= -15k, Profit >= +50k, Drawdown <= 20% |
| **Position Limits** | risk_manager.py:60-76 | Positions <= 20, Exposure <= 30% |
| **Kill Switch** | controls/__init__.py:131 | Consecutive losses <= 5 |
| **Leverage Risk** | risk_engine.py:254 | Total leverage <= 3.0× |
| **Correlation Risk** | risk_engine.py:193 | Avg correlation <= 0.7 |
| **Position Sizing** | capital_allocator.py:119 | Kelly criterion, 1-10% cap, adjusted by drawdown |
| **Execution** | execution_engine.py:31 | Broker failover, market/limit orders |
| **P&L Calculation** | portfolio_manager.py:109 | (LTP - entry_price) × quantity |
| **Close Position** | portfolio_manager.py:159 | Exit at S/L, Target, or manual |
