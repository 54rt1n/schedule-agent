"""Complete profile agenda shared by the CLI and MCP transports."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from schedule_core.agenda_contracts import AgendaRequest


def agenda_window(request: AgendaRequest, default_timezone: str):
    zone = ZoneInfo(request.timezone or default_timezone)
    if sum((request.date is not None, request.today, request.week)) > 1:
        raise ValueError("Choose only one of date, today, or week")
    if request.start is not None or request.end is not None:
        if request.date is not None or request.today or request.week:
            raise ValueError("Use explicit start/end or a date selector, not both")
        start, end = request.start, request.end
        if start is None or end is None:
            raise ValueError("Provide both start and end")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Explicit agenda start/end must include UTC offsets")
        if start >= end:
            raise ValueError("Agenda end must be after start")
        return start, end
    day = date.fromisoformat(request.date) if request.date else datetime.now(zone).date()
    if request.week:
        day -= timedelta(days=day.weekday())
    start = datetime.combine(day, datetime.min.time(), zone)
    return start, start + timedelta(days=7 if request.week else 1)
