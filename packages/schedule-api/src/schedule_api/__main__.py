# packages/schedule-api/src/schedule_api/__main__.py
# Copyright (C) 2025 Martin Bukowski

"""Main entry point for the A2A Scheduling Agent API."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError  # Import for handling 422
from fastapi.responses import JSONResponse
import json
from typing import Any

from schedule_a2a.dto import (
    JSONRPCResponse as A2AJSONRPCResponse # Needed for manual construction if required
)
from schedule_a2a.exceptions import (
    BaseA2AError,
    InternalError as A2AInternalError,
    InvalidRequestError as A2AInvalidRequestError,
    ParseError as A2AParseError
)
from schedule_core.core.logging_config import get_logger, setup_logging
from schedule_core.core.settings import Settings
from schedule_core.db.service import DatabaseService
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.exporters.ical_exporter import ICalExporter
from schedule_core.event.exporters.orgmode_exporter import OrgModeExporter
from schedule_nlp.llm_provider import LLMProvider
from schedule_nlp.nlp_processor import NLPProcessor
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_api.middleware.auth import APIKeyAuthMiddleware
from schedule_api.routes import rpc, well_known

logger = get_logger(__name__)

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

def main(settings: Settings) -> FastAPI:
    """Main entry point for the A2A Scheduling Agent API.
       Initializes settings, logging, lifespan, app, middleware, handlers, routers.
       Returns the configured app and settings.
    """
    setup_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialize resources on startup
        logger.info("Lifespan startup: Initializing resources...")

        try:
            await app.state.db_service.initialize()
            app.state.date_utils = DateUtils()
            app.state.llm_provider = (
                LLMProvider.from_settings(settings) if settings.llm_api_key else None
            )
            app.state.nlp_processor = NLPProcessor(
                date_utils=app.state.date_utils, llm_provider=app.state.llm_provider
            )
            app.state.conflict_resolver = ConflictResolver(date_utils=app.state.date_utils)
            app.state.exporters = {
                "ics": ICalExporter(date_utils=app.state.date_utils),
                "org": OrgModeExporter(date_utils=app.state.date_utils),
            }
            app.state.task_manager = SchedulingTaskManager(db_service=app.state.db_service)
            yield
        finally:
            if getattr(app.state, "llm_provider", None) is not None:
                await app.state.llm_provider.client.close()
            await app.state.db_service.close()

    app = FastAPI(
        title="A2A Scheduling Agent",
        version="1.0.0",
        description="An A2A Agent for scheduling and calendar management.",
        lifespan=lifespan,
    )

    # 0. Settings
    app.state.settings = settings
    logger.info("Settings initialized.")

    # 1. Database Service
    app.state.db_service = DatabaseService.from_settings(settings)
    logger.info("DatabaseService initialized.")

    # --- Add Middleware ---
    app.add_middleware(APIKeyAuthMiddleware)

    # --- Specific Exception Handlers --- 

    # Catch all A2A-defined errors inheriting from BaseA2AError
    @app.exception_handler(BaseA2AError)
    async def a2a_exception_handler(request: Request, exc: BaseA2AError):
        logger.warning(
            f"Caught A2A exception: {type(exc).__name__} code={exc.code} msg='{exc.message}'",
            request_url=request.url.path
        )
        request_id, _ = await _get_request_details(request)
        rpc_error_dto = exc.as_jsonrpc_error()
        return JSONResponse(
            status_code=200,
            content=A2AJSONRPCResponse(id=request_id, error=rpc_error_dto).model_dump(exclude_none=True)
        )

    # Handler for FastAPI's RequestValidationError (Raised *before* route -> A2A ParseError/InvalidRequestError)
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
            a2a_exc = A2AParseError(message="Failed to parse JSON request.")
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
                a2a_exc = A2AInvalidRequestError(
                    message="Invalid JSON-RPC request structure (e.g., missing 'method' or invalid 'jsonrpc').",
                    data=error_details,
                )
            else:
                # Other validation errors (e.g., type errors in top-level fields like 'id', general structure issues)
                # These indicate the request couldn't be parsed into a valid JSONRPCRequest *object*.
                # Map these to ParseError according to A2A spec (failure to parse the request).
                a2a_exc = A2AParseError(
                    message="Failed to parse request into valid JSON-RPC structure (e.g., invalid field type).",
                    data=error_details,
                )

        # Try to get ID even in parse error, if possible (already attempted above)
        if request_id is None:
            logger.warning("Could not determine request ID during ParseError handling.")

        # Check if these DTOs have as_jsonrpc_error or build manually
        rpc_error_dto = a2a_exc.as_jsonrpc_error() # Assuming they have it
        return JSONResponse(
            status_code=200,
            content=A2AJSONRPCResponse(id=request_id, error=rpc_error_dto).model_dump(exclude_none=True)
        )

    # Generic fallback handler (Generic Exception -> A2A InternalError)
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Handles any other unexpected exceptions not caught by specific handlers."""
        # Log with high severity as this is unexpected
        logger.exception(
            "Unhandled generic Exception caught by handler", exc_info=exc, request_url=request.url.path
        )
        # Attempt to get request_id, but don't fail if body is consumed
        request_id, params = await _get_request_details(request)
        task_id = params.get("id") if isinstance(params, dict) else None

        # Create a standard A2A InternalError response
        # Avoid exposing raw exception details unless necessary/configured
        error_data = {"taskId": task_id, "details": "An unexpected server error occurred."}
        a2a_exc = A2AInternalError(message="Internal server error", data=error_data)
        rpc_error_dto = a2a_exc.as_jsonrpc_error()

        return JSONResponse(
            status_code=200, # JSON-RPC still uses 200 OK
            content=A2AJSONRPCResponse(id=request_id, error=rpc_error_dto).model_dump(exclude_none=True)
        )

    # --- Routers ---

    app.include_router(well_known.router, prefix="/.well-known", tags=["Well-Known"])
    app.include_router(rpc.router, prefix="/api/rpc", tags=["JSON-RPC"])

    @app.get("/", tags=["Status"])
    async def read_root():
        """Basic endpoint to check if the API is running."""
        return {"message": "A2A Scheduling Agent is running."}

    return app


def create_app() -> FastAPI:
    """Factory function for creating the FastAPI app (used by uvicorn)."""
    from schedule_core.core.settings import Settings
    settings = Settings()
    return main(settings)


if __name__ == "__main__":
    import uvicorn
    settings = Settings()

    # Call main to get the app and settings
    app = main(settings)

    logger.info(
        f"Starting Uvicorn server on {settings.server_host}:{settings.server_port}",
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
        reload=settings.reload,
    )

    # Run the server
    uvicorn.run(
        app,
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
        reload=settings.reload,
    )
