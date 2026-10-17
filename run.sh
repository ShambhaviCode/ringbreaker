#!/usr/bin/env bash
# RingBreaker helper.  Usage: ./run.sh <command>
#   up      start FalkorDB (Docker; falls back to falkordblite if Docker isn't running)
#   seed    generate synthetic data and load both banks
#   demo    run the 3 scenarios in the terminal
#   app     start the web UI (http://localhost:8501)
#   mcp     run the MCP server on stdio (for Claude Desktop / other MCP clients)
#   test    run the test suite
#   all     up + seed + app
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}

up() {
  if $PY -c "import config; config.connect().list_graphs()" 2>/dev/null; then
    echo "FalkorDB is already running on port 6379."; return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker compose up -d
    echo "FalkorDB running. Browser: http://localhost:3000"
  else
    echo "Docker isn't running; starting FalkorDB from the falkordblite package instead."
    $PY -m pip install -q falkordblite
    BIN=$($PY -c "import redislite, os; print(os.path.join(os.path.dirname(redislite.__file__), 'bin'))")
    mkdir -p .fdb
    "$BIN/redis-server" --port 6379 --loadmodule "$BIN/falkordb.so" --dir "$PWD/.fdb" --daemonize yes
    echo "FalkorDB running on localhost:6379 (no Browser UI in this mode)."
  fi
  for i in $(seq 1 20); do
    $PY -c "import config; config.connect().list_graphs()" 2>/dev/null && return 0
    sleep 1
  done
  echo "FalkorDB did not answer on port 6379"; exit 1
}

case "${1:-}" in
  up)   up ;;
  seed) $PY -m data.generate && $PY -m graph.load ;;
  demo) $PY -m scripts.demo --reset ;;
  app)  $PY -m streamlit run app/streamlit_app.py ;;
  mcp)  $PY -m mcp_server.server ;;
  test) $PY -m pytest -q ;;
  all)  up; $PY -m data.generate && $PY -m graph.load; $PY -m streamlit run app/streamlit_app.py ;;
  *)    sed -n '2,10p' "$0"; exit 1 ;;
esac
