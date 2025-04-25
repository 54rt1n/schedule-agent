import pytest
import uuid
import json
from datetime import UTC, datetime

from sa.db.service import DatabaseService
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy # Needed for verification
from sa.a2a.dto import TaskStatus, TaskState
from sa.db.models.a2a import A2ATask


@pytest.mark.asyncio
async def test_save_a2a_task_simple(
    db_service: DatabaseService,
    get_a2a_task_by_id_strategy: GetA2ATaskByIdStrategy,
):
    """Test saving a new A2A task."""
    # --- Arrange ---
    strategy = SaveA2ATaskStrategy(db_service=db_service)
    task_id = str(uuid.uuid4())
    params = {"simple_save": True, "id": task_id}
    task_to_save = A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=TaskState.SUBMITTED.value,
        params_json=json.dumps(params),
        history_json=json.dumps([TaskStatus(state=TaskState.SUBMITTED).model_dump()])
    )

    # --- Act ---
    saved_task = await strategy.execute(data=task_to_save)

    # --- Assert ---
    assert saved_task is not None
    assert isinstance(saved_task, A2ATask)
    assert saved_task.id == task_id
    assert saved_task.state == TaskState.SUBMITTED.value
    assert saved_task.params_json == json.dumps(params)

    # Verify it was saved in the database
    verified_task = await get_a2a_task_by_id_strategy.execute(data=task_id)
    assert verified_task is not None
    assert verified_task.id == task_id
    assert verified_task.params_json == json.dumps(params) 