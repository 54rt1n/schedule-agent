"""Retain nominal-day versus elapsed-time ICS duration semantics during parsing."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from icalendar.parser import Contentlines


def component_durations(raw: bytes) -> list[str | None]:
    """Read original durations in component order, excluding nested alarm properties.

    icalendar decodes both PT24H and P1D to the same timedelta. Keep the original
    unfolded value so that daylight-saving transitions do not erase that distinction.
    """
    durations: list[str | None] = []
    stack: list[int | None] = []
    for line in Contentlines.from_ical(raw):
        if not line:
            continue
        name, _, value = line.parts()
        name = name.upper()
        if name == "BEGIN":
            index = None
            if value.upper() in {"VEVENT", "VTODO"}:
                index = len(durations)
                durations.append(None)
            stack.append(index)
        elif name == "END":
            stack.pop()
        elif name == "DURATION" and stack and stack[-1] is not None:
            durations[stack[-1]] = value
    return durations


def duration_end(start: date | datetime, duration: str, timezone: str) -> date | datetime:
    match = re.fullmatch(
        r"(?P<sign>[+-]?)P(?:(?P<weeks>\d+)W|(?:(?P<days>\d+)D)?"
        r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?)",
        duration,
    )
    if match is None or not any(
        match[name] for name in ("weeks", "days", "hours", "minutes", "seconds")
    ):
        raise ValueError("Invalid ICS duration")
    sign = -1 if match["sign"] == "-" else 1
    days = sign * (7 * int(match["weeks"] or 0) + int(match["days"] or 0))
    clock = sign * timedelta(
        hours=int(match["hours"] or 0),
        minutes=int(match["minutes"] or 0),
        seconds=int(match["seconds"] or 0),
    )
    if not isinstance(start, datetime):
        if clock:
            raise ValueError("Date-only ICS events require a day or week duration")
        return start + timedelta(days=days)
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo(timezone))
    nominal_end = start + timedelta(days=days)
    return (nominal_end.astimezone(UTC) + clock).astimezone(start.tzinfo)
