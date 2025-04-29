# sa/nlp/llm_strategy.py
# Copyright (C) 2025 Martin Bukowski

"""Provides an interface to interact with Language Models (LLMs)."""

import dateparser
from datetime import UTC, datetime
from pydantic import ValidationError
from typing import Any

from sa.core.logging_config import get_logger
from sa.nlp.dao import (
    LLMDateInfo,
    LLMFilters,
    LLMSchedulingParseResult,
)
from sa.nlp.llm_provider import LLMProvider

logger = get_logger(__name__)


class LLMStrategy:
    """
    Generic LLM provider that works with any OpenAI-compatible API.
    Works with local models (Ollama, LM Studio, etc.) and cloud services.
    Includes fallback logic for date parsing using dateparser.
    """

    def __init__(
        self,
        provider: LLMProvider,
    ):
        """
        Initialize the LLM provider.
        Reads config primarily from settings, allows overrides via init.

        Args:
            provider: LLM provider instance.
        """
        self.provider = provider

    async def parse_scheduling_query(self, query: str) -> LLMSchedulingParseResult:
        """Parse a full natural language query using LLM and Pydantic validation."""
        logger.debug("Parsing full scheduling query", query=query)
        # Note: Prompt includes dynamic current_date and the user_query
        system_prompt = f"""
        You are an intelligent scheduling assistant. Your task is to parse a user's natural language query related to calendar events and extract structured information.
        The current date is {datetime.now(UTC).date().isoformat()}.

        Analyze the query: "{query}"

        Determine the user's primary **intent**. Choose ONE from:
        - create_event: User wants to add a new event.
        - update_event: User wants to modify an existing event.
        - delete_event: User wants to remove an event.
        - find_event: User is looking for a specific event by title or other criteria.
        - query_schedule: User is asking about their schedule for a specific date/range.
        - query_availability: User is asking when they are free.
        - export_calendar: User wants to export calendar data.
        - count_events: User wants to know the number of events.
        - find_next_event: User is asking for their next upcoming event.
        - unknown: The intent is unclear or not related to scheduling.

        Extract **date and time information** relevant to the intent. Provide:
        - start_date: Start date (YYYY-MM-DD). Required if specific dates are mentioned or implied.
        - end_date: End date (YYYY-MM-DD). Use the same as start_date if it's a single day.
        - specific_time: Specific start time (HH:MM:SS), if mentioned.
        - end_time: Specific end time (HH:MM:SS), if duration or end time is mentioned.
        - time_description: Natural language description of the time (e.g., "this week", "tomorrow afternoon").
        - is_recurring: Boolean (true/false) if the event should repeat.
        - recurrence_rule: iCalendar RRULE string if recurring (e.g., 'FREQ=WEEKLY;BYDAY=MO').
        - timeZone: IANA timezone name, if provided or strongly implied.

        Extract **filters** mentioned in the query:
        - title: The title or subject of the event (string).
        - location: The location of the event (string).
        - participants: A list of participant names or emails (list[string]).
        - format: The requested export format (e.g., "ics", "org").

        Respond ONLY with a valid JSON object containing the following keys:
        - "intent": The determined intent (string).
        - "date_info": An object containing the extracted date/time information (object, optional). Include only found fields.
        - "filters": An object containing the extracted filters (object, optional). Include only found fields.
        - "confidence": Your overall confidence in the parsing (float, 0.0 to 1.0).

        Example Input: "Schedule a meeting about Project Alpha with Bob tomorrow at 3pm in the Main Office"
        Example Output:
        {{
          "intent": "create_event",
          "date_info": {{
            "start_date": "{datetime.now(UTC).date().isoformat()}", // Example: Today's date if "tomorrow" relative base is today
            "end_date": "{datetime.now(UTC).date().isoformat()}", // Example: Same date
            "specific_time": "15:00:00",
            "time_description": "tomorrow at 3pm"
          }},
          "filters": {{
            "title": "Project Alpha",
            "participants": ["Bob"],
            "location": "Main Office"
          }},
          "confidence": 0.9
        }}

        If crucial information like date/time for creation is missing, reflect that in the output (e.g., missing date_info fields) but still determine the likely intent.
        If the query is ambiguous or lacks scheduling details, set intent to 'unknown' and confidence lower.
        """  # No .format() needed, using f-string directly

        # Attempt to get raw JSON dict from LLM
        raw_llm_result = await self._generate_json(system_prompt, query)
        validated_result: LLMSchedulingParseResult | None = None

        if isinstance(raw_llm_result, dict):
            try:
                # Attempt to validate the raw dictionary using the Pydantic model
                validated_result = LLMSchedulingParseResult.model_validate(raw_llm_result)
                # No need to manually re-instantiate nested models if model_validate works correctly
                validated_result.method = "llm"  # Mark method as LLM
                logger.info(
                    "Successfully parsed and validated LLM response.",
                    intent=validated_result.intent,
                )
                return validated_result
            except ValidationError as e:
                logger.warning(
                    f"LLM response failed Pydantic validation: {e}", llm_output=raw_llm_result
                )
                # Fall through to fallback logic if validation fails
            except Exception:
                logger.exception(
                    "Unexpected error during Pydantic validation",
                    llm_output=raw_llm_result,
                )
                # Fall through to fallback
        else:
            logger.warning(
                "LLM did not return a dictionary. Attempting fallback.", result=raw_llm_result
            )
            # Fall through to fallback logic

        # --- Fallback Logic --- #
        logger.info("Proceeding with fallback logic after LLM failure or validation error.")
        fallback_date_info_dict = self._get_fallback_date_result(query)
        fallback_method = fallback_date_info_dict.get("method", "fallback_failure")
        # Create a default LLMSchedulingParseResult for fallback
        fallback_result = LLMSchedulingParseResult(
            intent="unknown",
            # Populate date_info using the fallback dictionary, ensure it's a DateInfo model
            date_info=LLMDateInfo.model_validate(fallback_date_info_dict)
            if fallback_date_info_dict
            else LLMDateInfo(),
            filters=LLMFilters(),  # Default empty filters
            confidence=0.2,  # Hardcoded low confidence for fallback
            method=fallback_method,
        )
        return fallback_result

    def _get_fallback_date_result(self, query: str) -> dict[str, Any]:
        """Fallback date parsing using dateparser when LLM fails or is unavailable."""
        logger.warning("Falling back to dateparser for query", query=query)
        try:
            # Settings to prefer future dates and use current time as reference
            parser_settings = {
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.now(UTC),
                # Consider adding TIMEZONE setting if user timezone is known
                # 'TIMEZONE': 'Your/Timezone' # Example
            }
            parsed_date = dateparser.parse(query, settings=parser_settings)

            if parsed_date:
                # Ensure the result is timezone-aware (UTC)
                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=UTC)
                else:
                    parsed_date = parsed_date.astimezone(UTC)

                date_iso = parsed_date.date().isoformat()
                return {
                    "start_date": date_iso,
                    "end_date": date_iso,  # Assume single day for basic fallback
                    "confidence": 0.6,  # Indicate lower confidence
                    "method": "fallback_dateparser",
                }
            logger.warning("dateparser failed to parse query", query=query)
            # Last resort - return today
            today_iso = datetime.now(UTC).date().isoformat()
            return {
                "start_date": today_iso,
                "end_date": today_iso,
                "confidence": 0.3,
                "method": "fallback_default",
            }
        except Exception:
            logger.exception("Error during dateparser fallback")
            today_iso = datetime.now(UTC).date().isoformat()
            return {
                "start_date": today_iso,
                "end_date": today_iso,
                "confidence": 0.1,
                "method": "fallback_error",
            }
