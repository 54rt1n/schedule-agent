# sa/api/routes/well_known.py
"""Handles the /.well-known/agent.json endpoint."""

from fastapi import APIRouter, Depends

from sa.a2a.agent_card import AgentCard, get_agent_card
from sa.api.dependencies import get_settings
from sa.core.settings import Settings

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
