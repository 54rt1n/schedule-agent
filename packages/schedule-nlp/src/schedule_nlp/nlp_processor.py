"""Parse natural-language requests without inventing executable fallback commands."""

from schedule_core.core.logging_config import get_logger
from schedule_nlp.dao import LLMSchedulingParseResult
from schedule_nlp.llm_strategy import LLMStrategy

logger = get_logger(__name__)


class NLPProcessor:
    def __init__(self, llm_provider, date_utils):
        self.llm_provider = llm_provider
        self.date_utils = date_utils

    async def parse_natural_language_query(self, query: str) -> LLMSchedulingParseResult:
        if self.llm_provider is None:
            return LLMSchedulingParseResult()
        try:
            return await LLMStrategy(self.llm_provider).parse_scheduling_query(query)
        except Exception:
            logger.exception("Unable to interpret scheduling request")
            return LLMSchedulingParseResult()
