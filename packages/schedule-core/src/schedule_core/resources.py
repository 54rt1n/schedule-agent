"""Resource operations hide legacy event/item storage from both transports."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from schedule_core import calendar_service as service
from schedule_core.agenda import agenda_window
from schedule_core.agenda_contracts import AgendaRequest
from schedule_core.contracts import EventCreateRequest, EventPatchRequest
from schedule_core.db.models import Event
from schedule_core.items import (
    ItemRecord,
    ItemRequest,
    complete,
    item_agenda,
    item_get,
    item_list,
    item_write,
)
from schedule_core.resource_contracts import (
    EventInput,
    EventRecord,
    EventResult,
    ScheduleResult,
    TaskRecord,
    TaskResult,
)


def event_record(value):
    if hasattr(value, "start_time"):
        return TypeAdapter(EventRecord).validate_python({"kind": "timed", **value.model_dump()})
    body = value.model_dump() if hasattr(value, "model_dump") else dict(value)
    fields = {
        "id",
        "kind",
        "title",
        "start",
        "time_zone",
        "location",
        "notes",
        "calendar_id",
        "rrule",
        "repeat",
        "occurrence_start",
    }
    if body["kind"] == "all_day":
        fields.add("end")
    return TypeAdapter(EventRecord).validate_python({k: v for k, v in body.items() if k in fields})


async def create_event(session, request, timezone):
    body = request.event.model_dump(exclude={"kind"})
    body["time_zone"] = body["time_zone"] or timezone
    if request.event.kind == "timed":
        value = await service.create_event(session, EventCreateRequest.model_validate(body))
    else:
        value = await item_write(session, ItemRequest(kind=request.event.kind, **body))
    record = event_record(value)
    return EventResult(event_id=record.id, event=record)


async def update_event(session, request):
    patch = request.patch.model_dump(exclude_unset=True)
    if not patch:
        raise ValueError("Provide at least one patch field")
    if await session.get(Event, request.id) is not None:
        value = await service.update_event(
            session,
            request.id,
            EventPatchRequest.model_validate(patch),
            scope=request.scope,
            occurrence=request.occurrence,
        )
    else:
        item = await item_get(session, request.id)
        if item.kind == "task":
            raise ValueError("Use task operations for a task")
        if request.scope != "series" or request.occurrence is not None:
            raise ValueError("Point/all-day/anniversary edits currently require series scope")
        original = event_record(item)
        merged = {**original.model_dump(exclude={"id", "occurrence_start"}), **patch}
        event = TypeAdapter(EventInput).validate_python(merged)
        body = event.model_dump()
        if body["time_zone"] is None:
            raise ValueError("time_zone cannot be cleared")
        value = await item_write(session, ItemRequest(**body), item.id)
    record = event_record(value)
    return EventResult(event_id=record.id, event=record)


async def delete_event(session, request):
    if await session.get(Event, request.id) is not None:
        await service.delete_event(
            session, request.id, scope=request.scope, occurrence=request.occurrence
        )
    else:
        item = await item_get(session, request.id)
        if item.kind == "task":
            raise ValueError("Use task operations for a task")
        if request.scope != "series" or request.occurrence is not None:
            raise ValueError("Point/all-day/anniversary deletion currently requires series scope")
        await session.delete(await session.get(ItemRecord, item.id))
    return EventResult(event_id=request.id)


async def task_operation(session, action, request, timezone):
    if action == "create":
        body = request.model_dump()
        body["time_zone"] = body["time_zone"] or timezone
        item = await item_write(session, ItemRequest(kind="task", **body))
    else:
        item = await item_get(session, request.id)
        if item.kind != "task":
            raise ValueError("Task operations require a task ID")
        if action == "delete":
            await session.delete(await session.get(ItemRecord, item.id))
            return TaskResult(task_id=item.id)
        if action == "complete":
            if item.state in {"DONE", "CANCELLED"}:
                raise ValueError("Only TODO or BLOCKED tasks can be completed")
            item = await complete(session, item.id, request.completed_at)
        elif action == "update":
            patch = request.patch.model_dump(exclude_unset=True)
            if not patch:
                raise ValueError("Provide at least one patch field")
            if item.state == "DONE" and patch.get("state", "DONE") not in {"DONE", "TODO"}:
                raise ValueError("Reopen a completed task to TODO before changing its state")
            merged = {**item.model_dump(exclude={"id", "completed_at"}), **patch}
            item = await item_write(session, ItemRequest.model_validate(merged), item.id)
        else:
            raise ValueError(f"Unknown task operation: {action}")
    return TaskResult(task_id=item.id, task=TaskRecord.model_validate(item.model_dump()))


def item_matches(item, request):
    if request.id and item.id != request.id:
        return False
    for field, expected in [("calendar_id", request.calendar), ("kind", request.kind)]:
        if expected and getattr(item, field) != expected:
            return False
    for field, expected in [("title", request.title), ("location", request.location)]:
        if expected and expected.casefold() not in (getattr(item, field) or "").casefold():
            return False
    if item.kind != "task":
        return not any((request.state, request.scheduled, request.due_before, request.participant))
    if request.participant:
        return False
    if request.state and item.state != request.state:
        return False
    if request.scheduled and (not item.start or str(item.start)[:10] != str(request.scheduled)):
        return False
    return not request.due_before or bool(
        item.deadline and str(item.deadline)[:10] <= str(request.due_before)
    )


async def query_schedule(session, request, timezone):
    start = end = None
    if request.occurrence is not None:
        event = await service.get_event(session, request.id)
        original = service.occurrence_start(event, request.occurrence)
        modified = next(
            (m for m in event.modified_instances if service.utc(m.original_date) == original), None
        )
        start = service.utc(modified.start_time) if modified else original
        end = start + timedelta(microseconds=1)
    elif request.view == "occurrences":
        window = AgendaRequest.model_validate(
            {k: getattr(request, k) for k in AgendaRequest.model_fields}
        )
        start, end = agenda_window(window, timezone)
    events, tasks = [], []
    if (
        request.resource != "tasks"
        and request.kind in {None, "timed"}
        and not any((request.state, request.scheduled, request.due_before))
    ):
        values = await service.list_events(
            session,
            start,
            end,
            definitions=request.view == "definitions",
            title=request.title,
            calendar=request.calendar,
            participant=request.participant,
            location=request.location,
        )
        events = [
            event_record(e)
            for e in values
            if (not request.id or e.id == request.id)
            and (
                request.occurrence is None
                or e.occurrence.original_start == service.utc(request.occurrence)
            )
        ]
    for item in await item_list(session):
        if request.resource == "events" and item.kind == "task":
            continue
        if request.resource == "tasks" and item.kind != "task":
            continue
        if not item_matches(item, request):
            continue
        rows = item_agenda(item, start, end) if start is not None else [item.model_dump()]
        if item.kind == "task":
            tasks.extend(TaskRecord.model_validate(row) for row in rows)
        else:
            events.extend(event_record(row) for row in rows)

    # Stable ordering independent of the backing table or insertion order.
    def key(row):
        occurrence = getattr(row, "occurrence_start", None)
        point = occurrence or getattr(row, "start_time", None) or getattr(row, "start", None)
        if isinstance(point, datetime):
            point = point.replace(tzinfo=ZoneInfo(row.time_zone)) if point.tzinfo is None else point
            return (point.timestamp(), str(row.id))
        if point is not None:
            return (
                datetime.combine(point, datetime.min.time(), ZoneInfo(row.time_zone)).timestamp(),
                str(row.id),
            )
        return (float("inf"), str(row.id))

    return ScheduleResult(
        count=len(events) + len(tasks), events=sorted(events, key=key), tasks=sorted(tasks, key=key)
    )
