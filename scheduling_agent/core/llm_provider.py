"""Provides an interface to interact with Language Models (LLMs)."""

import os
import json
from typing import Any, Dict, List, Union, Optional
from datetime import datetime, timedelta, timezone
from pydantic import ValidationError

# Attempt to import openai, but don't make it a hard requirement
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    openai = None # type: ignore
    OPENAI_AVAILABLE = False

# Attempt to import dateparser for fallback
try:
    import dateparser
    DATEPARSER_AVAILABLE = True
except ImportError:
    dateparser = None # type: ignore
    DATEPARSER_AVAILABLE = False

from scheduling_agent.logging_config import get_logger
from scheduling_agent.core.schemas import ( # Import the Pydantic models
    LLMSchedulingParseResult,
    LLMDateInfo,
    LLMFilters,
)

logger = get_logger(__name__)


class LLMProvider:
    """
    Generic LLM provider that works with any OpenAI-compatible API.
    Works with local models (Ollama, LM Studio, etc.) and cloud services.
    Includes fallback logic for date parsing using dateparser.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Initialize the LLM provider.

        Args:
            api_key: API key for the LLM service (defaults to env LLM_API_KEY).
            base_url: Base URL for API requests (defaults to env LLM_BASE_URL or OpenAI).
            model: Model name to use (defaults to env LLM_MODEL or gpt-3.5-turbo).
        """
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        self.client: Optional[openai.AsyncClient] = None

        if OPENAI_AVAILABLE:
            if not self.api_key and "openai.com" in self.base_url:
                 logger.warning("OpenAI API key not provided via init or LLM_API_KEY env var.")
            
            try:
                 self.client = openai.AsyncClient(
                    api_key=self.api_key, base_url=self.base_url
                )
                 logger.info(f"OpenAI client initialized for model {self.model} at {self.base_url}")
            except Exception as e:
                 logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
                 self.client = None # Ensure client is None if init fails
        else:
            logger.warning("openai package not installed. LLM functionality will be disabled. Run `pip install openai`")

        if not DATEPARSER_AVAILABLE:
             logger.warning("dateparser package not installed. Fallback date parsing will be unavailable. Run `pip install dateparser`")


    async def parse_scheduling_query(self, query: str) -> LLMSchedulingParseResult:
        """Parse a full natural language query using LLM and Pydantic validation."""
        logger.debug("Parsing full scheduling query", query=query)
        system_prompt = """
        You are an intelligent scheduling assistant. Your task is to parse a user's natural language query related to calendar events and extract structured information.
        The current date is {current_date}.

        Analyze the query: "{user_query}"

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

        Extract **filters** mentioned in the query:
        - title: The title or subject of the event (string).
        - location: The location of the event (string).
        - participants: A list of participant names or emails (list[string]).

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
            "start_date": "YYYY-MM-DD", // Tomorrow's date
            "end_date": "YYYY-MM-DD", // Tomorrow's date
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
        """.format(
            current_date=datetime.now(timezone.utc).date().isoformat(),
            user_query=query
        )

        # Attempt to get raw JSON dict from LLM
        raw_llm_result = await self._generate_json(system_prompt, query)
        validated_result: Optional[LLMSchedulingParseResult] = None

        if isinstance(raw_llm_result, dict):
            try:
                # Attempt to validate the raw dictionary using the Pydantic model
                validated_result = LLMSchedulingParseResult(**raw_llm_result)
                # Ensure nested models are instantiated if they came back as dicts
                if isinstance(validated_result.date_info, dict):
                     validated_result.date_info = LLMDateInfo(**validated_result.date_info)
                if isinstance(validated_result.filters, dict):
                     validated_result.filters = LLMFilters(**validated_result.filters)
                
                validated_result.method = 'llm' # Mark method as LLM
                logger.info("Successfully parsed and validated LLM response.", intent=validated_result.intent)
                return validated_result
            except ValidationError as e:
                logger.warning(f"LLM response failed Pydantic validation: {e}", llm_output=raw_llm_result)
                # Fall through to fallback logic if validation fails
            except Exception as e:
                 logger.error(f"Unexpected error during Pydantic validation: {e}", llm_output=raw_llm_result, exc_info=True)
                 # Fall through to fallback
        else:
            logger.warning("LLM did not return a dictionary. Attempting fallback.", result=raw_llm_result)
            # Fall through to fallback logic

        # --- Fallback Logic --- #
        logger.info("Proceeding with fallback logic after LLM failure or validation error.")
        fallback_date_info_dict = self._get_fallback_date_result(query)
        fallback_method = fallback_date_info_dict.get('method', 'fallback_failure')
        # Create a default LLMSchedulingParseResult for fallback
        # Populate date_info using the fallback dictionary
        fallback_result = LLMSchedulingParseResult(
            intent="unknown",
            date_info=LLMDateInfo(**fallback_date_info_dict) if fallback_date_info_dict else LLMDateInfo(),
            filters=LLMFilters(), # Default empty filters
            confidence=0.2, # Hardcoded low confidence for fallback
            method=fallback_method
        )
        return fallback_result

    async def _generate_json(
        self, system_prompt: str, user_message: str
    ) -> Optional[Union[Dict[str, Any], List[Any]]]:
        """Internal helper to call the LLM and parse its JSON response.
        Returns None if any error occurs.
        """
        logger.debug("Generating JSON from LLM", model=self.model)
        if not self.client:
            logger.error("LLM client not available for _generate_json call.")
            # Depending on the caller, they might handle fallback
            # Return empty dict as a signal of failure here.
            return None # Signal failure

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0, # For deterministic JSON generation
                response_format={"type": "json_object"} # Request JSON output
            )

            # Add checks for None before accessing attributes
            if not response.choices or not response.choices[0].message:
                logger.warning("LLM response structure missing expected parts (choices or message).")
                return None

            content = response.choices[0].message.content
            if content is None: # Explicit check for None content
                logger.warning("Received None content from LLM.")
                return None

            try:
                parsed_json = json.loads(content)
                logger.debug("Successfully parsed JSON from LLM.")
                if isinstance(parsed_json, dict) and 'method' not in parsed_json:
                    parsed_json['method'] = 'llm'
                return parsed_json
            except json.JSONDecodeError as json_err:
                logger.warning(f"Invalid JSON received from LLM: {json_err}", raw_content=content)
                return None # Failed to parse

        except openai.APIConnectionError as e:
            logger.error(f"LLM API connection error: {e}")
            return None
        except openai.RateLimitError as e:
            logger.error(f"LLM API rate limit exceeded: {e}")
            # Consider adding retry logic here with backoff
            return None
        except openai.APIStatusError as e:
            logger.error(f"LLM API status error: {e.status_code}", response=e.response)
            return None
        except Exception as e:
            logger.error(f"Unexpected error during LLM API call: {e}", exc_info=True)
            return None

    def _get_fallback_date_result(self, query: str) -> Dict[str, Any]:
        """Fallback date parsing using dateparser when LLM fails or is unavailable."""
        logger.warning("Falling back to dateparser for query", query=query)
        if not DATEPARSER_AVAILABLE:
            logger.error("dateparser not available for fallback.")
            return {}

        try:
            # Settings to prefer future dates and use current time as reference
            settings = {
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': datetime.now(timezone.utc)
                # Consider adding TIMEZONE setting if user timezone is known
            }
            parsed_date = dateparser.parse(query, settings=settings)

            if parsed_date:
                # Ensure the result is timezone-aware (UTC)
                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                else:
                    parsed_date = parsed_date.astimezone(timezone.utc)

                # Simple fallback: return start/end as the same parsed date
                # More sophisticated logic could try to detect ranges based on keywords
                date_iso = parsed_date.date().isoformat()
                return {
                    "start_date": date_iso,
                    "end_date": date_iso, # Assume single day for basic fallback
                    "confidence": 0.6, # Indicate lower confidence
                    "method": "fallback_dateparser"
                }
            else:
                logger.warning("dateparser failed to parse query", query=query)
                # Last resort - return today
                today_iso = datetime.now(timezone.utc).date().isoformat()
                return {
                    "start_date": today_iso,
                    "end_date": today_iso,
                    "confidence": 0.3,
                    "method": "fallback_default"
                }
        except Exception as e:
            logger.error(f"Error during dateparser fallback: {e}", exc_info=True)
            today_iso = datetime.now(timezone.utc).date().isoformat()
            return {
                "start_date": today_iso,
                "end_date": today_iso,
                "confidence": 0.1,
                "method": "fallback_error"
            } 