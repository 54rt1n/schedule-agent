FROM python:3.12-slim

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH=/app/.venv/bin:$PATH

# Install system dependencies (minimal set)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # System essentials
    ca-certificates \
    curl \
    # Development tools needed for some Python packages
    build-essential \
    # Version control (might be needed for some dependencies)
    git \
    # Process management
    supervisor \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -s /root/.cargo/bin/uv /usr/local/bin/uv

# Create app directory and set working directory
WORKDIR /app

# Copy dependency files, LICENSE, and README (needed for package build)
COPY pyproject.toml uv.lock LICENSE README.md ./
COPY packages/ ./packages/

# Setup Python virtual environment using uv
RUN python3.12 -m venv .venv && \
    /app/.venv/bin/pip install --upgrade pip setuptools wheel && \
    /app/.venv/bin/pip install uv

# Install dependencies (production only, not dev)
RUN /app/.venv/bin/uv sync --frozen --no-dev --all-packages

# Copy alembic configuration
COPY alembic.ini ./

# Copy application code

# Copy startup script and supervisor config
COPY scripts/startup.sh scripts/supervisord.conf ./scripts/
RUN chmod +x scripts/startup.sh

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose both A2A and MCP server ports
EXPOSE 8082 8081

# Use our startup script that handles migrations and starts both servers
CMD ["./scripts/startup.sh"] 