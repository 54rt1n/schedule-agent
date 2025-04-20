import logging
import sys
import structlog

# Import settings
from scheduling_agent.settings import settings

def setup_logging():
    """Configure logging using structlog."""

    # Use settings.log_level
    log_level = settings.log_level.upper()
    try:
        level = logging.getLevelName(log_level)
        # Ensure level is an int, getLevelName can return string if name is invalid
        if not isinstance(level, int):
             print(f"Invalid LOG_LEVEL '{settings.log_level}'. Defaulting to INFO.", file=sys.stderr)
             level = logging.INFO
             log_level = "INFO" # Update string version too
    except ValueError:
        print(f"Invalid LOG_LEVEL '{settings.log_level}'. Defaulting to INFO.", file=sys.stderr)
        level = logging.INFO
        log_level = "INFO" # Update string version too

    # Timestamper, key_order, and other structlog processors
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure the formatter for console output
    formatter = structlog.stdlib.ProcessorFormatter(
        # These run ONLY on entries destined for stdlib writers
        processor=structlog.dev.ConsoleRenderer(colors=True),
        # Add foreign processors for messages not originating from structlog
        foreign_pre_chain=shared_processors,
    )

    # Configure the standard library root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Set up a handler writing to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Suppress overly verbose logs from libraries if needed
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger = structlog.get_logger("scheduling_agent")
    logger.info("Logging configured", log_level=log_level)

def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger instance."""
    return structlog.get_logger(name)

# Example usage:
# from scheduling_agent.core.logging_config import setup_logging, get_logger
# setup_logging()
# logger = get_logger(__name__)
# logger.info("This is an info message", data={"key": "value"}) 