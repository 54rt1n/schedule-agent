# Schedule Agent

A production-ready FastAPI-based scheduling agent with both A2A (Agent-to-Agent) and MCP (Model Context Protocol) servers for calendar management.

## Quick Start

The easiest way to get Schedule Agent running is with our all-in-one setup script:

```bash
git clone <repository-url>
cd schedule-agent
./bin/00-all-in-one.sh
```

This script will:
1. Initialize the project environment
2. Build the container image  
3. Set up the environment
4. Run the container
5. Run tests to verify everything works

After completion, both servers will be available:
- **A2A Server**: `http://localhost:8082` (Agent-to-Agent communication)
- **MCP Server**: `http://localhost:8081` (Model Context Protocol)

## Manual Setup

If you prefer to run the setup steps individually:

```bash
# 1. Initialize project and install dependencies
./bin/01-init.sh

# 2. Build the container image
./bin/02-build-container.sh

# 3. Set up the container environment
./bin/03-setup-environment.sh

# 4. Run the container
./bin/04-run-container.sh

# 5. Check status and view logs
./bin/05-check-container.sh
./bin/09-view-logs.sh
```

## Configuration

The application is configured via `volume/config/app.env`. Key settings include:

```bash
# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///app/local/schedule_agent.db

# Server Configuration  
API_HOST=0.0.0.0 # For A2A endpoints
API_PORT=8082
MCP_HOST=0.0.0.0
MCP_PORT=8081
LOG_LEVEL=INFO

# Security (for production)
API_KEYS=your-api-key-1,your-api-key-2

# LLM Configuration
LLM_API_KEY=your-openai-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Agent Configuration
DEFAULT_TIMEZONE=UTC
AGENT_VERSION=0.1.0
```

## Container Management

- **View logs**: `./bin/09-view-logs.sh` (supports -f for follow, -n for line count)
- **Check status**: `./bin/05-check-container.sh`
- **Stop container**: `./bin/zy-shutdown.sh`  
- **Complete cleanup**: `./bin/zz-teardown.sh`

## Development

For local development without containers:

```bash
# Create virtual environment and install dependencies
python3.12 -m venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Run database migrations
alembic upgrade head

# Start the A2A server
uvicorn sa.api.__main__:create_app --factory --reload

# In another terminal, start the MCP server
python -m sa.mcp.__main__
```

## Testing

```bash
# Run all tests
./bin/06-run-tests.sh

# Run specific test types
./bin/06-run-tests.sh unit
./bin/06-run-tests.sh integration
```

## Architecture

The Schedule Agent provides two complementary interfaces:

### A2A Server (Port 8082)
- **FastAPI-based REST API**
- Agent-to-agent communication
- Event management and scheduling
- Calendar export capabilities

### MCP Server (Port 8081)  
- **Model Context Protocol implementation**
- Tool execution for LLMs
- Secure context management
- Structured tool interface

### Core Components
- **Database**: SQLite with SQLAlchemy/SQLModel (Alembic migrations)
- **LLM Integration**: Configurable OpenAI-compatible API support
- **Container**: Lean Python 3.12 container with supervisor process management

## Requirements

- Python 3.12+
- Docker or Podman
- uv (package manager) 