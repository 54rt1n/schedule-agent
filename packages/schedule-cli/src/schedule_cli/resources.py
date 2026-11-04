"""CLI grammar adapters for shared resource commands; no domain mutations."""

from schedule_core.calendar_service import ApplicationError
from schedule_core.commands import dispatch

from schedule_cli.inputs import input_payload
from schedule_cli.main import timestamp


def schedule_query(args):
    if args.command == "agenda":
        fields = ("start", "end", "date", "today", "week", "title", "calendar", "timezone")
        return {"view": "occurrences", **{key: getattr(args, key) for key in fields}}
    values = {"resource": args.command, "view": "definitions"}
    for key in (
        "id",
        "title",
        "calendar",
        "participant",
        "location",
        "kind",
        "state",
        "scheduled",
        "due_before",
    ):
        if getattr(args, key, None) is not None:
            values[key] = getattr(args, key)
    if getattr(args, "start", None) or getattr(args, "end", None):
        if getattr(args, "definitions", False):
            raise ValueError("Use --definitions or an occurrence window, not both")
        values.update(view="occurrences", start=args.start, end=args.end)
    if getattr(args, "occurrence", None):
        values.update(view="occurrences", occurrence=timestamp(args.occurrence))
    return values


def command_arguments(args, *, event_kind="timed"):
    group, action = args.command, getattr(args, "action", None)
    if group == "agenda" or (group in {"events", "tasks"} and action in {"get", "list"}):
        return "schedule.query", schedule_query(args)
    if group == "contacts" and action in {"get", "list"}:
        values = {"id": args.id} if action == "get" else {"name": args.name, "email": args.email}
        return "contacts.query", values
    if action == "create":
        values = input_payload(args)
        if group == "events":
            values = {"event": {"kind": "timed", **values}}
    elif action == "update":
        values = {"id": args.id, "patch": input_payload(args, event_kind=event_kind)}
    elif action == "complete":
        values = {"id": args.id, "completed_at": timestamp(args.at)}
    else:
        values = {"id": args.id}
    if group == "events" and action in {"update", "delete"}:
        values.update(scope=args.scope, occurrence=timestamp(args.occurrence))
    return group + "." + action, values


async def execute_resource(db, args, config):
    event_kind = "timed"
    if (
        args.command == "events"
        and args.action == "update"
        and args.data is None
        and (
            hasattr(args, "input_start")
            or hasattr(args, "input_end")
            or "end" in (getattr(args, "clear", None) or [])
        )
    ):
        current = await dispatch(db, "schedule.query", {"resource": "events", "id": args.id})
        if not current.events:
            raise ApplicationError("not_found", f"Event {args.id} not found")
        event_kind = current.events[0].kind
    command, values = command_arguments(args, event_kind=event_kind)
    if args.command == "agenda":
        from schedule_core.agenda import agenda_window
        from schedule_core.agenda_contracts import AgendaRequest

        from schedule_cli.agenda_output import AgendaContext

        zone = args.timezone or config.default_timezone
        request = AgendaRequest.model_validate(
            {key: value for key, value in values.items() if key != "view"}
        )
        start, end = agenda_window(request, zone)
        args.agenda_context = AgendaContext(start, end, zone)
        values = {
            key: value for key, value in values.items() if key not in {"date", "today", "week"}
        }
        values.update(start=start, end=end)
    result = await dispatch(db, command, values, timezone=config.default_timezone)
    body = result.model_dump(mode="json")
    if getattr(args, "action", None) == "get":
        rows = body[args.command]
        if not rows:
            raise ApplicationError("not_found", f"{args.command} record {args.id} not found")
        singular = args.command[:-1]
        return {"success": True, "schema_version": 1, singular + "_id": args.id, singular: rows[0]}
    return body
