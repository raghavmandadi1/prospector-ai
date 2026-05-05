#!/bin/bash
# Quick-start for local development (no Docker required)
#
# Prerequisites:
#   pip install -r backend/requirements-dev.txt
#   cd frontend && npm install && cd ..
#
# This runs:
#   1. Backend (FastAPI + uvicorn) on port 8000
#   2. Frontend (Vite) on port 5173

set -e

# Ensure DEV_MODE is on
export DEV_MODE=true
export APP_ENV=development
export CORS_ORIGINS=http://localhost:5173

echo "Starting GeoProspector in dev mode..."
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""

# Start backend in background
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

# Start frontend
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

# Cleanup on exit
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
}
trap cleanup EXIT

echo "Both services running. Press Ctrl+C to stop."
wait
