from fastapi import APIRouter, Depends

from scheduling_agent.agent_card import AgentCard, get_agent_card

router = APIRouter()

# TODO: Implement GET /.well-known/agent.json endpoint (Phase 2, Task 1) - Now Implemented


@router.get(
    "/agent.json",
    response_model=AgentCard,
    summary="Get Agent Capabilities",
    tags=["Well-Known"],
)
async def get_agent_card_endpoint() -> AgentCard:
    """Returns the agent's capabilities card."""
    return get_agent_card() 