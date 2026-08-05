# Symbol Tracking & Retry System

## Overview

The symbol tracking system monitors unknown stock symbols that fail to fetch prices, analyzes why they're failing, attempts corrections, and retries them periodically. This ensures no symbol is silently abandoned without investigation.

## Architecture

### Components

1. **SymbolTracker** (sources/broker/__init__.py)
   - Tracks failed symbol attempts
   - Records error reasons
   - Maintains successful corrections
   - Provides retry queue management
   - Generates tracking reports

2. **Symbol Correction Engine** (NSELiveBroker methods)
   - `_suggest_symbol_corrections()`: Generates alternative symbol formats
   - `_try_symbol_correction()`: Attempts to fetch with corrected formats

3. **Integration Points** (RajTradingBot in main.py)
   - `_check_and_retry_failed_symbols()`: Periodic retry and reporting
   - Called once per trading cycle (~every 60 seconds)

## How It Works

### Phase 1: Quote Fetch & Failure Tracking

When `NSELiveBroker.get_quote(symbol)` is called:

```
1. Try NSEKit MCP (if enabled)
2. Check if index/option (special handling)
3. Check if known stock (direct API)
4. Try API for unknown symbol
   ├─ Success? → Return quote
   └─ Fail? → Try symbol corrections
5. Try symbol format corrections
   ├─ Success? → Track correction, return quote
   └─ Fail? → Try yfinance fallback
6. Try yfinance
   ├─ Success? → Mark as successful, return quote
   └─ Fail? → Track failure with suggestions, return None
```

### Phase 2: Automatic Retry & Reporting

Every trading cycle, `_check_and_retry_failed_symbols()` executes:

- **Every 15 cycles (~15 minutes)**:
  - Prints human-readable tracking report
  - Shows all failed symbols and their reasons
  - Lists successful symbol corrections
  - Suggests corrected formats to try

- **Every 30 cycles (~30 minutes)**:
  - Retries up to 20 failed symbols
  - Attempts corrections and alternative formats
  - Reports success/failure counts
  - Updates tracking database

## Symbol Correction Strategies

When a symbol fails, NSELiveBroker suggests these correction formats:

```python
# Original symbol: "M&M"
Suggestions:
  1. "M&M.NS"    # Add NSE listing suffix
  2. "M&M.BO"    # Add Bombay Stock Exchange suffix
  3. "MANNM"     # Replace & with N
  4. "MANNM.NS"  # Corrected + NSE suffix
```

Common transformations:
- Remove special characters: `-`, `_`, space
- Add `.NS` suffix (NSE listing)
- Add `.BO` suffix (BSE listing)
- Replace `&` with similar letters

## Data Model

### SymbolTracker.failed_symbols

```python
{
    "SYMBOLNAME": {
        "attempt_count": 3,
        "last_error": "All methods failed",
        "errors": [
            "jugaad-data returned 404",
            "NseIndiaApi returned no data",
            "yfinance returned None",
        ],
        "corrected_formats": ["SYMBOLNAME.NS", "SYMBOLNAME.BO", "SYMBOLNAME_CORRECTED"],
        "last_attempt_time": 1690234567.89,
    }
}
```

### SymbolTracker.successful_corrections

```python
{
    "M&M": "MANNM",  # Original → Successful correction
    "HDFC": "HDFC.NS",  # Add suffix
}
```

### SymbolTracker.retry_queue

List of symbols pending retry:
```python
["SYMBOLNAME1", "SYMBOLNAME2", "SYMBOLNAME3"]
```

## API Reference

### NSELiveBroker Methods

#### `retry_failed_symbols(max_symbols: int = 10) -> Dict`
Retry fetching quotes for previously failed symbols.

**Returns:**
```python
{
    "retried": 10,  # Total symbols attempted
    "successful": 3,  # Successfully fetched
    "still_failed": 7,  # Still failing
}
```

#### `get_symbol_tracking_report() -> Dict`
Get raw tracking data.

**Returns:**
```python
{
    "total_failed": 15,
    "total_retry_queue": 12,
    "corrected_symbols": {"M&M": "MANNM", "HDFC": "HDFC.NS"},
    "failed_symbols": {
        "UNKNOWN1": {
            "attempt_count": 3,
            "last_error": "All methods failed",
            "total_errors": 3,
            "corrected_formats": ["UNKNOWN1.NS", "UNKNOWN1.BO"],
        }
    },
}
```

#### `print_symbol_tracking_report()`
Print human-readable report to logs.

**Sample Output:**
```
WARNING: === SYMBOL TRACKING REPORT ===
WARNING: Total failed: 15 | Retry queue: 12
INFO: Successful corrections: 2
INFO:   ✓ M&M → MANNM
INFO:   ✓ HDFC → HDFC.NS
WARNING: Failed symbols (13):
WARNING:   ✗ UNKNOWN1 (3 attempts): All methods failed (API, corrections...)
WARNING:   ✗ SYMBOL2 (1 attempt): jugaad-data returned 404...
INFO:     Suggested formats: ['SYMBOL2.NS', 'SYMBOL2.BO', 'SYMBOL2_CORRECTED']
```

### RajTradingBot Methods

#### `_check_and_retry_failed_symbols()`
Automatically called every trading cycle. Coordinates retries and reporting.

No parameters. Works with `self.broker` or `self.market_data_broker`.

## Logging

### Tracking Logs

All symbol tracking is logged at appropriate levels:

```
DEBUG: "Attempting NSEKit MCP quote fetch for SYMBOL"
DEBUG: "Unknown symbol SYMBOL, trying NSE API"
DEBUG: "Trying 3 symbol corrections for SYMBOL: [corrected formats]"
DEBUG: "Symbol correction SYMBOL.NS: no valid price data"
INFO:  "Symbol correction successful: SYMBOL → SYMBOL.NS (price: 150.25)"
INFO:  "NSE API quote for unknown symbol SYMBOL: 145.50"
WARNING: "Failed to fetch quote for SYMBOL - added to retry queue"
WARNING: "=== SYMBOL TRACKING REPORT ==="
INFO:  "Retrying 10 failed symbols..."
INFO:  "Retry successful for SYMBOL: 148.75"
```

## Examples

### Example 1: Symbol with Formatting Issue

```
Attempt 1: M&M
  ├─ NSEKit MCP: fails
  ├─ NSE API (M&M): 404 error
  ├─ Corrections tried:
  │  ├─ M&M.NS: 404 error
  │  ├─ M&M.BO: 404 error
  │  ├─ MANNM: 404 error
  │  └─ MANNM.NS: SUCCESS! (₹1234.50)
  └─ Result: Symbol tracked as M&M → MANNM

Report shows:
  ✓ M&M → MANNM (successful)
```

### Example 2: Symbol Doesn't Exist

```
Attempt 1: FAKESYMBOL
  ├─ NSEKit MCP: timeout
  ├─ NSE API: 404 "Not Found"
  ├─ Corrections: All return 404
  ├─ yfinance: No data
  └─ Result: Added to retry queue

Attempt 2 (30 mins later): FAKESYMBOL
  ├─ NSEKit MCP: timeout
  ├─ NSE API: 404 (same as before)
  └─ Result: Still failed, removed from retry queue after 3 attempts

Report shows:
  ✗ FAKESYMBOL (3 attempts): All methods failed...
  Suggested formats: ['FAKESYMBOL.NS', 'FAKESYMBOL.BO']
```

### Example 3: Temporary Network Issue

```
Attempt 1: SYMBOL
  ├─ NSEKit MCP: timeout
  ├─ NSE API: timeout
  ├─ Corrections: timeout
  ├─ yfinance: timeout
  └─ Result: Added to retry queue

Attempt 2 (30 mins later): SYMBOL
  ├─ NSEKit MCP: SUCCESS! (₹500.00)
  └─ Result: Symbol removed from failed list

Report shows:
  ✓ 1 symbol successfully retried after network recovery
```

## Configuration

### Retry Behavior

Edit `_check_and_retry_failed_symbols()` in main.py:

```python
# Print report every N cycles (60s * 15 = 15 minutes)
if self._symbol_retry_cycle % 15 == 0:
    broker.print_symbol_tracking_report()

# Retry symbols every M cycles (60s * 30 = 30 minutes)
if self._symbol_retry_cycle % 30 == 0:
    results = broker.retry_failed_symbols(max_symbols=20)  # Adjust max_symbols
```

### Max Retry Attempts

Edit `get_retry_candidates()` in SymbolTracker:

```python
def get_retry_candidates(self, max_attempts: int = 3) -> list:
    # Symbols with attempt_count < 3 will be retried
```

## Monitoring & Debugging

### Check Current Tracking Status

```python
broker = system.broker  # or system.market_data_broker
report = broker.get_symbol_tracking_report()

print(f"Failed symbols: {report['total_failed']}")
print(f"Retry queue: {report['total_retry_queue']}")
print(f"Successful corrections: {len(report['corrected_symbols'])}")
```

### View Full Report

```python
broker.print_symbol_tracking_report()
```

### Manually Retry Symbols

```python
results = broker.retry_failed_symbols(max_symbols=50)
print(f"Retried: {results['retried']}")
print(f"Successful: {results['successful']}")
print(f"Still failing: {results['still_failed']}")
```

## Performance Impact

- **Memory**: ~50 bytes per failed symbol (negligible)
- **CPU**: <1ms per retry attempt
- **Network**: Retry attempts only happen every 30 minutes
- **No impact on normal quote fetching** (only on failures)

## Integration with Other Systems

The symbol tracking system integrates with:

1. **DataProvider** (sources/data_provider/__init__.py)
   - Wraps `broker.get_quote()` calls
   - Automatically uses symbol tracking

2. **RajTradingBot** (main.py)
   - Calls `_check_and_retry_failed_symbols()` every cycle
   - Generates reports and retries

3. **Screener** (screener/engine.py)
   - May encounter unknown symbols during screening
   - All tracked automatically

## Future Enhancements

Potential improvements:

1. **ML-based symbol correction**: Use edit distance to suggest similar symbols
2. **NSE registry integration**: Validate against official NSE symbol list
3. **User manual corrections**: Allow manual mapping of alternative symbols
4. **Historical tracking**: Store long-term symbol success/failure metrics
5. **Auto-add corrections**: Automatically update STOCK_SYMBOLS when correction succeeds

## Troubleshooting

### Symbols stuck in retry queue
- Check logs for actual error: `grep "symbol_name" logs/*.txt`
- Manually verify symbol spelling/format
- Check if symbol delisted on NSE
- Try `broker.print_symbol_tracking_report()`

### Too many failed symbols
- May indicate network issues or NSE API problems
- Check MCP health: `system._check_mcp_health()`
- Monitor system logs for rate limiting errors
- Verify NSE website is accessible

### Corrections not being applied
- Check if suggested formats are actually valid NSE symbols
- Manually test with `broker.get_quote("SUGGESTED_FORMAT")`
- Add successful corrections to STOCK_SYMBOLS list

## Testing

Run symbol tracking tests:

```bash
# Test symbol correction logic
python -c "
from sources.broker import NSELiveBroker
broker = NSELiveBroker()
corrections = broker._suggest_symbol_corrections('M&M')
print(f'Suggestions for M&M: {corrections}')
"

# Test tracking report
broker.print_symbol_tracking_report()

# Test retry
results = broker.retry_failed_symbols(max_symbols=10)
print(results)
```
