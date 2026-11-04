"""NLP produces the same request envelope as structured A2A calls."""

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from schedule_nlp.llm_provider import LLMProvider
from schedule_nlp.nlp_processor import NLPProcessor


@pytest.mark.asyncio
async def test_contact_update_prompt_requires_identity():
    identity = "b42d2b89-ed4c-4ecf-a1f9-ed5c8346b2cd"

    async def respond(prompt, query):
        schemas = json.loads(prompt[prompt.index('{"create_event":') :])
        update = schemas["update_contact"]
        assert "id" in update["required"]
        assert update["properties"]["id"]["format"] == "uuid"
        return {"intent": "update_contact", "payload": {"id": identity, "phone": "555-1234"}}

    provider = AsyncMock(spec=LLMProvider)
    provider._generate_json.side_effect = respond
    result = await NLPProcessor(provider, None).parse_natural_language_query(
        f"Change {identity} phone to 555-1234"
    )
    assert result.intent == "update_contact"
    assert UUID(result.payload["id"]) == UUID(identity)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"intent": "create_event", "payload": {"title": "Meeting"}},
        {
            "intent": "query_events",
            "payload": {"start_time": "2030-01-01T00:00:00Z", "end_time": "2030-01-02T00:00:00Z"},
        },
        {"intent": "clarify", "payload": {"message": "When?"}},
    ],
)
async def test_parse_typed_request(response):
    provider = AsyncMock(spec=LLMProvider)
    provider._generate_json.return_value = response
    result = await NLPProcessor(provider, None).parse_natural_language_query("Schedule request")
    assert result.model_dump() == response
    assert provider._generate_json.await_args.args[1] == "Schedule request"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {"payload": []}])
async def test_invalid_provider_response_does_not_create_a_command(response):
    provider = AsyncMock(spec=LLMProvider)
    provider._generate_json.return_value = response
    result = await NLPProcessor(provider, None).parse_natural_language_query("Request")
    assert result.intent == "unknown"
    assert result.payload == {}


@pytest.mark.asyncio
async def test_provider_failure_does_not_invent_dates():
    provider = AsyncMock(spec=LLMProvider)
    provider._generate_json.side_effect = RuntimeError("Unavailable")
    result = await NLPProcessor(provider, None).parse_natural_language_query("Meeting tomorrow")
    assert result.intent == "unknown"
    assert result.payload == {}
    assert (
        await NLPProcessor(None, None).parse_natural_language_query("Meeting")
    ).intent == "unknown"
