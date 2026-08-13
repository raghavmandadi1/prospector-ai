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

# --no-cache forces every cell through the LLM instead of reading
# data/cache/cells.sqlite. Use it when measuring the benchmark's noise floor —
# cached runs are deterministic by construction and would report a floor of zero.
if [ "$1" = "--no-cache" ]; then
    export CACHE_ENABLED=false
    echo "Cell cache DISABLED — every cell will be scored by the LLM"
fi

echo "Starting GeoProspector in dev mode..."
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""

# Start backend in background
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

# Wait for the backend to actually answer before starting the frontend.
#
# Without this the script reports success no matter what. `set -e` does not fire
# for background jobs, and under --reload uvicorn's *reloader* survives an import
# error in the app while the worker subprocess dies — so $BACKEND_PID stays alive
# and looks healthy. The only symptom was ECONNREFUSED in the Vite proxy log,
# which reads like a frontend problem and is not one. Poll /health instead.
echo -n "Waiting for backend"
for i in $(seq 1 40); do
    if curl -sf -o /dev/null http://localhost:8000/health; then
        echo " — up"
        break
    fi
    if [ "$i" = "40" ]; then
        echo ""
        echo "Backend failed to start within 20s. The traceback is above this line."
        kill $BACKEND_PID 2>/dev/null
        exit 1
    fi
    echo -n "."
    sleep 0.5
done

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
