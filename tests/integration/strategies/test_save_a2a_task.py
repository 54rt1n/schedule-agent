import pytest
import uuid
import json
from datetime import UTC, datetime

from sa.db.service import DatabaseService
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from sa.a2a.dto import Message, Part, TaskStatus, TaskState
from sa.db.models.a2a import A2ATask
from sa.db.dao import GetA2ATaskById


@pytest.mark.asyncio
async def test_save_a2a_task_simple(
    db_service: DatabaseService,
    get_a2a_task_by_id_strategy: GetA2ATaskByIdStrategy,
):
    """Test saving a new A2A task."""
    # --- Arrange ---
    strategy = SaveA2ATaskStrategy(db_service=db_service)
    task_id = str(uuid.uuid4())
    initial_status = TaskStatus(state=TaskState.SUBMITTED, timestamp=datetime.now(UTC))
    initial_metadata = {"origin": "test"}
    task_to_save = A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=initial_status.state.value,
        status_timestamp=initial_status.timestamp,
        metadata_json=json.dumps(initial_metadata),
        history_json=json.dumps([initial_status.model_dump(mode='json')])
    )

    # --- Act ---
    saved_task = await strategy.execute(data=task_to_save)

    # --- Assert ---
    assert saved_task is not None
    assert isinstance(saved_task, A2ATask)
    assert saved_task.id == task_id
    assert saved_task.state == initial_status.state.value
    assert saved_task.status_timestamp == initial_status.timestamp.replace(tzinfo=None)
    assert saved_task.metadata_json == json.dumps(initial_metadata)

    # Verify it was saved in the database
    verified_task = await get_a2a_task_by_id_strategy.execute(data=GetA2ATaskById(id=task_id))
    assert verified_task is not None
    assert verified_task.id == task_id
    assert verified_task.status_timestamp == initial_status.timestamp.replace(tzinfo=None)
    assert verified_task.metadata_json == json.dumps(initial_metadata) 