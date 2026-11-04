#!/bin/bash
set -e

# Schedule Agent Container Startup Script
# This script handles database migrations and starts the FastAPI server

echo "=== Schedule Agent Container Startup ==="
echo "Starting up schedule-agent container..."

# Set defaults if not provided
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:////app/local/schedule_agent.db}"
export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8082}"
export MCP_HOST="${MCP_HOST:-0.0.0.0}"
export MCP_PORT="${MCP_PORT:-8081}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

echo "Configuration:"
echo "  DATABASE_URL: $DATABASE_URL"
echo "  A2A Server: $API_HOST:$API_PORT"
echo "  MCP Server: $MCP_HOST:$MCP_PORT"
echo "  LOG_LEVEL: $LOG_LEVEL"

# Clear logs on startup
echo "Clearing logs..."
LOCAL_DIR="/app/local"
LOG_DIR="$LOCAL_DIR"
if [ -d "$LOG_DIR" ]; then
    # Clear existing log files
    rm -f "$LOG_DIR"/*.log
    rm -f "$LOG_DIR"/supervisord.pid
    echo "✅ Cleared log files from $LOG_DIR"
else
    echo "Log directory $LOG_DIR does not exist - will be created"
fi

# Check if database file exists and show info
DB_FILE="$LOCAL_DIR/schedule_agent.db"
echo "Checking database state..."
if [ -f "$DB_FILE" ]; then
    echo "Database file exists: $(ls -la $DB_FILE)"
else
    echo "Database file does not exist - alembic will create it"
fi

echo "Checking database and alembic setup..."

# Debug the URL configuration
echo "Debugging database URL configuration..."
python -c "
from schedule_core.core.settings import Settings
settings = Settings()
print(f'Async URL (from env): {settings.database_url}')
print(f'Sync URL (for alembic): {settings.basic_database_url}')
"

# Initialize database with alembic (let it create the database file)
echo "Running alembic migrations..."
if alembic upgrade head; then
    echo "✅ Database migrations completed successfully"
else
    echo "❌ Alembic migrations failed"
    echo "🛑 ALEMBIC ERROR - Suspending container for debugging..."
    echo "   Container will stay alive. Use 'podman exec -it schedule-agent bash' to debug."
    echo "   Try running 'alembic current' and 'alembic upgrade head' manually."
    sleep infinity
fi

# Step 3: Start both A2A and MCP servers using supervisor
echo "Starting A2A and MCP servers..."
exec supervisord -c /app/scripts/supervisord.conf 