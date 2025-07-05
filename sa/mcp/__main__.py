# sa/mcp/__main__.py
"""Main entry point for the Scheduling Agent MCP server."""

import asyncio
import sys

from sa.core.logging_config import setup_logging, get_logger

from sa.core.settings import Settings
from sa.mcp.server import setup_mcp_server
from mcp.server.fastmcp import FastMCP

logger = get_logger(__name__)

# Make main async
async def main(settings: Settings):
    logger.info("Starting Scheduling Agent MCP...")

    # --- Run Async Setup Once ---
    mcp_instance: FastMCP | None = None
    try:
        mcp_instance = await setup_mcp_server(settings)
        if not mcp_instance:
             logger.critical("MCP instance setup failed. Exiting.")
             sys.exit(1)
    except Exception as setup_err:
        logger.exception(f"Failed during initial setup: {setup_err}")
        sys.exit(1)

    # --- Argument Parsing ---
    host = settings.server_host
    port = settings.server_port
    test_mode = "--test-mode" in sys.argv

    # --- Configure Server Settings ---
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                logger.warning(f"Invalid port specified: {sys.argv[i+1]}. Using default {port}.")
        elif arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]

    if hasattr(mcp_instance, 'settings'):
        mcp_instance.settings.host = host
        mcp_instance.settings.port = port
        logger.info(f"MCP settings configured: host={host}, port={port}")
    else:
        logger.warning("MCP instance does not have a 'settings' attribute to configure host/port.")

    # --- Run Server ---
    transport = "stdio" if test_mode else "sse"
    logger.info(f"Starting SchedulingAgentMCP server on {host}:{port} using {transport} transport...")

    exit_code = 0
    try:
        if transport == "stdio":
            logger.info("Running MCP server with stdio transport...")
            await mcp_instance.run_stdio_async()
        else: # sse
            logger.info("Running MCP server with sse transport...")
            await mcp_instance.run_sse_async()

        logger.info("Server run method completed normally.")

    except Exception as e:
        logger.exception(f"Server exited unexpectedly: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
        exit_code = 1

    logger.info(f"Main coroutine finished. Exiting with code {exit_code}.")

# --- Entry Point Check ---
if __name__ == "__main__":
    settings = Settings()
    setup_logging(settings)
    try:
        asyncio.run(main(settings))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught in top level. Exiting.")
