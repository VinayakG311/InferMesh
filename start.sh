#!/bin/bash
# start.sh — Start two proxy nodes and the router in one go.
#
# What this runs:
#   Node A  → proxy on :8000  (Ollama on :11434)
#   Node B  → proxy on :8001  (Ollama on :11435)
#   Router  → port :9000, routes between node-a and node-b
#
# NODE_ID and PROXY_URL tell the gossip layer who this router is
# and what address to broadcast to the network.
#
# Prerequisites:
#   Terminal 1: OLLAMA_HOST=0.0.0.0:11434 ollama serve
#   Terminal 2: OLLAMA_HOST=0.0.0.0:11435 ollama serve
#   pip install -r requirements.txt
#
# Usage:
#   chmod +x start.sh && ./start.sh
# Stop: Ctrl+C

set -e
trap "echo 'Stopping...'; kill 0" EXIT

echo "Starting Node A  (proxy :8000 → ollama :11434)"
NODE_ID=node-a \
OLLAMA_URL=http://localhost:11434 \
uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning &

echo "Starting Node B  (proxy :8001 → ollama :11435)"
NODE_ID=node-b \
OLLAMA_URL=http://localhost:11435 \
uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level warning &

sleep 2

echo "Starting Router  (:9000)"
NODE_ID=node-a \
PROXY_URL=http://localhost:8000 \
uvicorn router.main:app --host 0.0.0.0 --port 9000 --log-level info &

echo ""
echo "  Node A  → http://localhost:8000"
echo "  Node B  → http://localhost:8001"
echo "  Router  → http://localhost:9000"
echo "  Peers   → http://localhost:9000/peers"
echo ""
echo "Press Ctrl+C to stop everything."

wait