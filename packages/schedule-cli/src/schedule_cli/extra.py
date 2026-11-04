"""CLI adapters for interchange, recurrence, reporting and calendar items."""

import csv
import io
import json
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from schedule_core import calendar_service as service
from schedule_core.commands import COMMANDS
from schedule_core.contracts import (
    CalendarConfig,
    ContactCreateRequest,
    EventCreateRequest,
)
from schedule_core.exporting import ExportRequest, ExportResult, export_calendar
from schedule_core.interchange import (
    ImportPlan,
    ImportRequest,
    ImportResult,
    apply_import,
    import_result,
    plan_import,
)
from schedule_core.items import (
    CalendarRecord,
    CalendarRequest,
    SourceRecord,
    item_agenda,
    item_list,
)
from sqlalchemy import select

from schedule_cli.inputs import input_payload
from schedule_cli.main import payload, timestamp, window


def schemas():
    result = {
        name: {
            "request": command.request.model_json_schema(),
            "response": command.response.model_json_schema(),
        }
        for name, command in COMMANDS.items()
    }
    for name, request, response in [
        ("import", ImportRequest, ImportResult),
        ("apply-plan", ImportPlan, ImportResult),
        ("export", ExportRequest, ExportResult),
        ("calendars.create", CalendarRequest, CalendarRequest),
        ("config.set", CalendarConfig, CalendarConfig),
    ]:
        result[name] = {
            "request": request.model_json_schema(),
            "response": response.model_json_schema(),
        }
    from schedule_core.resource_contracts import ScheduleQuery, ScheduleResult
    from schedule_core.surface import (
        AvailabilityRequest,
        AvailabilityResult,
        CalendarsResult,
        ConfigResult,
        ConflictRequest,
        ConflictResult,
        ContactExportRequest,
        ContactImportRequest,
        ContactInterchangeResult,
        EmptyRequest,
        ExceptionRequest,
        IdRequest,
        MigrationRequest,
        PathRequest,
        ProfileRequest,
        ProfileResult,
        RangeRequest,
        SourceReconcileRequest,
        StorageResult,
    )

    extra = {
        "agenda": (ScheduleQuery, ScheduleResult),
        "conflicts.check": (ConflictRequest, ConflictResult),
        "availability.busy": (RangeRequest, AvailabilityResult),
        "availability.slots": (AvailabilityRequest, AvailabilityResult),
        "init": (CalendarConfig, StorageResult),
        "status": (EmptyRequest, StorageResult),
        "doctor": (EmptyRequest, StorageResult),
        "config.show": (EmptyRequest, ConfigResult),
        "config.set": (CalendarConfig, ConfigResult),
        "profiles.list": (EmptyRequest, ProfileResult),
        "profiles.show": (ProfileRequest, ProfileResult),
        "db.check": (EmptyRequest, StorageResult),
        "db.migrations.status": (EmptyRequest, StorageResult),
        "db.migrations.upgrade": (MigrationRequest, StorageResult),
        "contacts.import": (ContactImportRequest, ContactInterchangeResult),
        "contacts.export": (ContactExportRequest, ContactInterchangeResult),
    }
    for action in ("create", "inspect", "restore"):
        extra["backup." + action] = (PathRequest, StorageResult)
    for action in ("list", "get", "delete", "create", "update"):
        extra["calendars." + action] = (
            CalendarRequest
            if action in {"create", "update"}
            else IdRequest
            if action in {"get", "delete"}
            else EmptyRequest,
            CalendarsResult,
        )
    from schedule_core.commands import ParticipantsRequest
    from schedule_core.surface import (
        CapabilitiesResult,
        ExceptionsResult,
        ParticipantsResult,
        SchemaRequest,
        SchemaResult,
        SourcesResult,
    )

    extra.update(
        {
            "sources.list": (EmptyRequest, SourcesResult),
            "sources.show": (IdRequest, SourcesResult),
            "events.exceptions.list": (IdRequest, ExceptionsResult),
            "events.exceptions.restore": (ExceptionRequest, ExceptionsResult),
            "events.participants.list": (IdRequest, ParticipantsResult),
            "events.participants.set": (ParticipantsRequest, ParticipantsResult),
            "schema": (SchemaRequest, SchemaResult),
            "capabilities": (EmptyRequest, CapabilitiesResult),
        }
    )
    extra["sources.reconcile"] = (SourceReconcileRequest, ImportResult)
    from schedule_core.resource_contracts import EventResult, TaskResult

    for group, response in (("events", EventResult), ("tasks", TaskResult)):
        extra[group + ".list"] = (ScheduleQuery, ScheduleResult)
        extra[group + ".get"] = (ScheduleQuery, response)
    from schedule_core.commands import ContactListRequest
    from schedule_core.contracts import ContactResult, ContactsResult

    extra["contacts.list"] = (ContactListRequest, ContactsResult)
    extra["contacts.get"] = (ContactListRequest, ContactResult)
    for name, (request, response) in extra.items():
        result[name] = {
            "request": request.model_json_schema(),
            "response": response.model_json_schema(),
        }
    return result


def write_output(path, text):
    with path.open("x") as stream:
        stream.write(text)


async def execute_extra(db, args, config, data):
    async with db.get_session() as session:
        result = await operation(session, args, config, data)
        if result.get("success", True):
            await session.commit()
        else:
            await session.rollback()
        return result


async def operation(session, args, config, data):
    command = args.command
    if command == "events":
        return await event_extras(session, args)
    if command == "calendars":
        return await calendars(session, args)
    if command == "contacts":
        return await contact_interchange(session, args)
    if command in {"import", "apply-plan", "sources"}:
        return await imports(session, args, config)
    if command == "export":
        request = ExportRequest(
            format=args.format,
            mode=args.mode,
            start=timestamp(args.start),
            end=timestamp(args.end),
            calendar=args.calendar,
        )
        result = await export_calendar(session, request)
        if args.output:
            write_output(args.output, result.content)
            return {
                "success": True,
                "format": result.format,
                "count": result.count,
                "path": str(args.output.resolve()),
            }
        return result.model_dump(mode="json")
    if command == "conflicts":
        request = EventCreateRequest.model_validate(
            {"time_zone": config.default_timezone, **input_payload(args)}
        )
        conflicts = await service.check_conflicts(
            session,
            request,
            exclude=args.exclude_event,
            through=timestamp(args.through),
        )
        return {
            "success": True,
            "count": len(conflicts),
            "conflicts": [c.model_dump(mode="json") for c in conflicts],
            "checked_through": (
                timestamp(args.through) or request.start_time + timedelta(days=366)
            ).isoformat()
            if request.rrule
            else request.end_time.isoformat(),
        }
    if command == "availability":
        return await availability(session, args, config)
    raise ValueError(f"Unsupported command {command}")


async def event_extras(session, args):
    event = await service.get_event(session, args.id)
    if args.action == "participants":
        if args.operation == "set":
            from schedule_core.commands import ParticipantsRequest

            request = ParticipantsRequest(id=args.id, **input_payload(args))
            from schedule_core.commands import execute
            from schedule_core.resource_contracts import UpdateEvent

            await execute(
                session,
                "events.update",
                UpdateEvent(id=args.id, patch={"participant_identifiers": request.identifiers}),
            )
            event = await service.get_event(session, args.id)
        return {
            "success": True,
            "event_id": str(event.id),
            "identifiers": [p.identifier for p in event.participants],
        }
    if args.operation == "restore":
        await service.restore_occurrence(session, args.id, timestamp(args.occurrence))
        return {"success": True, "event_id": str(event.id), "restored": args.occurrence}
    return {
        "success": True,
        "event_id": str(event.id),
        "excluded": [service.utc(x.excluded_date).isoformat() for x in event.excluded_dates],
        "modified": [
            {
                **m.model_dump(mode="json"),
                "original_start": service.utc(m.original_date).isoformat(),
            }
            for m in event.modified_instances
        ],
    }


async def calendars(session, args):
    if args.action == "list":
        rows = (await session.execute(select(CalendarRecord))).scalars().all()
        values = [r.model_dump() for r in rows]
        if not any(v["id"] == "primary" for v in values):
            values.insert(0, {"id": "primary", "name": "Primary"})
        return {"success": True, "calendars": values}
    if args.action == "create":
        request = CalendarRequest.model_validate(input_payload(args))
        if await session.get(CalendarRecord, request.id):
            raise ValueError("Calendar already exists")
        row = CalendarRecord(**request.model_dump())
        session.add(row)
    else:
        row = await session.get(CalendarRecord, args.id)
        if row is None and args.id == "primary":
            row = CalendarRecord(id="primary", name="Primary")
            if args.action != "get":
                session.add(row)
        if row is None:
            raise ValueError("Calendar not found")
        if args.action == "delete":
            from schedule_core.db.models import Event

            count = (
                await session.execute(select(Event).where(Event.calendar_id == args.id))
            ).first()
            used = any(i.calendar_id == args.id for i in await item_list(session))
            if count or used:
                raise ValueError("Cannot delete a calendar containing items")
            if args.id == "primary":
                raise ValueError("Cannot delete the primary calendar")
            await session.delete(row)
            return {"success": True, "calendar_id": args.id}
        if args.action == "update":
            values = input_payload(args)
            if "id" in values and values["id"] != args.id:
                raise ValueError("Cannot rename calendar identity")
            request = CalendarRequest.model_validate({**row.model_dump(), **values})
            row.name = request.name
    return {"success": True, "calendar": row.model_dump()}


async def contact_interchange(session, args):
    if args.action == "export":
        contacts = await service.contact_list(session)
        records = [c.model_dump(mode="json") for c in contacts]
        if args.format == "json":
            content = json.dumps(records, indent=2) + "\n"
        else:
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=["id", "name", "email", "phone", "notes"])
            writer.writeheader()
            writer.writerows({k: r.get(k) for k in writer.fieldnames} for r in records)
            content = stream.getvalue()
        if args.output:
            write_output(args.output, content)
            return {
                "success": True,
                "path": str(args.output.resolve()),
                "count": len(records),
            }
        return {"success": True, "content": content, "count": len(records)}
    raw = (
        json.loads(args.file.read_text())
        if args.format == "json"
        else list(csv.DictReader(io.StringIO(args.file.read_text())))
    )
    if not isinstance(raw, list):
        raise ValueError("Contact import must be a list")
    validated = []
    for row in raw:
        values = dict(row)
        identity = values.pop("id", None)
        values.pop("created_at", None)
        values.pop("updated_at", None)
        for key in ["email", "phone", "notes"]:
            if values.get(key) == "":
                values[key] = None
        validated.append(
            (
                UUID(identity) if identity else None,
                ContactCreateRequest.model_validate(values),
            )
        )
    if args.apply:
        from schedule_core.db.models import Contact

        for identity, request in validated:
            old = await session.get(Contact, identity) if identity else None
            if old:
                await service.contact_write(session, request, identity)
            else:
                new = Contact(**request.model_dump(), **({"id": identity} if identity else {}))
                session.add(new)
        await session.flush()
    return {
        "success": True,
        "applied": args.apply,
        "count": len(validated),
        "contacts": [
            {"id": str(i) if i else None, **r.model_dump(mode="json")} for i, r in validated
        ],
    }


async def imports(session, args, config):
    if args.command == "sources":
        if args.action == "list":
            rows = (await session.execute(select(SourceRecord))).scalars().all()
            return {
                "success": True,
                "sources": [
                    {"id": r.id, "path": r.path, "options": json.loads(r.options)} for r in rows
                ],
            }
        row = await session.get(SourceRecord, args.id)
        if row is None:
            raise ValueError("Source not found")
        if args.action == "show":
            return {
                "success": True,
                "id": row.id,
                "path": row.path,
                "options": json.loads(row.options),
            }
        request = ImportRequest.model_validate_json(row.options)
        plan = await plan_import(session, request)
    elif args.command == "apply-plan":
        body = json.loads(args.file.read_text())
        plan = ImportPlan.model_validate(body)
    else:
        request = ImportRequest(
            source=args.source,
            format=args.format,
            recursive=args.recursive,
            timezone=args.timezone or config.default_timezone,
            on_unsupported=args.on_unsupported,
            conflicts=args.conflicts,
            since=args.since,
            until=args.until,
            **({"exclude": args.exclude} if args.exclude else {}),
        )
        plan = await plan_import(session, request)
    if getattr(args, "plan", None):
        write_output(args.plan, plan.model_dump_json(indent=2) + "\n")
    applied = getattr(args, "apply", False) or args.command == "apply-plan"
    if applied:
        await apply_import(session, plan)
    result = import_result(plan, applied).model_dump(mode="json")
    if getattr(args, "report", None):
        write_output(args.report, json.dumps(result, indent=2) + "\n")
    return result


async def availability(session, args, config):
    start, end = window(args, config.default_timezone)
    events = await service.list_events(
        session, start, end, calendar=getattr(args, "calendar", None)
    )
    periods = [(max(start, e.start_time), min(end, e.end_time)) for e in events]
    points = []
    for item in await item_list(session):
        if getattr(args, "calendar", None) and item.calendar_id != args.calendar:
            continue
        for occurrence in item_agenda(item, start, end):
            if item.kind == "point":
                points.append(occurrence)
            if item.kind == "all_day":
                begin = datetime.fromisoformat(occurrence["occurrence_start"])
                length = (item.end - item.start) if item.end else timedelta(days=1)
                periods.append((max(start, begin), min(end, begin + length)))
    periods.sort()
    merged = []
    for a, b in periods:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    if args.action == "busy":
        return {
            "success": True,
            "busy": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in merged],
            "point_appointments": points,
        }
    import re

    match = re.fullmatch(r"(\d+)([mh]?)", args.duration)
    if not match:
        raise ValueError("Duration must be positive minutes or hours (30m, 1h)")
    duration = timedelta(minutes=int(match[1]) * (60 if match[2] == "h" else 1))
    if duration <= timedelta():
        raise ValueError("Duration must be positive")
    zone = ZoneInfo(args.timezone or config.default_timezone)
    hours = payload(args.working_hours) if args.working_hours else None
    free = []
    cursor = start
    for a, b in [*merged, (end, end)]:
        while cursor + duration <= a:
            local = cursor.astimezone(zone)
            stop = (cursor + duration).astimezone(zone)
            allowed = True
            if hours:
                ranges = hours.get(str(local.weekday()), [])
                allowed = local.date() == stop.date() and any(
                    lo <= local.strftime("%H:%M") and stop.strftime("%H:%M") <= hi
                    for lo, hi in ranges
                )
            if allowed:
                free.append(
                    {
                        "start": cursor.isoformat(),
                        "end": (cursor + duration).isoformat(),
                    }
                )
            cursor += duration
            if len(free) > 10000:
                raise ValueError("Availability result exceeds 10000 slots; narrow the range")
        cursor = max(cursor, b)
    return {
        "success": True,
        "count": len(free),
        "slots": free,
        "point_appointments": points,
    }
