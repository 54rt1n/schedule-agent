from contextlib import asynccontextmanager
from fastapi import FastAPI
from scheduling_agent.api.routes import well_known, rpc
from scheduling_agent.logging_config import setup_logging

# Configure logging early
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # TODO: Initialize resources like DatabaseService connection pool
    print("Starting up and initializing resources...")
    yield
    # TODO: Clean up resources like closing DB connections
    print("Shutting down and cleaning up resources...")

app = FastAPI(
    title="A2A Scheduling Agent",
    version="0.1.0",
    description="An A2A Agent for scheduling and calendar management.",
    lifespan=lifespan,
)

# Include routers
app.include_router(well_known.router, prefix="/.well-known", tags=["Well-Known"])
app.include_router(rpc.router, prefix="/api/rpc", tags=["JSON-RPC"]) # Using a specific path for RPC

@app.get("/", tags=["Status"])
async def read_root():
    """Basic endpoint to check if the API is running."""
    return {"message": "A2A Scheduling Agent is running."}

# TODO: Add Centralized Error Handling (Phase 1, Task 3)
# @app.exception_handler(...)

# TODO: Set up Dependency Injection (Phase 1, Task 1)
# See api/dependencies.py 