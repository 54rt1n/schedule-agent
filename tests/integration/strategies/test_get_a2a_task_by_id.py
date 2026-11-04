import pytest
import uuid
import json
from datetime import UTC, datetime

from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from schedule_core.db.strategies.save_a2a_task import SaveA2ATaskStrategy # Needed for setup
from schedule_a2a.dto import Message, Part, TaskStatus, TaskState # Added Message, Part
from schedule_core.db.models.a2a import A2ATask
from schedule_core.db.dao import GetA2ATaskById # Added DAO import


@pytest.mark.asyncio
async def test_get_a2a_task_by_id_simple(
    db_service: DatabaseService,
    save_a2a_task_strategy: SaveA2ATaskStrategy,
):
    """Test retrieving an existing A2A task by ID."""
    # --- Arrange ---
    # Create a task to retrieve
    task_id = str(uuid.uuid4())
    initial_status = TaskStatus(state=TaskState.SUBMITTED, timestamp=datetime.now(UTC))
    initial_metadata = {"test_type": "get"}
    task_to_create = A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=initial_status.state.value,
        status_timestamp=initial_status.timestamp,
        metadata_json=json.dumps(initial_metadata),
        history_json=json.dumps([initial_status.model_dump(mode='json')])
    )
    await save_a2a_task_strategy.execute(data=task_to_create)

    strategy = GetA2ATaskByIdStrategy(db_service=db_service)

    # --- Act ---
    retrieved_task = await strategy.execute(data=GetA2ATaskById(id=task_id))

    # --- Assert ---
    assert retrieved_task is not None
    assert isinstance(retrieved_task, A2ATask)
    assert retrieved_task.id == task_id
    assert retrieved_task.state == initial_status.state.value
    assert retrieved_task.status_timestamp == initial_status.timestamp.replace(tzinfo=None)
    assert retrieved_task.metadata_json == json.dumps(initial_metadata)

    # Test retrieving non-existent returns None
    non_existent_id = str(uuid.uuid4())
    retrieved_none = await strategy.execute(data=GetA2ATaskById(id=non_existent_id))
    assert retrieved_none is None 