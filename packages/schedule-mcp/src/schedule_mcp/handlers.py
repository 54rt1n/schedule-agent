"""Scheduling MCP tools generated from the shared public Pydantic command contracts."""

import inspect

from pydantic import ConfigDict
from schedule_core.commands import COMMANDS, dispatch
from schedule_core.contracts import (
    FailureResult,
)
from schedule_core.errors import failure

TOOL_DESCRIPTIONS = {
    "query_schedule": (
        "Read events and tasks by ID or filters. view=definitions includes undated and completed tasks; "
        "view=occurrences returns an agenda, defaulting to today in the connected profile timezone."
    ),
    "create_event": "Create a timed, point, all-day or anniversary event. Timed events reject detected scheduling conflicts.",
    "update_event": (
        "Update event fields. Timed recurring events support series, occurrence or future scope; "
        "an occurrence timestamp requires explicit occurrence/future scope. Other event types require series scope."
    ),
    "delete_event": "Delete an event. Defaults to the whole series; timed occurrence/future deletion requires explicit scope and original occurrence time.",
    "create_task": "Create a task with optional scheduled date, deadline and Org repeater. Undated tasks remain available through definitions.",
    "update_task": "Update task fields or reopen a completed task with state=TODO. Use complete_task to record completion.",
    "delete_task": "Permanently delete a task by ID. To retain the record, update its state to CANCELLED instead.",
    "complete_task": "Complete a TODO/BLOCKED task. Org repeaters advance dates and return to TODO; other tasks become DONE. Retrying a repeating completion can advance it again.",
    "query_contacts": "Find contacts by ID, name substring or email substring. Phone numbers and notes are returned details, not search filters.",
    "create_contact": "Create a contact with name and optional email, phone and notes. Does not send messages or invitations.",
    "update_contact": "Update contact details by ID. Omitted fields stay unchanged; null clears optional fields. Notes replace the whole notes field.",
    "delete_contact": "Permanently delete a contact by ID. Linked documents and event participant email identifiers remain unchanged.",
}


def register_handlers(mcp, db_service, settings):
    names = {
        "query_schedule": "schedule.query",
        "create_event": "events.create",
        "update_event": "events.update",
        "delete_event": "events.delete",
        "create_task": "tasks.create",
        "update_task": "tasks.update",
        "delete_task": "tasks.delete",
        "complete_task": "tasks.complete",
        "create_contact": "contacts.create",
        "query_contacts": "contacts.query",
        "update_contact": "contacts.update",
        "delete_contact": "contacts.delete",
    }
    definitions = {name: (command, COMMANDS[command].request) for name, command in names.items()}
    for name, (command, model) in definitions.items():
        function = make_tool(name, command, model, db_service, settings)
        mcp.add_tool(
            function,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
        )
        # FastMCP otherwise builds a permissive argument DTO and drops unknown keys
        # before the shared request validator sees them. Retain its shallow-dump API.
        tool = mcp._tool_manager.get_tool(name)
        arguments = type(
            name + "Arguments",
            (model, tool.fn_metadata.arg_model),
            {"model_config": ConfigDict(extra="forbid", arbitrary_types_allowed=True)},
        )
        tool.fn_metadata.arg_model = arguments
        tool.parameters = arguments.model_json_schema()


def make_tool(name, command, model, db, settings):
    async def invoke(**kwargs):
        try:
            request = model.model_validate(kwargs)
            return await dispatch(
                db,
                command,
                request.model_dump(exclude_unset=True),
                timezone=settings.default_timezone,
            )
        except Exception as error:
            # Boundary failures are typed too; no raw ORM or generic response dictionaries.
            result = failure(error)
            result.pop("details", None)
            return FailureResult.model_validate(result)

    invoke.__name__ = name
    fields = model.model_fields
    parameters = []
    annotations = {}
    for key, field in fields.items():
        default = (
            inspect.Parameter.empty
            if field.is_required()
            else (field.default_factory() if field.default_factory else field.default)
        )
        parameters.append(
            inspect.Parameter(
                key,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=field.annotation,
            )
        )
        annotations[key] = field.annotation
    response = COMMANDS[command].response | FailureResult
    annotations["return"] = response
    invoke.__annotations__ = annotations
    invoke.__signature__ = inspect.Signature(parameters, return_annotation=response)
    return invoke
