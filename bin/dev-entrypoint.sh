#!/bin/sh
set -e

cd /app/frontend
if [ ! -d node_modules ] || [ -z "$(ls -A node_modules 2>/dev/null)" ]; then
  npm ci
fi

export VITE_PROXY_TARGET="${VITE_PROXY_TARGET:-http://127.0.0.1:6575}"

npm run dev -- --host 0.0.0.0 --port 6574 &
vite_pid=$!

cd /app
uvicorn backend.main:app --host 0.0.0.0 --port 6575 --reload &
api_pid=$!

trap 'kill "$vite_pid" "$api_pid" 2>/dev/null; wait' INT TERM

wait "$vite_pid" "$api_pid"
