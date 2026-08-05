# Quote Fetch Timeout Optimization Summary

## Problem Statement
Data fetching was timing out at 6+ seconds, causing startup delays and slow trade execution:
- Initial market data fetch (NIFTY quote for direction): 6s+
- Universe quote fetching: cascading timeouts
- Fallback methods (NSE scrape, NSE API) were becoming bottlenecks
- MCP performance unclear - not being used effectively

## Root Causes Identified
1. **NSE Scrape too slow**: HTTP request to nseindia.com with 15s timeout was always exceeding 4-6s wall clock time
2. **NSE API rate limiting**: 0.35s between calls + 3 concurrent workers = serialization bottleneck
3. **MCP not disabling gracefully**: If MCP failed, it would keep retrying (wasting 3s per symbol)
4. **Fallback chain too long**: Unknown symbols would try scrape → API → yfinance (sequential)
5. **High initial timeout**: 6s was too generous - allowed slow responses to accumulate
6. **Multiple retries**: 2 retries × 4-6s timeout per symbol = exponential slowdown

## Solutions Implemented

### 1. Disable MCP After Repeated Failures (sources/broker/__init__.py)
```python
# In __init__:
self._mcp_enabled = True
self._mcp_failure_count = 0

# In _get_nsekit_mcp_quote():
if not self._mcp_enabled or self._mcp_failure_count > 2:
    return None  # Skip MCP entirely after >2 failures
```
**Impact**: Avoids wasting 3-3.5s per symbol on failing MCP calls

### 2. Skip NSE Scrape for Stocks (sources/broker/__init__.py)
**Old flow for unknown symbols**: 
  - Try scrape (15s timeout, usually 5-6s wall clock) → 
  - Try API (0.35s rate limit + network) → 
  - Try yfinance

**New flow**:
  - Try API (0.35s rate limit + network) → Try yfinance

**Impact**: Eliminates 5-6s waste on every unknown symbol

### 3. Reduce Overall Timeout (config/config.yaml)
```yaml
quote_timeout: 4  # was 6 seconds
max_retries: 1    # was 2 retries
```
**Impact**: 
- 4s timeout = fail-fast on slow symbols
- 1 retry instead of 2 = avoid cascading delays
- Maximum single quote time: 4s × 2 attempts = 8s → now 4s × 2 = 8s (better parallelization)

### 4. MCP Performance Chain
```
quote_timeout (wrapper): 4s
├─ _get_nsekit_mcp_quote(): Returns within 3.5s or skipped
├─ _get_nse_api_quote(): Returns within 0.5s+ (rate limited)
└─ _get_yfinance_quote(): Returns within 2-3s
```

## Expected Improvements

### Before Optimization
- Single NIFTY quote: 6-10s (often scrape path)
- 10 unknown symbols: 50-60s (cumulative scrape timeouts)
- Universe of 200+ symbols with 3 concurrent workers: 15-20+ minutes startup

### After Optimization
- Single NIFTY quote: 0.5-2s (MCP or API, no scrape)
- 10 unknown symbols: 5-10s (API only, no scrape timeouts)
- Universe of 200+ symbols: 5-10 minutes startup (2-3x faster)

### Key Metrics
- MCP fast-fail: 3s per attempt instead of 6s
- Scrape elimination: 5-6s per unknown symbol saved
- Retry reduction: Halves worst-case scenarios
- Timeout reduction: 33% (6s → 4s) enables faster overall throughput

## Monitoring

Added logging for:
- MCP quote fetch entry: `log.debug(f"Attempting NSEKit MCP quote fetch for {symbol}")`
- MCP successful return: `log.info(f"NSEKit MCP quote used for {symbol}: {last_price}")`
- MCP disabled: `log.info("NSEKit MCP disabled due to repeated timeouts")`
- Fallback to API: `log.debug(f"Unknown symbol {symbol}, trying NSE API")`

## Trade-offs

1. **MCP Disable Threshold**: >2 failures (~3-7s per failure) before disabling
   - Risk: If MCP recovers, we won't use it
   - Mitigation: Restart bot to reset flag
   
2. **No Scrape**: Indices still use scrape as fallback after API fails
   - Risk: If NSE website down, indices won't fetch
   - Mitigation: yfinance available for major indices
   
3. **Lower Timeout**: May miss some very slow but valid responses
   - Risk: False failures on bad network
   - Mitigation: 1 retry + API fallback + yfinance
   
## Next Steps

1. Monitor initial startup time and first quote fetch latency
2. If still slow, profile quote_timeout usage: adjust to 3s or add per-symbol timeouts
3. Consider batching quotes to NSE API (if supported) to reduce round-trips
4. Cache universe quotes from screener for reuse in trading cycle

## Testing

To validate improvements:
```bash
# Run bot and check logs for:
# 1. Time to first trade: should be <5min for 200+ symbol universe
# 2. Quote fetch distribution: 70%+ should complete <1s
# 3. No repeated "NSEKit MCP quote fetch" logs (should skip after failures)

# Check config values:
grep quote_timeout config/config.yaml  # Should show 4
grep max_retries config/config.yaml    # Should show 1
```
