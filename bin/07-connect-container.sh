#!/bin/bash
# Connect to running schedule-agent container with interactive shell

set -e

CONTAINER_NAME="schedule-agent"

echo "🔗 Connecting to $CONTAINER_NAME container..."

# Check if container is running
if ! podman ps --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ Container '$CONTAINER_NAME' is not running"
    echo ""
    echo "To start the container, run:"
    echo "  ./bin/04-run-container.sh"
    echo ""
    echo "To check container status, run:"
    echo "  ./bin/05-check-container.sh"
    exit 1
fi

echo "✅ Container is running. Opening interactive shell..."
echo "💡 Type 'exit' to return to host shell"
echo ""

# Connect to the container with interactive bash
exec podman exec -it "$CONTAINER_NAME" bash 