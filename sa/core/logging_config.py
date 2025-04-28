# sa/core/logging_config.py
"""Logging configuration for the scheduling agent."""

import logging
import sys

import colorama
import structlog
from structlog.dev import Column, KeyValueColumnFormatter, LogLevelColumnFormatter # Import column formatters

# Import settings
from sa.core.settings import Settings


def setup_logging(settings: Settings):
    """Configure logging using structlog."""

    def add_pathline(_, __, event_dict):
        """
        Merge pathname and lineno into a single 'location' key for clickable file:line links.
        """
        pathname = event_dict.pop('pathname', None)
        lineno = event_dict.pop('lineno', None)
        if pathname and lineno is not None:
            location = f"({pathname}:{lineno})"
            event_dict['location'] = location
        return event_dict


    # Use settings.log_level
    log_level = settings.log_level.upper()
    try:
        level = logging.getLevelName(log_level)
        # Ensure level is an int, getLevelName can return string if name is invalid
        if not isinstance(level, int):
            level = logging.INFO
            log_level = "INFO"
    except ValueError:
        level = logging.INFO
        log_level = "INFO"

    # Timestamper, key_order, and other structlog processors
    shared_processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.CallsiteParameterAdder([
            structlog.processors.CallsiteParameter.PATHNAME,
            structlog.processors.CallsiteParameter.LINENO,
        ]),
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        #structlog.processors.StackInfoRenderer(),
        #structlog.dev.set_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_pathline,
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure the formatter for console output
    formatter = structlog.stdlib.ProcessorFormatter(
        # These run ONLY on entries destined for stdlib writers
        processor=structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=lambda sio, exc_info: None,
            columns=[
                Column("timestamp", KeyValueColumnFormatter(key_style=None, value_style=colorama.Fore.YELLOW, reset_style=colorama.Style.RESET_ALL, value_repr=str)),
                Column("level", LogLevelColumnFormatter(level_styles=structlog.dev.ConsoleRenderer.get_default_level_styles(), reset_style=colorama.Style.RESET_ALL)),
                Column("logger", KeyValueColumnFormatter(key_style=None, value_style=colorama.Style.BRIGHT + colorama.Fore.BLUE, reset_style=colorama.Style.RESET_ALL, value_repr=str, prefix="[", postfix="]")),
                Column("event", KeyValueColumnFormatter(key_style=None, value_style=colorama.Style.BRIGHT, reset_style=colorama.Style.RESET_ALL, value_repr=str)),
                Column("location", KeyValueColumnFormatter(key_style=None, value_style=colorama.Fore.CYAN, reset_style=colorama.Style.RESET_ALL, value_repr=str)),
                Column("", KeyValueColumnFormatter(key_style=colorama.Fore.CYAN, value_style=colorama.Fore.GREEN, reset_style=colorama.Style.RESET_ALL, value_repr=repr)),
            ]
        ),
        # Add foreign processors for messages not originating from structlog
        foreign_pre_chain=shared_processors,
    )

    # Configure the standard library root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Set up a handler writing to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Suppress overly verbose logs from libraries if needed
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    # Increase logging for SQLAlchemy engine and pool during debugging
    sa_engine_logger = logging.getLogger("sqlalchemy.engine.Engine")
    sa_engine_logger.setLevel(logging.WARNING)
    sa_engine_logger.propagate = False
    sa_engine_logger.addHandler(logging.NullHandler())
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

    logger = structlog.get_logger("sa")
    logger.setLevel(level)
    logger.info("Logging configured", log_level=log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger instance."""
    return structlog.get_logger(name)
