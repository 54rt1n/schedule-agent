"""Explicit state inspection, schema initialization and consistent SQLite backups."""

import json
import sqlite3
from pathlib import Path
from schedule_core.contracts import CalendarConfig

SCHEMA_VERSION = 1


def read_config(data):
    path = Path(data) / "config.json"
    return (
        CalendarConfig.model_validate_json(path.read_text())
        if path.exists()
        else CalendarConfig()
    )


def write_config(data, config):
    path = Path(data) / "config.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(config.model_dump_json(indent=2) + "\n")
    temporary.replace(path)


async def migrate(db):
    from schedule_core import items
    from sqlalchemy import text

    async with db.engine.begin() as connection:
        version = (await connection.execute(text("PRAGMA user_version"))).scalar()
        if version > SCHEMA_VERSION:
            raise ValueError("Database schema is newer than this CLI")
        await connection.run_sync(items.SQLModel.metadata.create_all)
        await connection.execute(text(f"PRAGMA user_version={SCHEMA_VERSION}"))


def inspect_database(path):
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        return {
            "integrity": integrity,
            "schema_version": version,
            "target_version": SCHEMA_VERSION,
            "tables": tables,
        }


def backup(data, target):
    data, target = Path(data), Path(target)
    if target.exists():
        raise ValueError("Backup destination already exists")
    # A directory manifest keeps config and the transactionally consistent database together.
    target.mkdir(parents=True)
    try:
        with (
            sqlite3.connect(
                (data / "schedule-agent.db").resolve().as_uri() + "?mode=ro", uri=True
            ) as source,
            sqlite3.connect(target / "schedule-agent.db") as dest,
        ):
            source.backup(dest)
        config = read_config(data)
        (target / "config.json").write_text(config.model_dump_json(indent=2))
        report = inspect_database(target / "schedule-agent.db")
        (target / "manifest.json").write_text(
            json.dumps({"format": "schedule-backup-v1", **report}, indent=2)
        )
        return report
    except BaseException:
        # A failed directory remains visible as incomplete (no valid manifest).
        raise


def inspect_backup(target):
    target = Path(target)
    manifest = json.loads((target / "manifest.json").read_text())
    if manifest.get("format") != "schedule-backup-v1":
        raise ValueError("Unknown backup format")
    CalendarConfig.model_validate_json((target / "config.json").read_text())
    report = inspect_database(target / "schedule-agent.db")
    if report["integrity"] != "ok" or report["schema_version"] > SCHEMA_VERSION:
        raise ValueError("Backup is corrupt or requires a newer schema")
    return report


def restore(data, target):
    target, data = Path(target), Path(data)
    report = inspect_backup(target)
    data.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(
            (target / "schedule-agent.db").resolve().as_uri() + "?mode=ro", uri=True
        ) as source,
        sqlite3.connect(data / "schedule-agent.db") as dest,
    ):
        source.backup(dest)
    write_config(
        data, CalendarConfig.model_validate_json((target / "config.json").read_text())
    )
    return report
