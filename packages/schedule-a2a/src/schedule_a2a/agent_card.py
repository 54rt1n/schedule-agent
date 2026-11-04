# packages/schedule-a2a/src/schedule_a2a/agent_card.py
# Copyright (C) 2025 Martin Bukowski

"""A2A Agent Card providing agent metadata and capabilities."""

from schedule_core.core.settings import Settings

from schedule_a2a.dto import AgentCapabilities, AgentCard, AgentSkill

# --- Agent Card Instance ---

def get_agent_card(settings: Settings) -> AgentCard:
    """Constructs and returns the configured AgentCard instance."""
    agent_card_instance = AgentCard(
        name="Scheduling Agent",
        description="Create, query, update, delete and export timed events, and manage contact details. Accepts structured requests and optional model-interpreted text.",
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
                    '{"intent":"create_event","payload":{"title":"API Event","start_time":"2030-01-07T10:00:00Z","end_time":"2030-01-07T11:00:00Z","time_zone":"UTC"}}',
                ],
            ),
            AgentSkill(
                id="query_events",
                name="Query Schedule",
                description="Read timed events within a start_time/end_time range. This A2A query does not include tasks or non-timed items.",
                examples=[
                    "What's on my calendar this week?",
                    "Show my events between June 1st and June 15th",
                    '{"intent":"query_events","payload":{"start_time":"2030-01-01T00:00:00Z","end_time":"2030-02-01T00:00:00Z"}}',
                ],
            ),
            AgentSkill(
                id="export_calendar",
                name="Export Calendar",
                description="Export calendar data in standard formats (iCalendar .ics, Org mode).",
                examples=[
                    "Export my calendar for June in iCalendar format",
                    "Create an org-mode export of my schedule for next week",
                    '{"intent":"export_calendar","payload":{"format":"ics","date_info":{"start_date":"2030-01-01","end_date":"2030-01-31"}}}',
                ],
            ),
            AgentSkill(
                id="update_event",
                name="Update Event",
                description="Update a timed event by ID; original_start_time selects one recurring occurrence.",
                examples=[
                    "Reschedule tomorrow's meeting to Friday",
                    "Update the location for the project review",
                ],
            ),
            AgentSkill(
                id="delete_event",
                name="Delete Event",
                description="Delete a timed event by ID. Deleting a recurring event removes the whole series.",
                examples=["Cancel the meeting on Wednesday", "Delete the recurring team sync"],
            ),
            AgentSkill(
                id="create_contact",
                name="Create Contact",
                description="Create a new contact with name, email, phone, and notes.",
                examples=[
                    "Add John Doe as a contact, email john.doe@example.com",
                    '{"intent":"create_contact","payload":{"name":"Jane Smith","email":"jane.s@example.net","phone":"555-1234"}}'
                ]
            ),
            AgentSkill(
                id="query_contacts",
                name="Query Contacts",
                description="Find contacts by name substring, email substring, or exact email.",
                examples=[
                    "Find contact John Doe",
                    "Show me the contact info for jane.s@example.net",
                    '{"intent":"query_contacts","payload":{"name_contains":"Smith"}}',
                    '{"intent":"query_contacts","payload":{"email":"john.doe@example.com"}}'
                ]
            ),
            AgentSkill(
                id="update_contact",
                name="Update Contact",
                description="Modify an existing contact's details (name, email, phone, notes) using their ID.",
                examples=[
                    "Update John Doe's phone number to 555-9876",
                    '{"intent":"update_contact","payload":{"id":"b42d2b89-ed4c-4ecf-a1f9-ed5c8346b2cd","phone":"555-9876"}}'
                ]
            ),
            AgentSkill(
                id="delete_contact",
                name="Delete Contact",
                description="Delete an existing contact using their ID.",
                examples=[
                    "Remove John Doe from my contacts",
                    '{"intent":"delete_contact","payload":{"id":"b42d2b89-ed4c-4ecf-a1f9-ed5c8346b2cd"}}'
                ]
            ),
        ],
    )
    return agent_card_instance
