"""Typed scheduling command registry; transports consume schemas from this module."""

from dataclasses import dataclass
from uuid import UUID

from schedule_core import calendar_service as service
from schedule_core import resource_contracts as resources_dto
from schedule_core import resources
from schedule_core.contracts import (
    ContactCreateRequest,
    ContactPatchRequest,
    ContactResult,
    ContactsResult,
    Contract,
)


class ContactGetRequest(Contract):
    id: UUID


class ContactListRequest(Contract):
    id: UUID | None = None
    name: str | None = None
    email: str | None = None


class ContactUpdateRequest(ContactGetRequest):
    patch: ContactPatchRequest


class ParticipantsRequest(ContactGetRequest):
    identifiers: list[str]


@dataclass(frozen=True)
class Command:
    request: type[Contract]
    response: type[Contract]


COMMANDS = {
    "schedule.query": Command(resources_dto.ScheduleQuery, resources_dto.ScheduleResult),
    "tasks.create": Command(resources_dto.TaskCreate, resources_dto.TaskResult),
    "tasks.update": Command(resources_dto.TaskUpdate, resources_dto.TaskResult),
    "tasks.delete": Command(resources_dto.ResourceId, resources_dto.TaskResult),
    "tasks.complete": Command(resources_dto.TaskComplete, resources_dto.TaskResult),
    "events.create": Command(resources_dto.CreateEvent, resources_dto.EventResult),
    "events.update": Command(resources_dto.UpdateEvent, resources_dto.EventResult),
    "events.delete": Command(resources_dto.DeleteEvent, resources_dto.EventResult),
    "contacts.create": Command(ContactCreateRequest, ContactResult),
    "contacts.query": Command(ContactListRequest, ContactsResult),
    "contacts.update": Command(ContactUpdateRequest, ContactResult),
    "contacts.delete": Command(ContactGetRequest, ContactResult),
}


async def dispatch(db, name, values, *, timezone="UTC"):
    command = COMMANDS[name]
    request = command.request.model_validate(values)
    async with db.get_session() as session:
        result = command.response.model_validate(
            await execute(session, name, request, timezone=timezone)
        )
        if name.split(".")[-1] not in {"get", "list", "query"}:
            await session.commit()
    return result


async def execute(session, name, r, *, timezone="UTC"):
    if name == "schedule.query":
        return await resources.query_schedule(session, r, timezone)
    if name.startswith("tasks."):
        return await resources.task_operation(session, name.split(".")[1], r, timezone)
    if name == "events.create":
        return await resources.create_event(session, r, timezone)
    if name == "events.update":
        return await resources.update_event(session, r)
    if name == "events.delete":
        return await resources.delete_event(session, r)
    if name == "contacts.query":
        contacts = await service.contact_list(session, r.name, r.email)
        if r.id is not None:
            contacts = [c for c in contacts if c.id == r.id]
        return ContactsResult(contacts=contacts, count=len(contacts))
    if name == "contacts.create":
        c = await service.contact_write(session, r)
        return ContactResult(contact_id=c.id, contact=c)
    if name == "contacts.update":
        c = await service.contact_write(session, r.patch, r.id)
        return ContactResult(contact_id=c.id, contact=c)
    if name == "contacts.delete":
        c = await service.contact_get(session, r.id)
        await session.delete(c)
        return ContactResult(contact_id=r.id)
    raise ValueError(f"Unknown command: {name}")
