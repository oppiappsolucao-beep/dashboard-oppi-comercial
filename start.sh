#!/bin/sh
set -e
PORT="${PORT:-8501}"
echo "Starting Dashboard Oppi Comercial (FastAPI) on 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers
