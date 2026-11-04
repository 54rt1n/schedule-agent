"""Calendar application operations shared by CLI and MCP.

Mutations validate merged state, own one transaction, and return public DTOs.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from schedule_core.contracts import (
    ContactDetail,
    EventCreateRequest,
    EventDetail,
    OccurrenceRef,
)
from schedule_core.db.models import (
    Contact,
    Event,
    ExcludedDate,
    ModifiedInstance,
    Participant,
)
from schedule_core.wall_time import require_existing_time


class ApplicationError(Exception):
    def __init__(self, code, message, conflicts=None):
        super().__init__(message)
        self.code = code
        self.conflicts = conflicts or []


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def stored(value):
    return utc(value).replace(tzinfo=None)


def detail(event):
    values = {
        key: getattr(event, key)
        for key in EventCreateRequest.model_fields
        if key != "participant_identifiers"
    }
    values.update(
        id=event.id,
        start_time=utc(event.start_time),
        end_time=utc(event.end_time),
        participant_identifiers=[p.identifier for p in event.participants],
        created_at=utc(event.created_at),
        updated_at=utc(event.updated_at),
    )
    return EventDetail.model_validate(values)


def event_statement():
    return select(Event).options(
        selectinload(Event.participants),
        selectinload(Event.excluded_dates),
        selectinload(Event.modified_instances),
    )


async def get_event(session, event_id):
    event = (
        await session.execute(event_statement().where(Event.id == UUID(str(event_id))))
    ).scalar_one_or_none()
    if event is None:
        raise ApplicationError("not_found", f"Event {event_id} not found")
    return event


def occurrences(event, start, end, *, base=None):
    """Expand in local wall time, preserving full original occurrence identity."""
    base = detail(event) if base is None else base
    if not base.rrule:
        return [base] if base.start_time < end and base.end_time > start else []
    rule = rrulestr(base.rrule, dtstart=base.start_time.astimezone(ZoneInfo(base.time_zone)))
    duration = base.end_time - base.start_time
    excluded = {utc(item.excluded_date) for item in event.excluded_dates}
    modifications = {utc(item.original_date): item for item in event.modified_instances}
    result = []
    # xafter avoids materializing an entire unbounded series. The query end is mandatory.
    for occurrence_index, occurrence in enumerate(rule.xafter(start - duration, inc=True)):
        if occurrence_index >= 10000:
            raise ValueError("Query exceeds 10000 recurring instances; narrow the time range")
        original = utc(occurrence)
        if original >= end:
            break
        if original in excluded or original in modifications:
            continue
        if original + duration <= start:
            continue
        require_existing_time(occurrence)
        result.append(
            base.model_copy(
                update=dict(
                    start_time=original,
                    end_time=original + duration,
                    rrule=None,
                    occurrence=OccurrenceRef(series_id=base.id, original_start=original),
                )
            )
        )
    # Moved occurrences may originate outside the query range, so handle them independently.
    for original, item in modifications.items():
        effective_start, effective_end = utc(item.start_time), utc(item.end_time)
        if original not in excluded and effective_start < end and effective_end > start:
            result.append(
                base.model_copy(
                    update=dict(
                        start_time=effective_start,
                        end_time=effective_end,
                        time_zone=item.time_zone,
                        title=item.title or base.title,
                        location=item.location,
                        notes=item.notes,
                        rrule=None,
                        occurrence=OccurrenceRef(series_id=base.id, original_start=original),
                    )
                )
            )
    return sorted(result, key=lambda item: item.start_time)


async def list_events(
    session,
    start=None,
    end=None,
    *,
    definitions=False,
    title=None,
    calendar=None,
    participant=None,
    location=None,
):
    if not definitions and (start is None or end is None or start >= end):
        raise ValueError("A valid start/end range is required for occurrences")
    events = list((await session.execute(event_statement())).scalars().all())
    result = []
    for event in events:
        if calendar and event.calendar_id != calendar:
            continue
        if participant and participant not in [p.identifier for p in event.participants]:
            continue
        candidates = [detail(event)] if definitions else occurrences(event, utc(start), utc(end))
        result.extend(
            candidate
            for candidate in candidates
            if (not title or title.casefold() in candidate.title.casefold())
            and (not location or location.casefold() in (candidate.location or "").casefold())
        )
    return sorted(result, key=lambda item: item.start_time)


def proposed_occurrences(proposed, through=None):
    if not proposed.rrule:
        return [(proposed.start_time, proposed.end_time)]
    if through is None:
        through = proposed.start_time + timedelta(days=366)
    rule = rrulestr(
        proposed.rrule,
        dtstart=proposed.start_time.astimezone(ZoneInfo(proposed.time_zone)),
    )
    duration = proposed.end_time - proposed.start_time
    result = []
    for point in rule.xafter(proposed.start_time, inc=True):
        if utc(point) >= through:
            break
        require_existing_time(point)
        point = utc(point)
        if len(result) >= 10000:
            raise ValueError("Recurrence check exceeds 10000 occurrences; use a smaller horizon")
        result.append((point, point + duration))
    return result


async def check_conflicts(session, proposed, exclude=None, through=None):
    windows = proposed_occurrences(proposed, through)
    if not windows:
        return []
    existing = await list_events(session, min(s for s, e in windows), max(e for s, e in windows))
    return [
        event
        for event in existing
        if not (
            str(event.id) == str(exclude[0])
            and event.occurrence
            and event.occurrence.original_start == exclude[1]
            if isinstance(exclude, tuple)
            else str(event.id) == str(exclude)
        )
        and any(start < event.end_time and end > event.start_time for start, end in windows)
    ]


async def check_series_conflicts(session, event, proposed):
    """Validate the proposed series with its cancellations and moved instances."""
    modified = event.modified_instances
    start = min([proposed.start_time] + [utc(m.start_time) for m in modified])
    end = max(
        [proposed.start_time + timedelta(days=366), proposed.end_time]
        + [utc(m.end_time) for m in modified]
    )
    effective = occurrences(
        event, start, end, base=EventDetail(**proposed.model_dump(), id=event.id)
    )
    existing = await list_events(session, start, end)
    return [
        other
        for other in existing
        if other.id != event.id
        and any(
            candidate.start_time < other.end_time and candidate.end_time > other.start_time
            for candidate in effective
        )
    ]


def apply_fields(event, request):
    for key, value in request.model_dump().items():
        if key == "participant_identifiers":
            continue
        setattr(event, key, stored(value) if key in {"start_time", "end_time"} else value)
    event.updated_at = datetime.now(UTC).replace(tzinfo=None)


async def set_participants(session, event, identifiers):
    await session.execute(delete(Participant).where(Participant.event_id == event.id))
    for value in dict.fromkeys(identifiers):
        if value.startswith("contact:"):
            contact = await contact_get(session, value.removeprefix("contact:"))
            if not contact.email:
                raise ValueError("Participant contact has no email")
            value = contact.email
        session.add(Participant(event_id=event.id, identifier=value))
    await session.flush()
    await session.refresh(event, ["participants"])


async def create_event(session, request, *, allow_overlap=False, event_id=None):
    if not allow_overlap:
        conflicts = await check_conflicts(session, request)
        if conflicts:
            raise ApplicationError("conflict", "Scheduling conflict detected", conflicts)
    event = Event(id=event_id) if event_id else Event()
    apply_fields(event, request)
    session.add(event)
    await session.flush()
    await set_participants(session, event, request.participant_identifiers)
    return detail(event)


def occurrence_start(event, value):
    if not event.rrule:
        raise ValueError("Occurrence scope requires a recurring event")
    point = value.replace(tzinfo=ZoneInfo(event.time_zone)) if value.tzinfo is None else value
    rule = rrulestr(
        event.rrule, dtstart=utc(event.start_time).astimezone(ZoneInfo(event.time_zone))
    )
    original = rule.before(point, inc=True)
    if original is None or utc(original) != utc(point):
        raise ValueError("Occurrence is not a member of the series")
    require_existing_time(original)
    return utc(point)


async def update_event(
    session, event_id, patch, *, scope="series", occurrence=None, allow_overlap=False
):
    event = await get_event(session, event_id)
    base = detail(event)
    values = patch.model_dump(exclude_unset=True)
    original = occurrence or values.pop("original_start_time", None)
    if original is not None and scope == "series":
        scope = "occurrence"
    values.pop("original_start_time", None)
    if not values:
        raise ValueError("No fields provided to update")
    current = base.model_dump(include=set(EventCreateRequest.model_fields))
    if scope != "series":
        if original is None:
            raise ValueError("--occurrence is required for occurrence/future scope")
        original = occurrence_start(event, original)
        current.update(start_time=original, end_time=original + (base.end_time - base.start_time))
        if scope == "occurrence":
            modified = next(
                (m for m in event.modified_instances if utc(m.original_date) == original),
                None,
            )
            if modified:
                current.update(
                    start_time=utc(modified.start_time),
                    end_time=utc(modified.end_time),
                    title=modified.title,
                    notes=modified.notes,
                    location=modified.location,
                    time_zone=modified.time_zone,
                )
            current["rrule"] = None
            if {"rrule", "participant_identifiers", "calendar_id", "description"} & values.keys():
                raise ValueError(
                    "Recurrence, calendar, participants and description belong to the series"
                )
    merged = EventCreateRequest.model_validate({**current, **values})
    if (
        scope == "series"
        and event.rrule
        and {"rrule", "start_time", "time_zone"} & values.keys()
        and (event.modified_instances or event.excluded_dates)
    ):
        raise ValueError("Restore series exceptions before changing its recurrence anchor or rule")
    if not allow_overlap and scope != "future":
        if scope == "series" and event.rrule:
            conflicts = await check_series_conflicts(session, event, merged)
        else:
            conflicts = await check_conflicts(
                session,
                merged,
                exclude=(event.id, original) if scope == "occurrence" else event.id,
            )
        if conflicts:
            raise ApplicationError("conflict", "Scheduling conflict detected", conflicts)
    if scope == "future":
        # Splitting a COUNT rule must retain only its remaining occurrences.
        import re

        if "rrule" not in values and re.search(r"COUNT=\d+", event.rrule, flags=re.IGNORECASE):
            rule = rrulestr(
                event.rrule,
                dtstart=base.start_time.astimezone(ZoneInfo(base.time_zone)),
            )
            remaining = sum(1 for d in rule if utc(d) >= original)
            merged.rrule = re.sub(
                r"COUNT=\d+", f"COUNT={remaining}", event.rrule, flags=re.IGNORECASE
            )
        return await split_series(session, event, original, merged, allow_overlap)
    if scope == "occurrence":
        item = next(
            (m for m in event.modified_instances if utc(m.original_date) == original),
            None,
        )
        if item is None:
            item = ModifiedInstance(event_id=event.id, original_date=stored(original))
        for key in (
            "start_time",
            "end_time",
            "title",
            "location",
            "notes",
            "time_zone",
        ):
            val = getattr(merged, key)
            setattr(item, key, stored(val) if key in {"start_time", "end_time"} else val)
        session.add(item)
        await session.flush()
        await session.refresh(event, ["modified_instances"])
        return EventDetail(
            **merged.model_dump(),
            id=event.id,
            occurrence=OccurrenceRef(series_id=event.id, original_start=original),
        )
    apply_fields(event, merged)
    await set_participants(session, event, merged.participant_identifiers)
    await session.flush()
    return detail(event)


async def split_series(session, event, original, merged, allow_overlap):
    """Keep original occurrence identities when the replacement keeps its anchor."""
    excluded = [
        x.model_dump(exclude={"id", "event_id"})
        for x in event.excluded_dates
        if utc(x.excluded_date) >= original
    ]
    modified = [
        x.model_dump(exclude={"id", "event_id"})
        for x in event.modified_instances
        if utc(x.original_date) >= original
    ]
    if excluded or modified:
        # COUNT is adjusted by the caller, but the recurrence pattern must stay stable.
        def pattern(rule):
            return [p for p in (rule or "").upper().split(";") if not p.startswith("COUNT=")]

        if (
            merged.start_time != original
            or merged.time_zone != event.time_zone
            or pattern(merged.rrule) != pattern(event.rrule)
        ):
            raise ValueError(
                "Restore future exceptions before changing the recurrence anchor or rule"
            )
        # Reject shortened COUNT rules that would strand an exception.
        for point in [x["excluded_date"] for x in excluded] + [
            x["original_date"] for x in modified
        ]:
            rule = rrulestr(
                merged.rrule, dtstart=merged.start_time.astimezone(ZoneInfo(merged.time_zone))
            )
            if rule.before(utc(point), inc=True) != utc(point):
                raise ValueError("Replacement recurrence would orphan a future exception")
    await truncate_series(session, event, original)
    replacement = await create_event(session, merged, allow_overlap=True)
    for model, rows in ((ExcludedDate, excluded), (ModifiedInstance, modified)):
        for values in rows:
            session.add(model(event_id=replacement.id, **values))
    await session.flush()
    row = await get_event(session, replacement.id)
    await session.refresh(row, ["excluded_dates", "modified_instances"])
    if not allow_overlap:
        conflicts = await check_series_conflicts(session, row, merged)
        if conflicts:
            raise ApplicationError("conflict", "Scheduling conflict detected", conflicts)
    return detail(row)


async def truncate_series(session, event, original):
    if original == utc(event.start_time):
        await delete_event(session, event.id)
        return
    parts = [
        part for part in event.rrule.split(";") if not part.upper().startswith(("COUNT=", "UNTIL="))
    ]
    parts.append("UNTIL=" + (original - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ"))
    event.rrule = ";".join(parts)
    for model, col in (
        (ModifiedInstance, ModifiedInstance.original_date),
        (ExcludedDate, ExcludedDate.excluded_date),
    ):
        await session.execute(
            delete(model).where(model.event_id == event.id, col >= stored(original))
        )
    await session.flush()
    await session.refresh(event, ["excluded_dates", "modified_instances"])


async def delete_event(session, event_id, *, scope="series", occurrence=None):
    if scope == "series" and occurrence is not None:
        raise ValueError("An occurrence requires explicit occurrence or future scope")
    event = await get_event(session, event_id)
    if scope != "series":
        if occurrence is None:
            raise ValueError("--occurrence is required")
        original = occurrence_start(event, occurrence)
        if scope == "future":
            await truncate_series(session, event, original)
        elif not any(utc(x.excluded_date) == original for x in event.excluded_dates):
            session.add(ExcludedDate(event_id=event.id, excluded_date=stored(original)))
            await session.flush()
            await session.refresh(event, ["excluded_dates"])
        return
    # Loaded relationships own cascade deletion; bulk deletes would leave stale objects.
    await session.delete(event)
    await session.flush()


async def restore_occurrence(session, event_id, original):
    event = await get_event(session, event_id)
    original = occurrence_start(event, original)
    base = detail(event)
    proposed = EventCreateRequest.model_validate(
        {
            **base.model_dump(include=set(EventCreateRequest.model_fields)),
            "start_time": original,
            "end_time": original + (base.end_time - base.start_time),
            "rrule": None,
        }
    )
    conflicts = await check_conflicts(session, proposed, exclude=(event.id, original))
    if conflicts:
        raise ApplicationError("conflict", "Scheduling conflict detected", conflicts)
    for model, col in (
        (ModifiedInstance, ModifiedInstance.original_date),
        (ExcludedDate, ExcludedDate.excluded_date),
    ):
        await session.execute(
            delete(model).where(model.event_id == event.id, col == stored(original))
        )
    await session.flush()
    await session.refresh(event, ["excluded_dates", "modified_instances"])


async def contact_get(session, contact_id):
    try:
        uid = UUID(str(contact_id))
    except ValueError as error:
        raise ValueError("Invalid contact_id format: must be a valid UUID") from error
    contact = await session.get(Contact, uid)
    if contact is None:
        raise ApplicationError("not_found", f"Contact {contact_id} not found")
    return contact


def contact_detail(contact):
    values = contact.model_dump()
    for key in ("created_at", "updated_at"):
        values[key] = utc(values[key])
    return ContactDetail.model_validate(values)


async def contact_list(session, name=None, email=None):
    statement = select(Contact).order_by(Contact.name, Contact.id)
    if name:
        statement = statement.where(Contact.name.icontains(name))
    if email:
        statement = statement.where(Contact.email.icontains(email))
    return [contact_detail(c) for c in (await session.execute(statement)).scalars().all()]


async def contact_write(session, request, contact_id=None):
    contact = await contact_get(session, contact_id) if contact_id else Contact()
    values = request.model_dump(exclude_unset=contact_id is not None)
    if not values:
        raise ValueError("No fields provided to update")
    if values.get("email"):
        existing = (
            await session.execute(select(Contact).where(Contact.email == values["email"]))
        ).scalar_one_or_none()
        if existing is not None and existing.id != contact.id:
            raise ApplicationError("duplicate", "Email already exists for another contact")
    for key, value in values.items():
        setattr(contact, key, value)
    contact.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(contact)
    await session.flush()
    return contact_detail(contact)
