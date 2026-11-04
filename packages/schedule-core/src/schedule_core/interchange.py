"""Planned calendar interchange with complete per-record diagnostics and provenance."""

import fnmatch
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import select

from schedule_core import calendar_service as service
from schedule_core.contracts import Contract, EventCreateRequest
from schedule_core.db.models import Event, ExcludedDate, ModifiedInstance
from schedule_core.items import (
    ItemRecord,
    ItemRequest,
    SourceIdentity,
    SourceRecord,
    item_write,
)


class ImportRequest(Contract):
    source: str
    format: Literal["org", "ics"] = "org"
    recursive: bool = False
    exclude: list[str] = Field(
        default_factory=lambda: [
            "archive",
            "vendor",
            ".git",
            ".venv",
            ".hermes",
            "templates",
            "h1.hello.org",
        ]
    )
    timezone: str = "UTC"
    on_unsupported: Literal["error", "skip"] = "error"
    conflicts: Literal["preserve", "reject"] = "preserve"
    since: date | None = None
    until: date | None = None


class ImportDecision(Contract):
    source: str
    line: int
    title: str
    source_key: str | None = None
    id: UUID | None = None
    action: Literal["create", "update", "unchanged", "skip", "error"]
    reason: str | None = None
    event: EventCreateRequest | None = None
    item: ItemRequest | None = None
    # Full recurrence exceptions are carried by interchange, not silently flattened.
    excluded: list[datetime] = Field(default_factory=list)
    modified: list["ImportedException"] = Field(default_factory=list)


class ImportedException(Contract):
    original_start: datetime
    event: EventCreateRequest


class ImportPlan(Contract):
    schema_version: Literal[1] = 1
    request: ImportRequest
    files: dict[str, str]
    database_fingerprint: str
    records: list[ImportDecision]


class ImportResult(Contract):
    schema_version: Literal[1] = 1
    success: bool = True
    applied: bool = False
    count: int
    created: list[UUID] = Field(default_factory=list)
    updated: list[UUID] = Field(default_factory=list)
    unchanged: list[UUID] = Field(default_factory=list)
    skipped: list[ImportDecision] = Field(default_factory=list)
    decisions: list[ImportDecision]
    overlap_policy: str


STAMP = re.compile(
    r"<(?P<date>\d{4}-\d{2}-\d{2})(?:\s+\w+)?(?:\s+(?P<start>\d{2}:\d{2})(?:-(?P<end>\d{2}:\d{2}))?)?(?:\s+(?P<repeat>(?:\+\+|\.\+|\+)\d+[dwmy]))?>"
)


def org_identity_heading(value):
    value = re.sub(r"^(TODO|DONE|BLOCKED|CANCELLED)\s+", "", value)
    return " ".join(STAMP.sub("", value).split())


def parse_stamp(match, zone):
    day = date.fromisoformat(match["date"])

    def clock(value):
        if value == "24:00":
            return datetime.combine(day + timedelta(days=1), datetime.min.time(), ZoneInfo(zone))
        return datetime.fromisoformat(day.isoformat() + "T" + value).replace(tzinfo=ZoneInfo(zone))

    start = clock(match["start"]) if match["start"] else day
    end = clock(match["end"]) if match["end"] else None
    return start, end, match["repeat"]


def parse_org_file(path, timezone):
    text = path.read_text()
    headings = list(re.finditer(r"(?m)^(\*+) (.+)$", text))
    inherited = []
    for index, head in enumerate(headings):
        body = text[
            head.end() : headings[index + 1].start() if index + 1 < len(headings) else len(text)
        ]
        level = len(head[1])
        raw_title = head[2].strip()
        while inherited and inherited[-1][0] >= level:
            inherited.pop()
        parent_props = {
            k: v
            for _, _, props in inherited
            for k, v in props.items()
            if k in {"TIMEZONE", "CATEGORY"}
        }
        props = {
            **parent_props,
            **dict(re.findall(r"(?m)^[ \t]*:([A-Z_]+):[ \t]*(.*?)[ \t]*$", body)),
        }
        inherited.append((level, raw_title, props))
        props["_source_key"] = "/".join(
            org_identity_heading(h[1]) for h in inherited
        )
        line = text.count("\n", 0, head.start()) + 1
        # Encoded metadata is data, not Org structure or scheduling timestamps.
        scheduling_body = re.sub(
            r"(?m)^[ \t]*:(?:(?:NOTES|DESCRIPTION)_JSON|MODIFIED|EXCLUDED):.*$", "", body
        )
        body = scheduling_body
        stamps = list(STAMP.finditer(raw_title + "\n" + scheduling_body))
        diaries = list(re.finditer(r"%%\(org-anniversary (\d+) (\d+) (\d+)\) (.+)", body))
        for anniversary in diaries:
            year, month, day = map(int, anniversary.group(1, 2, 3))
            yield (
                line,
                anniversary[4],
                {**props, "_source_key": props["_source_key"] + "/" + anniversary[4]},
                ItemRequest(
                    kind="anniversary",
                    title=anniversary[4],
                    start=date(year, month, day),
                    time_zone=props.get("TIMEZONE", timezone),
                ),
                [],
                [],
            )
        if not stamps:
            task = re.match(r"^(TODO|DONE|BLOCKED|CANCELLED)\s+(.+)", raw_title)
            if task:
                try:
                    yield (
                        line,
                        task[2],
                        props,
                        ItemRequest(
                            kind="task",
                            title=task[2],
                            state=task[1],
                            time_zone=props.get("TIMEZONE", timezone),
                            notes=json.loads(props["NOTES_JSON"])
                            if "NOTES_JSON" in props
                            else body.strip() or None,
                            calendar_id=props.get("CALENDAR", "primary"),
                            location=props.get("LOCATION"),
                        ),
                        [],
                        [],
                    )
                except (ValueError, KeyError, TypeError) as error:
                    yield line, raw_title, props, error, [], []
                    continue
            if re.search(r"<\d{4}-", raw_title + scheduling_body):
                yield (
                    line,
                    raw_title,
                    props,
                    ValueError("Unrecognized Org timestamp"),
                    [],
                    [],
                )
            continue
        try:
            title = STAMP.sub("", raw_title).strip() or (
                inherited[-2][1] if len(inherited) > 1 else "Calendar event"
            )
            state = re.match(r"^(TODO|DONE|BLOCKED|CANCELLED)\s+", title)
            if state:
                title = title[state.end() :]
            zone = props.get("TIMEZONE", timezone)
            stamp_text = raw_title + "\n" + scheduling_body
            is_task = state or "SCHEDULED:" in body or "DEADLINE:" in body
            is_range = len(stamps) == 2 and stamp_text[stamps[0].end():stamps[1].start()].strip() == "--"
            if is_task:
                slots = []
                for stamp in stamps:
                    marker = re.search(r"(SCHEDULED|DEADLINE):\s*$", stamp_text[:stamp.start()])
                    slots.append(marker[1] if marker else "SCHEDULED")
                    if stamp["end"]:
                        raise ValueError("Org task time ranges are not supported")
                if len(slots) != len(set(slots)) or is_range:
                    raise ValueError("Multiple Org timestamps for one task date are not supported")
                if len({stamp["repeat"] for stamp in stamps}) > 1:
                    raise ValueError("Different task date repeaters are not supported")
            elif len(stamps) > 1 and not is_range:
                raise ValueError("Multiple independent Org timestamps are not supported")
            if is_range and any(stamp["end"] for stamp in stamps):
                raise ValueError("Org ranges of daily time blocks are not supported")
            start, end, repeat = parse_stamp(stamps[0], zone)
            if is_range:
                end = parse_stamp(stamps[1], zone)[0]
                if type(start) is date and type(end) is date:
                    if end < start:
                        raise ValueError("Org date range ends before it starts")
                    end += timedelta(days=1)
            notes = re.sub(r"(?ms)^[ \t]*:PROPERTIES:[ \t]*\n.*?^[ \t]*:END:[ \t]*$", "", body)
            notes = re.sub(r"(?m)^.*(?:<\d{4}-\d{2}-\d{2}|CLOSED:).*$", "", notes).strip()
            common = dict(
                title=title,
                time_zone=zone,
                location=props.get("LOCATION"),
                notes=json.loads(props["NOTES_JSON"]) if "NOTES_JSON" in props else notes or None,
                calendar_id=props.get("CALENDAR", "primary"),
            )
            if state or "SCHEDULED:" in body or "DEADLINE:" in body:
                deadline = None
                scheduled = None
                for marker in ("SCHEDULED", "DEADLINE"):
                    match = re.search(marker + r":\s*(<[^>]+>)", body)
                    if match:
                        parsed = STAMP.fullmatch(match[1])
                        if not parsed:
                            raise ValueError("Unsupported task timestamp")
                        val, _, rep = parse_stamp(parsed, zone)
                        repeat = repeat or rep
                        if marker == "DEADLINE":
                            deadline = val
                        else:
                            scheduled = val
                if scheduled is None:
                    # Active task timestamps need not use the SCHEDULED keyword.
                    text_before_stamps = raw_title + "\n" + scheduling_body
                    bare = [
                        stamp for stamp in stamps
                        if not re.search(
                            r"(?:SCHEDULED|DEADLINE):\s*$", text_before_stamps[:stamp.start()]
                        )
                    ]
                    if bare:
                        scheduled = parse_stamp(bare[0], zone)[0]
                record = ItemRequest(
                    kind="task",
                    **common,
                    start=scheduled,
                    deadline=deadline,
                    repeat=repeat,
                    rrule=props.get("RRULE"),
                    state=state[1] if state else "TODO",
                )
            elif isinstance(start, date) and not isinstance(start, datetime):
                kind = props.get("SCHEDULE_KIND") or (
                    "anniversary" if repeat and repeat.endswith("y") else "all_day"
                )
                if kind not in {"all_day", "anniversary"}:
                    raise ValueError("Date-only Org entries require all_day or anniversary kind")
                record = ItemRequest(
                    kind=kind,
                    **common,
                    start=start,
                    end=end,
                    repeat=repeat,
                    rrule=props.get("RRULE"),
                )
            elif end is None:
                record = ItemRequest(
                    kind="point",
                    **common,
                    start=start,
                    repeat=repeat,
                    rrule=props.get("RRULE"),
                )
            else:
                # ++ on non-task standing event headings is calendar recurrence; task advancement is separate.
                rule = props.get("RRULE")
                if repeat:
                    parts = re.fullmatch(r"(?:\+\+|\.\+|\+)(\d+)([dwmy])", repeat)
                    rule = (
                        rule
                        or f"FREQ={dict(d='DAILY', w='WEEKLY', m='MONTHLY', y='YEARLY')[parts[2]]};INTERVAL={parts[1]}"
                    )
                record = EventCreateRequest(
                    **common,
                    start_time=start,
                    end_time=end,
                    rrule=rule,
                    description=json.loads(props.get("DESCRIPTION_JSON", "null")),
                    participant_identifiers=[
                        v.strip() for v in props.get("PARTICIPANTS", "").split(",") if v.strip()
                    ],
                )
            excluded = [datetime.fromisoformat(s) for s in json.loads(props.get("EXCLUDED", "[]"))]
            modified = [
                ImportedException.model_validate(v) for v in json.loads(props.get("MODIFIED", "[]"))
            ]
            yield line, title, props, record, excluded, modified
        except (ValueError, KeyError, TypeError) as error:
            yield line, raw_title, props, error, [], []


def parse_ics_file(path, timezone):
    from icalendar import Calendar

    from schedule_core.ics_duration import component_durations, duration_end

    raw = path.read_bytes()
    calendar = Calendar.from_ical(raw)
    components = [c for c in calendar.walk() if c.name in {"VEVENT", "VTODO"}]
    durations = dict(zip((id(c) for c in components), component_durations(raw), strict=True))
    bases = {}
    exceptions = {}
    for component in components:
        uid = str(component.get("UID", ""))
        original = component.get("RECURRENCE-ID")
        if original:
            exceptions.setdefault(uid, []).append(component)
        else:
            bases.setdefault(uid, []).append(component)

    def convert(c):
        if c.get("RDATE") is not None:
            raise ValueError("ICS RDATE is not supported; additional occurrences would be lost")
        if c.name == "VEVENT" and str(c.get("STATUS", "")).upper() == "CANCELLED":
            raise ValueError("Standalone cancelled VEVENTs are not supported")
        start = c.decoded("DTSTART", None)
        end = c.decoded("DTEND", None)
        duration = durations[id(c)]
        if end is None and start is not None and duration is not None:
            end = duration_end(start, duration, timezone)
        zone = str(
            c.get("X-SCHEDULE-TIMEZONE")
            or getattr(start, "tzinfo", None)
            or getattr(c.decoded("DUE", None), "tzinfo", None)
            or timezone
        )
        if zone in {"UTC+00:00", "tzutc()"}:
            zone = "UTC"
        common = dict(
            title=str(c.get("SUMMARY", "Untitled")),
            time_zone=zone,
            notes=str(c.get("DESCRIPTION", "")) or None,
            location=str(c.get("LOCATION", "")) or None,
            calendar_id=str(c.get("X-SCHEDULE-CALENDAR", "primary")),
        )
        if c.name == "VTODO":
            deadline = c.decoded("DUE", None)
            if duration is not None:
                if start is None or deadline is not None:
                    raise ValueError("VTODO DURATION requires DTSTART and cannot accompany DUE")
                if end <= start:
                    raise ValueError("VTODO DURATION must be positive")
                deadline = end
            state = {
                "NEEDS-ACTION": "TODO",
                "COMPLETED": "DONE",
                "IN-PROCESS": "BLOCKED",
                "CANCELLED": "CANCELLED",
            }.get(str(c.get("STATUS", "NEEDS-ACTION")), "TODO")
            return ItemRequest(
                **common,
                kind="task",
                start=start,
                deadline=deadline,
                state=state,
                repeat=str(c.get("X-ORG-REPEAT")) if c.get("X-ORG-REPEAT") else None,
                rrule=c.get("RRULE").to_ical().decode() if c.get("RRULE") else None,
            )
        if isinstance(start, datetime) and end:
            rule = c.get("RRULE")
            attendees = c.get("ATTENDEE", [])
            if not isinstance(attendees, list):
                attendees = [attendees]
            return EventCreateRequest(
                **common,
                description=str(c["X-SCHEDULE-DESCRIPTION"]) if "X-SCHEDULE-DESCRIPTION" in c else None,
                start_time=start,
                end_time=end,
                rrule=rule.to_ical().decode() if rule else None,
                participant_identifiers=[
                    str(a).removeprefix("MAILTO:").removeprefix("mailto:") for a in attendees
                ],
            )
        return ItemRequest(
            **common,
            kind=str(
                c.get(
                    "X-SCHEDULE-KIND",
                    "point" if isinstance(start, datetime) else "all_day",
                )
            ),
            repeat=str(c.get("X-ORG-REPEAT")) if c.get("X-ORG-REPEAT") else None,
            rrule=(
                c.get("RRULE").to_ical().decode()
                if c.get("RRULE") and str(c.get("X-SCHEDULE-DERIVED-RRULE", "")).upper() != "TRUE"
                else None
            ),
            start=start,
            end=end,
        )

    for uid in dict.fromkeys([*bases, *exceptions]):
        base_components = bases.get(uid, [])
        members = base_components + exceptions.get(uid, [])
        issue = (
            "ICS component requires a non-empty UID"
            if not uid.strip()
            else "Duplicate ICS base components for the same UID"
            if len(base_components) > 1
            else "ICS recurrence exception has no base component"
            if not base_components
            else None
        )
        if issue:
            for member in members:
                yield 0, str(member.get("SUMMARY", "Untitled")), {"ID": uid}, ValueError(issue), [], []
            continue
        c = base_components[0]
        try:
            record = convert(c)

            def exception_identity(value):
                if not isinstance(value, datetime):
                    return value
                return service.utc(
                    value.replace(tzinfo=ZoneInfo(record.time_zone))
                    if value.tzinfo is None else value
                )

            excluded = []
            modified = []
            exdates = c.get("EXDATE", [])
            if not isinstance(exdates, list):
                exdates = [exdates]
            for ex in exdates:
                excluded.extend(exception_identity(x.dt) for x in ex.dts)
            override_identities = set()
            for ex in exceptions.get(uid, []):
                identity = exception_identity(ex.decoded("RECURRENCE-ID"))
                if identity in override_identities:
                    raise ValueError("Duplicate ICS recurrence override identity")
                override_identities.add(identity)
                if ex["RECURRENCE-ID"].params.get("RANGE") is not None:
                    raise ValueError("ICS RECURRENCE-ID RANGE overrides are not supported")
                if str(ex.get("STATUS", "")).upper() == "CANCELLED":
                    excluded.append(identity)
                else:
                    modified.append(
                        ImportedException(
                            original_start=identity,
                            event=convert(ex),
                        )
                    )
            yield 0, record.title, {"ID": uid}, record, excluded, modified
        except (ValueError, TypeError, KeyError) as error:
            yield 0, str(c.get("SUMMARY", "Untitled")), {"ID": uid}, error, [], []


async def fingerprint(session):
    # Include relationship changes as well as base rows; sorting makes the plan stable.
    from schedule_core.db.models import Contact, Participant

    rows = []
    for model in (
        Event,
        Participant,
        ModifiedInstance,
        ExcludedDate,
        ItemRecord,
        Contact,
        SourceRecord,
        SourceIdentity,
    ):
        result = (await session.execute(select(model))).scalars().all()
        rows.extend(
            (
                model.__tablename__,
                json.dumps(row.model_dump(mode="json"), sort_keys=True),
            )
            for row in result
        )
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()


async def plan_import(session, request):
    source = Path(request.source).expanduser().resolve()
    request = request.model_copy(update={"source": str(source)})
    paths = (
        list(source.rglob("*." + request.format))
        if source.is_dir() and request.recursive
        else [source]
    )
    if source.is_dir() and not request.recursive:
        raise ValueError("Directory import requires --recursive")
    records = []
    files = {}
    seen = set()
    for path in sorted(paths):
        rel = path.relative_to(source) if source.is_dir() else Path(path.name)
        if any(
            fnmatch.fnmatch(str(rel), pattern) or pattern in rel.parts
            for pattern in request.exclude
        ):
            continue
        files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        prior_ids = {}
        if request.format == "org":
            prefix = f"myorg:{path}:"
            identities = (await session.execute(
                select(SourceIdentity).where(SourceIdentity.source_path == str(path))
            )).scalars().all()
            for identity in identities:
                if identity.source_key.startswith(prefix):
                    heading = "/".join(
                        org_identity_heading(part)
                        for part in identity.source_key[len(prefix):].split("/")
                    )
                    prior_ids.setdefault(heading, set()).add(identity.record_id)
        parser = parse_org_file if request.format == "org" else parse_ics_file
        for line, title, props, record, excluded, modified in parser(path, request.timezone):
            if isinstance(record, ItemRequest) and (excluded or modified):
                record = ValueError(
                    "Recurrence exceptions for non-timed items are not supported; "
                    "import would discard cancelled or modified occurrences"
                )
            if isinstance(record, Exception):
                records.append(
                    ImportDecision(
                        source=str(path),
                        line=line,
                        title=title,
                        action="skip" if request.on_unsupported == "skip" else "error",
                        reason=str(record),
                    )
                )
                continue
            source_key = f"myorg:{path}:{props.get('_source_key', title)}"
            identity = await session.get(SourceIdentity, source_key)
            explicit = props.get("EVENT_ID") or props.get("ID")
            previous = prior_ids.get(props.get("_source_key", title), set())
            if not explicit and len(previous) > 1:
                records.append(ImportDecision(
                    source=str(path), line=line, title=title, action="error",
                    reason="Ambiguous previous source identity; add explicit Org IDs",
                ))
                continue
            try:
                uid = UUID(explicit) if explicit else None
            except ValueError:
                uid = uuid5(NAMESPACE_URL, "calendar-uid:" + explicit)
            if uid is None:
                # Persisted source heading identity survives date/time edits; title changes require explicit IDs.
                uid = next(iter(previous)) if previous else (
                    identity.record_id if identity else uuid5(NAMESPACE_URL, source_key)
                )
                if isinstance(record, EventCreateRequest) and not previous and identity is None:
                    local = record.start_time.astimezone(ZoneInfo(record.time_zone))
                    legacy = uuid5(
                        NAMESPACE_URL,
                        f"schedule:{title}:{local.isoformat()}:{record.time_zone}",
                    )
                    if await session.get(Event, legacy):
                        uid = legacy
            if uid in seen:
                records.append(
                    ImportDecision(
                        source=str(path),
                        line=line,
                        title=title,
                        action="error",
                        reason="Ambiguous duplicate identity; add unique Org ID properties",
                    )
                )
                continue
            seen.add(uid)
            anchor = (
                record.start_time
                if isinstance(record, EventCreateRequest)
                else record.start or record.deadline
            )
            day = anchor.date() if isinstance(anchor, datetime) else anchor
            repeating = (
                record.rrule
                if isinstance(record, EventCreateRequest)
                else record.repeat or record.rrule or record.kind == "anniversary"
            )
            if day and (
                (request.since and day < request.since and not repeating)
                or (request.until and day > request.until)
            ):
                records.append(
                    ImportDecision(
                        source=str(path),
                        line=line,
                        title=title,
                        id=uid,
                        action="skip",
                        reason="Outside requested date range",
                    )
                )
                continue
            existing = await session.get(
                Event if isinstance(record, EventCreateRequest) else ItemRecord, uid
            )
            previous_kind = await session.get(
                ItemRecord if isinstance(record, EventCreateRequest) else Event, uid
            )
            action = "create" if existing is None and previous_kind is None else "update"
            if existing and previous_kind is None:
                if isinstance(record, EventCreateRequest):
                    full = await service.get_event(session, uid)
                    before = service.detail(full).model_dump(
                        include=set(EventCreateRequest.model_fields), mode="json"
                    )
                    if (
                        before == record.model_dump(mode="json")
                        and not excluded
                        and not modified
                        and not full.excluded_dates
                        and not full.modified_instances
                    ):
                        action = "unchanged"
                else:
                    before = json.loads(existing.body)
                    before.pop("id", None)
                    before.pop("completed_at", None)
                    if before == record.model_dump(mode="json"):
                        action = "unchanged"
            records.append(
                ImportDecision(
                    source=str(path),
                    line=line,
                    title=title,
                    source_key=source_key,
                    id=uid,
                    action=action,
                    event=record if isinstance(record, EventCreateRequest) else None,
                    item=record if isinstance(record, ItemRequest) else None,
                    excluded=excluded,
                    modified=modified,
                )
            )
    return ImportPlan(
        request=request,
        files=files,
        database_fingerprint=await fingerprint(session),
        records=records,
    )


async def apply_import(session, plan):
    if any(record.action == "error" for record in plan.records):
        raise ValueError(
            "Import has errors; inspect the plan or explicitly choose --on-unsupported skip"
        )
    if any(
        record.action in {"create", "update"}
        and record.item is not None
        and (record.excluded or record.modified)
        for record in plan.records
    ):
        raise ValueError("Recurrence exceptions for non-timed items are not supported")
    for file, digest in plan.files.items():
        if hashlib.sha256(Path(file).read_bytes()).hexdigest() != digest:
            raise service.ApplicationError("stale_plan", f"Source changed: {file}")
    if await fingerprint(session) != plan.database_fingerprint:
        raise service.ApplicationError("stale_plan", "Calendar changed since import preview")
    imported_events = []
    for record in plan.records:
        if record.source_key and record.id and record.action in {"create", "update", "unchanged"}:
            identity = await session.get(SourceIdentity, record.source_key)
            if identity is None:
                identity = SourceIdentity(
                    source_key=record.source_key,
                    record_id=record.id,
                    source_path=record.source,
                )
            else:
                identity.record_id = record.id
            session.add(identity)
        if record.action not in {"create", "update"}:
            continue
        if record.event:
            old_item = await session.get(ItemRecord, record.id)
            if old_item is not None:
                await session.delete(old_item)
            created_at = None
            if await session.get(Event, record.id) is not None:
                created_at = (await service.get_event(session, record.id)).created_at
                await service.delete_event(session, record.id)
                await session.flush()
            await service.create_event(
                session,
                record.event,
                event_id=record.id,
                # Validate the final effective calendar after all replacements and
                # exceptions are installed, not each intermediate base series.
                allow_overlap=True,
            )
            if created_at is not None:
                (await service.get_event(session, record.id)).created_at = created_at
            for point in record.excluded:
                session.add(ExcludedDate(event_id=record.id, excluded_date=service.stored(point)))
            for mod in record.modified:
                e = mod.event
                session.add(
                    ModifiedInstance(
                        event_id=record.id,
                        original_date=service.stored(mod.original_start),
                        start_time=service.stored(e.start_time),
                        end_time=service.stored(e.end_time),
                        title=e.title,
                        time_zone=e.time_zone,
                        location=e.location,
                        notes=e.notes,
                    )
                )
            imported_events.append(record.id)
        else:
            if await session.get(Event, record.id) is not None:
                await service.delete_event(session, record.id)
            await item_write(session, record.item, record.id)
    if plan.request.conflicts == "reject":
        await session.flush()
        for event_id in imported_events:
            event = await service.get_event(session, event_id)
            await session.refresh(event, ["excluded_dates", "modified_instances"])
            proposed = service.detail(event)
            request = EventCreateRequest.model_validate(
                proposed.model_dump(include=set(EventCreateRequest.model_fields))
            )
            conflicts = await service.check_series_conflicts(session, event, request)
            if conflicts:
                raise service.ApplicationError("conflict", "Scheduling conflict detected", conflicts)
    source_id = hashlib.sha256(str(Path(plan.request.source).resolve()).encode()).hexdigest()[:16]
    row = await session.get(SourceRecord, source_id)
    if row is None:
        row = SourceRecord(
            id=source_id,
            path=plan.request.source,
            options=plan.request.model_dump_json(),
        )
    else:
        row.path = plan.request.source
        row.options = plan.request.model_dump_json()
    session.add(row)
    await session.flush()


def import_result(plan, applied=False):
    records = plan.records
    return ImportResult(
        success=not any(r.action == "error" for r in records),
        applied=applied,
        count=sum(r.action in {"create", "update", "unchanged"} for r in records),
        created=[r.id for r in records if r.action == "create"],
        updated=[r.id for r in records if r.action == "update"],
        unchanged=[r.id for r in records if r.action == "unchanged"],
        skipped=[r for r in records if r.action == "skip"],
        decisions=records,
        overlap_policy="Imported calendars may overlap; import preserves their events."
        if plan.request.conflicts == "preserve"
        else "Conflicts rejected",
    )
