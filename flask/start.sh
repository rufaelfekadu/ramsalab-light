#!/bin/bash

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Get environment variables with defaults
FLASK_PORT=${FLASK_PORT:-5000}
FLASK_ENV=${FLASK_ENV:-production}
WORKERS=${GUNICORN_WORKERS:-4}
TIMEOUT=${GUNICORN_TIMEOUT:-120}

log "Starting application initialization..."

# Wait for database to be ready (with retry logic)
log "Waiting for database connection..."
max_attempts=30
attempt=0

while [ $attempt -lt $max_attempts ]; do
    #warn "yo"
    #python -c 'from app.database import db; from app import create_app; app = create_app(); app.app_context().push(); db.engine.connect()'
    if python -c "from app.database import db; from app import create_app; app = create_app(); app.app_context().push(); db.engine.connect()" 2>/dev/null; then
        log "Database connection established"
        break
    fi
    attempt=$((attempt + 1))
    if [ $attempt -eq $max_attempts ]; then
        error "Failed to connect to database after $max_attempts attempts"
        exit 1
    fi
    warn "Database not ready, retrying... ($attempt/$max_attempts)"
    sleep 2
done

# Run database migrations
log "Running database migrations..."
if ! alembic upgrade head; then
    error "Database migration failed"
    exit 1
fi
log "Database migrations completed successfully"

# Check if database reset is requested
RESET_DB=${RESET_DB:-false}
SHOULD_RESET=false
if [ "$RESET_DB" = "true" ] || [ "$RESET_DB" = "1" ]; then
    SHOULD_RESET=true
    log "RESET_DB is enabled - clearing database..."
    if ! python clear_db.py; then
        error "Database reset failed"
        exit 1
    fi
    log "Database cleared successfully"
fi

# Check if database needs population
log "Checking if database needs initial data..."
DB_HAS_DATA=false
if python -c "from app.database import db; from app.models import Question; from app import create_app; app = create_app(); app.app_context().push(); exit(0 if Question.query.first() else 1)" 2>/dev/null; then
    DB_HAS_DATA=true
fi

# Populate database if it's empty or if reset was requested
if [ "$SHOULD_RESET" = "true" ] || [ "$DB_HAS_DATA" = "false" ]; then
    if [ "$SHOULD_RESET" = "true" ]; then
        log "Populating database with initial data after reset..."
    else
        log "Database is empty, populating with initial data..."
    fi
    if ! python populate_db.py; then
        error "Database population failed"
        exit 1
    fi
    log "Database population completed successfully"
else
    log "Database already contains data, skipping population"
fi

# Start the application
log "Starting application server..."
if [ "$FLASK_ENV" = "production" ]; then
    log "Running in production mode with Gunicorn (${WORKERS} workers)"
    exec gunicorn \
        --bind "0.0.0.0:${FLASK_PORT}" \
        --workers "${WORKERS}" \
        --timeout "${TIMEOUT}" \
        --access-logfile "access.log" \
        --error-logfile "error.log" \
        --log-level info \
        --preload \
        run:app
else
    log "Running in development mode"
    exec python run.py
fi

