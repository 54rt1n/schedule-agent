"""Typed boundary for reporting, interchange and local calendar administration."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from schedule_core.agenda_contracts import ItemOccurrence
from schedule_core.contracts import (
    CalendarConfig,
    Contract,
    EventCreateRequest,
    EventDetail,
)


class EmptyRequest(Contract):
    pass


class IdRequest(Contract):
    id: str


class RangeRequest(Contract):
    start: datetime
    end: datetime
    calendar: str | None = None


class AvailabilityRequest(RangeRequest):
    duration: str
    timezone: str | None = None
    working_hours: dict[str, list[tuple[str, str]]] | None = None


class TimeSlot(Contract):
    start: datetime
    end: datetime


class AvailabilityResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    count: int | None = None
    busy: list[TimeSlot] | None = None
    slots: list[TimeSlot] | None = None
    point_appointments: list[ItemOccurrence] = []


class ConflictResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    count: int
    conflicts: list[EventDetail]
    checked_through: datetime


class ConfigResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    config: CalendarConfig
    data_dir: str


class StorageResult(Contract):
    success: bool = True
    schema_version: int = 1
    target_version: int | None = None
    data_dir: str | None = None
    database: str | None = None
    timezone: str | None = None
    config: CalendarConfig | None = None
    integrity: str | None = None
    tables: list[str] | None = None
    preview: bool | None = None
    destination: str | None = None


class ParticipantsResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    event_id: UUID
    identifiers: list[str]


class CalendarDetail(Contract):
    id: str
    name: str


class CalendarsResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    calendars: list[CalendarDetail] | None = None
    calendar: CalendarDetail | None = None
    calendar_id: str | None = None


class FileResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    path: str
    count: int
    format: str | None = None


def validate_output(command, action, result):
    """Validate transport output after handlers; never let implicit ORM shape leak."""
    if not result.get("success"):
        return result
    model = None
    if command == "agenda":
        from schedule_core.resource_contracts import ScheduleResult

        model = ScheduleResult

    elif command == "availability":
        model = AvailabilityResult
    elif command == "conflicts":
        model = ConflictResult
    elif command == "config":
        model = ConfigResult
    elif command in {"init", "status", "doctor", "db", "backup"}:
        model = StorageResult
    elif command == "calendars":
        model = CalendarsResult
    elif command == "events" and action == "participants":
        model = ParticipantsResult
    elif command == "sources" and action in {"list", "show"}:
        model = SourcesResult
    elif command == "events" and action == "exceptions":
        model = ExceptionsResult
    elif command == "schema":
        model = SchemaResult
    elif command == "capabilities":
        model = CapabilitiesResult
    elif command == "profiles":
        model = ProfileResult
    elif command == "contacts" and action in {"import", "export"}:
        model = ContactInterchangeResult
    elif command == "export" and "path" in result:
        model = FileResult
    if model:
        return model.model_validate(result).model_dump(
            mode="json", exclude_none=command != "agenda"
        )
    return result


class ExceptionRequest(IdRequest):
    occurrence: datetime


class ConflictRequest(Contract):
    event: EventCreateRequest
    exclude_event: UUID | None = None
    through: datetime | None = None


class PathRequest(Contract):
    file: str
    apply: bool = False


class ContactImportRow(Contract):
    id: UUID | None = None
    name: str
    email: str | None = None
    phone: str | None = None
    notes: str | None = None


class ContactInterchangeResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    count: int
    applied: bool | None = None
    contacts: list[ContactImportRow] | None = None
    content: str | None = None
    path: str | None = None


class ProfileResolution(Contract):
    name: str
    home: str
    data_dir: str


class ProfileListing(Contract):
    listing: str


class ProfileResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    profiles: ProfileResolution | ProfileListing


class MigrationRequest(Contract):
    apply: bool = False


class ContactImportRequest(PathRequest):
    format: Literal["json", "csv"]


class ContactExportRequest(Contract):
    format: Literal["json", "csv"] = "json"
    output: str | None = None


class ProfileRequest(Contract):
    name: str


class CalendarPatchRequest(Contract):
    id: str
    name: str


class SourceReconcileRequest(IdRequest):
    plan: str | None = None
    apply: bool = False




# These administrative result models intentionally carry structured schemas/config
# rather than ORM objects; scheduling records retain their specific DTOs above.
from pydantic import JsonValue

from schedule_core.interchange import ImportRequest


class SourceInfo(Contract):
    id: str
    path: str
    options: ImportRequest


class SourcesResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    sources: list[SourceInfo] | None = None
    id: str | None = None
    path: str | None = None
    options: ImportRequest | None = None


class ModifiedOccurrence(Contract):
    id: UUID
    event_id: UUID
    original_date: datetime
    original_start: datetime
    start_time: datetime
    end_time: datetime
    time_zone: str
    title: str | None = None
    location: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class ExceptionsResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    event_id: UUID
    excluded: list[datetime] | None = None
    modified: list[ModifiedOccurrence] | None = None
    restored: datetime | None = None


class SchemaRequest(Contract):
    name: str | None = None


class SchemaResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    schemas: dict[str, JsonValue]


class CapabilitiesResult(Contract):
    success: Literal[True] = True
    schema_version: Literal[1] = 1
    commands: list[str]
    recurrence_conflict_horizon_days: int
    profile_adapter: str
    formats: dict[str, list[str]]
