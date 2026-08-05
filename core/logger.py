# utils/logger.py
import logging
import os
import threading
from typing import Any

import requests

# Import configuration values. Use a relative import to avoid package discovery
# issues when ``raj_trading_bot`` is not a proper package on the Python
# path. ``LOG_LEVEL`` may not be defined in the config, so provide a sensible
# default.
try:
    from .config import LOG_FILE, LOG_LEVEL  # type: ignore
except Exception:
    # Fallback: define defaults if the config does not expose them.
    LOG_FILE = "bot.log"
    LOG_LEVEL = "INFO"

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DEFAULT_LOGS_DIR = os.path.join(ROOT_DIR, "logs")
if os.access(ROOT_DIR, os.W_OK):
    LOGS_DIR = DEFAULT_LOGS_DIR
else:
    LOGS_DIR = os.path.join("/tmp", "raj_trading_bot_logs")
os.makedirs(LOGS_DIR, exist_ok=True)


class WebhookAlertHandler(logging.Handler):
    """Send selected log records to Discord or Telegram webhooks."""

    def __init__(
        self,
        webhook_url: str,
        *,
        source_name: str = "bot",
        timeout: float = 2.0,
        level: int = logging.WARNING,
    ) -> None:
        super().__init__(level=level)
        self.webhook_url = webhook_url
        self.source_name = source_name
        self.timeout = timeout
        self._is_telegram = "api.telegram.org" in webhook_url
        self._telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self._should_send(record):
                return
            payload = self._build_payload(record)
            thread = threading.Thread(
                target=self._post_payload, args=(payload,), daemon=True
            )
            thread.start()
        except Exception:
            self.handleError(record)

    def _should_send(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        message = record.getMessage().lower()
        return any(
            token in message
            for token in (
                "trade fill",
                "filled",
                "order executed",
                "entry signal",
                "exit signal",
                "position opened",
                "position closed",
            )
        )

    def _build_payload(self, record: logging.LogRecord) -> dict[str, Any]:
        message = self.format(record)
        if self._is_telegram:
            payload: dict[str, Any] = {
                "text": f"[{record.levelname}] {self.source_name}: {message}"
            }
            if self._telegram_chat_id:
                payload["chat_id"] = self._telegram_chat_id
            return payload
        return {
            "content": f"[{record.levelname}] {self.source_name}: {message}",
            "username": self.source_name,
        }

    def _post_payload(self, payload: dict[str, Any]) -> None:
        try:
            with self._lock:
                requests.post(self.webhook_url, json=payload, timeout=self.timeout)
        except requests.exceptions.RequestException:
            return


def _configure_webhook_handler(logger: logging.Logger, fmt: logging.Formatter) -> None:
    """Attach an alert webhook handler if environment variables are configured."""
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    telegram_url = os.getenv("TELEGRAM_WEBHOOK_URL")
    if not telegram_url and telegram_token and telegram_chat_id:
        telegram_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL") or telegram_url or os.getenv(
        "ALERT_WEBHOOK_URL"
    )
    if not webhook_url:
        return

    if any(isinstance(handler, WebhookAlertHandler) for handler in logger.handlers):
        return

    webhook_handler = WebhookAlertHandler(
        webhook_url=webhook_url,
        source_name=logger.name,
    )
    webhook_handler.setFormatter(fmt)
    logger.addHandler(webhook_handler)


def get_logger(name="bot"):
    logger = logging.getLogger(name)
    # If the logger has already been configured for daily rotation, reuse it.
    # We store a custom attribute to avoid adding duplicate handlers on repeated
    # calls (which can happen when the module is imported multiple times).
    if getattr(logger, "_daily_configured", False):
        return logger

    # Prevent log propagation to root handlers, which can duplicate output and
    # make debug routing unreliable.
    logger.propagate = False
    # Ensure the logger captures all levels; handlers will filter appropriately.
    # Using DEBUG here guarantees that DEBUG records reach the debug handler while
    # the INFO handler (set to INFO) discards them.
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console (with UTF-8 encoding for Windows compatibility)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File for INFO (and higher) logs. Use a daily rotating filename inside the
    # ``logs`` directory. The previous implementation derived the directory from
    # ``LOG_FILE`` which could be a plain filename (e.g., ``bot.log``) resulting
    # in an empty ``logs_dir`` and files being created at the repository root.
    # We now explicitly use the ``logs`` folder as the base directory.
    from datetime import datetime

    date_str = datetime.now().strftime("%Y-%m-%d")
    raw_log_path = os.path.join(LOGS_DIR, f"logs_{date_str}.txt")
    raw_fh = logging.FileHandler(raw_log_path, encoding="utf-8")
    # Capture INFO and above (exclude DEBUG).
    raw_fh.setLevel(logging.INFO)
    raw_fh.setFormatter(fmt)
    logger.addHandler(raw_fh)

    # Separate file for DEBUG logs only, also daily, placed under ``logs/debug``.
    debug_log_dir = os.path.join(LOGS_DIR, "debug")
    os.makedirs(debug_log_dir, exist_ok=True)
    debug_log_path = os.path.join(debug_log_dir, f"debug_{date_str}.txt")
    debug_fh = logging.FileHandler(debug_log_path, encoding="utf-8")
    debug_fh.setLevel(logging.DEBUG)
    debug_fh.setFormatter(fmt)

    # Filter to allow only DEBUG records.
    class DebugFilter(logging.Filter):
        def filter(self, record):
            return record.levelno == logging.DEBUG

    debug_fh.addFilter(DebugFilter())
    logger.addHandler(debug_fh)

    _configure_webhook_handler(logger, fmt)

    # Mark the logger as configured to prevent re‑adding handlers.
    logger._daily_configured = True

    return logger


# Compatibility shim used by tests and legacy code. Allows creating a logger with
# a custom name and optional log file path. If ``log_file`` is not provided, the
# default ``LOG_FILE`` from the configuration is used.
def setup_logger(name: str = "bot", log_file: str = None):
    """Create and configure a logger.

    Parameters
    ----------
    name: str
        Logger name.
    log_file: str, optional
        Path to the log file. Falls back to the ``LOG_FILE`` constant from the
        configuration if omitted.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler – use provided path or default from config
    file_path = log_file if log_file is not None else LOG_FILE
    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _configure_webhook_handler(logger, fmt)

    return logger
