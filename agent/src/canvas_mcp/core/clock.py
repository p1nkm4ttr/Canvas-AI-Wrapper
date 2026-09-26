"""Local time: the one place that knows the configured TIMEZONE.

Canvas timestamps are UTC. "Today", "tomorrow", and "which calendar day is
this deadline on" must all be answered in the student's zone, never the
host's: a laptop left on UTC (or a server in another country) would
otherwise schedule reviews for the wrong day and pick the wrong module for
a date. Nothing downstream may call date.today() directly.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import get_config


def local_tz() -> ZoneInfo | timezone:
    name = get_config().timezone or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def local_today() -> date:
    return datetime.now(local_tz()).date()


def local_date_of(iso_utc: str) -> date | None:
    """Calendar date, in the configured zone, of a Canvas ISO timestamp.
    Returns None for anything unparseable (every Canvas field is optional)."""
    try:
        return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(local_tz()).date()
    except (ValueError, AttributeError, TypeError):
        return None
