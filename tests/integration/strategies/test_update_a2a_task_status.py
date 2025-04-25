import pytest
import uuid
import json
from datetime import UTC, datetime

from sa.db.service import DatabaseService
from sa.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy, UpdateStatusInput
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy # Needed for setup
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy # Needed for verification
from sa.a2a.dto import TaskStatus, TaskState
from sa.db.models.a2a import A2ATask


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
    initial_task = A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=TaskState.SUBMITTED.value,
        params_json=json.dumps({"action": "test"}),
        history_json=json.dumps([TaskStatus(state=TaskState.SUBMITTED).model_dump()])
    )
    await save_a2a_task_strategy.execute(data=initial_task)

    strategy = UpdateA2ATaskStatusStrategy(db_service=db_service)
    new_status = TaskStatus(state=TaskState.WORKING, message=None)
    strategy_input: UpdateStatusInput = (task_id, new_status)

    # --- Act ---
    await strategy.execute(data=strategy_input)

    # --- Assert ---
    # Verify the task state was updated in the database
    updated_task = await get_a2a_task_by_id_strategy.execute(data=task_id)
    assert updated_task is not None
    assert updated_task.state == TaskState.WORKING.value
    # Check history update (optional, but good practice)
    history = json.loads(updated_task.history_json)
    assert len(history) == 2 # Initial + Update
    assert history[0]["state"] == TaskState.SUBMITTED.value
    assert history[1]["state"] == TaskState.WORKING.value