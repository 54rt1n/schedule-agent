import uuid
from fastapi.testclient import TestClient
from fastapi import FastAPI # Import FastAPI for app type hint

from schedule_a2a.dto import TaskState, Message, Part, TaskSendParams
from schedule_nlp.dao import LLMSchedulingParseResult
from schedule_nlp.nlp_processor import NLPProcessor # Import base NLPProcessor
from schedule_api.dependencies import get_nlp_processor # Import the dependency function
from ..conftest import build_rpc_request

def test_rpc_tasks_send_text_input_success(client: TestClient, test_app: FastAPI, auth_headers_valid: dict):
    """Clarification preserves the caller task and session identifiers."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    initial_message_text = "Schedule a meeting for tomorrow at 2 PM"
    
    # --- Configure Mock NLP Result --- 
    mock_nlp_result = LLMSchedulingParseResult(
        intent="clarify", payload={"message": "What date and duration?"},
    )

    # --- Create Mock NLPProcessor --- 
    class MockNLPProcessor(NLPProcessor):
        async def parse_natural_language_query(self, query: str) -> LLMSchedulingParseResult:
            # Return the pre-defined mock result, ignore actual query for this test
            return mock_nlp_result

    mock_processor_instance = MockNLPProcessor(llm_provider=None, date_utils=None)
    
    # Store original dependency overrides if any
    original_overrides = test_app.dependency_overrides.copy()
    test_app.dependency_overrides[get_nlp_processor] = lambda: mock_processor_instance
    
    try:
        # --- Prepare and Send Request ---
        send_params = TaskSendParams(
            id=task_id,
            sessionId=session_id,
            message=Message(role="user", parts=[Part(type="text", text=initial_message_text)])
        )
        rpc_send_payload = build_rpc_request("tasks/send", send_params.model_dump())

        response_send = client.post("/api/rpc", json=rpc_send_payload, headers=auth_headers_valid)
        # --- Assertions ---
        assert response_send.status_code == 200
        response_send_data = response_send.json()
        assert "result" in response_send_data
        assert response_send_data.get("error") is None
        
        # Validate the returned task
        created_task_result = response_send_data["result"]
        assert created_task_result["id"] == task_id
        assert created_task_result["sessionId"] == session_id
        
        assert created_task_result["status"]["state"] == TaskState.INPUT_REQUIRED.value
        
    finally:
        # --- Restore Original Dependencies --- 
        test_app.dependency_overrides = original_overrides
