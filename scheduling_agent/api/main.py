from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError # Import for handling 422
import json # Import json

from scheduling_agent.api.routes import well_known, rpc
from scheduling_agent.logging_config import setup_logging, get_logger
from scheduling_agent.common.types import (
    JSONRPCResponse as A2AJSONRPCResponse, # Avoid naming conflict with fastapi.responses.JSONResponse
    InternalError,
    ParseError,
    InvalidRequestError,
)

# Import actual exceptions directly
from scheduling_agent.db.exceptions import DatabaseError
from scheduling_agent.core.exceptions import NLPError

# Import TaskManager and dependencies for lifespan
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.api.dependencies import (
    get_db_service, get_nlp_processor, get_conflict_resolver, get_exporters
)
# Import session factory
from scheduling_agent.db.database import async_session_factory

# Configure logging early
setup_logging()
logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources
    logger.info("Starting up and initializing resources...")
    # Create and store the TaskManager instance
    # Note: Dependency getters might initialize their own resources if using lru_cache
    # Ensure session_factory is correctly provided
    # db_service = get_db_service() # DB service uses its own @lru_cache
    # nlp_processor = get_nlp_processor() # NLP uses @lru_cache
    # conflict_resolver = get_conflict_resolver() # Conflict resolver uses @lru_cache
    # exporters = get_exporters() # Exporters use @lru_cache

    # Instantiate TaskManager with necessary dependencies and the session factory
    task_manager_instance = SchedulingTaskManager(
        session_factory=async_session_factory, # Pass the actual factory
        nlp_processor=get_nlp_processor(),
        db_service=get_db_service(),
        conflict_resolver=get_conflict_resolver(),
        exporters=get_exporters()
    )
    app.state.task_manager = task_manager_instance
    logger.info("SchedulingTaskManager instance created and stored in app state.")

    yield

    # Clean up resources
    # Add cleanup for TaskManager if needed (e.g., close connections)
    # Potentially clear caches if using @lru_cache extensively and need reset
    logger.info("Shutting down and cleaning up resources...")

app = FastAPI(
    title="A2A Scheduling Agent",
    version="0.1.0",
    description="An A2A Agent for scheduling and calendar management.",
    lifespan=lifespan,
)

# --- Add Middleware ---
from scheduling_agent.api.middleware.auth import APIKeyAuthMiddleware
app.add_middleware(APIKeyAuthMiddleware)

# --- Centralized Error Handlers (Phase 3, Task 2) ---

@app.exception_handler(DatabaseError)
async def database_exception_handler(request: Request, exc: DatabaseError):
    logger.exception("Unhandled DatabaseError caught", exc_info=exc, request_url=request.url.path)
    # Map to A2A InternalError
    a2a_error = InternalError(message="An internal database error occurred.", data=str(exc))
    # Note: We don't have the original JSON-RPC request ID here easily.
    # Return a generic JSONResponse containing the A2A error structure.
    # Ideally, middleware would store the request ID in request.state if available.
    return JSONResponse(
        status_code=200, # JSON-RPC errors usually use 200 OK
        content=A2AJSONRPCResponse(id=None, error=a2a_error.as_jsonrpc_error()).model_dump(exclude_none=True)
    )

@app.exception_handler(NLPError)
async def nlp_exception_handler(request: Request, exc: NLPError):
    logger.exception("Unhandled NLPError caught", exc_info=exc, request_url=request.url.path)
    # Map to A2A InternalError
    a2a_error = InternalError(message="An internal NLP processing error occurred.", data=str(exc))
    return JSONResponse(
        status_code=200,
        content=A2AJSONRPCResponse(id=None, error=a2a_error.as_jsonrpc_error()).model_dump(exclude_none=True)
    )

# Handler for FastAPI's validation errors (e.g., invalid JSON, missing required fields in body)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # This handler catches errors during request validation *before* the route handler is called.
    # This includes invalid JSON, or valid JSON that doesn't match the top-level Pydantic model (JSONRPCRequest).

    raw_body_str = "<Could not read body>"
    request_id = None # Initialize request_id
    is_json_decode_error = False
    try:
        raw_body = await request.body()
        raw_body_str = raw_body.decode()
        # Try to parse the ID even if the rest is invalid
        try:
            body_json = json.loads(raw_body_str)
            request_id = body_json.get("id")
        except json.JSONDecodeError:
            is_json_decode_error = True # Mark as JSON decode failure
            logger.error("JSONDecodeError caught by validation handler", body=raw_body_str, request_url=request.url.path)
            # Fall through to return ParseError below
    except Exception as e:
        logger.error("Error reading request body in validation handler", exc_info=e)

    # Log the validation error details
    logger.error(
        "RequestValidationError caught by validation handler",
        errors=exc.errors(),
        body=raw_body_str,
        request_url=request.url.path,
        determined_request_id=request_id
    )

    # Now determine the correct JSON-RPC error based on validation details
    if is_json_decode_error:
        # If JSON itself is invalid, it's definitely a ParseError
        a2a_error = ParseError(message="Failed to parse JSON request.")
        # ID is likely None or garbage here, default to None
        request_id = None
    else:
        error_details = exc.errors()
        is_invalid_rpc_structure = any(
            # Check if the error location points specifically to 'method' or 'jsonrpc' within the body
            err.get('loc') == ('body', 'method') or 
            err.get('loc') == ('body', 'jsonrpc')
            for err in error_details
        )

        if is_invalid_rpc_structure:
            # Missing or invalid top-level JSON-RPC fields (method, jsonrpc)
            a2a_error = InvalidRequestError(
                message="Invalid JSON-RPC request structure (e.g., missing 'method' or invalid 'jsonrpc').",
                data=error_details
            )
        else:
            # Other validation errors (e.g., type errors in top-level fields like 'id', general structure issues)
            # These indicate the request couldn't be parsed into a valid JSONRPCRequest *object*.
            # Map these to ParseError according to A2A spec (failure to parse the request).
            a2a_error = ParseError(
                message="Failed to parse request into valid JSON-RPC structure (e.g., invalid field type).",
                data=error_details
            )

    return JSONResponse(
        status_code=200, # Use 200 for JSON-RPC errors
        content=A2AJSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error()).model_dump(exclude_none=True)
    )

# Generic fallback handler
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled generic Exception caught by handler", exc_info=exc, request_url=request.url.path)
    # Map to A2A InternalError
    a2a_error = InternalError(message="An unexpected internal server error occurred.", data=str(exc))
    # Attempt to get request_id (best effort)
    request_id = None
    try:
        raw_body = await request.body()
        raw_body_str = raw_body.decode()
        import json
        body_json = json.loads(raw_body_str)
        request_id = body_json.get("id")
    except Exception:
        pass # Keep request_id as None

    return JSONResponse(
        status_code=200, # JSON-RPC errors usually use 200 OK
        content=A2AJSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error()).model_dump(exclude_none=True)
    )

# --- Routers --- 

# Include routers AFTER error handlers are defined
app.include_router(well_known.router, prefix="/.well-known", tags=["Well-Known"])
app.include_router(rpc.router, prefix="/api/rpc", tags=["JSON-RPC"])

@app.get("/", tags=["Status"])
async def read_root():
    """Basic endpoint to check if the API is running."""
    return {"message": "A2A Scheduling Agent is running."}


# TODO: Set up Dependency Injection (Phase 1, Task 1)
# See api/dependencies.py 