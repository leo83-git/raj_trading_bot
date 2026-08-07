# ═══════════════════════════════════════════════════════════════
#  Logger utility for raj_trading_bot - Linux Optimized
# ═══════════════════════════════════════════════════════════════
import logging
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from logging import NullHandler
from logging.handlers import RotatingFileHandler, SysLogHandler
from pathlib import Path
from typing import Any, ClassVar

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


@contextmanager
def correlation_scope(correlation_id: str) -> Iterator[None]:
    """Attach a correlation ID to logs in the current thread/task context."""
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


# Linux-optimized structured formatter
class LinuxStructuredFormatter(logging.Formatter):
    """Structured log formatter for Linux/ELK stack compatibility"""

    def __init__(self, include_lineno: bool = False):
        super().__init__()
        self.include_lineno = include_lineno

    def format(self, record):
        # Build structured log entry
        log_data = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process_id": record.process,
            "thread_id": record.thread,
        }
        correlation_id = getattr(record, "correlation_id", "") or _correlation_id.get()
        if correlation_id:
            log_data["correlation_id"] = correlation_id
        if hasattr(record, "event_type"):
            log_data["event_type"] = record.event_type

        if self.include_lineno:
            log_data["line"] = record.lineno
            log_data["file"] = record.pathname

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Format as JSON for structured logging
        import json

        return json.dumps(log_data, default=str)


class ColoredLinuxFormatter(logging.Formatter):
    """Colorized formatter for console output with Linux ANSI codes"""

    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors and sys.stdout.isatty()

    def format(self, record):
        if self.use_colors:
            color = self.COLORS.get(record.levelname, self.RESET)
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            record.name = f"\033[1m{record.name}\033[0m"

        fmt = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
        if hasattr(record, "lineno") and record.levelno <= logging.DEBUG:
            fmt += " [%(filename)s:%(lineno)d]"

        self._style._fmt = fmt
        return super().format(record)


def get_logger(
    name: str,
    level: str = "INFO",
    log_to_file: bool = True,
    log_to_console: bool = True,
    log_to_syslog: bool = False,
    max_bytes: int = 50 * 1024 * 1024,  # 50MB
    backup_count: int = 5,
    structured: bool = False,
) -> logging.Logger:
    """
    Get optimized logger for Linux production environment

    Args:
        name: Logger name (usually module name)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_file: Enable file logging with rotation
        log_to_console: Enable console output
        log_to_syslog: Enable syslog integration
        max_bytes: Maximum log file size before rotation
        backup_count: Number of rotated log files to keep
        structured: Use JSON structured logging format

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Ensure logs directory is absolute and consistent across working directories.
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

    # If the logger already has been configured, preserve existing handlers but
    # ensure it can still emit DEBUG records to the debug file.
    if logger.handlers:
        logger.setLevel(logging.DEBUG)
        if log_to_file and not any(
            isinstance(handler, logging.FileHandler)
            and getattr(handler, "baseFilename", "").endswith("debug.log")
            for handler in logger.handlers
        ):
            # Add missing debug-only file handler if needed.
            date_str = time.strftime("%Y-%m-%d")
            debug_log_path = log_dir / f"debug_{date_str}.log"
            debug_fh = logging.FileHandler(debug_log_path, encoding="utf-8")
            debug_fh.setLevel(logging.DEBUG)
            debug_fh.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(name)s | %(levelname)s | %(process)d | %(thread)d | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S%z",
                )
            )

            class DebugFilter(logging.Filter):
                def filter(self, record):
                    return record.levelno == logging.DEBUG

            debug_fh.addFilter(DebugFilter())
            logger.addHandler(debug_fh)
        return logger

    # Set level to DEBUG so all records can be routed by handler filters.
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # Prevent duplicate logs

    # Ensure logs directory exists with proper permissions
    log_dir = Path("logs")
    log_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

    # Console handler
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        # Show only INFO and above on the console (hide DEBUG messages).
        console_handler.setLevel(logging.INFO)

        if structured:
            console_handler.setFormatter(LinuxStructuredFormatter(include_lineno=True))
        else:
            console_handler.setFormatter(ColoredLinuxFormatter(use_colors=True))

        logger.addHandler(console_handler)

    # File handler with rotation
    if log_to_file:
        log_file = (
            log_dir / f"trading_{int(time.time() // 86400)}.log"
        )  # Daily rotation

        try:
            file_handler = RotatingFileHandler(
                filename=log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            # The logger itself is set to DEBUG; the main file should capture
            # INFO and above while allowing DEBUG to flow to the debug file.
            file_handler.setLevel(logging.INFO)
            if structured:
                file_handler.setFormatter(LinuxStructuredFormatter(include_lineno=True))
            else:
                file_handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s | %(name)s | %(levelname)s | %(process)d | %(thread)d | %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S%z",
                    )
                )
            logger.addHandler(file_handler)

            # Add separate DEBUG‑only log file (captures only DEBUG records)
            debug_log_dir = log_dir / "debug"
            debug_log_dir.mkdir(mode=0o755, exist_ok=True)
            date_str = time.strftime("%Y-%m-%d")
            debug_log_path = debug_log_dir / f"debug_{date_str}.log"
            debug_fh = logging.FileHandler(debug_log_path, encoding="utf-8")
            debug_fh.setLevel(logging.DEBUG)
            debug_fh.setFormatter(file_handler.formatter)

            class DebugFilter(logging.Filter):
                def filter(self, record):
                    return record.levelno == logging.DEBUG

            debug_fh.addFilter(DebugFilter())
            logger.addHandler(debug_fh)
        except (OSError, PermissionError) as e:
            # Fallback to console only if file can't be opened
            print(f"Warning: Could not create log file: {e}", file=sys.stderr)

    # Syslog handler for Linux system logging
    if log_to_syslog:
        try:
            syslog_handler = SysLogHandler(address="/dev/log")
            syslog_handler.setLevel(logging.WARNING)  # Only warnings+ to syslog
            syslog_handler.setFormatter(
                logging.Formatter("%(name)s[%(process)d]: %(levelname)s %(message)s")
            )
            logger.addHandler(syslog_handler)
        except (OSError, ConnectionRefusedError):
            # Syslog not available, continue without it
            pass

    return logger


def get_structured_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Convenience function to get JSON structured logger"""
    return get_logger(name, level, structured=True)


def log_domain_event(logger: logging.Logger, event: Mapping[str, Any]) -> None:
    """Best-effort structured event logging that can never escape to callers."""
    try:
        logger.info(
            "domain_event %s",
            event.get("event_type", "unknown"),
            extra={
                "correlation_id": event.get("correlation_id", ""),
                "event_type": event.get("event_type", "unknown"),
            },
        )
    except Exception:  # noqa: BLE001 - logging must never affect trading
        return


def suppress_third_party_logs():
    """Suppress noisy third-party library logs for better performance"""
    noisy_loggers = [
        "urllib3",
        "requests",
        "websocket",
        "websockets",
        "asyncio",
        "matplotlib",
        "PIL",
        "tqdm",
        "nse",
        "yfinance",
        "finnhub",
    ]

    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
        # Add null handler to prevent propagation
        logging.getLogger(logger_name).addHandler(NullHandler())
