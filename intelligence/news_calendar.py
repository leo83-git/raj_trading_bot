# ═══════════════════════════════════════════════════════════════
#  News/Event Awareness — Avoid trading during high-impact events
# ═══════════════════════════════════════════════════════════════
import datetime
from dataclasses import dataclass

from quant_utils.logger import get_logger

log = get_logger("news_awareness")


@dataclass
class Event:
    name: str
    date: datetime.datetime
    type: str  # RBI, BUDGET, FED, EARNINGS,Results: None
    impact: str  # HIGH, MEDIUM, LOW
    symbols_affected: list[str]
    blackout_minutes: int  # Minutes before/after to avoid


class EventCalendar:
    """Tracks high-impact events to avoid trading"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.events = []
        self._init_upcoming_events()

    def _init_upcoming_events(self):
        """Initialize known upcoming high-impact events for 2024-2025"""

        # RBI Policy Meetings (Reserve Bank of India)
        rbi_dates = [
            datetime.datetime(2024, 4, 3, 10, 0),
            datetime.datetime(2024, 6, 5, 10, 0),
            datetime.datetime(2024, 8, 7, 10, 0),
            datetime.datetime(2024, 10, 9, 10, 0),
            datetime.datetime(2024, 12, 4, 10, 0),
            datetime.datetime(2025, 2, 7, 10, 0),
            datetime.datetime(2025, 4, 9, 10, 0),
            datetime.datetime(2025, 6, 4, 10, 0),
            datetime.datetime(2025, 8, 6, 10, 0),
            datetime.datetime(2025, 10, 8, 10, 0),
            datetime.datetime(2025, 12, 3, 10, 0),
        ]

        for date in rbi_dates:
            self.events.append(
                Event(
                    name="RBI Policy",
                    date=date,
                    type="RBI",
                    impact="HIGH",
                    symbols_affected=[
                        "NIFTY",
                        "BANKNIFTY",
                        "NIFTY50",
                        "RELIANCE",
                        "HDFCBANK",
                        "ICICIBANK",
                    ],
                    blackout_minutes=90,  # 90 min: 30 before + 60 after
                )
            )

        # India Union Budget
        budget_dates = [
            datetime.datetime(2024, 7, 23, 11, 0),  # Usually last week of July
            datetime.datetime(2025, 2, 1, 11, 0),  # Usually Feb 1
            datetime.datetime(2025, 7, 23, 11, 0),
        ]

        for date in budget_dates:
            self.events.append(
                Event(
                    name="Union Budget",
                    date=date,
                    type="BUDGET",
                    impact="HIGH",
                    symbols_affected=[
                        "NIFTY",
                        "BANKNIFTY",
                        "NIFTY50",
                        "RELIANCE",
                        "TCS",
                        "HDFCBANK",
                    ],
                    blackout_minutes=180,  # 3 hours: 1 before + 2 after
                )
            )

        # US Fed Meetings (approximate - check actual dates)
        fed_dates = [
            datetime.datetime(2024, 1, 30, 22, 30),  # Typically 2 days after RBI
            datetime.datetime(2024, 3, 19, 22, 30),
            datetime.datetime(2024, 5, 1, 22, 30),
            datetime.datetime(2024, 6, 11, 22, 30),
            datetime.datetime(2024, 7, 30, 22, 30),
            datetime.datetime(2024, 9, 17, 22, 30),
            datetime.datetime(2024, 11, 6, 22, 30),
            datetime.datetime(2024, 12, 17, 22, 30),
            datetime.datetime(2025, 1, 28, 22, 30),
            datetime.datetime(2025, 3, 18, 22, 30),
            datetime.datetime(2025, 5, 6, 22, 30),
            datetime.datetime(2025, 6, 17, 22, 30),
        ]

        for date in fed_dates:
            self.events.append(
                Event(
                    name="Fed Meeting",
                    date=date,
                    type="FED",
                    impact="HIGH",
                    symbols_affected=[
                        "NIFTY",
                        "BANKNIFTY",
                        "NIFTY50",
                    ],  # Global risk-off
                    blackout_minutes=180,  # 3 hours: 1 before + 2 after
                )
            )

        # Major earnings (update with actual dates)
        earnings_events = [
            {"symbol": "RELIANCE", "date": datetime.datetime(2024, 10, 16, 14, 30)},
            {"symbol": "TCS", "date": datetime.datetime(2024, 10, 10, 14, 30)},
            {"symbol": "INFY", "date": datetime.datetime(2024, 10, 18, 14, 30)},
            {"symbol": "HDFCBANK", "date": datetime.datetime(2024, 10, 15, 14, 30)},
        ]

        for ev in earnings_events:
            self.events.append(
                Event(
                    name=f"{ev['symbol']} Earnings",
                    date=ev["date"],
                    type="EARNINGS",
                    impact="MEDIUM",
                    symbols_affected=[ev["symbol"]],
                    blackout_minutes=60,
                )
            )

        log.info(f"Event calendar initialized with {len(self.events)} events")

    def get_upcoming_events(self, hours: int = 24) -> list[Event]:
        """Get events in the next N hours"""
        now = datetime.datetime.now()
        cutoff = now + datetime.timedelta(hours=hours)

        upcoming = []
        for event in self.events:
            if now <= event.date <= cutoff:
                upcoming.append(event)

        return sorted(upcoming, key=lambda e: e.date)

    def is_trading_allowed(self, symbol: str) -> tuple:
        """
        Check if trading is allowed for the symbol.

        Returns:
            (allowed: bool, reason: str)
        """
        now = datetime.datetime.now()

        for event in self.events:
            event_start = event.date - datetime.timedelta(
                minutes=event.blackout_minutes // 2
            )
            event_end = event.date + datetime.timedelta(minutes=event.blackout_minutes)

            if event_start <= now <= event_end:
                if (
                    not symbol
                    or symbol in event.symbols_affected
                    or "ALL" in event.symbols_affected
                ):
                    remaining = (event.date - now).total_seconds() / 60

                    if remaining > 0:
                        reason = f"{event.name} in {int(remaining)}min ({event.impact} impact)"
                    else:
                        reason = f"{event.name} ended {int(-remaining)}min ago ({event.impact} impact)"

                    return False, reason

        return True, "No high-impact events"

    def get_events_today(self) -> list[Event]:
        """Get all events for today"""
        now = datetime.datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)

        return [e for e in self.events if today_start <= e.date <= today_end]

    def should_avoid_event(self, event_type: str = None) -> tuple:
        """Check if currently in an event blackout window"""
        now = datetime.datetime.now()

        for event in self.events:
            if event_type and event.type != event_type:
                continue

            event_start = event.date - datetime.timedelta(
                minutes=event.blackout_minutes // 2
            )
            event_end = event.date + datetime.timedelta(minutes=event.blackout_minutes)

            if event_start <= now <= event_end:
                return True, event.name + f" ({event.type})"

        return False, None


class NewsFilter:
    """Simple news filter to avoid trading during major news"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.calendar = EventCalendar(config)

        self.keywords_high_impact = [
            "rbi governor",
            "rbi policy",
            "repo rate",
            "reverse repo",
            "union budget",
            "tax",
            "fiscal deficit",
            "fed chair",
            "fed rate",
            "interest rate",
            "federal reserve",
            "inflation",
            "wpi",
            "cpi",
            "trade war",
            "tariff",
            "import duty",
        ]

    def check_news_impact(self, news_headlines: list[str]) -> tuple:
        """
        Check if any news headlines indicate high-impact event.

        Returns:
            (should_avoid: bool, reason: str)
        """
        if not news_headlines:
            return False, ""

        for headline in news_headlines:
            headline_lower = headline.lower()

            for keyword in self.keywords_high_impact:
                if keyword in headline_lower:
                    return True, f"High-impact keyword: {keyword}"

        return False, ""

    def should_trade(self, symbol: str, news_headlines: list[str] = None) -> tuple:
        """
        Comprehensive check: calendar events + news.

        Returns:
            (allowed: bool, reason: str)
        """
        allowed, reason = self.calendar.is_trading_allowed(symbol)
        if not allowed:
            return False, reason

        if news_headlines:
            news_allowed, news_reason = self.check_news_impact(news_headlines)
            if not news_allowed:
                return False, news_reason

        return True, "OK"


def create_news_filter(config: dict = None) -> NewsFilter:
    return NewsFilter(config)
