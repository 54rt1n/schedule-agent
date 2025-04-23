# scheduling_agent/mcp/server.py

import asyncio
import sys
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
# Remove AsyncEngine import, engine is managed in lifespan now
# from sqlalchemy.ext.asyncio import AsyncEngine

from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.db.database import (
    create_db_engine,
    create_session_factory,
    initialize_database,
)
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger # Keep get_logger
from scheduling_agent.settings import settings
from .handlers import register_handlers # Import from sibling handlers module

logger = get_logger("scheduling_agent.mcp.server")

# Module-level dictionary to hold shared state
_shared_state = {}

@asynccontextmanager
async def mcp_lifespan(app: FastMCP | None = None):
    """
    ASGI Lifespan: Manages DB engine and session factory via module state.
    """
    logger.info("MCP Lifespan: Startup phase starting...")
    db_engine = None # Ensure variable exists for finally block
    try:
        # 1. Create and store DB Engine
        logger.info("Lifespan: Initializing database engine...")
        db_engine = create_db_engine(settings.database_url)
        _shared_state["db_engine"] = db_engine

        # 2. Initialize DB Tables
        logger.info("Lifespan: Initializing database tables...")
        await initialize_database(db_engine)
        logger.info("Lifespan: Database initialized.")

        # 3. Create and store Session Factory
        logger.info("Lifespan: Creating session factory...")
        session_factory = create_session_factory(db_engine)
        _shared_state["session_factory"] = session_factory
        logger.info("Lifespan: Session factory created.")

        # Log completion
        logger.info("MCP Lifespan: Startup complete. Engine and factory are ready.")
        yield # Server runs here

    finally:
        # Clean up engine on shutdown
        logger.info("MCP Lifespan: Shutdown phase starting...")
        engine_to_dispose = _shared_state.pop("db_engine", None) # Pop engine
        _shared_state.pop("session_factory", None) # Pop factory too

        if engine_to_dispose:
            logger.info("Lifespan: Attempting to dispose database engine (timeout=5s)...")
            try:
                # Add timeout to the dispose call
                await asyncio.wait_for(engine_to_dispose.dispose(), timeout=5.0)
                logger.info("Lifespan: Database engine disposed successfully.")
            except asyncio.TimeoutError:
                logger.error("Lifespan: Database engine disposal timed out after 5 seconds!")
            except Exception as e:
                 logger.exception(f"Lifespan: Error during database engine disposal: {e}")
        else:
             logger.warning("Lifespan: No database engine found in shared state to dispose.")
        logger.info("MCP Lifespan: Shutdown complete.")


# Updated setup function - Much simpler
async def setup_mcp_server() -> FastMCP:
    """
    Initializes the MCP instance with lifespan manager and registers handlers.
    Services are created per-request/tool-call in handlers using the shared session factory.
    """
    logger.info("Setting up MCP Server instance with lifespan...")

    try:
        # Lifespan manages engine/factory startup/shutdown and DB init
        mcp = FastMCP("SchedulingAgentMCP", lifespan=mcp_lifespan)
        logger.info("FastMCP server instance created.")

        register_handlers(mcp=mcp)

        logger.info("MCP Server setup complete.")
        return mcp

    except Exception as e:
        logger.exception(f"Failed to set up MCP server instance: {e}")
        sys.exit(1) # Exit if setup fails 