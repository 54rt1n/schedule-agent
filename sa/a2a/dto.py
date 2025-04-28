# sa/a2a/dto.py
"""Pydantic models for the Agent-to-Agent (A2A) Communication Protocol.

This module defines the data transfer objects (DTOs) used for communication
between agents following the A2A specification (a2aspec/a2a.json).
These models ensure data consistency and validation according to the protocol.

Class Hierarchy and Usage:

Core Data Structures:
- Task: The central object representing a task being processed by an agent.
    - Contains unique `id` and optional `sessionId`.
    - Holds the current `TaskStatus`.
    - May contain a list of generated `Artifact` objects.
    - May contain a `history` list of `Message` objects representing the
      conversation flow (user requests and agent responses) for the task.
    - Includes optional `metadata`.
- TaskStatus: Represents the state of a task at a point in time.
    - Contains the mandatory `state` (an enum from `TaskState`).
    - May include an associated `Message` providing context.
    - May include a `timestamp` indicating when the status was recorded.
- Message: Represents a single message exchanged between a user and an agent.
    - Has a `role` (user or agent).
    - Contains a list of `Part` objects making up the message content.
    - Includes optional `metadata`.
- Artifact: Represents data generated or consumed by a task.
    - Has optional `name` and `description`.
    - Contains a list of `Part` objects representing the artifact's content.
    - Includes fields like `index`, `append`, `lastChunk` for handling
      streamed or chunked artifacts.
    - Includes optional `metadata`.
- Part: Represents a piece of content within a Message or Artifact.
    - Discriminated by `type`: "text", "data", or "file".
    - Contains the corresponding content field (`text`, `data`, or `file`).
    - `file` content uses the `FileContent` model.
    - Includes optional `metadata`.
- FileContent: Represents file data, either inline or via URI.
    - Contains optional `name` and `mimeType`.
    - Must contain *either* `bytes` (base64 encoded) *or* a `uri`.
- TaskState: An enumeration (`Enum`) defining the possible states of a task
    (submitted, working, input-required, completed, canceled, failed, unknown).

Method Parameter Models:
These models define the structure of the `params` object in JSON-RPC requests
for specific A2A methods.
- TaskSendParams: Used for `tasks/send` and `tasks/sendSubscribe` methods.
    - Includes the task `id`, optional `sessionId`, the initiating `message`,
      and optional `pushNotification` config, `historyLength` preference,
      and `metadata`.
- TaskIdParams: A common structure to identify a task by its `id`.
    - Includes the required `id` and optional `metadata`.
    - Used as a base for other parameter models.
- TaskGetParams: Used for `tasks/get` and `tasks/resubscribe` methods.
    (Corresponds to `TaskQueryParams` in the spec).
    - Inherits `id` and `metadata` from `TaskIdParams`.
    - Adds optional `historyLength` preference.
- CancelTaskParams: Used for the `tasks/cancel` method.
    - Simply inherits from `TaskIdParams` (uses `id` and optional `metadata`).

Authentication & Push Notification Models:
- AuthenticationInfo: Defines authentication schemes and optional credentials.
- PushNotificationConfig: Defines the endpoint URL, optional token, and optional
    `AuthenticationInfo` for agents supporting push notifications.

Streaming Event Models:
Used in responses for streaming methods like `tasks/sendSubscribe`.
- TaskStatusUpdateEvent: Signals a change in task status during streaming.
    - Contains the task `id`, the new `TaskStatus`, a `final` flag, and
      optional `metadata`.
- TaskArtifactUpdateEvent: Signals the generation of an artifact during streaming.
    - Contains the task `id`, the generated `Artifact`, and optional `metadata`.

Agent Metadata Models:
Used for agent discovery and capability description (e.g., via an agent card).
- AgentCard: Provides overall information about an agent.
    - Includes `name`, `description`, `url`, `version`, `documentationUrl`,
      `capabilities`, `authentication` requirements, default I/O modes, and
      a list of `skills`.
- AgentCapabilities: Defines boolean flags for supported features like
    `streaming`, `pushNotifications`, and `stateTransitionHistory`.
- AgentSkill: Describes a specific capability or function the agent offers.
    - Includes `id`, `name`, `description`, `tags`, `examples`, and supported
      `inputModes`/`outputModes`.

JSON-RPC Base Models:
- JSONRPCError, JSONRPCRequest, JSONRPCResponse: Standard Pydantic models
    representing the basic structure of JSON-RPC 2.0 messages, errors,
    and responses, forming the envelope for A2A communication.
"""

from datetime import UTC, datetime
from enum import Enum
from pydantic import BaseModel, Field, HttpUrl, model_validator
from typing import Any, Literal, Optional
import uuid

# --- Core JSON-RPC Models (Assumed correct as per spec) ---

class JSONRPCError(BaseModel):
    """Represents a JSON-RPC Error object.

    Source: a2aspec/a2a.json#L721
    """
    code: int = Field(..., description="A Number that indicates the error type that occurred.")
    message: str = Field(..., description="A String providing a short description of the error.")
    data: Any | None = Field(None, description="A Primitive or Structured value that contains additional information about the error. May be omitted.")

class JSONRPCRequest(BaseModel):
    """Represents a JSON-RPC Request object.

    Source: a2aspec/a2a.json#L777
    """
    jsonrpc: Literal["2.0"] = Field("2.0", description="A String specifying the version of the JSON-RPC protocol.")
    method: str = Field(..., description="A String containing the name of the method to be invoked.")
    params: dict[str, Any] | list[Any] | BaseModel | None = Field(None, description="A Structured value that holds the parameter values to be used during the invocation of the method. May be omitted.")
    id: str | int | None = Field(None, description="An identifier established by the Client. If omitted, the request is a notification.")

class JSONRPCResponse(BaseModel):
    """Represents a JSON-RPC Response object.

    Source: a2aspec/a2a.json#L810
    """
    jsonrpc: Literal["2.0"] = Field("2.0", description="A String specifying the version of the JSON-RPC protocol.")
    result: Any | None = Field(None, description="The value returned by the invoked method. This is required on success.")
    error: JSONRPCError | None = Field(None, description="An Error object if an error occurred. Null on success.")
    id: str | int | None = Field(..., description="Must be the same as the value of the id member in the Request Object.")

    @model_validator(mode="before")
    def check_result_or_error(cls, values): # noqa: N805
        if values.get("result") is None and values.get("error") is None:
            raise ValueError('Either "result" or "error" must be defined')
        if values.get("result") is not None and values.get("error") is not None:
            raise ValueError('Cannot define both "result" and "error"')
        return values

# --- A2A Core Data Structures (Aligned with a2aspec/a2a.json) ---

class TaskState(str, Enum):
    """Enumeration of possible task states (aligned with spec).

    Source: a2aspec/a2a.json#L1368
    """
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    UNKNOWN = "unknown"

class FileContent(BaseModel):
    """Represents the content of a file (aligned with spec).

    Source: a2aspec/a2a.json#L404
    """
    name: str | None = Field(None, description="Name of the file.")
    mimeType: str | None = Field(None, description="MIME type of the file.")
    bytes: str | None = Field(None, description="File content encoded as base64.")
    uri: HttpUrl | str | None = Field(None, description="URI reference to the file content.")

    @model_validator(mode="before")
    def check_bytes_or_uri(cls, values): # noqa: N805
        if values.get("bytes") is None and values.get("uri") is None:
            raise ValueError("Either 'bytes' or 'uri' must be provided for FileContent")
        if values.get("bytes") is not None and values.get("uri") is not None:
            raise ValueError("Cannot provide both 'bytes' and 'uri' for FileContent")
        return values

class Part(BaseModel):
    """Represents a part of a message or artifact (aligned with spec Part union).

    Source: a2aspec/a2a.json#L942 (Defines the Part union)
    """
    type: Literal["text", "data", "file"] = Field(..., description="The type of the part.")
    text: str | None = Field(None, description="Text content if type is 'text'.")
    data: Any | None = Field(None, description="JSON-serializable data if type is 'data'.")
    file: FileContent | None = Field(None, description="File content if type is 'file'.")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata for the part.")

    @model_validator(mode="before")
    def check_content_based_on_type(cls, values): # noqa: N805
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

        content_fields = []
        if part_type == "text": content_fields.append(text)
        if part_type == "data": content_fields.append(data)
        if part_type == "file": content_fields.append(file_content)
        other_fields = [f for f in [text, data, file_content] if f not in content_fields]

        if any(f is not None for f in other_fields):
             raise ValueError(f"Part of type '{part_type}' should only have '{part_type}' content field set.")

        if part_type == "file" and file_content is not None and not isinstance(file_content, (dict, FileContent)):
             raise ValueError("Part of type 'file' must have 'file' field as a FileContent object or dict")

        return values

class Message(BaseModel):
    """Represents a message exchanged (aligned with spec).

    Source: a2aspec/a2a.json#L848
    """
    role: Literal["user", "agent"] = Field(..., description="The role of the message sender.")
    parts: list[Part] = Field(..., description="A list of message parts.")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata for the message.")

class Artifact(BaseModel):
    """Represents an artifact (aligned with spec).

    Source: a2aspec/a2a.json#L213
    """
    name: str | None = Field(None, description="The name of the artifact.")
    description: str | None = Field(None, description="A human-readable description.")
    parts: list[Part] = Field(..., description="A list of artifact parts.")
    index: int = Field(default=0, description="Index for streaming artifacts.")
    append: bool | None = Field(None, description="Flag for appending streamed artifacts.")
    lastChunk: bool | None = Field(None, description="Flag for last chunk of streamed artifact.")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata for the artifact.")

class TaskStatus(BaseModel):
    """Represents the current status of a task (aligned with spec).

    Source: a2aspec/a2a.json#L1384
    """
    state: TaskState = Field(..., description="The current state of the task.")
    message: Message | None = Field(None, description="An optional message associated with the current state.")
    timestamp: datetime | None = Field(None, description="Timestamp of the status update (UTC).")

    @model_validator(mode="before")
    def handle_timestamp(cls, values): # noqa: N805
        ts = values.get("timestamp")
        if ts is None:
            pass
        elif isinstance(ts, str):
            try:
                if ts.endswith('Z'):
                    ts = ts[:-1] + '+00:00'
                values["timestamp"] = datetime.fromisoformat(ts)
                if values["timestamp"].tzinfo is None:
                     values["timestamp"] = values["timestamp"].replace(tzinfo=UTC)
                else:
                     values["timestamp"] = values["timestamp"].astimezone(UTC)
            except ValueError:
                raise ValueError(f"Invalid timestamp format: {ts}")
        elif isinstance(ts, datetime):
             if ts.tzinfo is None:
                 values["timestamp"] = ts.replace(tzinfo=UTC)
             else:
                 values["timestamp"] = ts.astimezone(UTC)
        return values

class Task(BaseModel):
    """Represents an A2A Task object (aligned with spec).

    Source: a2aspec/a2a.json#L1133
    """
    id: str = Field(..., description="Unique identifier for the task.")
    sessionId: str | None = Field(None, description="Identifier for a multi-turn session.")
    status: TaskStatus = Field(..., description="The current status of the task.")
    artifacts: list[Artifact] | None = Field(None, description="A list of artifacts generated by the task.")
    history: list[Message] | None = Field(None, description="History of messages exchanged in the task (user/agent).")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata for the task.")

# --- Payload Wrapper for Structured Data --- #

class StructuredPayloadWrapper(BaseModel):
    """A wrapper for structured data sent within Part.data.

    This allows specifying an explicit 'intent' alongside the actual data payload
    while keeping the Part structure spec-compliant.
    """
    intent: str = Field(..., description="The specific operation intent (e.g., 'create_event', 'query_events').")
    payload: Any = Field(..., description="The actual data payload for the specified intent.")

# --- Authentication & Push Notification Models (Added from spec) ---

class AuthenticationInfo(BaseModel):
    """Authentication details (aligned with spec).

    Source: a2aspec/a2a.json#L269
    """
    schemes: list[str] = Field(..., description="List of supported authentication schemes.")
    credentials: str | None = Field(None, description="Credentials string (scheme-dependent).")

class PushNotificationConfig(BaseModel):
    """Configuration for push notifications (aligned with spec).

    Source: a2aspec/a2a.json#L910
    """
    url: HttpUrl | str = Field(..., description="Endpoint URL for push notifications.")
    token: str | None = Field(None, description="Bearer token or similar for the push endpoint.")
    authentication: AuthenticationInfo | None = Field(None, description="Authentication details for the push endpoint.")

# --- Method Parameter Models (Aligned with a2aspec/a2a.json) ---

class TaskSendParams(BaseModel):
    """Parameters for the 'tasks/send' method (aligned with spec).

    Source: a2aspec/a2a.json#L1323
    """
    id: str = Field(..., description="Client-provided unique task identifier.")
    sessionId: str | None = Field(None, description="Session identifier.")
    message: Message = Field(..., description="The user's message initiating or continuing the task.")
    pushNotification: PushNotificationConfig | None = Field(None, description="Configuration for push notifications.")
    historyLength: int | None = Field(None, ge=0, description="Maximum number of history messages to track/return.")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata for the send request.")

class TaskIdParams(BaseModel):
    """Common parameters identifying a task by ID (aligned with spec).

    Source: a2aspec/a2a.json#L1266
    """
    id: str = Field(..., description="The unique identifier of the task.")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata.")

class TaskGetParams(TaskIdParams):
    """Parameters for the 'tasks/get' method (aligned with spec TaskQueryParams).

    Source: a2aspec/a2a.json#L1292 (TaskQueryParams)
    """
    historyLength: int | None = Field(None, ge=0, description="Maximum number of history messages to include in the response.")

class CancelTaskParams(TaskIdParams):
    """Parameters for the 'tasks/cancel' method (aligned with spec TaskIdParams).

    Source: Uses TaskIdParams (a2aspec/a2a.json#L1266)
    """
    pass

# --- Streaming Event Models (Aligned with spec) ---

class TaskStatusUpdateEvent(BaseModel):
    """Event sent during streaming for task status update (aligned with spec).

    Source: a2aspec/a2a.json#L1433
    """
    id: str = Field(..., description="The task identifier.")
    status: TaskStatus = Field(..., description="The new task status.")
    final: bool = Field(default=False, description="Indicates if this is the final status update.")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata for the event.")

class TaskArtifactUpdateEvent(BaseModel):
    """Event sent during streaming for new artifact (aligned with spec).

    Source: a2aspec/a2a.json#L1467
    """
    id: str = Field(..., description="The task identifier.")
    artifact: Artifact = Field(..., description="The artifact that was generated.")
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata for the event.")

# --- Agent Card Models (Aligned with spec) ---

class AgentSkill(BaseModel):
    """Represents a skill the agent can perform (aligned with spec).

    Source: a2aspec/a2a.json#L160
    """
    id: str = Field(..., description="Unique identifier for the skill.")
    name: str = Field(..., description="Human-readable name of the skill.")
    description: str | None = Field(None, description="Description of what the skill does.")
    tags: list[str] | None = Field(None, description="Tags associated with the skill.")
    examples: list[str] | None = Field(None, description="Example invocations or use cases.")
    inputModes: list[str] | None = Field(None, description="Supported input MIME types for this skill.")
    outputModes: list[str] | None = Field(None, description="Supported output MIME types for this skill.")

class AgentCapabilities(BaseModel):
    """Defines the capabilities of the agent according to A2A spec.

    Source: a2aspec/a2a.json#L30
    """
    streaming: bool = Field(default=False, description="Supports streaming responses.")
    pushNotifications: bool = Field(default=False, description="Supports push notifications.")
    stateTransitionHistory: bool = Field(default=False, description="Provides state history.")

class AgentCard(BaseModel):
    """A2A Agent Card providing agent metadata and capabilities (aligned with spec).

    Source: a2aspec/a2a.json#L47
    """
    name: str = Field(..., description="Agent's name.")
    description: str | None = Field(None, description="Brief description of the agent.")
    url: HttpUrl | str = Field(..., description="Base URL for the agent's API endpoint.")
    version: str = Field(..., description="Agent version.")
    documentationUrl: HttpUrl | str | None = Field(None, description="URL for agent documentation.")
    capabilities: AgentCapabilities = Field(..., description="Agent's A2A capabilities.")
    authentication: AuthenticationInfo | None = Field(None, description="Authentication required by the agent.")
    defaultInputModes: list[str] = Field(default=["text"], description="Default input MIME types.")
    defaultOutputModes: list[str] = Field(default=["text"], description="Default output MIME types.")
    skills: list[AgentSkill] = Field(..., description="List of skills the agent offers.")

