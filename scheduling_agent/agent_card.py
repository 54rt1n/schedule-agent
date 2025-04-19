from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict, Any
import os # Import os for environment variables


class AgentSkill(BaseModel):
    """Represents a skill the agent can perform."""
    id: str = Field(..., description="Unique identifier for the skill.")
    name: str = Field(..., description="Human-readable name of the skill.")
    description: str = Field(..., description="Description of what the skill does.")
    parameters: Optional[Dict[str, Any]] = Field(None, description="JSON Schema describing parameters the skill accepts.")
    examples: List[str] = Field(default=[], description="Example invocations or use cases.")
    # TODO: Add parameters definition if needed by A2A spec


class AgentCapabilities(BaseModel):
    """Defines the capabilities of the agent according to A2A spec."""
    streaming: bool = Field(default=False, description="Supports streaming responses.")
    pushNotifications: bool = Field(default=False, description="Supports push notifications.")
    stateTransitionHistory: bool = Field(default=False, description="Provides state history.")


class AgentCard(BaseModel):
    """A2A Agent Card providing agent metadata and capabilities."""
    name: str = Field(..., description="Agent's name.")
    description: str = Field(..., description="Brief description of the agent.")
    url: HttpUrl = Field(..., description="Base URL for the agent's API endpoint.")
    version: str = Field(..., description="Agent version.")
    capabilities: AgentCapabilities = Field(..., description="Agent's A2A capabilities.")
    defaultInputModes: List[str] = Field(
        default=["text/plain", "application/json"],
        description="Default supported input MIME types."
    )
    defaultOutputModes: List[str] = Field(
        default=["text/plain", "application/json"],
        description="Default supported output MIME types."
    )
    skills: List[AgentSkill] = Field(default=[], description="List of skills the agent offers.")

# --- Agent Card Instance ---

# Define the specific AgentCard construction within the getter function
def get_agent_card() -> AgentCard:
    """Constructs and returns the configured AgentCard instance.

    Reads configuration from environment variables.
    """
    # Load configuration from environment variables with defaults
    agent_host = os.getenv("AGENT_HOST", "localhost")
    agent_port = int(os.getenv("AGENT_PORT", "8000"))
    agent_version = os.getenv("AGENT_VERSION", "0.1.0") # Or read from pyproject.toml?

    # Construct the AgentCard dynamically
    agent_card_instance = AgentCard(
        name="Scheduling Agent",
        description="Calendar management service for creating, querying, and exporting events using LLMs and conflict resolution.",
        url=f"http://{agent_host}:{agent_port}/", # Use loaded config
        version=agent_version, # Use loaded config
        capabilities=AgentCapabilities(
            streaming=False, # Not implemented yet
            pushNotifications=False, # Not implemented yet
            stateTransitionHistory=False
        ),
        defaultInputModes=["text/plain", "application/json"],
        defaultOutputModes=[
            "text/plain",
            "application/json",
            "text/calendar", # For iCal export
            # Add other output types like org-mode if applicable
        ],
        skills=[
            AgentSkill(
                id="create_event",
                name="Create Calendar Event",
                description="Create new calendar events with optional recurrence, performing conflict checks.",
                examples=[
                    "Schedule a team meeting on Wednesday at 2pm",
                    "Create a recurring event every Monday at 9am with bob@example.com",
                    '{"title": "API Event", "startTime": "2024-09-01T10:00:00Z", "endTime": "2024-09-01T11:00:00Z"}'
                ]
            ),
            AgentSkill(
                id="query_schedule",
                name="Query Schedule",
                description="Find events using date ranges or natural language queries.",
                examples=[
                    "What's on my calendar this week?",
                    "Show me all meetings with Marketing team between June 1st and June 15th",
                    '{"startDate": "2024-08-01", "endDate": "2024-08-07"}'
                ]
            ),
            AgentSkill(
                id="export_calendar",
                name="Export Calendar",
                description="Export calendar data in standard formats (iCalendar .ics, Org mode).",
                examples=[
                    "Export my calendar for June in iCalendar format",
                    "Create an org-mode export of my schedule for next week",
                    '{"format": "ics", "startDate": "2024-10-01", "endDate": "2024-10-31"}'
                ]
            ),
            AgentSkill(
                id="update_event",
                name="Update Event",
                description="Modify existing calendar events (Not Implemented Yet).",
                examples=[
                    "Reschedule tomorrow's meeting to Friday",
                    "Update the location for the project review"
                ]
            ),
            AgentSkill(
                id="delete_event",
                name="Delete Event",
                description="Delete existing calendar events (Not Implemented Yet).",
                examples=[
                    "Cancel the meeting on Wednesday",
                    "Delete the recurring team sync"
                ]
            )
        ]
    )
    return agent_card_instance 