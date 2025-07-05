#!/bin/bash
set -e

# Schedule Agent Environment Setup Script
# This script sets up the environment for running the Schedule Agent container

echo "=== Schedule Agent Environment Setup ==="

# Check if podman or docker is installed
if command -v podman &> /dev/null; then
    CONTAINER_CMD="podman"
    echo "Using Podman for container operations."
elif command -v docker &> /dev/null; then
    CONTAINER_CMD="docker"
    echo "Using Docker for container operations."
else
    echo "Neither Podman nor Docker found. Please install one of them and try again."
    exit 1
fi

# Set up directories with appropriate permissions
echo "Setting up directories for container volumes..."

# Create directories if they don't exist
mkdir -p volume/{config,local}

# Ensure permissions are set correctly
# All directories under volume need to be writable by the container
chmod -R 777 volume

echo "Directories set up successfully."

# Check if SELinux is enabled
if command -v getenforce &> /dev/null && [ "$(getenforce)" != "Disabled" ]; then
    echo "SELinux is enabled, setting appropriate context for volume mounts..."
    # Set SELinux context for container volumes
    if command -v chcon &> /dev/null; then
        chcon -Rt container_file_t volume
        echo "SELinux context set for container volumes."
    else
        echo "Warning: chcon command not found, SELinux context not set."
    fi
fi

# Create a network for the container if it doesn't exist
NETWORK_NAME="schedule-agent-network"
if ! ${CONTAINER_CMD} network ls | grep -q ${NETWORK_NAME}; then
    echo "Creating container network: ${NETWORK_NAME}..."
    ${CONTAINER_CMD} network create ${NETWORK_NAME}
    echo "Network created."
else
    echo "Network ${NETWORK_NAME} already exists."
fi

# Set up container-compose file if using Docker
if [ "${CONTAINER_CMD}" = "docker" ] && [ -f "podman-compose.yml" ]; then
    echo "Creating docker-compose.yml from podman-compose.yml..."
    cp podman-compose.yml docker-compose.yml
    echo "docker-compose.yml created."
fi

echo "=== Environment Setup Complete ==="
echo "The environment is ready for running the Schedule Agent container."
echo "To run the container, execute: bin/04-run-container.sh" 