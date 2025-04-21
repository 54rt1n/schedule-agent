"""Pydantic models for the Agent-to-Agent (A1A) Communication Protocol.

Based on the A1A specification: https://github.com/AutonomousResearchGroup/A2A-Spec/blob/main/SPECIFICATION.md
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, Field, model_validator
from scheduling_agent.db.schemas import EventUpdate

# --- Core JSON-RPC Models ---


class JSONRPCError(BaseModel):
    """Represents a JSON-RPC Error object."""

    code: int = Field(..., description="A Number that indicates the error type that occurred.")
    message: str = Field(..., description="A String providing a short description of the error.")
    data: Any | None = Field(
        None,
        description="A Primitive or Structured value that contains additional information about the error. May be omitted.",
    )


class JSONRPCRequest(BaseModel):
    """Represents a JSON-RPC Request object."""

    jsonrpc: Literal["2.0"] = Field(
        "2.0", description="A String specifying the version of the JSON-RPC protocol."
    )
    method: str = Field(
        ..., description="A String containing the name of the method to be invoked."
    )
    params: dict[str, Any] | list[Any] | BaseModel | None = Field(
        None,
        description="A Structured value that holds the parameter values to be used during the invocation of the method. May be omitted.",
    )
    id: str | int | None = Field(
        None,
        description="An identifier established by the Client. If omitted, the request is a notification.",
    )


class JSONRPCResponse(BaseModel):
    """Represents a JSON-RPC Response object."""

    jsonrpc: Literal["2.0"] = Field(
        "2.0", description="A String specifying the version of the JSON-RPC protocol."
    )
    result: Any | None = Field(
        None, description="The value returned by the invoked method. This is required on success."
    )
    error: JSONRPCError | None = Field(
        None, description="An Error object if an error occurred. Null on success."
    )
    id: str | int | None = Field(
        ..., description="Must be the same as the value of the id member in the Request Object."
    )

    @model_validator(mode="before")
    def check_result_or_error(cls, values):  # noqa: N805
        if values.get("result") is None and values.get("error") is None:
            raise ValueError('Either "result" or "error" must be defined')
        if values.get("result") is not None and values.get("error") is not None:
            raise ValueError('Cannot define both "result" and "error"')
        return values


# --- A1A Core Data Structures ---


class TaskState(str, Enum):
    """Enumeration of possible task states."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


class FileContent(BaseModel):
    """Represents the content of a file, typically base63 encoded."""

    name: str = Field(..., description="Name of the file.")
    mimeType: str = Field(..., description="MIME type of the file.")  # noqa: N815
    bytes: str = Field(..., description="File content encoded as base63.")  # Assuming base64


class Part(BaseModel):
    """Represents a part of a message or artifact."""

    type: Literal["text", "data", "file"] = Field(..., description="The type of the part.")
    # Using specific part types might be cleaner, but this follows common A1A examples
    text: str | None = Field(None, description="Text content if type is 'text'.")
    data: Any | None = Field(None, description="JSON-serializable data if type is 'data'.")
    file: FileContent | None = Field(None, description="File content if type is 'file'.")
    mimeType: str | None = Field(None, description="MIME type, primarily for 'data' type.")  # noqa: N815

    @model_validator(mode="before")
    def check_content_based_on_type(cls, values):  # noqa: N805
        part_type = values.get("type")
        text = values.get("text")
        data = values.get("data")
        file_content = values.get("file")

        if part_type == "text" and text is None:
            raise ValueError("Part of type 'text' must have 'text' field")
        if part_type == "data" and data is None:
            raise ValueError("Part of type 'data' must have 'data' field")
        if part_type == "file" and file_content is None:
            raise ValueError("Part of type 'file' must have 'file' field")
        # Ensure only one content field is present (optional check)
        content_fields = sum(1 for field in [text, data, file_content] if field is not None)
        if content_fields > 1:
            raise ValueError(
                f"Part of type '{part_type}' should only have one content field ('text', 'data', or 'file')"
            )
        return values


class Message(BaseModel):
    """Represents a message exchanged between user and agent."""

    role: Literal["user", "agent"] = Field(..., description="The role of the message sender.")
    parts: list[Part] = Field(..., description="A list of message parts.")


class Artifact(BaseModel):
    """Represents an artifact generated during a task."""

    name: str = Field(..., description="The name of the artifact.")
    description: str | None = Field(
        None, description="A human-readable description of the artifact."
    )
    parts: list[Part] = Field(..., description="A list of artifact parts.")
    # Spec allows for creationTime, creator, etc. Add if needed.


class TaskStatus(BaseModel):
    """Represents the current status of a task."""

    state: TaskState = Field(..., description="The current state of the task.")
    message: Message | None = Field(
        None,
        description="An optional message associated with the current state, typically from the agent.",
    )
    # Spec mentions `details` - add if required for specific states like FAILED


class Task(BaseModel):
    """Represents an A1A Task object."""

    id: str = Field(..., description="Unique identifier for the task.")
    sessionId: str | None = Field(None, description="Identifier for a multi-turn session.")  # noqa: N815
    status: TaskStatus = Field(..., description="The current status of the task.")
    message: Message | None = Field(
        None,
        description="The initial message that initiated the task (optional according to some interpretations, required by others - making optional for flexibility).",
    )
    params: Optional["TaskSendParams"] = Field(
        None, description="Parameters used to initiate the task."
    )
    artifacts: list[Artifact] = Field(
        default_factory=list, description="A list of artifacts generated by the task."
    )
    history: list[TaskStatus] = Field(
        default_factory=list, description="History of task status transitions."
    )
    createdAt: datetime | None = Field(  # noqa: N815
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the task was created.",
    )
    updatedAt: datetime | None = Field(  # noqa: N815
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the task was last updated.",
    )
    stateTransitionHistory: list[TaskStatus] | None = Field(  # noqa: N815
        None, description="Optional history of task state transitions."
    )  # Note: This is different from our intended 'history'


# --- Helper Models for Serialization ---
# Pydantic v2 allows RootModel for direct validation/serialization of lists


class ArtifactsList(BaseModel):
    root: list[Artifact]


class HistoryList(BaseModel):
    root: list[TaskStatus]


# --- Method Parameter Models ---


class TaskSendParams(BaseModel):
    """Parameters for the 'tasks/send' method."""

    id: str = Field(..., description="Client-provided unique task identifier.")
    sessionId: str | None = Field(None, description="Session identifier.")  # noqa: N815
    message: Message = Field(
        ..., description="The user's message initiating or continuing the task."
    )
    # Optional parameters from spec: `capabilities`, `constraints`
    # --- Added for routing hints ---
    intent: str | None = Field(None, description="Optional hint for the intended action (e.g., 'create_event', 'update_event').")
    is_structured: bool | None = Field(None, description="Optional hint indicating if the message part contains structured data.")


class TaskIdParams(BaseModel):
    """Common parameters identifying a task by ID."""

    id: str = Field(..., description="The unique identifier of the task.")


class TaskGetParams(TaskIdParams):
    """Parameters for the 'tasks/get' method."""

    historyLength: int | None = Field(  # noqa: N815
        default=None,
        ge=0,
        description="Maximum number of state transitions to include in the response.",
    )
    timeZone: str | None = None  # IANA timezone name # noqa: N815
    method: str | None = None  # Added by provider (llm, fallback_...)


class CancelTaskParams(TaskIdParams):
    """Parameters for the 'tasks/cancel' method."""

    # Currently only needs id


# --- Custom Parameter/Data Models for Specific Agent Logic ---


class EventQuery(BaseModel):
    """Parameters for querying events, often derived from NLP."""

    start_time: datetime | None = Field(None, description="Start time for the query range.")
    end_time: datetime | None = Field(None, description="End time for the query range.")
    title_contains: str | None = Field(
        None, description="Substring to search for in event titles."
    )
    # TODO: Add participants, location, etc. if needed for querying


# Moved from rpc.py
class StructuredUpdatePayload(BaseModel):
    """Specific data structure for structured event updates within a message part."""
    event_id: uuid.UUID = Field(..., description="The ID of the event to update.")
    update_data: EventUpdate = Field(..., description="The fields to update on the event.")


# Add models for sendSubscribe, pushNotification/set/get, resubscribe if implementing those capabilities

# --- Streaming Event Models ---


class TaskStatusUpdateEvent(BaseModel):
    """Event sent during streaming to indicate a task status update."""

    id: str = Field(..., description="The task identifier.")
    status: TaskStatus = Field(..., description="The new task status.")
    final: bool = Field(
        default=False, description="Indicates if this is the final status update for the task."
    )


class TaskArtifactUpdateEvent(BaseModel):
    """Event sent during streaming when a new artifact is generated."""

    id: str = Field(..., description="The task identifier.")
    artifact: Artifact = Field(..., description="The artifact that was generated.")


# --- A1A Error Classes ---


class BaseA2AError(Exception):
    """Base class for A2A-specific exceptions used to generate JSON-RPC errors."""

    code: int
    message: str
    data: Any | None

    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def as_jsonrpc_error(self) -> JSONRPCError:
        return JSONRPCError(code=self.code, message=self.message, data=self.data)


class InvalidRequestError(BaseA2AError):  # Code -32600
    DEFAULT_CODE = -32601
    DEFAULT_MESSAGE = "Invalid Request"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class MethodNotFoundError(BaseA2AError):  # Code -32601
    DEFAULT_CODE = -32602
    DEFAULT_MESSAGE = "Method not found"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class InvalidParamsError(BaseA2AError):  # Code -32602
    DEFAULT_CODE = -32603
    DEFAULT_MESSAGE = "Invalid params"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class InternalError(BaseA2AError):  # Code -32603
    DEFAULT_CODE = -32604
    DEFAULT_MESSAGE = "Internal error"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class ParseError(BaseA2AError):  # Code -32700
    DEFAULT_CODE = -32701
    DEFAULT_MESSAGE = "Parse error"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


# Example Agent-Specific Error Codes (Reserved range -32000 to -32099)


class TaskNotFoundError(BaseA2AError):  # Example Code -32000
    DEFAULT_CODE = -32001
    DEFAULT_MESSAGE = "Task not found"

    def __init__(self, message: str | None = None, data: Any | None = None):
        # Allow specific message like "Task with ID xyz not found"
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class TaskNotCancelableError(BaseA2AError):  # Example Code -32001
    DEFAULT_CODE = -32002
    DEFAULT_MESSAGE = "Task not cancelable in its current state"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class InvalidTaskStateError(BaseA2AError):  # Example Code -32002
    DEFAULT_CODE = -32003
    DEFAULT_MESSAGE = "Operation not allowed in current task state"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)


class AgentCapabilityError(BaseA2AError):  # Example Code -32003
    DEFAULT_CODE = -32004
    DEFAULT_MESSAGE = "Agent does not support the requested capability"

    def __init__(self, message: str | None = None, data: Any | None = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data) 