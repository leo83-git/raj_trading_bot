# ═══════════════════════════════════════════════════════════════
#  Zerodha F&O Trading Bot — Configuration
# ═══════════════════════════════════════════════════════════════

import os

# ── Broker Selection ──────────────────────────────────────────
# Broker is fixed to zerodha
BROKER_TYPE = os.environ.get("BROKER_TYPE", "zerodha")


# ── Zerodha API Credentials ──────────────────────────────────
# Get from: https://kite.trade/
# Option 1: Hard-code credentials directly (easier for development)
# Option 2: Use environment variables (recommended for production)
# Export: ZERODHA_API_KEY, ZERODHA_API_SECRET, ZERODHA_ACCESS_TOKEN
ZERODHA_API_KEY = os.environ.get("ZERODHA_API_KEY", "yzxs1h7v1q6e02mp")
ZERODHA_API_SECRET = os.environ.get(
    "ZERODHA_API_SECRET", "q5gscgpji7mcegockwypo628esiuclhj"
)
ZERODHA_ACCESS_TOKEN = os.environ.get(
    "ZERODHA_ACCESS_TOKEN", ""
)  # Access token (generated via OAuth)
ZERODHA_USER_ID = os.environ.get(
    "ZERODHA_USER_ID", "sekhar20383@gmail.com"
)  # Zerodha user ID
ZERODHA_PASSWORD = os.environ.get("ZERODHA_PASSWORD", "29Nov@2018")  # Zerodha password
ZERODHA_TOTP_SECRET = os.environ.get(
    "ZERODHA_TOTP_SECRET", "KPCFPVWZQBAHIRFKJMBB46GPI2GAEZRE"
)  # TOTP secret for 2FA

# ── Zerodha-Specific Settings ──────────────────────────────────
# Product type for orders (Zerodha specific)
# MIS: Intraday (auto-square off)
# CNC: Cash and Carry (delivery)
# NRML: Normal (F&O positional)
ZERODHA_PRODUCT_TYPE = "NRML"  # MIS | CNC | NRML

# Multi-leg order delay (Zerodha supports basket orders natively)
ZERODHA_MULTI_LEG_DELAY = 0.1  # 100ms delay between legs

# Daily order limit (Zerodha: 3000 orders/day)
ZERODHA_MAX_DAILY_ORDERS = 3000

# Token expiry time (Zerodha tokens expire at 6:00 AM daily)
ZERODHA_TOKEN_EXPIRY_TIME = "06:00"  # 6:00 AM
ZERODHA_TOKEN_REFRESH_TIME = "06:01"  # 6:01 AM (1 minute after expiry)

# ── Zerodha Enable/Disable ────────────────────────────────────
# Enable or disable Zerodha integration
ZERODHA_ENABLED = os.environ.get("ZERODHA_ENABLED", "true").lower() == "true"

# ── Zerodha Exchange ───────────────────────────────────────────
# Default exchange for Zerodha instruments
ZERODHA_EXCHANGE = "NSE"

# ── Paper Trading ─────────────────────────────────────────────
PAPER_TRADE = True  # True = simulate trades, False = real orders


# ── Instruments to trade ──────────────────────────────────────
INSTRUMENTS = [
    {"symbol": "NIFTY", "exchange": "NFO", "lot_size": 25},
    {"symbol": "BANKNIFTY", "exchange": "NFO", "lot_size": 15},
]

# ── Active Strategy ───────────────────────────────────────────
# Options: PIVOT_BREAKOUT | MA_CROSSOVER | RSI | MACD |
#          VWAP_STRATEGY | ORB_STRATEGY | PRICE_ACTION | COMBINED |
#          BOLLINGER_BANDS | RANGE_TRADING | SIDEWAYS_ENHANCED
ACTIVE_STRATEGY = "COMBINED"  # COMBINED runs all and picks highest-confidence signal

# ── Option Selection Settings ─────────────────────────────────
OPTION_TYPE_AUTO = True  # Auto-detect CE or PE from market direction
OPTION_EXPIRY_PREF = "WEEKLY"  # WEEKLY | MONTHLY
OTM_OFFSET = 0  # 0=ATM, 1=1 strike OTM, -1=1 strike ITM
STRIKE_STEP_NIFTY = 50  # Nifty strike step
STRIKE_STEP_BNIFTY = 100  # BankNifty strike step

# ── Capital & Risk ────────────────────────────────────────────
PAPER_CAPITAL = 500000  # Paper trading starting capital ₹
MAX_CAPITAL_PER_TRADE = 50000  # Max capital per trade
MAX_RISK_PER_TRADE = 0.01  # 1% of capital max risk per trade
MAX_DAILY_LOSS = None  # No daily loss limit
MAX_DAILY_PROFIT = None  # No daily profit limit
MAX_OPEN_POSITIONS = None  # No limit on open positions
RISK_REWARD_RATIO = 2.0  # Minimum R:R ratio
SL_BUFFER_PCT = 0.30  # SL buffer (30% of ATR)

# ── Strategy Parameters ───────────────────────────────────────
# MA Crossover
MA_FAST = 9
MA_SLOW = 21
MA_TYPE = "EMA"  # EMA | SMA

# RSI
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Pivot Breakout
PIVOT_TYPE = "CLASSIC"  # CLASSIC | CAMARILLA | FIBONACCI
VOLUME_FILTER = 1.3  # Volume must be 1.3x average

# VWAP
VWAP_DEVIATION = 1.0  # Standard deviation bands

# ORB (Opening Range Breakout)
ORB_MINUTES = 15  # Opening range period in minutes

# Price Action
PA_LOOKBACK = 20  # Swing high/low lookback periods

# Bollinger Bands (for sideways/range trading)
BB_PERIOD = 20  # Bollinger Bands period
BB_STD_DEV = 2.0  # Standard deviation for bands
BB_SIDEWAYS_LOOKBACK = 30  # Lookback for sideways detection

# Range Trading (for sideways markets)
RANGE_LOOKBACK = 30  # Lookback for support/resistance levels

# Sideways Enhanced Strategy
SIDEWAYS_RANGE_LOOKBACK = 35  # Lookback for sideways detection
SIDEWAYS_RSI_OVERSOLD = 35  # RSI oversold threshold for bullish entries
SIDEWAYS_RSI_OVERBOUGHT = 65  # RSI overbought threshold for bearish entries
SIDEWAYS_SQUEEZE_RATIO = 0.6  # Max squeeze ratio for bonus (lower = tighter squeeze)
SIDEWAYS_RANGE_BOTTOM = 0.35  # Max range position for bullish entries (0-1)
SIDEWAYS_RANGE_TOP = 0.65  # Min range position for bearish entries (0-1)
SIDEWAYS_VOL_MULTIPLIER = 1.2  # Volume must be X times average

# ── Dynamic Stock Selection ───────────────────────────
DYNAMIC_STOCK_SELECTION = True  # Enable dynamic stock picking
SECTOR_ANALYSIS_INTERVAL = (
    600  # Seconds between sector analysis (10 min) - Increased from 1800 to 600
)
MAX_STOCKS_TO_TRADE = 4  # Maximum stocks to select
MIN_STOCK_PRICE = 100  # Minimum stock price for selection
MAX_STOCK_PRICE = 5000  # Maximum stock price for selection

# ── Dynamic Target & Stop Loss ───────────────────────
DYNAMIC_TARGETS_ENABLED = True  # Enable dynamic target adjustment
TRAILING_STOP_ENABLED = True  # Enable trailing stops
TARGET_ADJUSTMENT_INTERVAL = 60  # Seconds between target adjustments

# ── Parallel Execution ───────────────────────────────
NUMBER_OF_INSTANCES = 2  # Number of parallel bot instances

# Sideways Market Detection
SIDEWAYS_RANGE_MIN = 1.5  # Min range % to consider sideways
SIDEWAYS_RANGE_MAX = 4.0  # Max range % to consider sideways
SIDEWAYS_MID_ZONE_RATIO = 0.55  # % of candles in middle of range

# Combined strategy — minimum signals to confirm
COMBINED_MIN_SIGNALS = 2  # Need at least 2 strategies to agree

# ── Timing ────────────────────────────────────────────────────
MARKET_OPEN = "09:15"
NO_NEW_TRADES_AFTER = "15:00"
SQUARE_OFF_TIME = "15:20"
CANDLE_INTERVAL = "FIVE_MINUTE"  # ONE_MINUTE | FIVE_MINUTE | FIFTEEN_MINUTE

# ── Telegram Alerts (optional) ────────────────────────────────
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = "8695428021:AAHGBSYPAEmNRNjtrN4DdjSfKmSWgd_yleQ"
TELEGRAM_CHAT_ID = "5510134387"

# ── Logging ───────────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "bot.log")
TRADES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "trades.csv"
)
LOG_LEVEL = "INFO"

# ── PostgreSQL Database (Optional) ───────────────────────────────
# Set these to enable PostgreSQL trade logging
# If not set, falls back to CSV logging
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5433")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "trading_bot")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")

# ── Paper Trading ────────────────────────────────────────────
USE_PAPER_TRADER = True

# ── Real-time Data in Paper Trading ────────────────────────────
FORCE_REALTIME_DATA = True  # Force real-time data even in paper trading mode
