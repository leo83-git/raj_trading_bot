# raj_trading_bot - Ultimate Production-Grade Platform

A self-evolving, production-grade quant trading platform designed for the Indian stock market (NIFTY, BANKNIFTY options).

## Features

- **Multi-Brain System**: ML + DL + RL models fused for decision making
- **Dynamic Capital Allocation**: Confidence and volatility-based position sizing
- **Full Hedging Engine**: Delta, Gamma, Vega, and drawdown hedging
- **Multi-Broker Support**: AngelOne + Zerodha with automatic failover
- **Options Intelligence**: Greeks calculation, IV surface, skew detection
- **Self-Learning**: Automatic model retraining based on performance
- **Hard Safety**: Kill switch, exposure limits, time-based shutdown
- **Telegram Alerts**: Real-time trade, error, and risk notifications
- **Docker Ready**: VPS deployment with auto-restart

## Architecture

```
Data Layer (Live + Historical + Order Book)
        ↓
Feature Engine (TA + Microstructure + Options + Greeks)
        ↓
Model Layer (ML + DL + RL)
        ↓
Meta Controller (strategy selection + confidence)
        ↓
Capital Allocator (dynamic sizing)
        ↓
Portfolio Manager (multi-position)
        ↓
Hedging Engine (Greeks + drawdown)
        ↓
Execution Engine (multi-broker, low latency)
        ↓
Monitoring + Alerts + Kill Switch
        ↓
Self-Learning Loop (retrain + adapt)
```

## Directory Structure

```
raj_trading_bot/
├── core/                    # Core utilities
├── data/                    # Data layer
│   ├── live/               # Live market data
│   ├── historical/         # Historical storage
│   ├── orderbook/          # Order book data
│   └── options/            # Options chain data
├── features/                # Feature engine
│   ├── ta.py              # Technical analysis
│   ├── greeks.py           # Options Greeks
│   └── microstructure.py  # Market microstructure
├── models/                  # ML/DL/RL models
│   ├── ml/                # Pattern recognition
│   ├── dl/                # Order book + vol surface
│   └── rl/                # Decision optimization
├── strategies/             # Trading strategies
├── portfolio/              # Portfolio management
├── execution/              # Execution engine
├── risk/                   # Risk management
├── hedging/                # Hedging engine
├── alerts/                 # Alert system
├── dashboard/              # Streamlit dashboard
├── infra/                  # Docker + scripts
├── training/               # Self-learning
├── config/                 # Configuration
└── main.py                 # Entry point
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run in paper trading mode
python main.py
```

## Manual Data Downloads

The repository provides a few CLI scripts that can be executed manually to fetch
different types of data. All scripts are located in the `scripts/` directory
and can be run with the standard Python interpreter.

| Script | Description |
|--------|-------------|
| `scripts/download_instruments.py` | Downloads the latest instrument list from Zerodha and stores it in the database. |
| `scripts/download_market_snapshot.py` | Retrieves a market snapshot (e.g., open/close prices) and persists it. |
| `scripts/download_historical_daily.py` | **Daily historical data updater** – fetches the most recent candle data for all tradable symbols after market close and stores it in DuckDB/Parquet. |

### Running the daily historical updater

```bash
python scripts/download_historical_daily.py
```

The script uses the same logic as `download_all_historical_to_sqlite.py` but is
intended to be scheduled (e.g., via `cron`) to run automatically after the
market session ends.

#### Example `cron` entry (run at 18:30 every weekday)

```cron
30 18 * * 1-5 /usr/bin/python3 /home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_historical_daily.py \
        >> /var/log/qt_historical_download.log 2>&1
```

Adjust the time and path to the Python interpreter as needed for your environment.

#### Optional: Systemd timer alternative

If you prefer `systemd` over `cron`, you can create a service and timer that run the script after market close.

**`/etc/systemd/system/download-historical.service`**
```ini
[Unit]
Description=Download daily historical candle data
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_historical_daily.py
```

**`/etc/systemd/system/download-historical.timer`**
```ini
[Unit]
Description=Run download-historical.service after market close

[Timer]
# 4 PM IST = 10:30 UTC (adjust if your server uses UTC)
OnCalendar=*-*-* 10:30:00
Persistent=true
# Restrict to weekdays
OnCalendar=Mon..Fri *-*-* 10:30:00

[Install]
WantedBy=timers.target
```

Enable and start the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now download-historical.timer
```

You can check the next scheduled run with:

```bash
systemctl list-timers --all | grep download-historical
```

Both approaches guarantee the script runs automatically after the market session ends, keeping your DuckDB/Parquet stores up‑to‑date.

You can also invoke the wrapper directly for ad‑hoc runs:

```bash
/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/run_download_historical_daily.sh
```

The wrapper handles environment activation and logs output to `/var/log/qt_historical_download_*.log`.

## Configuration

Edit `raj_trading_bot/config/config.yaml` to customize:
- Broker settings
- Capital and risk limits
- Model weights
- Telegram alerts

## Requirements

- Python 3.11+
- SmartAPI (AngelOne) or Kite Connect (Zerodha) credentials
- Telegram bot for alerts (optional)

## Running Live Integration Tests

- The repository includes an optional live integration test that validates option premiums against live MCP/NSE option chains:
        - `tests/test_send_trade_alert_and_integration.py::test_integration_live_option_premiums`

- This test is skipped by default. To run it locally you must have network access and reliable access to MCP/NSE endpoints. Run it with:

```bash
RUN_LIVE_TESTS=1 /home/rajasekhar/vibe-coding/raj_trading_bot/.venv/bin/python -m pytest -q tests/test_send_trade_alert_and_integration.py::test_integration_live_option_premiums
```

- Notes:
        - Results may be flaky due to network latency, MCP availability, or NSE site changes.
        - The CI workflow runs unit tests on push/PR and can run this live test only when manually triggered with `run_live=true` or when `RUN_LIVE_TESTS=1` is set in the workflow environment.

