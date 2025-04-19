from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
import typing

from scheduling_agent.settings import settings # Import the settings instance
from scheduling_agent.logging_config import get_logger

logger = get_logger(__name__)

# Load valid API keys from settings
# Doing this once at module load time is generally okay, but if keys could
# change dynamically, it would need to be reloaded within the middleware.
VALID_API_KEYS = set(settings.api_keys)
if not VALID_API_KEYS:
    logger.warning("No API_KEYS found in settings. Authentication will be disabled.")

class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Allow access to public paths like /.well-known/
        if request.url.path.startswith("/.well-known"):
            return await call_next(request)
        
        # Skip auth if no keys are configured (allows local dev without keys)
        if not VALID_API_KEYS:
            logger.debug("Skipping API key auth as no keys are configured.")
            return await call_next(request)

        authorization: typing.Optional[str] = request.headers.get("Authorization")
        error_response_content = {"detail": "Not authenticated or invalid API key."}
        status_code = status.HTTP_403_FORBIDDEN

        if not authorization:
            logger.warning("Missing Authorization header", url=request.url.path)
            return JSONResponse(
                status_code=status_code,
                content=error_response_content
            )

        scheme, _, api_key = authorization.partition(" ")

        if scheme.lower() != "bearer":
            logger.warning("Invalid Authorization scheme", scheme=scheme, url=request.url.path)
            return JSONResponse(
                status_code=status_code,
                content=error_response_content
            )

        if api_key not in VALID_API_KEYS:
            logger.warning("Invalid API key provided", url=request.url.path)
            # Avoid logging the invalid key itself for security
            return JSONResponse(
                status_code=status_code,
                content=error_response_content
            )

        # If key is valid, proceed with the request
        logger.debug("Valid API key provided, proceeding.", url=request.url.path)
        response = await call_next(request)
        return response 