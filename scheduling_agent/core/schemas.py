from pydantic import BaseModel, Field

# --- Schemas for LLM Output Validation ---


class LLMDateInfo(BaseModel):
    """Pydantic model for validating date/time info from LLM."""

    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None  # YYYY-MM-DD
    specific_time: str | None = None  # HH:MM:SS
    end_time: str | None = None  # HH:MM:SS
    time_description: str | None = None  # e.g., "tomorrow afternoon"
    is_recurring: bool | None = False
    recurrence_rule: str | None = None  # iCalendar RRULE string
    timeZone: str | None = None  # IANA timezone name # noqa: N815
    method: str | None = None  # Added by provider (llm, fallback_...)


class LLMFilters(BaseModel):
    """Pydantic model for validating filter info from LLM."""

    title: str | None = None
    location: str | None = None
    # Use default_factory for mutable defaults like lists
    participants: list[str] | None = Field(default_factory=list)
    format: str | None = None  # Renamed back to format


class LLMSchedulingParseResult(BaseModel):
    """Pydantic model for the overall LLM scheduling query parsing result."""

    intent: str = "unknown"
    # Use default_factory for nested models that should default to an empty instance
    date_info: LLMDateInfo | None = Field(default_factory=LLMDateInfo)
    filters: LLMFilters | None = Field(default_factory=LLMFilters)
    confidence: float = 0.0
    method: str | None = None  # Added by provider (llm, fallback_...)
    original_query: str | None = None  # Added by NLPProcessor later

