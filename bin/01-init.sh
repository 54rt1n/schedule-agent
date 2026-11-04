#!/bin/bash
set -e

# Schedule Agent Initialization Script
# This script sets up the project directory structure and dependencies

echo "=== Initializing Schedule Agent Project ==="

# Make the script executable
chmod +x bin/*.sh

# Create necessary directories if they don't exist
echo "Creating directory structure..."
mkdir -p volume/{config,local}

# Set permissions for container directories
# All directories under volume need to be writable by the container
chmod -R 777 volume

# Create app.env as a copy of default environment if it doesn't exist
if [ ! -f volume/config/app.env ]; then
    echo "Creating default app configuration..."
    cat > volume/config/app.env << EOF
# Schedule Agent Configuration
# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///app/local/schedule_agent.db

# Server Configuration
API_HOST=0.0.0.0
API_PORT=8082
MCP_HOST=0.0.0.0
MCP_PORT=8081
LOG_LEVEL=INFO

# Security Configuration (leave empty for development)
API_KEYS=

# LLM Configuration (configure as needed)
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Agent Configuration
DEFAULT_TIMEZONE=UTC
AGENT_VERSION=0.1.0
INCLUDE_VALIDATION_ERROR_DETAIL=false
RELOAD=false
EOF
    echo "App configuration created at volume/config/app.env"
fi

# Check for uv installation
if ! command -v uv &> /dev/null; then
    echo "Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "uv installed successfully"
fi

# Install dependencies using uv
echo "Installing dependencies..."
uv sync --all-packages --extra dev
echo "Dependencies installed"

# Check for Docker or Podman
if command -v podman &> /dev/null; then
    echo "Podman found, will use for container operations."
    CONTAINER_CMD="podman"
elif command -v docker &> /dev/null; then
    echo "Docker found, will use for container operations."
    CONTAINER_CMD="docker"
else
    echo "Warning: Neither Podman nor Docker found. You need to install one of them:"
    echo "  For Ubuntu/Debian: sudo apt install podman"
    echo "  For RHEL/Fedora: sudo dnf install podman"
    echo ""
fi

echo "=== Schedule Agent Project Initialized ==="
echo "You can now proceed to build the container with bin/02-build-container.sh" 