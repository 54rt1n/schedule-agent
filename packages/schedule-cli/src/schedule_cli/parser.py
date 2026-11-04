"""CLI grammar. Business validation and schemas belong to schedule_core."""

import argparse
from pathlib import Path

from schedule_cli.inputs import add_input_flags


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = Parser(
        prog="schedule",
        description="Typed local scheduling; global options precede commands.",
    )
    selection = p.add_mutually_exclusive_group()
    selection.add_argument("--data-dir", type=Path)
    selection.add_argument(
        "--profile", help="Hermes profile (requires the Hermes profile adapter)"
    )
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--version", action="version", version="schedule-cli 0.1.0 / schema 1"
    )
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--timezone", default="UTC")
    for name in ["status", "doctor", "capabilities"]:
        commands.add_parser(name)
    schema = commands.add_parser("schema")
    schema.add_argument("name", nargs="?")
    config = commands.add_parser("config").add_subparsers(dest="action", required=True)
    config.add_parser("show")
    setting = config.add_parser("set")
    setting.add_argument("--timezone")
    setting.add_argument("--log-level")
    profiles = commands.add_parser("profiles").add_subparsers(
        dest="action", required=True
    )
    profiles.add_parser("list")
    profiles.add_parser("show").add_argument("name")
    agenda = commands.add_parser("agenda")
    window(agenda, convenience=True)
    agenda.add_argument("--title")
    agenda.add_argument("--calendar")
    agenda.add_argument("--details", action="store_true", help="Include human-readable notes, descriptions and participants")
    agenda.add_argument("--timezone", help="Display timezone; defaults to the selected calendar's configured timezone")
    for group in ["events", "contacts", "tasks", "calendars"]:
        sub = commands.add_parser(group).add_subparsers(dest="action", required=True)
        listing = sub.add_parser("list")
        if group == "events":
            window(listing)
            for key in ["title", "calendar", "participant", "location"]:
                listing.add_argument("--" + key)
            listing.add_argument("--definitions", action="store_true")
            listing.add_argument("--kind", choices=["timed", "point", "all_day", "anniversary"])
        if group == "contacts":
            listing.add_argument("--name")
            listing.add_argument("--email")
        if group == "tasks":
            listing.add_argument("--state")
            listing.add_argument("--scheduled")
            listing.add_argument("--due-before")
            for key in ("title", "calendar", "location"):
                listing.add_argument("--" + key)
        get = sub.add_parser("get")
        get.add_argument("id")
        if group == "events":
            get.add_argument("--occurrence")
        create = sub.add_parser("create")
        add_input_flags(create, group, "create")
        update = sub.add_parser("update")
        update.add_argument("id")
        add_input_flags(update, group, "update")
        deletion = sub.add_parser("delete")
        deletion.add_argument("id")
        if group == "events":
            for item in (update, deletion):
                item.add_argument(
                    "--scope",
                    choices=["series", "occurrence", "future"],
                    default="series",
                )
                item.add_argument("--occurrence")
            exc = sub.add_parser("exceptions").add_subparsers(
                dest="operation", required=True
            )
            exc.add_parser("list").add_argument("id")
            restore = exc.add_parser("restore")
            restore.add_argument("id")
            restore.add_argument("--occurrence", required=True)
            participants = sub.add_parser("participants").add_subparsers(
                dest="operation", required=True
            )
            participants.add_parser("list").add_argument("id")
            setp = participants.add_parser("set")
            setp.add_argument("id")
            add_input_flags(setp, "participants", "update")
        if group == "tasks":
            complete = sub.add_parser("complete")
            complete.add_argument("id")
            complete.add_argument("--at")

        if group == "contacts":
            imp = sub.add_parser("import")
            imp.add_argument("file", type=Path)
            imp.add_argument("--format", choices=["json", "csv"], required=True)
            imp.add_argument("--apply", action="store_true")
            exp = sub.add_parser("export")
            exp.add_argument("--format", choices=["json", "csv"], default="json")
            exp.add_argument("--output", type=Path)
    conflicts = commands.add_parser("conflicts").add_subparsers(
        dest="action", required=True
    )
    check = conflicts.add_parser("check")
    add_input_flags(check, "events", "update")
    check.add_argument("--exclude-event")
    check.add_argument("--through")
    availability = commands.add_parser("availability").add_subparsers(
        dest="action", required=True
    )
    busy = availability.add_parser("busy")
    window(busy)
    busy.add_argument("--calendar")
    slots = availability.add_parser("slots")
    window(slots)
    slots.add_argument("--calendar")
    slots.add_argument(
        "--duration", required=True, help="Minutes or duration such as 30m/1h"
    )
    slots.add_argument("--timezone")
    slots.add_argument(
        "--working-hours", help="JSON map of weekday numbers 0..6 to HH:MM ranges"
    )
    imp = commands.add_parser("import")
    imp.add_argument("source")
    imp.add_argument("--format", choices=["org", "ics"], default="org")
    imp.add_argument("--recursive", action="store_true")
    imp.add_argument("--exclude", action="append")
    imp.add_argument("--timezone")
    imp.add_argument("--on-unsupported", choices=["error", "skip"], default="error")
    imp.add_argument("--conflicts", choices=["preserve", "reject"], default="preserve")
    imp.add_argument("--since")
    imp.add_argument("--until")
    imp.add_argument("--report", type=Path)
    imp.add_argument("--plan", type=Path)
    imp.add_argument("--apply", action="store_true")
    apply = commands.add_parser("apply-plan")
    apply.add_argument("file", type=Path)
    export = commands.add_parser("export")
    export.add_argument("--format", choices=["org", "ics", "json"], default="org")
    export.add_argument("--output", type=Path)
    export.add_argument(
        "--mode", choices=["definitions", "occurrences"], default="definitions"
    )
    window(export)
    export.add_argument("--calendar")
    sources = commands.add_parser("sources").add_subparsers(
        dest="action", required=True
    )
    sources.add_parser("list")
    sources.add_parser("show").add_argument("id")
    reconcile = sources.add_parser("reconcile")
    reconcile.add_argument("id")
    reconcile.add_argument("--plan", type=Path)
    reconcile.add_argument("--apply", action="store_true")
    backup = commands.add_parser("backup").add_subparsers(dest="action", required=True)
    for name in ["create", "inspect", "restore"]:
        c = backup.add_parser(name)
        c.add_argument("file", type=Path)
        if name == "restore":
            c.add_argument("--apply", action="store_true")
    db = commands.add_parser("db").add_subparsers(dest="action", required=True)
    db.add_parser("check")
    migrations = db.add_parser("migrations").add_subparsers(
        dest="operation", required=True
    )
    migrations.add_parser("status")
    migrations.add_parser("upgrade").add_argument("--apply", action="store_true")
    return p


def window(p, convenience=False):
    p.add_argument("--start", help="ISO time including offset")
    p.add_argument("--end", help="Exclusive ISO time including offset")
    if convenience:
        g = p.add_mutually_exclusive_group()
        g.add_argument("--date")
        g.add_argument("--today", action="store_true")
        g.add_argument("--week", action="store_true")
