from pydantic import BaseModel, Field, ValidationError
from typing import Optional, List, Dict, Any

# --- Schemas for LLM Output Validation ---

class LLMDateInfo(BaseModel):
    """Pydantic model for validating date/time info from LLM."""
    start_date: Optional[str] = None # YYYY-MM-DD
    end_date: Optional[str] = None   # YYYY-MM-DD
    specific_time: Optional[str] = None # HH:MM:SS
    end_time: Optional[str] = None      # HH:MM:SS
    time_description: Optional[str] = None # e.g., "tomorrow afternoon"
    is_recurring: Optional[bool] = False
    recurrence_rule: Optional[str] = None # iCalendar RRULE string
    timeZone: Optional[str] = None # IANA timezone name
    method: Optional[str] = None # Added by provider (llm, fallback_...)

class LLMFilters(BaseModel):
    """Pydantic model for validating filter info from LLM."""
    title: Optional[str] = None
    location: Optional[str] = None
    # Use default_factory for mutable defaults like lists
    participants: Optional[List[str]] = Field(default_factory=list)

class LLMSchedulingParseResult(BaseModel):
    """Pydantic model for the overall LLM scheduling query parsing result."""
    intent: str = "unknown"
    # Use default_factory for nested models that should default to an empty instance
    date_info: Optional[LLMDateInfo] = Field(default_factory=LLMDateInfo)
    filters: Optional[LLMFilters] = Field(default_factory=LLMFilters)
    confidence: float = 0.0
    method: Optional[str] = None # Added by provider (llm, fallback_...)
    original_query: Optional[str] = None # Added by NLPProcessor later

# --- Schemas for Database Models (Example - can be expanded) ---
# These might live in db/models.py or db/schemas.py eventually

class EventBase(BaseModel):
    title: str
    start_time: str # ISO 8601 UTC
    end_time: str   # ISO 8601 UTC
    time_zone: str  # IANA Timezone
    location: Optional[str] = None
    notes: Optional[str] = None
    rrule: Optional[str] = None

class EventCreate(EventBase):
    participants: Optional[List[str]] = None

class EventRead(EventBase):
    id: str
    participants: List[str] = [] # Assume participants are loaded
    created_at: str
    updated_at: str

    class Config:
        orm_mode = True # For Pydantic v1
        # from_attributes = True # For Pydantic v2+ 