import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from schedule_a2a.dto import AgentCard


def test_advertised_query_example_dispatches(client, auth_headers_valid):
    card = client.get("/.well-known/agent.json").json()
    skill = next(s for s in card["skills"] if s["name"] == "Query Schedule")
    data = json.loads(next(e for e in skill["examples"] if e.startswith("{")))
    assert data["intent"] == skill["id"]
    response = client.post(
        "/api/rpc",
        headers=auth_headers_valid,
        json={
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tasks/send",
            "params": {
                "id": str(uuid4()),
                "message": {"role": "user", "parts": [{"type": "data", "data": data}]},
            },
        },
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body.get("error") is None, body
    assert body["result"]["status"]["state"] == "completed"


def test_get_agent_card(client: TestClient):
    """Test the /.well-known/agent.json endpoint."""
    response = client.get("/.well-known/agent.json")

    assert response.status_code == 200
    # Validate response against the Pydantic model
    try:
        agent_card_data = response.json()
        AgentCard.model_validate(agent_card_data)  # Raises ValidationError on failure
    except Exception as e:
        pytest.fail(
            f"Response is not valid JSON or does not match AgentCard schema: {e}\nResponse: {response.text}"
        )

    # Optionally assert specific fields
    # These are example assertions, adjust based on your actual agent card content
