# packages/schedule-mcp/src/schedule_mcp/server.py
# Copyright (C) 2025 Martin Bukowski

"""Server for the Scheduling Agent MCP."""

import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from schedule_core.core.logging_config import get_logger
from schedule_core.core.settings import Settings
from schedule_core.db.service import DatabaseService

from schedule_mcp.handlers import register_handlers

logger = get_logger(__name__)

async def setup_mcp_server(settings: Settings) -> FastMCP:
    """
    Initializes the MCP instance with lifespan manager and registers handlers.
    Services are created per-request/tool-call in handlers using the shared session factory.
    """
    try:
        logger.info("Setting up MCP Server instance with lifespan...")
        db_service = DatabaseService.from_settings(settings)

        await db_service.initialize()

        @asynccontextmanager
        async def mcp_lifespan(app: FastMCP):
            """
            ASGI Lifespan: Manages DB engine and session factory via module state.
            """
            logger.info("MCP Lifespan: Startup phase starting...")
            try:
                # 1. Create and store DB Engine
                logger.info("Lifespan: Initializing database engine...")

                # 2. Initialize DB Tables
                logger.info("Lifespan: Initializing database tables...")
                await db_service.initialize()
                logger.info("Lifespan: Database initialized.")

                yield

            finally:
                # Clean up engine on shutdown
                logger.info("MCP Lifespan: Shutdown phase starting...")

                if db_service:
                    logger.info("Lifespan: Attempting to dispose database service...")
                    try:
                        await db_service.close()
                        logger.info("Lifespan: Database service disposed successfully.")
                    except Exception as e:
                        logger.exception(f"Lifespan: Error during database service disposal: {e}")
                else:
                    logger.warning("Lifespan: No database service found in shared state to dispose.")
                logger.info("MCP Lifespan: Shutdown complete.")

        # Lifespan manages engine/factory startup/shutdown and DB init
        mcp = FastMCP("SchedulingAgentMCP", lifespan=mcp_lifespan)
        logger.info("FastMCP server instance created.")

        register_handlers(
            mcp=mcp,
            settings=settings,
            db_service=db_service,
        )

        logger.info("MCP Server setup complete.")
        return mcp

    except Exception as e:
        logger.exception(f"Failed to set up MCP server instance: {e}")
        sys.exit(1)