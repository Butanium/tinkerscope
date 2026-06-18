#!/bin/bash
# tinkerscope launcher.
#
# Two modes:
#   dev (default)  — backend on a fixed port + the vite dev server (HMR) whose
#                    /api proxy targets that port. Both cleaned up on exit.
#   packaged       — build the web UI, then serve API + built UI from ONE
#                    process (no vite). Triggered by --build / --prod.
#
# Scan dirs are passed positionally (default: cwd). The backend entrypoint
# (`tinkerscope`) handles port auto-pick, the instance registry, and idempotent
# coexistence with other running instances — so we do NOT kill ports here. We do
# pin an explicit dev port so vite's proxy (web/vite.config.ts) has a fixed
# target.
#
# Usage:
#   ./run.sh [DIR ...]                 # dev: backend + vite
#   ./run.sh --build [DIR ...]         # packaged: build + single process
#   ./run.sh --prod  [DIR ...]        # alias for --build
#   DEV_BACKEND_PORT=8770 ./run.sh …   # override the dev backend/proxy port

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
WEB="$DIR/web"

# vite.config.ts proxies /api to 127.0.0.1:8765 by default — match it so the
# dev frontend talks to our backend without editing the proxy config.
DEV_BACKEND_PORT="${DEV_BACKEND_PORT:-8765}"

MODE="dev"
DIRS=()
for arg in "$@"; do
    case "$arg" in
        --build|--prod) MODE="packaged" ;;
        --dev)          MODE="dev" ;;
        -h|--help)
            sed -n '2,21p' "$0"
            exit 0
            ;;
        *)              DIRS+=("$arg") ;;
    esac
done

# Default scan root = cwd (the project you launched from).
if [ "${#DIRS[@]}" -eq 0 ]; then
    DIRS=("$(pwd)")
fi
echo "Scanning: ${DIRS[*]}"

# ── Node on PATH (vite needs node >= 18) ─────────────────────────────
NVM_NODE="$HOME/.nvm/versions/node/v22.20.0/bin"
if [ -d "$NVM_NODE" ]; then
    export PATH="$NVM_NODE:$PATH"
fi

poll_health() {
    # Poll a backend's /api/health until ready or the process dies / times out.
    local port="$1" pid="$2"
    local url="http://127.0.0.1:${port}/api/health"
    for _ in $(seq 1 150); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "Backend exited before becoming ready." >&2
            return 1
        fi
        if curl -fsS "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.2
    done
    echo "Backend did not become ready in time." >&2
    return 1
}

# ─────────────────────────────────────────────────────────────────────
# Packaged / single-process mode
# ─────────────────────────────────────────────────────────────────────
if [ "$MODE" = "packaged" ]; then
    if [ ! -d "$WEB/node_modules" ]; then
        echo "Installing npm dependencies..."
        ( cd "$WEB" && npm install )
    fi
    echo "Building web UI..."
    ( cd "$WEB" && npm run build )
    echo "Serving API + built UI from one process..."
    # No explicit --port: the entrypoint auto-picks a free port and prints the
    # URL, and coexists with any other running instance.
    exec uv run tinkerscope "${DIRS[@]}"
fi

# ─────────────────────────────────────────────────────────────────────
# Dev mode: backend + vite, both cleaned up on exit
# ─────────────────────────────────────────────────────────────────────
BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
    [ -n "$BACKEND_PID" ]  && kill "$BACKEND_PID"  2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Start the backend on the fixed dev port the vite proxy targets.
echo "Starting backend on :${DEV_BACKEND_PORT}..."
uv run tinkerscope "${DIRS[@]}" --port "$DEV_BACKEND_PORT" &
BACKEND_PID=$!

echo "Waiting for backend readiness..."
if ! poll_health "$DEV_BACKEND_PORT" "$BACKEND_PID"; then
    exit 1
fi
echo "Backend ready: http://127.0.0.1:${DEV_BACKEND_PORT}"

# Install npm deps if needed, then start vite (its /api proxy → the backend).
if [ ! -d "$WEB/node_modules" ]; then
    echo "Installing npm dependencies..."
    ( cd "$WEB" && npm install )
fi
echo "Starting vite dev server..."
( cd "$WEB" && npm run dev ) &
FRONTEND_PID=$!

echo ""
echo "tinkerscope (dev) running:"
echo "  Frontend (vite): check the vite output above for the URL"
echo "  Backend:         http://127.0.0.1:${DEV_BACKEND_PORT}"
echo "  Drive it:        tinkpg ls | tinkpg open <run> | tinkpg chat <run> \"...\""
echo ""

# Exit (and trigger cleanup) as soon as either child dies.
wait -n "$BACKEND_PID" "$FRONTEND_PID"
