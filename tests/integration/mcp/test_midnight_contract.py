"""MCP argument validation preserves date-only and midnight datetime inputs."""

import json
from datetime import datetime

import pytest
from mcp.server import MCPServer
from schedule_mcp.handlers import register_handlers


@pytest.mark.asyncio
async def test_midnight_through_mcp(mcp_test_db_service, mcp_test_settings):
    server = MCPServer("midnight-contract")
    register_handlers(server, mcp_test_db_service, mcp_test_settings)

    async def call(name, arguments):
        reply = await server.call_tool(name, arguments)
        blocks = reply.content
        value = json.loads(blocks[0].text)
        assert value["success"], value
        return value

    created = await call(
        "create_task",
        {
            "title": "Midnight UTC",
            "start": "2030-01-07T00:00:00Z",
            "deadline": "2030-01-08",
            "time_zone": "America/Chicago",
        },
    )
    assert datetime.fromisoformat(created["task"]["start"]) == datetime.fromisoformat(
        "2030-01-07T00:00:00Z"
    )
    assert created["task"]["deadline"] == "2030-01-08"
    await call(
        "update_task",
        {
            "id": created["task_id"],
            "patch": {"start": "2030-01-08T00:00:00Z"},
        },
    )
    queried = await call("query_schedule", {"id": created["task_id"]})
    assert datetime.fromisoformat(queried["tasks"][0]["start"]) == datetime.fromisoformat(
        "2030-01-08T00:00:00Z"
    )
