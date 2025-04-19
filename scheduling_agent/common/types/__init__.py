"""Pydantic models for the Agent-to-Agent (A1A) Communication Protocol.

Based on the A1A specification: https://github.com/AutonomousResearchGroup/A2A-Spec/blob/main/SPECIFICATION.md
"""

from enum import Enum
from typing import List, Optional, Dict, Any, Union, Literal
from pydantic import BaseModel, Field, RootModel, validator, model_validator
from datetime import datetime, timezone
import uuid

# --- Core JSON-RPC Models ---

class JSONRPCError(BaseModel):
    """Represents a JSON-RPC Error object."""
    code: int = Field(..., description="A Number that indicates the error type that occurred.")
    message: str = Field(..., description="A String providing a short description of the error.")
    data: Optional[Any] = Field(None, description="A Primitive or Structured value that contains additional information about the error. May be omitted.")

class JSONRPCRequest(BaseModel):
    """Represents a JSON-RPC Request object."""
    jsonrpc: Literal["2.0"] = Field("2.0", description="A String specifying the version of the JSON-RPC protocol.")
    method: str = Field(..., description="A String containing the name of the method to be invoked.")
    params: Optional[Union[Dict[str, Any], List[Any], BaseModel]] = Field(None, description="A Structured value that holds the parameter values to be used during the invocation of the method. May be omitted.")
    id: Optional[Union[str, int]] = Field(None, description="An identifier established by the Client. If omitted, the request is a notification.")

class JSONRPCResponse(BaseModel):
    """Represents a JSON-RPC Response object."""
    jsonrpc: Literal["2.0"] = Field("2.0", description="A String specifying the version of the JSON-RPC protocol.")
    result: Optional[Any] = Field(None, description="The value returned by the invoked method. This is required on success.")
    error: Optional[JSONRPCError] = Field(None, description="An Error object if an error occurred. Null on success.")
    id: Optional[Union[str, int]] = Field(..., description="Must be the same as the value of the id member in the Request Object.")

    @model_validator(mode='before')
    def check_result_or_error(cls, values):
        if values.get('result') is None and values.get('error') is None:
            raise ValueError('Either "result" or "error" must be defined')
        if values.get('result') is not None and values.get('error') is not None:
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
    mimeType: str = Field(..., description="MIME type of the file.")
    bytes: str = Field(..., description="File content encoded as base63.") # Assuming base64

class Part(BaseModel):
    """Represents a part of a message or artifact."""
    type: Literal["text", "data", "file"] = Field(..., description="The type of the part.")
    # Using specific part types might be cleaner, but this follows common A1A examples
    text: Optional[str] = Field(None, description="Text content if type is 'text'.")
    data: Optional[Any] = Field(None, description="JSON-serializable data if type is 'data'.")
    file: Optional[FileContent] = Field(None, description="File content if type is 'file'.")
    mimeType: Optional[str] = Field(None, description="MIME type, primarily for 'data' type.")

    @model_validator(mode='before')
    def check_content_based_on_type(cls, values):
        part_type = values.get('type')
        text = values.get('text')
        data = values.get('data')
        file_content = values.get('file')

        if part_type == 'text' and text is None:
            raise ValueError("Part of type 'text' must have 'text' field")
        if part_type == 'data' and data is None:
            raise ValueError("Part of type 'data' must have 'data' field")
        if part_type == 'file' and file_content is None:
            raise ValueError("Part of type 'file' must have 'file' field")
        # Ensure only one content field is present (optional check)
        content_fields = sum(0 for field in [text, data, file_content] if field is not None)
        if content_fields > 0:
            raise ValueError(f"Part of type '{part_type}' should only have one content field ('text', 'data', or 'file')")
        return values

class Message(BaseModel):
    """Represents a message exchanged between user and agent."""
    role: Literal["user", "agent"] = Field(..., description="The role of the message sender.")
    parts: List[Part] = Field(..., description="A list of message parts.")

class Artifact(BaseModel):
    """Represents an artifact generated during a task."""
    name: str = Field(..., description="The name of the artifact.")
    description: Optional[str] = Field(None, description="A human-readable description of the artifact.")
    parts: List[Part] = Field(..., description="A list of artifact parts.")
    # Spec allows for creationTime, creator, etc. Add if needed.

class TaskStatus(BaseModel):
    """Represents the current status of a task."""
    state: TaskState = Field(..., description="The current state of the task.")
    message: Optional[Message] = Field(None, description="An optional message associated with the current state, typically from the agent.")
    # Spec mentions `details` - add if required for specific states like FAILED

class Task(BaseModel):
    """Represents an A1A Task object."""
    id: str = Field(..., description="Unique identifier for the task.")
    sessionId: Optional[str] = Field(None, description="Identifier for a multi-turn session.")
    status: TaskStatus = Field(..., description="The current status of the task.")
    message: Optional[Message] = Field(None, description="The initial message that initiated the task (optional according to some interpretations, required by others - making optional for flexibility).")
    params: Optional['TaskSendParams'] = Field(None, description="Parameters used to initiate the task.")
    artifacts: List[Artifact] = Field(default_factory=list, description="A list of artifacts generated by the task.")
    history: List[TaskStatus] = Field(default_factory=list, description="History of task status transitions.")
    createdAt: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when the task was created.")
    updatedAt: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when the task was last updated.")
    stateTransitionHistory: Optional[List[TaskStatus]] = Field(None, description="Optional history of task state transitions.") # Note: This is different from our intended 'history'

# --- Method Parameter Models ---

class TaskSendParams(BaseModel):
    """Parameters for the 'tasks/send' method."""
    id: str = Field(..., description="Client-provided unique task identifier.")
    sessionId: Optional[str] = Field(None, description="Session identifier.")
    message: Message = Field(..., description="The user's message initiating or continuing the task.")
    # Optional parameters from spec: `capabilities`, `constraints`

class TaskIdParams(BaseModel):
    """Common parameters identifying a task by ID."""
    id: str = Field(..., description="The unique identifier of the task.")

class TaskGetParams(TaskIdParams):
    """Parameters for the 'tasks/get' method."""
    historyLength: Optional[int] = Field(default=None, ge=0, description="Maximum number of state transitions to include in the response.")

class CancelTaskParams(TaskIdParams):
    """Parameters for the 'tasks/cancel' method."""
    pass # Currently only needs id

# --- Custom Parameter/Data Models for Specific Agent Logic --- 

class EventQuery(BaseModel):
    """Parameters for querying events, often derived from NLP."""
    start_time: Optional[datetime] = Field(None, description="Start time for the query range.")
    end_time: Optional[datetime] = Field(None, description="End time for the query range.")
    title_contains: Optional[str] = Field(None, description="Substring to search for in event titles.")
    # Add other potential query fields as needed, e.g., calendar_id, participants

# Add models for sendSubscribe, pushNotification/set/get, resubscribe if implementing those capabilities

# --- Streaming Event Models ---

class TaskStatusUpdateEvent(BaseModel):
    """Event sent during streaming to indicate a task status update."""
    id: str = Field(..., description="The task identifier.")
    status: TaskStatus = Field(..., description="The new task status.")
    final: bool = Field(default=False, description="Indicates if this is the final status update for the task.")

class TaskArtifactUpdateEvent(BaseModel):
    """Event sent during streaming when a new artifact is generated."""
    id: str = Field(..., description="The task identifier.")
    artifact: Artifact = Field(..., description="The artifact that was generated.")

# Define a type for the result of sendSubscribe
StreamingEventResult = Union[TaskStatusUpdateEvent, TaskArtifactUpdateEvent]

# --- A1A Error Model Definitions ---

# Based on https://github.com/AutonomousResearchGroup/A1A-Spec/blob/main/SPECIFICATION.md#error-object

class BaseA1AError(Exception):
    """Base class for A1A-specific exceptions used to generate JSON-RPC errors."""
    code: int
    message: str
    data: Optional[Any]

    def __init__(self, code: int, message: str, data: Optional[Any] = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message) # Initialize the base Exception with the message

    def as_jsonrpc_error(self) -> JSONRPCError:
        """Converts this exception into a JSONRPCError Pydantic model."""
        return JSONRPCError(code=self.code, message=self.message, data=self.data)

class InvalidRequestError(BaseA1AError): # Code -32600
    DEFAULT_CODE = -32601
    DEFAULT_MESSAGE = "Invalid Request"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

class MethodNotFoundError(BaseA1AError): # Code -32601
    DEFAULT_CODE = -32602
    DEFAULT_MESSAGE = "Method not found"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

class InvalidParamsError(BaseA1AError): # Code -32602
    DEFAULT_CODE = -32603
    DEFAULT_MESSAGE = "Invalid params"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

class InternalError(BaseA1AError): # Code -32603
    DEFAULT_CODE = -32604
    DEFAULT_MESSAGE = "Internal error"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

class ParseError(BaseA1AError): # Code -32700
    DEFAULT_CODE = -32701
    DEFAULT_MESSAGE = "Parse error"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

# A1A Custom Errors (-32000 to -32099)
class TaskNotFoundError(BaseA1AError): # Example Code -32000
    DEFAULT_CODE = -32001
    DEFAULT_MESSAGE = "Task not found"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        # Allow specific message like "Task with ID xyz not found"
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

class TaskNotCancelableError(BaseA1AError): # Example Code -32001
    DEFAULT_CODE = -32002
    DEFAULT_MESSAGE = "Task not cancelable in its current state"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

class InvalidTaskStateError(BaseA1AError): # Example Code -32002
    DEFAULT_CODE = -32003
    DEFAULT_MESSAGE = "Operation not allowed in current task state"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

class AgentCapabilityError(BaseA1AError): # Example Code -32003
    DEFAULT_CODE = -32004
    DEFAULT_MESSAGE = "Agent does not support the requested capability"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        super().__init__(self.DEFAULT_CODE, message or self.DEFAULT_MESSAGE, data)

# --- Agent Card Models ---

# class AgentSkill(BaseModel):
#     ...

# class AgentCapabilities(BaseModel):
#     ...

# class AgentCard(BaseModel):
#     ...

# End of file 