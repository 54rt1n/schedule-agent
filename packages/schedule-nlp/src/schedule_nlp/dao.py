"""Typed output envelope shared by NLP interpretation and structured dispatch."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class LLMSchedulingParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = "unknown"
    payload: dict[str, Any] = Field(default_factory=dict)
