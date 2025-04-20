import uuid

import httpx
import pytest

# Import necessary types
from scheduling_agent.common.types import (
    JSONRPCRequest,
    MethodNotFoundError,
)

# Import fixtures from conftest
from .conftest import RPC_URL


@pytest.mark.asyncio
async def test_rpc_unknown_method(async_client: httpx.AsyncClient):
    """Test calling an unknown RPC method."""
    request_id = str(uuid.uuid4())
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="foo/bar",  # An unknown method
        params={"arg1": "value1"},
        id=request_id,
    )

    # Action: Send the request
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200  # JSON-RPC uses 200 OK even for errors
    response_data = response.json()

    # Validate JSON-RPC response structure for error
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    assert response_data["error"] is not None

    # Validate the specific A2A error (MethodNotFoundError)
    error_data = response_data["error"]
    assert error_data.get("code") == MethodNotFoundError.DEFAULT_CODE
    assert error_data.get("message") == MethodNotFoundError.DEFAULT_MESSAGE


# TODO: Add tests for requests without an ID (Notifications) if required
# TODO: Add tests for invalid JSON-RPC structure (e.g., missing jsonrpc field)
