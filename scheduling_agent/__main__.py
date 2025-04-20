import uvicorn

from scheduling_agent.api.main import app
from scheduling_agent.logging_config import get_logger  # Import logger
from scheduling_agent.settings import settings

# No need for explicit logging setup here, should be handled by logging_config and lifespan

logger = get_logger(__name__)

if __name__ == "__main__":
    logger.info(
        f"Starting server on {settings.server_host}:{settings.server_port} with log level {settings.log_level}",
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level,
    )
    uvicorn.run(
        app,  # Reference the FastAPI app instance
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),  # uvicorn expects lower case log level
        # Add other uvicorn options as needed, e.g., reload=True for development
    )
