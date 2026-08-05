# ═══════════════════════════════════════════════════════════════
#  Notifier — Telegram Alerts
# ═══════════════════════════════════════════════════════════════
import requests

from quant_utils.logger import get_logger

log = get_logger("notifier")

TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = "8695428021:AAHGBSYPAEmNRNjtrN4DdjSfKmSWgd_yleQ"
TELEGRAM_CHAT_ID = "5510134387"


def send_telegram_message(message: str, parse_mode: str = "Markdown") -> bool:
    """Send message via Telegram bot"""
    if not TELEGRAM_ENABLED:
        log.debug(f"Telegram disabled, skipping: {message}")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": parse_mode}
        response = requests.post(url, data=data, timeout=5)
        return response.status_code == 200
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False


def alert_info(message: str):
    """Send info alert"""
    log.info(message)
    send_telegram_message(f"ℹ️ {message}")


def alert_error(message: str):
    """Send error alert"""
    log.error(message)
    send_telegram_message(f"❌ {message}")


def alert_warning(message: str):
    """Send warning alert"""
    log.warning(message)
    send_telegram_message(f"⚠️ {message}")


def alert_trade(symbol: str, direction: str, entry: float, quantity: int):
    """Send trade alert"""
    msg = f"🎯 *TRADE EXECUTED*\n\nSymbol: {symbol}\nDirection: {direction}\nEntry: ₹{entry:.2f}\nQuantity: {quantity}"
    log.info(msg)
    send_telegram_message(msg)


def alert_exit(symbol: str, pnl: float, reason: str):
    """Send exit alert"""
    emoji = "🟢" if pnl > 0 else "🔴"
    msg = f"{emoji} *POSITION CLOSED*\n\nSymbol: {symbol}\nPnL: ₹{pnl:.2f}\nReason: {reason}"
    log.info(msg)
    send_telegram_message(msg)


def alert_killswitch(trigger: str, details: str):
    """Send kill switch alert"""
    msg = f"🛑 *KILL SWITCH TRIGGERED*\n\nTrigger: {trigger}\nDetails: {details}"
    log.critical(msg)
    send_telegram_message(msg)
