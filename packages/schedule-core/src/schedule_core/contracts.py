"""Public transport-independent scheduling contracts (schema version 1)."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


def parse_calendar_date(value):
    """Resolve ISO strings before union coercion can discard a midnight time/offset."""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return datetime.fromisoformat(value)
    return value


CalendarDate = Annotated[date | datetime, BeforeValidator(parse_calendar_date)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_serializer("*", mode="wrap", when_used="json", check_fields=False)
    def serialize_datetime(self, value, handler):
        return value.isoformat() if isinstance(value, datetime) else handler(value)


class CalendarConfig(Contract):
    default_timezone: str = "UTC"
    log_level: Literal["INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("default_timezone")
    @classmethod
    def valid_zone(cls, value):
        ZoneInfo(value)
        return value


class EventCreateRequest(Contract):
    title: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    time_zone: str = "UTC"
    location: str | None = None
    notes: str | None = None
    rrule: str | None = None
    participant_identifiers: list[str] = Field(default_factory=list)
    calendar_id: str = "primary"
    description: str | None = None

    @model_validator(mode="after")
    def validate_event(self):
        zone = ZoneInfo(self.time_zone)
        for name in ("start_time", "end_time"):
            value = getattr(self, name)
            if value.tzinfo is None:
                value = value.replace(tzinfo=zone)
            # Reject nonexistent wall times instead of moving a commitment silently.
            local = value.astimezone(zone)
            if local.astimezone(UTC).astimezone(zone).replace(
                tzinfo=None
            ) != local.replace(tzinfo=None):
                raise ValueError(f"{name} is a nonexistent local time")
            setattr(self, name, value.astimezone(UTC))
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.rrule:
            import re

            if any(
                int(v) <= 0
                for v in re.findall(r"(?:INTERVAL|COUNT)=(-?\d+)", self.rrule.upper())
            ):
                raise ValueError("Recurrence COUNT and INTERVAL must be positive")
            rule = rrulestr(self.rrule, dtstart=self.start_time.astimezone(zone))
            # Finite bounded iteration is owned by the query service.
            if rule is None:
                raise ValueError("Invalid recurrence rule")
        return self


class EventPatchRequest(Contract):
    title: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    time_zone: str | None = None
    location: str | None = None
    notes: str | None = None
    rrule: str | None = None
    participant_identifiers: list[str] | None = None
    calendar_id: str | None = None
    description: str | None = None
    original_start_time: datetime | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        required = {
            "title",
            "start_time",
            "end_time",
            "time_zone",
            "calendar_id",
            "participant_identifiers",
        }
        for name in self.model_fields_set & required:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ContactCreateRequest(Contract):
    name: str = Field(min_length=1)
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = None


class ContactPatchRequest(Contract):
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def valid_name(self):
        if "name" in self.model_fields_set and not self.name:
            raise ValueError("name cannot be empty or null")
        return self


class OccurrenceRef(Contract):
    series_id: UUID
    original_start: datetime


class EventDetail(EventCreateRequest):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None
    occurrence: OccurrenceRef | None = None


class ContactDetail(ContactCreateRequest):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FieldIssue(Contract):
    path: str
    message: str


class CommandError(Contract):
    code: str
    message: str
    issues: list[FieldIssue] = Field(default_factory=list)


class EventResult(Contract):
    schema_version: Literal[1] = 1
    success: Literal[True] = True
    event_id: UUID
    event: EventDetail | None = None


class EventsResult(Contract):
    schema_version: Literal[1] = 1
    success: Literal[True] = True
    count: int
    events: list[EventDetail]


class ContactResult(Contract):
    schema_version: Literal[1] = 1
    success: Literal[True] = True
    contact_id: UUID
    contact: ContactDetail | None = None


class ContactsResult(Contract):
    schema_version: Literal[1] = 1
    success: Literal[True] = True
    count: int
    contacts: list[ContactDetail]


class FailureResult(Contract):
    schema_version: Literal[1] = 1
    success: Literal[False] = False
    error: str
    problem: CommandError
    conflicts: list[EventDetail] = Field(default_factory=list)
