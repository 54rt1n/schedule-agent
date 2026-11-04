"""Schedule command adapter: typed application contracts, local durable state."""

import asyncio
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from schedule_cli.parser import parser


def payload(value):
    raw = (
        sys.stdin.read()
        if value == "-"
        else Path(value[1:]).read_text()
        if value.startswith("@")
        else value
    )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--data must be a JSON object")
    return data


def resolve_data(args):
    if args.profile:
        from schedule_cli.profiles import resolve

        return resolve(args.profile)
    if args.data_dir is not None:
        return args.data_dir.expanduser().resolve()
    here = Path.cwd().resolve()
    for parent in (here, *here.parents):
        if (parent / ".schedule" / "schedule-agent.db").is_file():
            return parent / ".schedule"
    if args.command == "init":
        return here / ".schedule"
    raise ValueError("No calendar selected; use --profile NAME, --data-dir PATH, or schedule init")


def timestamp(value):
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Times must include a UTC offset")
    return result


def window(args, timezone):
    start, end = timestamp(args.start), timestamp(args.end)
    if args.command == "agenda" and not start and not end:
        zone = ZoneInfo(args.timezone or timezone)
        date = datetime.fromisoformat(args.date).date() if args.date else datetime.now(zone).date()
        if args.week:
            date -= timedelta(days=date.weekday())
        start = datetime.combine(date, datetime.min.time(), zone)
        end = start + timedelta(days=7 if args.week else 1)
    if start is None or end is None or start >= end:
        raise ValueError("Provide --start and --end with end after start")
    return start, end


async def execute(args):
    from schedule_core.contracts import CalendarConfig
    from schedule_core.core.logging_config import setup_logging
    from schedule_core.core.settings import Settings
    from schedule_core.db.service import DatabaseService
    from schedule_core.storage import (
        backup,
        inspect_backup,
        inspect_database,
        migrate,
        read_config,
        restore,
        write_config,
    )

    if args.command in {"schema", "capabilities"}:
        from schedule_cli.extra import schemas

        registry = schemas()
        if args.command == "schema":
            if args.name and args.name not in registry:
                raise ValueError("Unknown schema command")
            return {
                "success": True,
                "schema_version": 1,
                "schemas": {args.name: registry[args.name]} if args.name else registry,
            }
        return {
            "success": True,
            "schema_version": 1,
            "commands": sorted(registry),
            "recurrence_conflict_horizon_days": 366,
            "profile_adapter": "schedule_cli.profiles",
            "formats": {"import": ["org", "ics"], "export": ["org", "ics", "json"]},
        }
    if args.command == "profiles":
        from schedule_cli.profiles import provider

        return {
            "success": True,
            "profiles": provider()(args.action, getattr(args, "name", None)),
        }
    data = resolve_data(args)
    db_path = data / "schedule-agent.db"
    if args.command == "backup":
        if args.action == "create":
            result = backup(data, args.file)
        elif args.action == "inspect":
            result = inspect_backup(args.file)
        elif args.apply:
            result = restore(data, args.file)
        else:
            result = {
                "preview": True,
                **inspect_backup(args.file),
                "destination": str(data),
            }
        return {"success": True, **result}
    config = read_config(data)
    if args.command == "init" and not (data / "config.json").exists():
        config = CalendarConfig(default_timezone=args.timezone)
    if args.command == "config":
        if args.action == "set":
            updates = {}
            if args.timezone:
                updates["default_timezone"] = args.timezone
            if args.log_level:
                updates["log_level"] = args.log_level
            config = CalendarConfig.model_validate({**config.model_dump(), **updates})
            if not data.exists():
                raise ValueError("Initialize the calendar first")
            write_config(data, config)
        return {"success": True, "config": config.model_dump(), "data_dir": str(data)}
    if args.command != "init" and not db_path.is_file():
        raise ValueError(f"Calendar is not initialized: {data}")
    if args.command in {"status", "doctor"}:
        result = {
            "success": True,
            "data_dir": str(data),
            "database": str(db_path),
            "timezone": config.default_timezone,
            "config": config.model_dump(),
        }
        if args.command == "doctor":
            result.update(inspect_database(db_path))
            result["success"] = result["integrity"] == "ok"
        return result
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///" + db_path.as_posix(),
        default_timezone=config.default_timezone,
        log_level="ERROR",
    )
    setup_logging(settings)
    db = DatabaseService.from_settings(settings)
    try:
        if args.command == "init":
            data.mkdir(parents=True, exist_ok=True)
            await migrate(db)
            if not (data / "config.json").exists():
                write_config(data, config)
            return {
                "success": True,
                "database": str(db_path),
                "timezone": config.default_timezone,
            }
        if args.command == "db":
            if args.action == "migrations" and args.operation == "upgrade" and args.apply:
                await migrate(db)
            return {"success": True, **inspect_database(db_path)}
        if args.command == "agenda" or (
            args.command in {"events", "tasks", "contacts"}
            and args.action in {"create", "update", "get", "list", "delete", "complete"}
        ):
            from schedule_cli.resources import execute_resource

            return await execute_resource(db, args, config)
        from schedule_cli.extra import execute_extra

        return await execute_extra(db, args, config, data)
    finally:
        await db.close()


def render(result, args):
    if args.json:
        print(json.dumps(result, default=str))
        return
    if args.command == "agenda":
        if not result.get("success"):
            print("Unable to read agenda: " + result["error"])
        else:
            from schedule_cli.agenda_output import format_agenda

            print(format_agenda(result, args.agenda_context, details=args.details))
        return
    if result.get("success") and "events" in result:
        print(f"{len(result['events'])} event(s)")
        for event in result["events"]:
            if event["kind"] == "timed":
                zone = ZoneInfo(getattr(args, "timezone", None) or event["time_zone"])
                start = datetime.fromisoformat(event["start_time"]).astimezone(zone)
                end = datetime.fromisoformat(event["end_time"]).astimezone(zone)
                print(
                    f"{start:%Y-%m-%d %H:%M} — {end:%Y-%m-%d %H:%M} {zone.key} | {event['title']} | {event['id']}"
                )
            else:
                print(
                    f"{event.get('occurrence_start') or event['start']} | {event['kind']} | {event['title']} | {event['id']}"
                )
            if getattr(args, "details", False):
                print(json.dumps(event, indent=2))
        for task in result.get("tasks", []):
            print(
                f"{task.get('occurrence_start') or task.get('start') or task.get('deadline') or 'undated'} | {task['state']} | {task['title']} | {task['id']}"
            )
    elif "content" in result:
        print(result["content"], end="")
    else:
        print(json.dumps(result, indent=2, default=str))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = None
    try:
        args = parser().parse_args(argv)
        with redirect_stdout(sys.stderr):
            result = asyncio.run(execute(args))
            from schedule_core.surface import validate_output

            result = validate_output(args.command, getattr(args, "action", None), result)
    except Exception as error:
        # A process boundary always emits a machine-readable failure. Domain validation
        # is strict; unexpected failures retain a distinct internal-error code.
        from schedule_core.calendar_service import ApplicationError
        from schedule_core.errors import failure
        from sqlalchemy.exc import SQLAlchemyError

        if isinstance(
            error,
            (
                ValueError,
                TypeError,
                KeyError,
                OSError,
                ApplicationError,
                SQLAlchemyError,
            ),
        ):
            result = failure(error)
        else:
            result = failure(ApplicationError("internal", str(error)))
        if args is None:
            print(json.dumps(result) if "--json" in argv else result["error"])
            return 2
    render(result, args)
    return 0 if result.get("success") else 1
