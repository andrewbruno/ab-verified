"""Small shared helpers: identifiers, time, money, JSON columns.

Identifiers are UUIDs, never sequential integers (§13, enumeration
resistance). Instants are stored in UTC and displayed in Australia/Sydney
(NFR-13).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

SYDNEY = timezone(timedelta(hours=10))  # AEST; the display offset only.


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def in_days(days: int) -> str:
    return iso(now() + timedelta(days=days))


def in_minutes(minutes: int) -> str:
    return iso(now() + timedelta(minutes=minutes))


def is_past(value: str | None) -> bool:
    parsed = parse(value)
    return parsed is not None and parsed <= now()


def json_load(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return [] if default is None else default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return [] if default is None else default


def json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def as_bool(value: Any) -> bool:
    return bool(value) and value not in ("0", "false", "False")


# --- Display -------------------------------------------------------------
def fmt_datetime(value: str | datetime | None) -> str:
    parsed = parse(value) if isinstance(value, str) else value
    if parsed is None:
        return "-"
    local = parsed.astimezone(SYDNEY)
    return f"{local.day} {local:%b %Y}, {local:%I:%M %p}".replace(" 0", " ")


def fmt_date(value: str | date | None) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, str):
        parsed = parse(value)
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(value + "T00:00:00+00:00")
            except ValueError:
                return value
        value = parsed
    if isinstance(value, datetime):
        value = value.astimezone(SYDNEY).date()
    return value.strftime("%d %b %Y").lstrip("0")


def fmt_money(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"${value:,.0f}"


def fmt_range(low: float | None, high: float | None) -> str:
    if low is None and high is None:
        return "Not stated"
    if low is not None and high is not None:
        return f"{fmt_money(low)} to {fmt_money(high)}"
    return fmt_money(low if low is not None else high)


def relative(value: str | None) -> str:
    """A short 'in 3 days' / '2 hours ago' for closing dates and queue ages."""
    parsed = parse(value)
    if parsed is None:
        return "-"
    delta = parsed - now()
    seconds = int(abs(delta.total_seconds()))
    future = delta.total_seconds() > 0
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        unit, count = "minute", seconds // 60
    elif seconds < 86400:
        unit, count = "hour", seconds // 3600
    else:
        unit, count = "day", seconds // 86400
    plural = "" if count == 1 else "s"
    return f"in {count} {unit}{plural}" if future else f"{count} {unit}{plural} ago"
