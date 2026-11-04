"""Public event/task contracts, independent of storage representation and transport."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from schedule_core.agenda_contracts import AgendaRequest
from schedule_core.contracts import CalendarDate, Contract, EventDetail


class EventFields(Contract):
    title: str = Field(min_length=1)
    time_zone: str | None = None
    location: str | None = None
    notes: str | None = None
    calendar_id: str = "primary"
    rrule: str | None = None


class TimedEvent(EventFields):
    kind: Literal["timed"] = "timed"
    start_time: datetime
    end_time: datetime
    description: str | None = None
    participant_identifiers: list[str] = Field(default_factory=list)


class PointEvent(EventFields):
    kind: Literal["point"] = "point"
    start: datetime
    repeat: str | None = None


class AllDayEvent(EventFields):
    kind: Literal["all_day"] = "all_day"
    start: date
    end: date | None = None
    repeat: str | None = None


class AnniversaryEvent(EventFields):
    kind: Literal["anniversary"] = "anniversary"
    start: date
    repeat: str | None = None


EventInput = Annotated[
    TimedEvent | PointEvent | AllDayEvent | AnniversaryEvent, Field(discriminator="kind")
]


class CreateEvent(Contract):
    event: EventInput


class EventPatch(Contract):
    title: str | None = None
    time_zone: str | None = None
    location: str | None = None
    notes: str | None = None
    calendar_id: str | None = None
    rrule: str | None = None
    repeat: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    start: CalendarDate | None = None
    end: date | None = None
    description: str | None = None
    participant_identifiers: list[str] | None = None


class ResourceId(Contract):
    id: UUID


class DeleteEvent(ResourceId):
    scope: Literal["series", "occurrence", "future"] = "series"
    occurrence: datetime | None = None

    @model_validator(mode="after")
    def coherent_scope(self):
        if self.scope == "series" and self.occurrence is not None:
            raise ValueError("An occurrence requires explicit occurrence or future scope")
        if self.scope != "series" and self.occurrence is None:
            raise ValueError("Occurrence/future scope requires an occurrence timestamp")
        return self


class UpdateEvent(DeleteEvent):
    patch: EventPatch


class TimedRecord(EventDetail):
    kind: Literal["timed"] = "timed"


class PointRecord(PointEvent):
    id: UUID
    occurrence_start: datetime | None = None


class AllDayRecord(AllDayEvent):
    id: UUID
    occurrence_start: datetime | None = None


class AnniversaryRecord(AnniversaryEvent):
    id: UUID
    occurrence_start: datetime | None = None


EventRecord = Annotated[
    TimedRecord | PointRecord | AllDayRecord | AnniversaryRecord, Field(discriminator="kind")
]


class EventResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    event_id: UUID
    event: EventRecord | None = None


class TaskCreate(Contract):
    title: str = Field(min_length=1)
    start: CalendarDate | None = None
    deadline: CalendarDate | None = None
    time_zone: str | None = None
    repeat: str | None = None
    state: Literal["TODO", "BLOCKED"] = "TODO"
    location: str | None = None
    notes: str | None = None
    calendar_id: str = "primary"


class TaskPatch(Contract):
    title: str | None = None
    start: CalendarDate | None = None
    deadline: CalendarDate | None = None
    time_zone: str | None = None
    repeat: str | None = None
    state: Literal["TODO", "BLOCKED", "CANCELLED"] | None = None
    location: str | None = None
    notes: str | None = None
    calendar_id: str | None = None


class TaskUpdate(ResourceId):
    patch: TaskPatch


class TaskComplete(ResourceId):
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def offset_required(self):
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            raise ValueError("completed_at must include an offset")
        return self


class TaskRecord(Contract):
    id: UUID
    kind: Literal["task"] = "task"
    title: str
    start: CalendarDate | None = None
    deadline: CalendarDate | None = None
    end: CalendarDate | None = None
    time_zone: str
    repeat: str | None = None
    rrule: str | None = None
    state: Literal["TODO", "DONE", "BLOCKED", "CANCELLED"]
    location: str | None = None
    notes: str | None = None
    calendar_id: str
    completed_at: datetime | None = None
    occurrence_start: datetime | None = None


class TaskResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    task_id: UUID
    task: TaskRecord | None = None


class ScheduleQuery(AgendaRequest):
    view: Literal["definitions", "occurrences"] = "definitions"
    resource: Literal["events", "tasks"] | None = None
    id: UUID | None = None
    occurrence: datetime | None = None
    kind: Literal["timed", "point", "all_day", "anniversary"] | None = None
    state: Literal["TODO", "DONE", "BLOCKED", "CANCELLED"] | None = None
    scheduled: date | None = None
    due_before: date | None = None
    participant: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def coherent_filters(self):
        if self.occurrence is not None:
            if self.id is None or self.resource != "events" or self.view != "occurrences":
                raise ValueError("An occurrence lookup requires an event ID and occurrence view")
            if self.occurrence.tzinfo is None:
                raise ValueError("occurrence must include an offset")
            if any((self.start, self.end, self.date, self.today, self.week)):
                raise ValueError("Choose an occurrence identity or a date window")
        if self.view == "definitions" and any(
            (self.start, self.end, self.date, self.today, self.week)
        ):
            raise ValueError(
                "Date windows require view=occurrences; task filters use scheduled/due_before"
            )
        if self.resource == "events" and any((self.state, self.scheduled, self.due_before)):
            raise ValueError("Task filters require tasks or a mixed schedule query")
        if self.resource == "tasks" and (self.kind or self.participant):
            raise ValueError("Event filters require events or a mixed schedule query")
        return self


class ScheduleResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    count: int
    events: list[EventRecord] = Field(default_factory=list)
    tasks: list[TaskRecord] = Field(default_factory=list)
