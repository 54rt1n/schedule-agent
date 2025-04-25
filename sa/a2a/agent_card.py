# sa/a2a/agent_card.py
"""A2A Agent Card providing agent metadata and capabilities."""

from sa.core.settings import Settings
from sa.a2a.dto import AgentCard, AgentCapabilities, AgentSkill


# --- Agent Card Instance ---

def get_agent_card(settings: Settings) -> AgentCard:
    """Constructs and returns the configured AgentCard instance."""
    agent_card_instance = AgentCard(
        name="Scheduling Agent",
        description="Calendar management service for creating, querying, and exporting events using LLMs and conflict resolution.",
        url=f"http://{settings.server_host}:{settings.server_port}/",
        version=settings.agent_version,
        capabilities=AgentCapabilities(
            streaming=False,
            pushNotifications=False,
            stateTransitionHistory=False,
        ),
        defaultInputModes=["text/plain", "application/json"],
        defaultOutputModes=[
            "text/plain",
            "application/json",
            "text/calendar",
        ],
        skills=[
            AgentSkill(
                id="create_event",
                name="Create Calendar Event",
                description="Create new calendar events with optional recurrence, performing conflict checks.",
                examples=[
                    "Schedule a team meeting on Wednesday at 2pm",
                    "Create a recurring event every Monday at 9am with bob@example.com",
                    '{"title": "API Event", "startTime": "2024-09-01T10:00:00Z", "endTime": "2024-09-01T11:00:00Z"}',
                ],
            ),
            AgentSkill(
                id="query_schedule",
                name="Query Schedule",
                description="Find events using date ranges or natural language queries.",
                examples=[
                    "What's on my calendar this week?",
                    "Show me all meetings with Marketing team between June 1st and June 15th",
                    '{"startDate": "2024-08-01", "endDate": "2024-08-07"}',
                ],
            ),
            AgentSkill(
                id="export_calendar",
                name="Export Calendar",
                description="Export calendar data in standard formats (iCalendar .ics, Org mode).",
                examples=[
                    "Export my calendar for June in iCalendar format",
                    "Create an org-mode export of my schedule for next week",
                    '{"format": "ics", "startDate": "2024-10-01", "endDate": "2024-10-31"}',
                ],
            ),
            AgentSkill(
                id="update_event",
                name="Update Event",
                description="Modify existing calendar events (Not Implemented Yet).",
                examples=[
                    "Reschedule tomorrow's meeting to Friday",
                    "Update the location for the project review",
                ],
            ),
            AgentSkill(
                id="delete_event",
                name="Delete Event",
                description="Delete existing calendar events (Not Implemented Yet).",
                examples=["Cancel the meeting on Wednesday", "Delete the recurring team sync"],
            ),
        ],
    )
    return agent_card_instance
