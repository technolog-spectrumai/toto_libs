#!/bin/bash

# Load environment variables from .env if present
if [ -f .env ]; then
    echo "📦 Loading environment variables from .env"
    export $(grep -v '^#' .env | xargs)
fi

# Explicitly set REDIS_HOST for local dev
export REDIS_HOST=localhost
echo "🌐 REDIS_HOST set to $REDIS_HOST"

# Enable Redis memory overcommit
echo "🔧 Setting vm.overcommit_memory = 1"
sudo sysctl vm.overcommit_memory=1
echo "vm.overcommit_memory = 1" | sudo tee -a /etc/sysctl.conf > /dev/null

# Start Redis server
echo "🔁 Starting Redis..."
redis-server > redis.log 2>&1 &
REDIS_PID=$!

# Start Celery worker
echo "🧵 Starting Celery worker..."
celery -A toto worker --loglevel=info > celery_worker.log 2>&1 &
WORKER_PID=$!

# Start Celery beat
echo "⏰ Starting Celery beat..."
celery -A toto beat --loglevel=info > celery_beat.log 2>&1 &
BEAT_PID=$!

# Show process IDs
echo "✅ Redis PID: $REDIS_PID"
echo "✅ Celery Worker PID: $WORKER_PID"
echo "✅ Celery Beat PID: $BEAT_PID"

# Trap Ctrl+C to clean up
trap cleanup SIGINT

cleanup() {
    echo "🧹 Shutting down..."
    kill $REDIS_PID $WORKER_PID $BEAT_PID
    wait
    echo "✅ All services stopped."
    exit 0
}

# Wait for background processes
wait
