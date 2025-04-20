import json  # Import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError  # Import for handling 422
from fastapi.responses import JSONResponse

from scheduling_agent.a2a.types import (
    InternalError,
    InvalidRequestError,
    ParseError,
)
from scheduling_agent.a2a.types import (
    JSONRPCResponse as A2AJSONRPCResponse,  # Avoid naming conflict with fastapi.responses.JSONResponse
)
from scheduling_agent.api.middleware.auth import APIKeyAuthMiddleware
from scheduling_agent.api.routes import rpc, well_known
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exceptions import NLPError
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter
from scheduling_agent.core.llm_provider import LLMProvider
from scheduling_agent.core.nlp_processor import NLPProcessor

# Import db engine/session factory creation functions
from scheduling_agent.db.database import create_db_engine, create_session_factory

# Import actual exceptions directly
from scheduling_agent.db.exceptions import DatabaseError
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger, setup_logging
from scheduling_agent.settings import settings

# Import components needed for lifespan initialization
from scheduling_agent.a2a.task_manager import SchedulingTaskManager


# --- Helper Function for Error Handling ---
async def _get_request_details(request: Request) -> tuple[Any | None, dict[str, Any] | None]:
    """Attempts to parse the request body to get JSON-RPC ID and params."""
    request_id: Any | None = None
    params: dict[str, Any] | None = None
    try:
        raw_body = await request.body()
        body_json = json.loads(raw_body.decode())
        if isinstance(body_json, dict):
            request_id = body_json.get("id")
            params = body_json.get("params")
    except json.JSONDecodeError:
        logger.warning("Failed to decode JSON body in error handler", request_url=request.url.path)
    except RuntimeError as re:
        # Catch the specific error that occurs when the request body cannot be read again
        if "Receive channel has not been made available" in str(re):
            logger.warning(
                "Could not re-read request body in error handler (likely already consumed)",
                request_url=request.url.path,
            )
            # Cannot reliably get ID or params in this case, return None
            return None, None
        # Re-raise other RuntimeErrors
        logger.exception(
            "Unexpected RuntimeError reading request body in error handler",
            exc_info=re,
            request_url=request.url.path,
        )
        raise
    except Exception as e:
        logger.exception(
            "Unexpected error reading request body in error handler",
            exc_info=e,
            request_url=request.url.path,
        )
    return request_id, params


# Configure logging early
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources on startup
    logger.info("Lifespan startup: Initializing resources...")

    # 1. Database Engine & Session Factory
    app.state.db_engine = create_db_engine(settings.database_url)
    app.state.session_factory = create_session_factory(app.state.db_engine)
    logger.info("Database engine and session factory initialized.")

    # 2. Core Utilities & Providers
    app.state.date_utils = DateUtils()
    # LLMProvider uses settings internally
    app.state.llm_provider = LLMProvider()
    app.state.nlp_processor = NLPProcessor(
        date_utils=app.state.date_utils, llm_provider=app.state.llm_provider
    )
    logger.info("Core utilities (DateUtils, LLMProvider, NLPProcessor) initialized.")

    # 3. Database Service
    app.state.db_service = DatabaseService(
        session_factory=app.state.session_factory,
        date_utils=app.state.date_utils,
        # conflict_resolver will be injected later
    )
    logger.info("DatabaseService initialized.")

    # 4. Conflict Resolver (needs db_service)
    app.state.conflict_resolver = ConflictResolver(
        db_service=app.state.db_service, date_utils=app.state.date_utils
    )
    # Now inject it back into db_service
    app.state.db_service.conflict_resolver = app.state.conflict_resolver
    logger.info("ConflictResolver initialized and injected into DatabaseService.")

    # 5. Exporters
    app.state.exporters = {
        "ics": ICalExporter(date_utils=app.state.date_utils),
        "org": OrgModeExporter(date_utils=app.state.date_utils),
    }
    logger.info("Calendar exporters initialized.")

    # 6. Task Manager (needs several dependencies)
    app.state.task_manager = SchedulingTaskManager(
        session_factory=app.state.session_factory,
        nlp_processor=app.state.nlp_processor,
        db_service=app.state.db_service,
        conflict_resolver=app.state.conflict_resolver,
        exporters=app.state.exporters,
    )
    logger.info("SchedulingTaskManager initialized.")

    logger.info("Lifespan startup complete. Application ready.")
    yield
    # Clean up resources on shutdown
    logger.info("Lifespan shutdown: Cleaning up resources...")
    if hasattr(app.state, "db_engine") and app.state.db_engine:
        await app.state.db_engine.dispose()
        logger.info("Database engine disposed.")
    # Add other cleanup if needed
    logger.info("Lifespan shutdown complete.")


app = FastAPI(
    title="A2A Scheduling Agent",
    version="0.1.0",
    description="An A2A Agent for scheduling and calendar management.",
    lifespan=lifespan,
)

# --- Add Middleware ---
app.add_middleware(APIKeyAuthMiddleware)

# --- Centralized Error Handlers (Phase 3, Task 2) ---


@app.exception_handler(DatabaseError)
async def database_exception_handler(request: Request, exc: DatabaseError):
    logger.exception("Unhandled DatabaseError caught", exc_info=exc, request_url=request.url.path)
    request_id, params = await _get_request_details(request)
    task_id = params.get("id") if isinstance(params, dict) else None
    # Map to A2A InternalError
    error_data = {"taskId": task_id, "details": str(exc)}
    a2a_error = InternalError(message="An internal database error occurred.", data=error_data)
    return JSONResponse(
        status_code=200,  # JSON-RPC errors usually use 200 OK
        content=A2AJSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error()).model_dump(
            exclude_none=True
        ),
    )


@app.exception_handler(NLPError)
async def nlp_exception_handler(request: Request, exc: NLPError):
    logger.exception("Unhandled NLPError caught", exc_info=exc, request_url=request.url.path)
    request_id, params = await _get_request_details(request)
    task_id = params.get("id") if isinstance(params, dict) else None
    # Map to A2A InternalError
    error_data = {"taskId": task_id, "details": str(exc)}
    a2a_error = InternalError(message="An internal NLP processing error occurred.", data=error_data)
    return JSONResponse(
        status_code=200,
        content=A2AJSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error()).model_dump(
            exclude_none=True
        ),
    )


# Handler for FastAPI's validation errors (e.g., invalid JSON, missing required fields in body)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # This handler catches errors during request validation *before* the route handler is called.
    # This includes invalid JSON, or valid JSON that doesn't match the top-level Pydantic model (JSONRPCRequest).

    raw_body_str = "<Could not read body>"
    request_id = None  # Initialize request_id
    is_json_decode_error = False
    try:
        raw_body = await request.body()
        raw_body_str = raw_body.decode()
        # Try to parse the ID even if the rest is invalid
        try:
            body_json = json.loads(raw_body_str)
            request_id = body_json.get("id")
        except json.JSONDecodeError:
            is_json_decode_error = True  # Mark as JSON decode failure
            logger.exception(
                "JSONDecodeError caught by validation handler",
                body=raw_body_str,
                request_url=request.url.path,
            )
            # Fall through to return ParseError below
    except Exception as e:
        logger.exception("Error reading request body in validation handler", exc_info=e)

    # Log the validation error details
    logger.error(
        "RequestValidationError caught by validation handler",
        errors=exc.errors(),
        body=raw_body_str,
        request_url=request.url.path,
        determined_request_id=request_id,
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
            err.get("loc") == ("body", "method") or err.get("loc") == ("body", "jsonrpc")
            for err in error_details
        )

        if is_invalid_rpc_structure:
            # Missing or invalid top-level JSON-RPC fields (method, jsonrpc)
            a2a_error = InvalidRequestError(
                message="Invalid JSON-RPC request structure (e.g., missing 'method' or invalid 'jsonrpc').",
                data=error_details,
            )
        else:
            # Other validation errors (e.g., type errors in top-level fields like 'id', general structure issues)
            # These indicate the request couldn't be parsed into a valid JSONRPCRequest *object*.
            # Map these to ParseError according to A2A spec (failure to parse the request).
            a2a_error = ParseError(
                message="Failed to parse request into valid JSON-RPC structure (e.g., invalid field type).",
                data=error_details,
            )

    # Try to get ID even in parse error, if possible (already attempted above)
    if request_id is None:
        logger.warning("Could not determine request ID during ParseError handling.")

    return JSONResponse(
        status_code=200,  # Use 200 for JSON-RPC errors
        content=A2AJSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error()).model_dump(
            exclude_none=True
        ),
    )


# Generic fallback handler
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catches any unhandled exception and returns a standard A2A InternalError."""
    # Log the original exception first
    logger.exception(
        "Unhandled generic Exception caught by handler",  # Restored original log message
        exc_info=exc,  # Let logger format the traceback
        request_url=request.url.path,
    )

    # Restore original logic with inner try/except
    request_id = None
    task_id = None
    error_details = f"Unhandled exception of type {type(exc).__name__}: {exc!s}"

    try:
        # Best effort to get request details for context, but don't let this fail the handler
        request_id, params = await _get_request_details(request)
        if isinstance(params, dict):
            task_id = params.get("id")  # Use "id" from params for task_id

        # Prepare the A2A error structure
        error_data = {"taskId": task_id, "details": error_details}
        a2a_error = InternalError(
            message="An unexpected internal server error occurred.", data=error_data
        )
        response_content = A2AJSONRPCResponse(
            id=request_id, error=a2a_error.as_jsonrpc_error()
        ).model_dump(exclude_none=True)

    except Exception as handler_exc:
        # If getting details or formatting response fails, log *that* error
        # and return a very generic InternalError
        logger.exception(
            "Error occurred *within* the generic exception handler itself",
            handler_error=str(handler_exc),
            original_exception=error_details,
            request_url=request.url.path,
            exc_info=handler_exc,
        )
        fallback_error = InternalError(
            message="An unexpected internal server error occurred.", data={"details": error_details}
        )
        response_content = A2AJSONRPCResponse(
            id=None, error=fallback_error.as_jsonrpc_error()
        ).model_dump(exclude_none=True)
        # Ensure request_id is None if we failed to get it
        request_id = None

    # Always return a JSONResponse, even if generating the content failed above
    return JSONResponse(
        status_code=200,  # JSON-RPC errors usually use 200 OK
        content=response_content,
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
