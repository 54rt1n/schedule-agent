"""Typed non-timed Org items; kept separate from timed-event persistence."""

import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from pydantic import Field as PydanticField
from pydantic import model_validator
from sqlalchemy import Column, Text, select
from sqlmodel import Field, SQLModel

from schedule_core.calendar_service import ApplicationError
from schedule_core.contracts import CalendarDate, Contract
from schedule_core.wall_time import require_existing_time


class ItemRequest(Contract):
    kind: Literal["task", "anniversary", "all_day", "point"]
    title: str = PydanticField(min_length=1)
    start: CalendarDate | None = None
    end: CalendarDate | None = None
    deadline: CalendarDate | None = None
    time_zone: str = "UTC"
    repeat: str | None = None
    rrule: str | None = None
    state: Literal["TODO", "DONE", "BLOCKED", "CANCELLED"] = "TODO"
    location: str | None = None
    notes: str | None = None
    calendar_id: str = "primary"

    @model_validator(mode="after")
    def valid_item(self):
        zone = ZoneInfo(self.time_zone)
        for name in ("start", "end", "deadline"):
            value = getattr(self, name)
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=zone)
                local = value.astimezone(zone)
                if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local.replace(
                    tzinfo=None
                ):
                    raise ValueError(f"{name} is a nonexistent local time")
                setattr(self, name, local)
        if self.kind != "task" and self.start is None:
            raise ValueError("start is required")
        if self.kind == "point" and not isinstance(self.start, datetime):
            raise ValueError("Point appointments require a date and time")
        if self.kind in {"anniversary", "all_day"} and isinstance(self.start, datetime):
            raise ValueError("All-day items and anniversaries require a date, not a time")
        if self.end is not None and self.start is not None and self.end <= self.start:
            raise ValueError("end must be after start")
        if self.repeat and not re.fullmatch(r"(?:\+\+|\.\+|\+)[1-9]\d*[dwmy]", self.repeat):
            raise ValueError("repeat must be an Org +, ++ or .+ repeater")
        if self.rrule:
            if any(
                int(v) <= 0 for v in re.findall(r"(?:INTERVAL|COUNT)=(-?\d+)", self.rrule.upper())
            ):
                raise ValueError("Recurrence COUNT and INTERVAL must be positive")
            from dateutil.rrule import rrulestr

            anchor = self.start or self.deadline
            if anchor is None:
                raise ValueError("Recurrence needs a start or deadline")
            point = (
                anchor
                if isinstance(anchor, datetime)
                else datetime.combine(anchor, datetime.min.time(), ZoneInfo(self.time_zone))
            )
            rrulestr(self.rrule, dtstart=point)
        return self


class ItemDetail(ItemRequest):
    id: UUID
    completed_at: datetime | None = None


class ItemRecord(SQLModel, table=True):
    __tablename__ = "schedule_item"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kind: str = Field(index=True)
    body: str = Field(sa_column=Column(Text, nullable=False))


class SourceRecord(SQLModel, table=True):
    __tablename__ = "schedule_source"
    id: str = Field(primary_key=True)
    path: str
    options: str


class SourceIdentity(SQLModel, table=True):
    __tablename__ = "schedule_source_identity"
    source_key: str = Field(primary_key=True)
    record_id: UUID
    source_path: str


class CalendarRecord(SQLModel, table=True):
    __tablename__ = "schedule_calendar"
    id: str = Field(primary_key=True)
    name: str


class CalendarRequest(Contract):
    id: str = PydanticField(pattern=r"^[A-Za-z0-9_-]+$")
    name: str = PydanticField(min_length=1)


async def item_get(session, item_id):
    row = await session.get(ItemRecord, UUID(str(item_id)))
    if row is None:
        raise ApplicationError("not_found", f"Item {item_id} not found")
    return ItemDetail.model_validate_json(row.body)


async def item_list(session, kind=None):
    stmt = select(ItemRecord)
    if kind:
        stmt = stmt.where(ItemRecord.kind == kind)
    return [
        ItemDetail.model_validate_json(row.body)
        for row in (await session.execute(stmt)).scalars().all()
    ]


async def item_write(session, request, item_id=None):
    uid = UUID(str(item_id)) if item_id else uuid4()
    result = ItemDetail(**request.model_dump(), id=uid)
    row = await session.get(ItemRecord, uid)
    if row is not None:
        previous = ItemDetail.model_validate_json(row.body)
        if previous.kind == result.kind == "task" and previous.state == result.state:
            result.completed_at = previous.completed_at
    if row is None:
        row = ItemRecord(id=uid, kind=request.kind, body=result.model_dump_json())
    else:
        row.kind = request.kind
        row.body = result.model_dump_json()
    session.add(row)
    await session.flush()
    return result


def advance(value, repeat, completed):
    match = re.fullmatch(r"(\+\+|\.\+|\+)(\d+)([dwmy])", repeat)
    mode, count, unit = match.groups()
    delta = relativedelta(**{dict(d="days", w="weeks", m="months", y="years")[unit]: int(count)})
    if not isinstance(value, datetime):
        completed = completed.date()
    if mode == ".+":
        return completed + delta
    value = value + delta
    if mode == "++":
        while value <= completed:
            value = value + delta
    return value


async def complete(session, item_id, at=None, reopen=False):
    item = await item_get(session, item_id)
    if item.kind != "task":
        raise ValueError("Only tasks can be completed or reopened")
    at = at or datetime.now(ZoneInfo(item.time_zone))
    at = at.astimezone(ZoneInfo(item.time_zone))
    if reopen:
        item.state = "TODO"
        item.completed_at = None
    else:
        item.completed_at = at
        item.state = "DONE"
        if item.repeat:
            if item.start is None and item.deadline is None:
                raise ValueError("Repeating tasks need a scheduled date or deadline")
            for field in ("start", "deadline"):
                value = getattr(item, field)
                if value:
                    if isinstance(value, datetime) and value.tzinfo is None:
                        value = value.replace(tzinfo=ZoneInfo(item.time_zone))
                    advanced = advance(value, item.repeat, at)
                    if isinstance(advanced, datetime) and (
                        advanced.astimezone(UTC).astimezone(advanced.tzinfo).replace(tzinfo=None)
                        != advanced.replace(tzinfo=None)
                    ):
                        raise ValueError(f"Advancing {field} produces a nonexistent local time")
                    setattr(item, field, advanced)
            item.state = "TODO"
    row = await session.get(ItemRecord, item.id)
    row.body = item.model_dump_json()
    return item


def item_agenda(item, start, end):
    if item.state in {"DONE", "CANCELLED"}:
        return []
    if item.kind == "task" and item.start is not None and item.deadline is not None:
        scheduled = item_agenda(item.model_copy(update={"deadline": None}), start, end)
        due = item_agenda(item.model_copy(update={"start": None, "end": None}), start, end)
        # Both dates are actionable; retain the original DTO and deduplicate equal dates.
        points = {
            datetime.fromisoformat(row["occurrence_start"]).astimezone(UTC)
            for row in scheduled + due
        }
        return [
            {**item.model_dump(mode="json"), "occurrence_start": point.isoformat()}
            for point in sorted(points)
        ]
    anchor = item.start or item.deadline
    if anchor is None:
        return []
    zone = ZoneInfo(item.time_zone)

    def as_dt(value):
        if isinstance(value, datetime):
            return value.replace(tzinfo=zone) if value.tzinfo is None else value
        return datetime.combine(value, datetime.min.time(), zone)

    point = as_dt(anchor)
    duration = (
        as_dt(item.end) - point
        if item.end
        else (timedelta(days=1) if item.kind == "all_day" else timedelta())
    )
    if item.rrule:
        from dateutil.rrule import rrulestr

        result = []
        for occurrence in rrulestr(item.rrule, dtstart=point).xafter(start - duration, inc=True):
            if occurrence >= end:
                break
            if occurrence < start and occurrence + duration <= start:
                continue
            require_existing_time(occurrence)
            result.append(
                {
                    **item.model_dump(mode="json"),
                    "occurrence_start": occurrence.isoformat(),
                }
            )
            if len(result) > 10000:
                raise ValueError("Agenda exceeds 10000 occurrences")
        return result
    repeat = item.repeat or ("+1y" if item.kind == "anniversary" else None)
    result = []
    while point < end:
        if point >= start or point + duration > start:
            require_existing_time(point)
            result.append(
                {
                    **item.model_dump(mode="json"),
                    "occurrence_start": point.isoformat(),
                    "title": item.title.replace("%d", str(point.year - anchor.year))
                    if item.kind == "anniversary"
                    else item.title,
                }
            )
        # Tasks show their next action once; completion owns repeat advancement.
        if not repeat or item.kind == "task":
            break
        point = advance(point, repeat.replace("++", "+").replace(".+", "+"), point)
        if len(result) > 10000:
            raise ValueError("Agenda exceeds 10000 item occurrences")
    return result
