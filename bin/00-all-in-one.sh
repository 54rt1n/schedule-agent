#!/bin/bash
set -e

# Schedule Agent All-in-One Setup Script
# This script runs all setup steps in sequence

echo "=== Schedule Agent All-in-One Setup ==="
echo "This script will run all setup steps in sequence."
echo "Press CTRL+C at any time to cancel."
echo ""
echo "Steps to be executed:"
echo "1. Initialize project environment"
echo "2. Build container"
echo "3. Setup environment for container"
echo "4. Run container"
echo "5. Run tests"
echo ""
echo "Press ENTER to continue or CTRL+C to cancel..."
read -r

echo "=== Step 1: Initialize Project Environment ==="
bin/01-init.sh
echo ""

echo "=== Step 2: Build Container ==="
bin/02-build-container.sh
echo ""

echo "=== Step 3: Setup Environment for Container ==="
bin/03-setup-environment.sh
echo ""

echo "=== Step 4: Run Container ==="
bin/04-run-container.sh
echo ""

echo "=== Step 5: Run Tests ==="
bin/06-run-tests.sh
echo ""

echo "=== All-in-One Setup Complete ==="
echo "Schedule Agent is now set up and running."
echo "You can access the services at:"
echo "  A2A Server: http://localhost:8082"
echo "  MCP Server: http://localhost:8081"
echo ""
echo "Useful commands:"
echo "- View container logs: podman logs -f schedule-agent"
echo "- Stop container: podman stop schedule-agent"
echo "- Start container: podman start schedule-agent"
echo "- Remove container: podman rm schedule-agent"
echo ""
echo "Thank you for using Schedule Agent!" 