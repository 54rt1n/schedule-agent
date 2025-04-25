# sa/api/middleware/auth.py
"""Middleware for API key authentication."""

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from sa.core.logging_config import get_logger
from sa.core.settings import Settings

logger = get_logger(__name__)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Allow access to public paths like /.well-known/
        if request.url.path.startswith("/.well-known"):
            return await call_next(request)

        # Retrieve settings from application state instead of creating a new instance
        settings: Settings | None = getattr(request.app.state, "settings", None)
        if settings is None:
            # This should not happen if the app is configured correctly
            logger.error("Settings not found in application state!")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Internal server error: Configuration missing."},
            )

        # Skip auth if no keys are configured (allows local dev without keys)
        api_keys = settings.api_keys
        if not api_keys:
            logger.debug("Skipping API key auth as no keys are configured.")
            return await call_next(request)

        authorization: str | None = request.headers.get("Authorization")
        error_response_content = {"detail": "Not authenticated or invalid API key."}
        status_code = status.HTTP_403_FORBIDDEN

        if not authorization:
            logger.warning("Missing Authorization header", url=request.url.path)
            return JSONResponse(status_code=status_code, content=error_response_content)

        scheme, _, api_key = authorization.partition(" ")

        if scheme.lower() != "bearer":
            logger.warning("Invalid Authorization scheme", scheme=scheme, url=request.url.path)
            return JSONResponse(status_code=status_code, content=error_response_content)

        if api_key not in api_keys:
            logger.warning("Invalid API key provided", url=request.url.path)
            # Avoid logging the invalid key itself for security
            return JSONResponse(status_code=status_code, content=error_response_content)

        # If key is valid, proceed with the request
        logger.debug("Valid API key provided, proceeding.", url=request.url.path)
        response = await call_next(request)
        return response
