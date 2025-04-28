import pytest
import uuid
import json
from datetime import UTC, datetime, timedelta

from sa.db.service import DatabaseService
from sa.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from sa.a2a.dto import Message, Part, TaskStatus, TaskState
from sa.db.models.a2a import A2ATask
from sa.db.dao import UpdateA2ATaskStatus, GetA2ATaskById


@pytest.mark.asyncio
async def test_update_a2a_task_status_simple(
    db_service: DatabaseService,
    save_a2a_task_strategy: SaveA2ATaskStrategy,
    get_a2a_task_by_id_strategy: GetA2ATaskByIdStrategy,
):
    """Test updating the status of an A2A task."""
    # --- Arrange ---
    # Create an initial task to update
    task_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    initial_status = TaskStatus(
        state=TaskState.SUBMITTED,
        timestamp=now - timedelta(seconds=10) # Give it a past timestamp
    )
    initial_metadata = {"source": "initial"}
    initial_task = A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=initial_status.state.value,
        status_timestamp=initial_status.timestamp,
        metadata_json=json.dumps(initial_metadata),
        history_json=json.dumps([initial_status.model_dump(mode='json')])
    )
    await save_a2a_task_strategy.execute(data=initial_task)

    strategy = UpdateA2ATaskStatusStrategy(db_service=db_service)
    new_status_message = Message(role="agent", parts=[Part(type="text", text="Processing...")])
    new_status = TaskStatus(
        state=TaskState.WORKING,
        message=new_status_message,
        timestamp=now
    )
    strategy_input = UpdateA2ATaskStatus(
        id=task_id,
        new_state=new_status.state.value,
        new_status_timestamp=new_status.timestamp,
        new_status_message_json=new_status.message.model_dump_json() if new_status.message else None,
    )

    # --- Act ---
    await strategy.execute(data=strategy_input)

    # --- Assert ---
    # Verify the task state was updated in the database
    updated_task = await get_a2a_task_by_id_strategy.execute(data=GetA2ATaskById(id=task_id))
    assert updated_task is not None
    assert updated_task.state == new_status.state.value
    assert updated_task.status_timestamp == new_status.timestamp.replace(tzinfo=None)
    assert updated_task.metadata_json == json.dumps(initial_metadata)

    # Check history update - Strategy should NOT modify history unless explicitly told
    history = json.loads(updated_task.history_json)
    assert len(history) == 1 # Verify history length remains 1
    # Check the single status entry is the initial one
    assert history[0]["state"] == initial_status.state.value
    # Compare aware datetimes (fromisoformat preserves timezone from mode='json')
    assert datetime.fromisoformat(history[0]["timestamp"]) == initial_status.timestamp
    # Ensure checks for the second status (which shouldn't exist) remain commented out
    # assert history[1]["state"] == new_status.state.value
    # assert datetime.fromisoformat(history[1]["timestamp"]) == new_status.timestamp
    # assert history[1]["message"]["role"] == new_status_message.role