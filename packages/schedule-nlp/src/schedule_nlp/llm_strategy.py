"""Translate text into the existing structured scheduling request envelope."""

import json
from datetime import UTC, datetime
from uuid import UUID

from schedule_core.db.dao import (
    ContactCreate,
    ContactDelete,
    ContactQuery,
    ContactUpdate,
    EventCreate,
    EventDelete,
    EventUpdate,
    TimeRange,
)

from schedule_nlp.dao import LLMSchedulingParseResult


class ContactUpdatePayload(ContactUpdate):
    """Contact identity travels with the fields in the A2A update payload."""

    id: UUID


class LLMStrategy:
    def __init__(self, provider):
        self.provider = provider

    async def parse_scheduling_query(self, query: str) -> LLMSchedulingParseResult:
        schemas = {
            name: model.model_json_schema()
            for name, model in {
                "create_event": EventCreate,
                "update_event": EventUpdate,
                "delete_event": EventDelete,
                "query_events": TimeRange,
                "create_contact": ContactCreate,
                "update_contact": ContactUpdatePayload,
                "delete_contact": ContactDelete,
                "query_contacts": ContactQuery,
            }.items()
        }
        prompt = (
            f"Today is {datetime.now(UTC).date().isoformat()}. "
            'Return only JSON {"intent": name, "payload": request}. '
            "If the input is a conversation, interpret the latest user message using the preceding turns. "
            "Use the request schemas below. Never invent missing dates, durations, time zones or IDs. "
            'If required information is missing, use intent clarify and payload {"message": question}. '
            "For unrelated requests use intent unknown and an empty payload. "
            'For export_calendar use payload {"format":"ics" or "org", '
            '"date_info":{"start_date": ISO date, "end_date": ISO date}}. ' + json.dumps(schemas)
        )
        raw = await self.provider._generate_json(prompt, query)
        return LLMSchedulingParseResult.model_validate(raw)
