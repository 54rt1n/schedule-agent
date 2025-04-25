import pytest
from fastapi.testclient import TestClient
from sa.a2a.dto import AgentCard

def test_get_agent_card(client: TestClient):
    """Test the /.well-known/agent.json endpoint."""
    response = client.get("/.well-known/agent.json")

    assert response.status_code == 200
    # Validate response against the Pydantic model
    try:
        agent_card_data = response.json()
        AgentCard.model_validate(agent_card_data) # Raises ValidationError on failure
    except Exception as e:
        pytest.fail(f"Response is not valid JSON or does not match AgentCard schema: {e}\nResponse: {response.text}")

    # Optionally assert specific fields
    # These are example assertions, adjust based on your actual agent card content
    # assert agent_card_data["name"] == "Scheduling Agent"
    # assert "create_event" in [skill["id"] for skill in agent_card_data["skills"]] 