"""Processes natural language queries related to scheduling."""

import asyncio
from typing import Dict, Any, Optional

from scheduling_agent.core.llm_provider import LLMProvider
from scheduling_agent.core.date_utils import DateUtils
from .schemas import LLMSchedulingParseResult, LLMDateInfo, LLMFilters
from scheduling_agent.logging_config import get_logger

logger = get_logger(__name__)

class NLPProcessor:
    """Parses natural language scheduling queries using LLM."""

    def __init__(self, llm_provider: LLMProvider, date_utils: DateUtils):
        """Initialize the NLP processor.

        Args:
            llm_provider: An instance of LLMProvider.
            date_utils: An instance of DateUtils.
        """
        self.llm_provider = llm_provider
        # DateUtils might still be needed for validation or formatting later
        self.date_utils = date_utils
        logger.info("NLPProcessor initialized.")

    async def parse_natural_language_query(self, query: str) -> LLMSchedulingParseResult:
        """
        Processes a natural language query using an LLM provider (if available).

        Args:
            query: The natural language query string.

        Returns:
            An LLMSchedulingParseResult Pydantic model containing the structured
            parsing results.
        """
        llm_result: LLMSchedulingParseResult | None = None
        if self.llm_provider:
            logger.debug(f"Attempting LLM parse for query: '{query}'")
            try:
                llm_result = await self.llm_provider.parse_scheduling_query(query)
            except Exception as e:
                logger.error(
                    f"LLM provider raised an exception: {e}", exc_info=True
                )
                # Fall through to llm_result is None logic

        # Handle LLM Failure or Absence
        if llm_result is None:
            logger.warning("LLM Provider did not return a result, returning failure structure.")
            # Return a default failure Pydantic model
            failure_model = LLMSchedulingParseResult(
                intent="unknown",
                confidence=0.0,
                method="failure" # Indicates complete LLM failure or absence
                # date_info and filters will use their default_factory
            )
            logger.info(
                "NLP parsing complete (failure)",
                intent=failure_model.intent,
                method=failure_model.method
            )
            # Return the Pydantic model directly
            return failure_model

        # Handle Successful LLM Response (including provider's internal fallbacks)
        logger.info(
            "NLP parsing complete", intent=llm_result.intent, method=llm_result.method
        )

        # Return the Pydantic model directly, no conversion to dict
        return llm_result

    # --- Removed _extract_filters and _determine_query_intent methods --- #

# End of file 