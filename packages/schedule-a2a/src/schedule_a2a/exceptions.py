# packages/schedule-a2a/src/schedule_a2a/exceptions.py
# Copyright (C) 2025 Martin Bukowski

"""Custom exceptions for the Agent-to-Agent (A2A) Communication Protocol.

Based on the A2A specification (a2aspec)
"""

from typing import Any

from schedule_a2a.dto import JSONRPCError

# JSON-RPC Error Codes (as defined by JSON-RPC 2.0 spec)
# https://www.jsonrpc.org/specification#error_object
PARSE_ERROR = -32700      # Invalid JSON was received by the server. An error occurred on the server while parsing the JSON text.
INVALID_REQUEST = -32600  # The JSON sent is not a valid Request object.
METHOD_NOT_FOUND = -32601 # The method does not exist / is not available.
INVALID_PARAMS = -32602   # Invalid method parameter(s).
INTERNAL_ERROR = -32603   # Internal JSON-RPC error.
# -32000 to -32099 are reserved for implementation-defined server-errors.
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
INVALID_TASK_STATE = -32003
AGENT_CAPABILITY_ERROR = -32004


# --- A2A Error Classes ---

class BaseA2AError(Exception):
    """Base class for A2A-specific exceptions used to generate JSON-RPC errors."""

    code: int
    message: str
    data: Any | None

    def __init__(self, code: int, message: str, data: Any | None = None):
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        """Return the message associated with the error."""
        return self.message

    def as_jsonrpc_error(self) -> JSONRPCError:
        return JSONRPCError(code=self.code, message=self.message, data=self.data)


class InvalidRequestError(BaseA2AError):  # Code -32600
    DEFAULT_CODE = INVALID_REQUEST
    DEFAULT_MESSAGE = "Invalid Request"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class MethodNotFoundError(BaseA2AError):  # Code -32601
    DEFAULT_CODE = METHOD_NOT_FOUND
    DEFAULT_MESSAGE = "Method not found"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class InvalidParamsError(BaseA2AError):  # Code -32602
    DEFAULT_CODE = INVALID_PARAMS
    DEFAULT_MESSAGE = "Invalid params"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class InternalError(BaseA2AError):  # Code -32603
    DEFAULT_CODE = INTERNAL_ERROR
    DEFAULT_MESSAGE = "Internal error"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class ParseError(BaseA2AError):  # Code -32700
    DEFAULT_CODE = PARSE_ERROR
    DEFAULT_MESSAGE = "Parse error"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


# Example Agent-Specific Error Codes (Reserved range -32000 to -32099)

class TaskNotFoundError(BaseA2AError):  # Example Code -32000
    DEFAULT_CODE = TASK_NOT_FOUND
    DEFAULT_MESSAGE = "Task not found"

    def __init__(self, message: str | None = None, data: Any | None = None):
        # Allow specific message like "Task with ID xyz not found"
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class TaskNotCancelableError(BaseA2AError):  # Example Code -32001
    DEFAULT_CODE = TASK_NOT_CANCELABLE
    DEFAULT_MESSAGE = "Task not cancelable in its current state"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class InvalidTaskStateError(BaseA2AError):  # Example Code -32002
    DEFAULT_CODE = INVALID_TASK_STATE
    DEFAULT_MESSAGE = "Operation not allowed in current task state"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class AgentCapabilityError(BaseA2AError):  # Example Code -32003
    DEFAULT_CODE = AGENT_CAPABILITY_ERROR
    DEFAULT_MESSAGE = "Agent does not support the requested capability"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)
