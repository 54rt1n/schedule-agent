# packages/schedule-api/src/schedule_api/routes/well_known.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the /.well-known/agent.json endpoint."""

from fastapi import APIRouter, Depends

from schedule_a2a.agent_card import AgentCard, get_agent_card
from schedule_api.dependencies import get_settings
from schedule_core.core.settings import Settings

router = APIRouter()

@router.get(
    "/agent.json",
    response_model=AgentCard,
    summary="Get Agent Capabilities",
    tags=["Well-Known"],
)
async def get_agent_card_endpoint(
    settings: Settings = Depends(get_settings)
) -> AgentCard:
    """Returns the agent's capabilities card."""
    return get_agent_card(settings=settings)
