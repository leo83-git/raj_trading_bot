# ═══════════════════════════════════════════════════════════════
#  Alerts System (Telegram + Notifications)
# ═══════════════════════════════════════════════════════════════

import datetime as dt
import threading
import time
from collections.abc import Callable
from enum import IntEnum

import requests

from quant_utils.logger import get_logger

log = get_logger("alerts")


class AlertSeverity(IntEnum):
    """Operational alert priority used for routing and suppression."""

    INFO = 10
    WARNING = 20
    ERROR = 30
    CRITICAL = 40


class AlertManager:
    """Telegram alerts and notifications"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.enabled = self.config.get("telegram_enabled", True)
        self.bot_token = self.config.get(
            "telegram_bot_token", "8695428021:AAHGBSYPAEmNRNjtrN4DdjSfKmSWgd_yleQ"
        )
        self.chat_id = self.config.get("telegram_chat_id", "5510134387")

        self.alert_history = []
        self.alert_counts = {"trade": 0, "error": 0, "risk": 0, "info": 0}
        self.dedup_window_seconds = float(self.config.get("dedup_window_seconds", 300))
        self.minimum_severity = AlertSeverity[
            str(self.config.get("minimum_severity", "INFO")).upper()
        ]
        self._last_alert: dict[str, float] = {}
        self._lock = threading.Lock()

        log.info("Alert manager initialized")

    def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send message via Telegram"""
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {"chat_id": self.chat_id, "text": message, "parse_mode": parse_mode}
            response = requests.post(url, data=data, timeout=10)
            return response.status_code == 200
        except requests.RequestException as exc:
            log.error("Telegram error: %s", exc)
            return False

    def send_alert(
        self,
        severity: AlertSeverity,
        key: str,
        message: str,
        *,
        correlation_id: str = "",
        sender: Callable[[str], bool] | None = None,
    ) -> bool:
        """Send a severity-filtered, deduplicated alert without raising.

        ``sender`` supports non-network transports in deployments and tests.
        Duplicate keys are suppressed for ``dedup_window_seconds``; critical
        alerts bypass suppression so emergency conditions remain visible.
        """
        try:
            severity = AlertSeverity(severity)
            if severity < self.minimum_severity:
                return False
            now = time.monotonic()
            with self._lock:
                previous = self._last_alert.get(key)
                if (
                    severity is not AlertSeverity.CRITICAL
                    and previous is not None
                    and now - previous < self.dedup_window_seconds
                ):
                    return False
                self._last_alert[key] = now
            prefix = f"[{severity.name}]"
            if correlation_id:
                prefix += f" [{correlation_id}]"
            delivered = (sender or self.send_message)(f"{prefix} {message}")
            self.alert_history.append(
                {
                    "type": "operational",
                    "severity": severity.name,
                    "key": key,
                    "correlation_id": correlation_id,
                    "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                    "delivered": bool(delivered),
                }
            )
            return bool(delivered)
        except Exception:  # Alert transports must not affect trading.
            log.debug("P7 alert delivery failed", exc_info=True)
            return False

    def run_health_checks(
        self, checks: dict[str, Callable[[], bool]]
    ) -> dict[str, bool]:
        """Run independent health probes; one broken probe cannot stop others."""
        results: dict[str, bool] = {}
        for name, check in checks.items():
            try:
                results[name] = bool(check())
            except Exception:  # User-supplied probes are intentionally isolated.
                results[name] = False
                log.debug("P7 health check failed: %s", name, exc_info=True)
            if not results[name]:
                self.send_alert(
                    AlertSeverity.ERROR,
                    f"health:{name}",
                    f"Health check failed: {name}",
                )
        return results

    def alert_trade(
        self,
        symbol: str,
        direction: str,
        entry: float,
        quantity: int,
        option_symbol: str = "",
    ):
        """Send trade execution alert"""
        emoji = "🟢" if direction == "BUY" else "🔴"
        msg = (
            f"{emoji} *TRADE EXECUTED*\n\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Entry: ₹{entry:.2f}\n"
            f"Quantity: {quantity}"
        )

        if option_symbol:
            msg += f"\nOption: {option_symbol}"

        self.send_message(msg)
        self.alert_counts["trade"] += 1
        self.alert_history.append({"type": "trade", "symbol": symbol})
        log.info(f"Trade alert sent: {symbol}")

    def alert_exit(self, symbol: str, pnl: float, reason: str):
        """Send position exit alert"""
        emoji = "🟢" if pnl > 0 else "🔴"
        msg = (
            f"{emoji} *POSITION CLOSED*\n\n"
            f"Symbol: {symbol}\n"
            f"PnL: ₹{pnl:.2f}\n"
            f"Reason: {reason}"
        )

        self.send_message(msg)
        self.alert_history.append({"type": "exit", "symbol": symbol, "pnl": pnl})
        log.info(f"Exit alert: {symbol} | PnL: ₹{pnl:.2f}")

    def alert_error(self, message: str, details: str = ""):
        """Send error alert"""
        msg = f"❌ *ERROR*\n\n{message}"
        if details:
            msg += f"\n\nDetails: {details}"

        self.send_message(msg)
        self.alert_counts["error"] += 1
        self.alert_history.append({"type": "error", "message": message})
        log.error(f"Error alert: {message}")

    def alert_risk(self, trigger: str, details: str):
        """Send risk alert"""
        msg = f"⚠️ *RISK ALERT*\n\nTrigger: {trigger}\nDetails: {details}"

        self.send_message(msg)
        self.alert_counts["risk"] += 1
        self.alert_history.append({"type": "risk", "trigger": trigger})
        log.warning(f"Risk alert: {trigger}")

    def alert_killswitch(self, trigger: str, details: str):
        """Send kill switch alert"""
        msg = (
            f"🛑 *KILL SWITCH TRIGGERED*\n\n"
            f"Trigger: {trigger}\n"
            f"Details: {details}\n\n"
            f"*All positions will be closed!*"
        )

        self.send_message(msg)
        self.alert_history.append({"type": "killswitch", "trigger": trigger})
        log.critical(f"Kill switch: {trigger}")

    def alert_info(self, message: str):
        """Send info alert"""
        msg = f"ℹ️ *INFO*\n\n{message}"
        self.send_message(msg)
        self.alert_counts["info"] += 1
        self.alert_history.append({"type": "info", "message": message})

    def alert_start(self, mode: str, strategy: str, capital: float):
        """Send bot start alert"""
        msg = (
            f"🤖 *BOT STARTED*\n\n"
            f"Mode: {mode}\n"
            f"Strategy: {strategy}\n"
            f"Capital: ₹{capital:,.0f}"
        )
        self.send_message(msg)

    def alert_stop(self):
        """Send bot stop alert"""
        msg = "🛑 *BOT STOPPED*\n\nAll positions closed."
        self.send_message(msg)

    def get_alert_stats(self) -> dict:
        """Get alert statistics"""
        return {
            "total_alerts": len(self.alert_history),
            "counts": self.alert_counts,
            "recent": self.alert_history[-10:],
        }

    def set_enabled(self, enabled: bool):
        """Enable/disable alerts"""
        self.enabled = enabled
        log.info(f"Alerts {'enabled' if enabled else 'disabled'}")
