"""Loss-aware exports of calendar definitions or a bounded occurrence view."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select

from schedule_core import calendar_service as service
from schedule_core.contracts import Contract
from schedule_core.interchange import ImportedException
from schedule_core.items import item_agenda, item_list


class ExportRequest(Contract):
    format: Literal["org", "ics", "json"] = "org"
    mode: Literal["definitions", "occurrences"] = "definitions"
    start: datetime | None = None
    end: datetime | None = None
    calendar: str | None = None


class ExportResult(Contract):
    schema_version: Literal[1] = 1
    success: Literal[True] = True
    format: str
    count: int
    content: str


def stamp(value, repeat=None):
    fmt = "%Y-%m-%d %a %H:%M" if isinstance(value, datetime) else "%Y-%m-%d %a"
    return "<" + value.strftime(fmt) + (" " + repeat if repeat else "") + ">"


def flatten_item(item, start, end):
    """Export concrete instances with stable identities and no recurrence metadata."""
    if not item.repeat and not item.rrule and item.kind != "anniversary":
        return [item] if item_agenda(item, start, end) else []
    if item.kind == "task" and not item.rrule:
        # Completion-based repeaters have one current action, not forecast instances.
        if not item_agenda(item, start, end):
            return []
        identity = item.id
        if item.repeat:
            anchor = item.start or item.deadline
            identity = uuid5(item.id, "task-action:" + anchor.isoformat())
        return [item.model_copy(update={"id": identity, "repeat": None})]
    zone = ZoneInfo(item.time_zone)

    def local(value):
        if isinstance(value, datetime):
            return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
        return datetime.combine(value, datetime.min.time(), zone)

    anchor = item.start or item.deadline
    if anchor is None:
        return []
    origin = local(anchor)
    deadline_offset = local(item.deadline) - origin if item.deadline else timedelta()
    seed = item.model_copy(update={"deadline": None}) if item.start else item
    rows = item_agenda(
        seed, start - max(deadline_offset, timedelta()), end - min(deadline_offset, timedelta())
    )
    result = []
    for row in rows:
        point = datetime.fromisoformat(row["occurrence_start"]).astimezone(zone)
        delta = point - origin
        changes = {
            "id": uuid5(item.id, point.isoformat()),
            "title": row["title"],
            "repeat": None,
            "rrule": None,
            "kind": "all_day" if item.kind == "anniversary" else item.kind,
        }
        for field in ("start", "end", "deadline"):
            value = getattr(item, field)
            if value is not None:
                shifted = local(value) + delta
                changes[field] = shifted if isinstance(value, datetime) else shifted.date()
        instance = item.model_copy(update=changes)
        if item_agenda(instance, start, end):
            result.append(instance)
    return result


def timed_snapshot_ids(event):
    """IDs produced by reimporting the existing Org and ICS occurrence identities."""
    original = event.occurrence.original_start.isoformat()
    return {
        uuid5(event.id, original),
        uuid5(NAMESPACE_URL, f"calendar-uid:{event.id}/{original}"),
    }


async def export_calendar(session, request):
    if (request.start is None) != (request.end is None):
        raise ValueError("Export range needs both --start and --end")
    events = await service.list_events(
        session,
        request.start,
        request.end,
        definitions=request.mode == "definitions",
        calendar=request.calendar,
    )
    if request.mode == "definitions" and request.start:
        relevant = await service.list_events(
            session, request.start, request.end, calendar=request.calendar
        )
        ids = {e.id for e in relevant}
        events = [e for e in events if e.id in ids]
    items = await item_list(session)
    # A persisted snapshot owns its identity, including its edits, completion and
    # calendar/range placement. Never regenerate its former state from the source.
    persisted_ids = {item.id for item in items}
    if request.mode == "occurrences":
        persisted_ids.update((await session.execute(select(service.Event.id))).scalars())
        events = [
            event for event in events
            if not event.occurrence or not timed_snapshot_ids(event) & persisted_ids
        ]
    if request.calendar:
        items = [i for i in items if i.calendar_id == request.calendar]
    if request.start:
        if request.mode == "occurrences":
            items = [
                instance
                for item in items
                for instance in flatten_item(item, request.start, request.end)
                if instance.id == item.id or instance.id not in persisted_ids
            ]
        else:
            items = [i for i in items if item_agenda(i, request.start, request.end)]
    base = (
        {e.id: await service.get_event(session, e.id) for e in events}
        if request.mode == "definitions"
        else {}
    )
    if request.format == "json":
        return ExportResult(
            format="json",
            count=len(events) + len(items),
            content=json.dumps(
                {
                    "schema_version": 1,
                    "events": [e.model_dump(mode="json") for e in events],
                    "items": [i.model_dump(mode="json") for i in items],
                },
                indent=2,
            ),
        )
    if request.format == "ics":
        return ExportResult(
            format="ics",
            count=len(events) + len(items),
            content=render_ics(events, items, base),
        )
    chunks = []
    for e in events:
        zone = ZoneInfo(e.time_zone)
        start = e.start_time.astimezone(zone)
        end = e.end_time.astimezone(zone)
        lines = [
            "* " + e.title,
            ":PROPERTIES:",
            f":EVENT_ID: {uuid5(e.id, e.occurrence.original_start.isoformat()) if e.occurrence else e.id}",
            f":TIMEZONE: {e.time_zone}",
            f":CALENDAR: {e.calendar_id}",
        ]
        if e.description is not None:
            lines.append(":DESCRIPTION_JSON: " + json.dumps(e.description))
        if e.notes is not None:
            lines.append(":NOTES_JSON: " + json.dumps(e.notes))
        if e.location:
            lines.append(":LOCATION: " + e.location)
        if e.rrule and request.mode == "definitions":
            lines.append(":RRULE: " + e.rrule)
        if e.participant_identifiers:
            lines.append(":PARTICIPANTS: " + ", ".join(e.participant_identifiers))
        row = base.get(e.id)
        if row and row.excluded_dates:
            lines.append(
                ":EXCLUDED: "
                + json.dumps([service.utc(x.excluded_date).isoformat() for x in row.excluded_dates])
            )
        if row and row.modified_instances:
            mods = []
            for m in row.modified_instances:
                values = e.model_dump(include=set(service.EventCreateRequest.model_fields))
                values.update(
                    start_time=service.utc(m.start_time),
                    end_time=service.utc(m.end_time),
                    title=m.title or e.title,
                    time_zone=m.time_zone,
                    location=m.location,
                    notes=m.notes,
                    rrule=None,
                )
                mods.append(
                    ImportedException(
                        original_start=service.utc(m.original_date),
                        event=service.EventCreateRequest.model_validate(values),
                    ).model_dump(mode="json")
                )
            lines.append(":MODIFIED: " + json.dumps(mods))
        lines.extend(
            [
                ":END:",
                stamp(start)[:-1] + "-" + end.strftime("%H:%M") + ">"
                if start.date() == end.date()
                else stamp(start) + "--" + stamp(end),
            ]
        )
        chunks.append("\n".join(lines))
    for item in items:
        zone = ZoneInfo(item.time_zone)

        def local_stamp(value, repeat=None):
            return stamp(value.astimezone(zone) if isinstance(value, datetime) else value, repeat)

        prefix = item.state + " " if item.kind == "task" else ""
        lines = [
            "* " + prefix + item.title,
            ":PROPERTIES:",
            f":ID: {item.id}",
            f":SCHEDULE_KIND: {item.kind}",
            f":TIMEZONE: {item.time_zone}",
            f":CALENDAR: {item.calendar_id}",
            *([":RRULE: " + item.rrule] if item.rrule else []),
            *([":LOCATION: " + item.location] if item.location else []),
            *([":NOTES_JSON: " + json.dumps(item.notes)] if item.notes is not None else []),
            ":END:",
        ]
        if item.start:
            lines.append(
                ("SCHEDULED: " if item.kind == "task" else "")
                + local_stamp(
                    item.start,
                    item.repeat or ("+1y" if item.kind == "anniversary" else None),
                )
                + (
                    "--" + local_stamp(
                        item.end - timedelta(days=1)
                        if isinstance(item.end, date) and not isinstance(item.end, datetime)
                        else item.end
                    ) if item.end else ""
                )
            )
        if item.deadline:
            lines.append("DEADLINE: " + local_stamp(item.deadline, item.repeat))
        chunks.append("\n".join(lines))
    return ExportResult(
        format="org",
        count=len(events) + len(items),
        content="\n\n".join(chunks) + ("\n" if chunks else ""),
    )


def render_ics(events, items, base):
    from icalendar import Calendar, Event, Todo, vRecur

    cal = Calendar()
    cal.add("prodid", "-//Schedule Agent//Calendar//EN")
    cal.add("version", "2.0")

    def entry(e, uid, original=None):
        c = Event()
        c.add("uid", str(uid))
        c.add("dtstamp", datetime.now(UTC))
        c.add("summary", e.title)
        c.add("x-schedule-calendar", e.calendar_id)
        c.add("x-schedule-timezone", e.time_zone)
        if e.description is not None:
            c.add("x-schedule-description", e.description)
        zone = ZoneInfo(e.time_zone)
        c.add("dtstart", e.start_time.astimezone(zone))
        c.add("dtend", e.end_time.astimezone(zone))
        if e.notes:
            c.add("description", e.notes)
        if e.location:
            c.add("location", e.location)
        if original:
            c.add("recurrence-id", original.astimezone(zone))
        elif e.rrule:
            c.add("rrule", vRecur.from_ical(e.rrule))
        for person in e.participant_identifiers:
            c.add("attendee", "MAILTO:" + person)
        return c

    for e in events:
        row = base.get(e.id)
        # Flattened instances get unique UIDs, not duplicate series UIDs.
        uid = (
            str(e.id)
            if row or not e.occurrence
            else f"{e.id}/{e.occurrence.original_start.isoformat()}"
        )
        values = e.model_copy(update={"rrule": e.rrule if row else None})
        c = entry(values, uid)
        if row:
            for x in row.excluded_dates:
                c.add(
                    "exdate",
                    service.utc(x.excluded_date).astimezone(ZoneInfo(e.time_zone)),
                )
        cal.add_component(c)
        if row:
            for m in row.modified_instances:
                changed = e.model_copy(
                    update=dict(
                        start_time=service.utc(m.start_time),
                        end_time=service.utc(m.end_time),
                        title=m.title or e.title,
                        time_zone=m.time_zone,
                        notes=m.notes,
                        location=m.location,
                        rrule=None,
                    )
                )
                cal.add_component(entry(changed, e.id, service.utc(m.original_date)))
    for item in items:
        c = Todo() if item.kind == "task" else Event()
        c.add("uid", str(item.id))
        c.add("x-schedule-kind", item.kind)
        c.add("x-schedule-calendar", item.calendar_id)
        c.add("x-schedule-timezone", item.time_zone)
        if item.repeat:
            c.add("x-org-repeat", item.repeat)
        if item.location:
            c.add("location", item.location)
        c.add("dtstamp", datetime.now(UTC))
        c.add("summary", item.title)
        if item.start:
            c.add("dtstart", item.start)
        if item.end:
            c.add("dtend", item.end)
        if item.deadline:
            c.add("due", item.deadline)
        if item.kind == "task":
            c.add(
                "status",
                dict(
                    TODO="NEEDS-ACTION",
                    DONE="COMPLETED",
                    BLOCKED="IN-PROCESS",
                    CANCELLED="CANCELLED",
                )[item.state],
            )
        if item.rrule:
            c.add("rrule", vRecur.from_ical(item.rrule))
        if item.repeat and not item.rrule and item.kind != "task":
            # Org task completion repeaters have no equivalent RFC recurrence semantics.
            import re

            # RFC month/year rules skip invalid dates; Org advancement clamps them.
            # Preserve which rule is only a projection for other calendar clients.
            c.add("x-schedule-derived-rrule", "TRUE")
            number, unit = re.fullmatch(r"(?:\+\+|\.\+|\+)(\d+)([dwmy])", item.repeat).groups()
            c.add(
                "rrule",
                {
                    "freq": dict(d="daily", w="weekly", m="monthly", y="yearly")[unit],
                    "interval": int(number),
                },
            )
        if item.kind == "anniversary" and not item.repeat and not item.rrule:
            c.add("x-schedule-derived-rrule", "TRUE")
            c.add("rrule", {"freq": "yearly"})
        if item.notes:
            c.add("description", item.notes)
        cal.add_component(c)
    return cal.to_ical().decode()
