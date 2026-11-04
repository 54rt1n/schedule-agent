"""Opt-in contract tests against a configured model endpoint."""
import os

import pytest

from schedule_core.core.settings import Settings
from schedule_core.db.dao import EventCreate, TimeRange
from schedule_nlp.llm_provider import LLMProvider
from schedule_nlp.llm_strategy import LLMStrategy

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_INTEGRATION_TESTS", "false").lower() != "true",
    reason="Requires explicit live-model opt-in and LLM configuration",
)

@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "intent", "schema"), [
    ("Create a meeting titled Sync on January 7 2030 from 12:00 to 13:00 UTC.", "create_event", EventCreate),
    ("List events between January 7 2030 00:00 UTC and January 8 2030 00:00 UTC.", "query_events", TimeRange),
    ("Tell me a joke about calendars", "unknown", None),
    ("Schedule a meeting, but I have not decided the date or duration.", "clarify", None),
])
async def test_live_scheduling_contract(query, intent, schema):
    provider = LLMProvider.from_settings(Settings())
    try:
        result = await LLMStrategy(provider).parse_scheduling_query(query)
        assert result.intent == intent
        if schema is not None:
            schema.model_validate(result.payload)
        elif intent == "clarify":
            assert result.payload["message"]
        else:
            assert result.payload == {}
    finally:
        await provider.client.close()
