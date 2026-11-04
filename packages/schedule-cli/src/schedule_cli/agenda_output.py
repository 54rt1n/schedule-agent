"""Human agenda presentation; application records are never changed here."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class AgendaContext:
    start: datetime
    end: datetime
    timezone: str


@dataclass
class Entry:
    row: dict
    start: datetime
    end: datetime | None
    all_day: bool = False
    task: bool = False
    date_only: bool = False


def clock(value, *, period=True):
    text = str(value.hour % 12 or 12)
    if value.minute or value.second or value.microsecond:
        text += f":{value.minute:02}"
    if value.second or value.microsecond:
        text += f":{value.second:02}"
    if value.microsecond:
        text += (f".{value.microsecond:06}").rstrip("0")
    return text + (" am" if value.hour < 12 else " pm") if period else text


def day_name(day, year=None):
    return f"{day:%A}, {day:%B} {day.day}" + (f", {day.year}" if day.year != year else "")


def instant(value, zone):
    point = datetime.fromisoformat(value)
    return point.replace(tzinfo=zone) if point.tzinfo is None else point.astimezone(zone)


def entry_for(row, zone, *, task=False):
    if row["kind"] == "timed":
        return Entry(row, instant(row["start_time"], zone), instant(row["end_time"], zone))
    anchor = row.get("start") or row.get("deadline")
    point = row.get("occurrence_start") or anchor
    date_only = bool(anchor and len(anchor) == 10)
    all_day = row["kind"] in {"all_day", "anniversary"}
    if task:
        owner = ZoneInfo(row.get("time_zone") or zone.key)
        matching = [
            value
            for value in (row.get("start"), row.get("deadline"))
            if value
            and instant(value, owner).astimezone(UTC) == instant(point, owner).astimezone(UTC)
        ]
        if matching:
            date_only = all(len(value) == 10 for value in matching)
    if date_only or all_day:
        # Date-only values retain their calendar date, including recurring instances.
        owner = ZoneInfo(row.get("time_zone") or zone.key)
        day = instant(point, owner).date() if len(point) > 10 else date.fromisoformat(point)
        start = datetime.combine(day, time.min, zone)
    else:
        start = instant(point, zone)
    end = None
    if all_day:
        days = (
            (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
            if row.get("end")
            else 1
        )
        end = start + timedelta(days=days)
    return Entry(row, start, end, all_day=all_day, task=task, date_only=date_only)


def continuous_spans(entries):
    """Group equal adjacent labels at midnight, retaining every source row elsewhere."""
    result = []
    fields = ("title", "calendar_id", "time_zone", "location", "notes", "description", "rrule")

    def matches(left, right):
        return all(left.row.get(key) == right.row.get(key) for key in fields) and left.row.get(
            "participant_identifiers", []
        ) == right.row.get("participant_identifiers", [])

    for entry in sorted(entries, key=lambda e: e.start.timestamp()):
        owner = ZoneInfo(entry.row.get("time_zone") or entry.start.tzinfo.key)
        if entry.row["kind"] == "timed" and entry.start.astimezone(owner).time() == time.min:
            previous = [
                e
                for e in result
                if e.row["kind"] == "timed" and e.end == entry.start and matches(e, entry)
            ]
            followers = [e for e in entries if e.start == entry.start and matches(e, entry)]
            if len(previous) == len(followers) == 1:
                previous[0].end = entry.end
                continue
        result.append(entry)
    return result


def time_label(entry, day):
    start, end = entry.start, entry.end
    if entry.all_day:
        return "All day"
    if end is None:
        return clock(start)
    last_day = (end - timedelta(microseconds=1)).date()
    if start.date() < day:
        until = clock(end) if last_day == day else f"{day_name(end.date(), day.year)}, {clock(end)}"
        if end.time() == time.min and end.date() == day + timedelta(days=1):
            until = "midnight"
        return "Continues until " + until
    if end.date() != start.date():
        if end.date() == day + timedelta(days=1) and end.time() == time.min:
            return clock(start) + "–midnight"
        return f"{clock(start)}–{day_name(end.date(), day.year)}, {clock(end)}"
    if start.utcoffset() != end.utcoffset():
        return f"{clock(start)} {start.tzname()}–{clock(end)} {end.tzname()}"
    return clock(start, period=(start.hour < 12) != (end.hour < 12)) + "–" + clock(end)


def task_label(entry):
    row = entry.row
    point = entry.start
    owner = ZoneInfo(row.get("time_zone") or point.tzinfo.key)
    original = instant(row.get("occurrence_start") or row.get("start") or row["deadline"], owner)
    matches = []
    for field, label in (("start", "Scheduled"), ("deadline", "Due")):
        value = row.get(field)
        if value and instant(value, owner).astimezone(UTC) == original.astimezone(UTC):
            matches.append(label)
    label = " and ".join(matches) if matches else "Recurring" if row.get("rrule") else ""
    state = " (blocked)" if row["state"] == "BLOCKED" else ""
    prefix = ((label + " task") if label else "Task") + state + ": "
    return (clock(point) + "  " if not entry.date_only else "") + prefix


def detail_lines(row):
    for field in ("description", "notes"):
        if row.get(field):
            yield from ("      " + line for line in row[field].splitlines())
    if row.get("participant_identifiers"):
        yield "      With: " + ", ".join(row["participant_identifiers"])


def format_agenda(result, context, *, details=False):
    zone = ZoneInfo(context.timezone)
    start, end = context.start.astimezone(zone), context.end.astimezone(zone)
    entries = continuous_spans([entry_for(row, zone) for row in result["events"]])
    entries.extend(entry_for(row, zone, task=True) for row in result["tasks"])
    days = defaultdict(list)
    for entry in entries:
        day = max(entry.start.date(), start.date())
        last = (entry.end - timedelta(microseconds=1)).date() if entry.end else entry.start.date()
        # The query may overlap a foreign-zone date at the edge of this window.
        # Retain that returned calendar date instead of silently hiding the record.
        if (entry.all_day or entry.date_only) and (
            last < start.date() or entry.start.date() > (end - timedelta(microseconds=1)).date()
        ):
            days[entry.start.date()].append(entry)
            continue
        while day <= last and day <= (end - timedelta(microseconds=1)).date():
            days[day].append(entry)
            day += timedelta(days=1)
    if not days:
        heading = day_name(start.date())
        last = (end - timedelta(microseconds=1)).date()
        if last != start.date():
            heading += "–" + day_name(last)
        return heading + "\n  No scheduled events or tasks."
    output = []
    for day, rows in sorted(days.items()):
        if output:
            output.append("")
        output.append(day_name(day))
        tasks_started = False
        for entry in sorted(rows, key=lambda e: (e.task, e.start.timestamp(), e.row["title"])):
            if entry.task and not tasks_started:
                output.append("  Tasks")
                tasks_started = True
            label = task_label(entry) if entry.task else time_label(entry, day) + "  "
            location = " — " + entry.row["location"] if entry.row.get("location") else ""
            output.append("    " + label + entry.row["title"] + location)
            if entry.start.date() < start.date() and day == start.date() and not entry.all_day:
                output.append(
                    f"      Started {day_name(entry.start.date(), day.year)}, {clock(entry.start)}"
                )
            if details:
                output.extend(detail_lines(entry.row))
    return "\n".join(output)
