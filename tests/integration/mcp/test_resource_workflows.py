"""Public resource operations through real MCP, CLI reads and temporary SQLite."""

import json
import subprocess
import sys

import pytest
from mcp.server.fastmcp import FastMCP
from schedule_core.core.settings import Settings
from schedule_core.db.service import DatabaseService
from schedule_core.storage import migrate

from schedule_mcp.handlers import register_handlers


@pytest.mark.asyncio
async def test_agent_resource_workflows(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///" + str(tmp_path / "schedule-agent.db"),
        default_timezone="America/Chicago",
    )
    db = DatabaseService.from_settings(settings)
    await migrate(db)
    (tmp_path / "config.json").write_text('{"default_timezone":"America/Chicago"}')
    mcp = FastMCP("resources")
    register_handlers(mcp, db, settings)

    async def call(tool_name, **values):
        reply = await mcp.call_tool(tool_name, values)
        blocks = reply[0] if isinstance(reply, tuple) else reply
        return json.loads(blocks[0].text)

    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "schedule_cli", "--data-dir", str(tmp_path), "--json", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    try:
        for event in [
            {
                "kind": "timed",
                "title": "Meeting",
                "start_time": "2030-01-07T09:00:00-06:00",
                "end_time": "2030-01-07T10:00:00-06:00",
            },
            {"kind": "point", "title": "Appointment", "start": "2030-01-07T12:00:00-06:00"},
            {"kind": "all_day", "title": "Holiday", "start": "2030-01-07"},
            {"kind": "anniversary", "title": "Birthday", "start": "1990-01-07"},
        ]:
            created = await call("create_event", event=event)
            assert created["success"], created
            eid = created["event_id"]
            updated = await call("update_event", id=eid, patch={"notes": "updated"})
            assert updated["success"], updated
            assert cli("events", "get", eid)["event"] == updated["event"]
            assert (await call("query_schedule", resource="events", id=eid))["events"] == [
                updated["event"]
            ]
            assert (await call("delete_event", id=eid))["success"]
            assert (await call("query_schedule", id=eid))["count"] == 0
        task = await call("create_task", title="Habit", start="2030-01-07", repeat="++1d")
        assert task["success"], task
        tid = task["task_id"]
        assert task["task"]["time_zone"] == "America/Chicago"
        assert (await call("update_task", id=tid, patch={"state": "BLOCKED"}))["task"][
            "state"
        ] == "BLOCKED"
        with pytest.raises(Exception):
            await call("update_task", id=tid, patch={"state": "DONE"})
        done = await call("complete_task", id=tid, completed_at="2030-01-10T12:00:00-06:00")
        assert done["success"], done
        assert done["task"]["state"] == "TODO"
        assert done["task"]["start"] == "2030-01-11"
        assert cli("tasks", "get", tid)["task"] == done["task"]
        assert (await call("query_schedule", resource="tasks", scheduled="2030-01-11")) == cli(
            "tasks", "list", "--scheduled", "2030-01-11"
        )
        assert (await call("delete_task", id=tid))["success"]
        task = await call("create_task", title="Undated")
        tid = task["task_id"]
        assert (await call("query_schedule", resource="tasks", id=tid))["count"] == 1
        assert (await call("query_schedule", view="occurrences", id=tid))["count"] == 0
        assert (await call("complete_task", id=tid))["task"]["state"] == "DONE"
        assert (await call("update_task", id=tid, patch={"state": "TODO"}))["task"][
            "completed_at"
        ] is None
        assert (await call("delete_event", id=tid))["success"] is False
        contact = await call("create_contact", name="Someone", email="somebody@example.com")
        cid = contact["contact_id"]
        assert (await call("update_contact", id=cid, patch={"phone": "555"}))["success"]
        assert (await call("query_contacts", id=cid))["contacts"][0]["phone"] == "555"
        assert (await call("delete_contact", id=cid))["success"]
        series = await call(
            "create_event",
            event={
                "kind": "timed",
                "title": "Daily",
                "start_time": "2030-01-07T09:00:00-06:00",
                "end_time": "2030-01-07T10:00:00-06:00",
                "rrule": "FREQ=DAILY;COUNT=3",
            },
        )
        eid = series["event_id"]
        original = "2030-01-08T09:00:00-06:00"
        moved = await call(
            "update_event",
            id=eid,
            scope="occurrence",
            occurrence=original,
            patch={
                "start_time": "2030-01-08T12:00:00-06:00",
                "end_time": "2030-01-08T13:00:00-06:00",
            },
        )
        assert moved["success"], moved
        queried = await call(
            "query_schedule", id=eid, resource="events", view="occurrences", occurrence=original
        )
        assert queried["events"][0] == cli("events", "get", eid, "--occurrence", original)["event"]
        assert (await call("delete_event", id=eid, scope="future", occurrence=original))["success"]
        after = await call(
            "query_schedule",
            view="occurrences",
            resource="events",
            start="2030-01-07T00:00:00-06:00",
            end="2030-01-11T00:00:00-06:00",
        )
        assert after["count"] == 1
        with pytest.raises(Exception):
            await call(
                "create_event",
                event={
                    "kind": "point",
                    "title": "Bad",
                    "start": "2030-01-07T12:00:00-06:00",
                    "end_time": "2030-01-07T13:00:00-06:00",
                },
            )
    finally:
        await db.close()
