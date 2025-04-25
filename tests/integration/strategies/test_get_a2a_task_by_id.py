import pytest
import uuid
import json
from datetime import UTC, datetime

from sa.db.service import DatabaseService
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy # Needed for setup
from sa.a2a.dto import TaskStatus, TaskState
from sa.db.models.a2a import A2ATask


@pytest.mark.asyncio
async def test_get_a2a_task_by_id_simple(
    db_service: DatabaseService,
    save_a2a_task_strategy: SaveA2ATaskStrategy,
):
    """Test retrieving an existing A2A task by ID."""
    # --- Arrange ---
    # Create a task to retrieve
    task_id = str(uuid.uuid4())
    task_to_create = A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=TaskState.SUBMITTED.value,
        params_json=json.dumps({"get_test": task_id}),
        history_json=json.dumps([TaskStatus(state=TaskState.SUBMITTED).model_dump()])
    )
    await save_a2a_task_strategy.execute(data=task_to_create)

    strategy = GetA2ATaskByIdStrategy(db_service=db_service)

    # --- Act ---
    retrieved_task = await strategy.execute(data=task_id)

    # --- Assert ---
    assert retrieved_task is not None
    assert isinstance(retrieved_task, A2ATask)
    assert retrieved_task.id == task_id
    assert retrieved_task.state == TaskState.SUBMITTED.value

    # Test retrieving non-existent returns None
    non_existent_id = str(uuid.uuid4())
    retrieved_none = await strategy.execute(data=non_existent_id)
    assert retrieved_none is None 