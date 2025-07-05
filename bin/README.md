# Schedule Agent Scripts

This directory contains utility scripts for the Schedule Agent project. These scripts help with setting up the development environment, building and running the container, and running tests.

## Available Scripts

### 00-all-in-one.sh

A convenience script that runs all setup steps in sequence to set up the Schedule Agent project. This includes:
1. Initializing the project environment
2. Building the container
3. Setting up the environment for the container
4. Running the container
5. Running tests

Usage:
```bash
bin/00-all-in-one.sh
```

### 01-init.sh

Initializes the Schedule Agent project by:
- Creating necessary directories
- Setting up a default configuration
- Installing dependencies using uv
- Checking for container tools (Podman/Docker)

Usage:
```bash
bin/01-init.sh
```

### 02-build-container.sh

Builds the container image for Schedule Agent using Podman or Docker.

Usage:
```bash
bin/02-build-container.sh
```

### 03-setup-environment.sh

Sets up the environment for running the Schedule Agent container, including:
- Creating necessary directories with appropriate permissions
- Checking for SELinux status and setting appropriate context
- Creating a container network

Usage:
```bash
bin/03-setup-environment.sh
```

### 04-run-container.sh

Runs the Schedule Agent container with both A2A (port 8000) and MCP (port 8001) servers using either Docker Compose or a direct container run command.

Usage:
```bash
bin/04-run-container.sh
```

### 05-check-container.sh

Checks the status of the Schedule Agent container and displays detailed information including logs if the container is not running properly.

Usage:
```bash
bin/05-check-container.sh
```

### 06-run-tests.sh

Runs tests for the Schedule Agent project, with options for running unit tests, integration tests, or all tests, and optionally generating coverage reports.

Usage:
```bash
bin/06-run-tests.sh [unit|integration|all]
```

### 09-view-logs.sh

Provides various options for viewing Schedule Agent logs with filtering and live tail capabilities.

Usage:
```bash
bin/09-view-logs.sh [options]
```

Options:
- `-f, --follow`: Follow log output (live tail)
- `-n, --lines NUM`: Show last NUM lines
- `-s, --since TIME`: Show logs since timestamp
- `-t, --timestamps`: Show timestamps
- `-h, --help`: Show help message

Examples:
```bash
bin/09-view-logs.sh           # Show all logs
bin/09-view-logs.sh -f        # Follow logs in real-time
bin/09-view-logs.sh -n 50     # Show last 50 lines
bin/09-view-logs.sh -f -t     # Follow logs with timestamps
bin/09-view-logs.sh -s 1h     # Show logs from last hour
```

### zy-shutdown.sh

Safely shuts down the Schedule Agent container without removing it.

Usage:
```bash
bin/zy-shutdown.sh
```

### zz-teardown.sh

Completely removes the container, network, and images. Use with caution.

Usage:
```bash
bin/zz-teardown.sh
```

## General Usage

For a first-time setup, you can either run the all-in-one script:

```bash
bin/00-all-in-one.sh
```

Or run the scripts individually in sequence:

```bash
bin/01-init.sh
bin/02-build-container.sh
bin/03-setup-environment.sh
bin/04-run-container.sh
bin/05-check-container.sh     # Check container status
bin/06-run-tests.sh           # Run tests
bin/09-view-logs.sh           # View container logs (optional)
```

## Requirements

- Python 3.12+
- Docker or Podman
- uv (for package management)

## Configuration

The scripts use the configuration file at `volume/config/app.env` for container settings. You can modify this file to change database connections, API keys, and other settings. 