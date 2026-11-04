"""Real MCP registration shares strict DTOs and the owning profile's timezone."""

import json
import subprocess
import sys
from datetime import UTC

import pytest
from mcp.server.fastmcp import FastMCP
from schedule_core.agenda import agenda_window
from schedule_core.agenda_contracts import AgendaRequest
from schedule_core.core.settings import Settings
from schedule_core.db.service import DatabaseService

from schedule_mcp.handlers import register_handlers


@pytest.mark.asyncio
async def test_typed_mcp_arguments_and_profile_timezone(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///" + str(tmp_path / "calendar.db"),
        default_timezone="America/Chicago",
        log_level="ERROR",
    )
    db = DatabaseService.from_settings(settings)
    await db.initialize()
    try:
        server = FastMCP("typed-schedule-test")
        register_handlers(server, db, settings)
        tools = await server.list_tools()
        assert len(tools) == 12
        schemas = {t.name: t.inputSchema for t in tools}
        assert schemas["create_event"]["additionalProperties"] is False
        assert schemas["update_event"]["$defs"]["EventPatch"]["additionalProperties"] is False
        reply = await server.call_tool(
            "create_event",
            {
                "event": {
                    "kind": "timed",
                    "title": "Meeting",
                    "start_time": "2030-01-07T10:00:00",
                    "end_time": "2030-01-07T11:00:00",
                }
            },
        )
        content = reply[0] if isinstance(reply, tuple) else reply
        event = json.loads(content[0].text)["event"]
        assert event["time_zone"] == "America/Chicago"
        assert event["start_time"] == "2030-01-07T16:00:00+00:00"
        with pytest.raises(Exception, match="Extra inputs"):
            await server.call_tool(
                "update_event",
                {"id": event["id"], "patch": {"titel": "Misspelled"}},
            )
        with pytest.raises(Exception, match="Extra inputs"):
            await server.call_tool("create_contact", {"name": "Invalid", "unknown": "field"})
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_complete_mcp_agenda_matches_cli(tmp_path):
    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "schedule_cli", "--data-dir", str(tmp_path), "--json", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    cli("init", "--timezone", "America/Chicago")
    for values in [
        {"kind": "task", "title": "Deadline", "start": "2030-01-06", "deadline": "2030-01-07"},
        {"kind": "all_day", "title": "Day", "start": "2030-01-07"},
        {"kind": "anniversary", "title": "Birthday", "start": "1990-01-07"},
        {"kind": "point", "title": "Point", "start": "2030-01-07T12:00:00-06:00"},
    ]:
        group = "tasks" if values["kind"] == "task" else "events"
        if group == "tasks":
            values.pop("kind")
        cli(group, "create", "--data", json.dumps(values))
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///" + str(tmp_path / "schedule-agent.db"),
        default_timezone="America/Chicago",
        log_level="ERROR",
    )
    db = DatabaseService.from_settings(settings)
    server = FastMCP("agenda-test")
    register_handlers(server, db, settings)

    async def call(name, arguments):
        if name == "agenda":
            name = "query_schedule"
            arguments = {"view": "occurrences", **arguments}
        if name == "create_event":
            arguments = {"event": {"kind": "timed", **arguments}}
        result = await server.call_tool(name, arguments)
        blocks = result[0] if isinstance(result, tuple) else result
        return json.loads(blocks[0].text)

    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert set(tools) == {
            "query_schedule",
            "create_task",
            "update_task",
            "delete_task",
            "complete_task",
            "create_event",
            "update_event",
            "delete_event",
            "create_contact",
            "query_contacts",
            "update_contact",
            "delete_contact",
        }
        assert not ({"import", "export", "apply_plan"} & tools.keys())
        assert tools["query_schedule"].inputSchema["additionalProperties"] is False
        assert (
            await call(
                "create_event",
                {
                    "title": "Evening",
                    "start_time": "2030-01-08T03:00:00Z",
                    "end_time": "2030-01-08T04:00:00Z",
                    "rrule": "FREQ=DAILY;COUNT=2",
                },
            )
        )["success"]
        agenda = await call("agenda", {"date": "2030-01-07"})
        expected = cli("agenda", "--date", "2030-01-07")
        assert agenda == expected
        assert agenda["count"] == 5
        assert {event["kind"] for event in agenda["events"]} == {
            "timed",
            "all_day",
            "anniversary",
            "point",
        }
        assert len(agenda["tasks"]) == 1
        assert len(agenda["events"]) == 4
        assert (
            await call(
                "agenda", {"start": "2030-01-07T00:00:00-06:00", "end": "2030-01-08T00:00:00-06:00"}
            )
        ) == agenda
        next_day = await call("agenda", {"date": "2030-01-08"})
        assert next_day == cli("agenda", "--date", "2030-01-08")
        assert len(next_day["events"]) == 1
        assert (
            next_day["events"][0]["occurrence"]["original_start"]
            != next(e for e in agenda["events"] if e["kind"] == "timed")["occurrence"][
                "original_start"
            ]
        )
        assert (await call("agenda", {})) == cli("agenda")
        assert (await call("agenda", {"week": True})) == cli("agenda", "--week")
        filtered = await call("agenda", {"date": "2030-01-07", "title": "Birthday"})
        assert filtered["count"] == 1
        assert (await call("agenda", {"date": "2030-01-07", "calendar": "other"}))["count"] == 0
        for values in [
            {"start": "2030-01-07T00:00:00Z"},
            {"start": "2030-01-08T00:00:00Z", "end": "2030-01-07T00:00:00Z"},
            {"date": "2030-01-07", "week": True},
        ]:
            assert (await call("agenda", values))["success"] is False
        with pytest.raises(Exception, match="Extra inputs"):
            await server.call_tool("query_schedule", {"unknown": True})
    finally:
        await db.close()


@pytest.mark.parametrize(("day", "hours"), [("2030-03-10", 23), ("2030-11-03", 25)])
def test_agenda_local_day_respects_dst(day, hours):
    start, end = agenda_window(AgendaRequest(date=day), "America/Chicago")
    assert start.hour == end.hour == 0
    assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == hours * 3600
