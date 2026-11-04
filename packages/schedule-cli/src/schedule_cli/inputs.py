"""Translate command-line fields into application request dictionaries."""

import argparse

FIELDS = {
    "events": {
        "title": ("--title",),
        "start": ("--start", "--start-time"),
        "end": ("--end", "--end-time"),
        "time_zone": ("--timezone",),
        "calendar_id": ("--calendar",),
        "location": ("--location",),
        "notes": ("--notes",),
        "description": ("--description",),
        "rrule": ("--rrule",),
        "repeat": ("--repeat",),
        "participant_identifiers": ("--participant",),
    },
    "tasks": {
        "title": ("--title",),
        "start": ("--scheduled", "--start"),
        "deadline": ("--deadline",),
        "time_zone": ("--timezone",),
        "calendar_id": ("--calendar",),
        "location": ("--location",),
        "notes": ("--notes",),
        "repeat": ("--repeat",),
        "state": ("--state",),
    },
    "contacts": {key: ("--" + key,) for key in ("name", "email", "phone", "notes")},
    "calendars": {key: ("--" + key,) for key in ("id", "name")},
    "participants": {"identifiers": ("--participant",)},
}
CLEARABLE = {
    "events": ("location", "notes", "description", "rrule", "repeat", "end", "participants"),
    "tasks": ("scheduled", "deadline", "repeat", "location", "notes"),
    "contacts": ("email", "phone", "notes"),
    "calendars": (),
    "participants": ("participants",),
}


def add_input_flags(parser, group, action):
    parser.add_argument(
        "--data", help="Optional JSON, @file, or - for stdin; use instead of field flags"
    )
    for field, flags in FIELDS[group].items():
        if group == "calendars" and action == "update" and field == "id":
            continue
        options = {
            "dest": "input_" + field,
            "default": argparse.SUPPRESS,
            "metavar": flags[0].removeprefix("--").upper(),
        }
        if field in {"start", "end", "deadline"}:
            options["help"] = "ISO date/time; times without an offset use the record timezone"
        if field == "time_zone":
            options["help"] = "IANA timezone, such as America/Chicago"
        if field in {"participant_identifiers", "identifiers"}:
            options.update(
                action="append",
                help="Repeat to supply multiple participants; replaces the list on update",
            )
        if field == "state":
            options["choices"] = ["TODO", "BLOCKED"] + (["CANCELLED"] if action == "update" else [])
        parser.add_argument(*flags, **options)
    if group == "events" and action == "create":
        parser.add_argument(
            "--kind",
            dest="input_kind",
            default=argparse.SUPPRESS,
            choices=["timed", "point", "all_day", "anniversary"],
        )
    if action == "update" and CLEARABLE[group]:
        parser.add_argument(
            "--clear",
            action="append",
            choices=CLEARABLE[group],
            help="Clear a field; repeat for multiple fields",
        )


def input_payload(args, *, event_kind="timed"):
    values = {
        key.removeprefix("input_"): value
        for key, value in vars(args).items()
        if key.startswith("input_")
    }
    clear = getattr(args, "clear", None) or []
    if args.data is not None:
        if values or clear:
            raise ValueError("Use field flags or --data, not both")
        from schedule_cli.main import payload

        return payload(args.data)
    for name in clear:
        field = {"scheduled": "start", "participants": "participant_identifiers"}.get(name, name)
        if args.command == "events" and getattr(args, "action", None) == "participants":
            field = "identifiers"
        if field in values:
            raise ValueError(f"Cannot set and clear {name} together")
        values[field] = [] if name == "participants" else None
    kind = values.get("kind", event_kind)
    if args.command in {"events", "conflicts"} and kind == "timed":
        for field in ("start", "end"):
            if field in values:
                values[field + "_time"] = values.pop(field)
    if not values:
        raise ValueError("Provide field flags or --data; see --help")
    return values
