#!/bin/sh
set -e
PORT="${PORT:-8501}"
echo "Starting Oppi CRM Comercial (Streamlit) on 0.0.0.0:${PORT}"
exec streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port="${PORT}" \
  --browser.gatherUsageStats=false
