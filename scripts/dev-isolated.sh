#!/bin/bash
# Launch an ISOLATED tinkerscope instance for dev / verification work.
#
# The standing rule (see CLAUDE.md "Build / verify"): NEVER test against the
# user's live instance or its state — test against a copy. This script makes
# that a one-liner: it copies the real state home (conversations, prefs,
# highlights, pins — so the instance has realistic fixtures) into a throwaway
# XDG_STATE_HOME, strips the instance registry (so `tinkpg` discovery inside
# the isolated env can't resolve to the live server), and launches from this
# checkout via `uv run`. Web UI served from this checkout's `web/dist` — run
# `npm run build` in web/ first if you changed the frontend.
#
# Usage:
#   scripts/dev-isolated.sh [--port N] [--state-src DIR] [--fresh] [SCAN_DIR ...]
#
#   SCAN_DIR ...   scan roots (default: cwd). Use the same roots as the live
#                  instance to inherit its conversations (state is keyed by a
#                  hash of the scan roots — the copy carries every key).
#   --port N       fixed port (default: the entrypoint auto-picks a free one).
#   --state-src D  state home to snapshot (default: $XDG_STATE_HOME or
#                  ~/.local/state, + /tinkerscope). The source is only READ.
#   --fresh        start with EMPTY state instead of a snapshot.
#
# The isolated state dir is printed on startup and left in /tmp for inspection.
# Runs in the foreground (agents: use run_in_background), Ctrl-C to stop.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PORT=""
STATE_SRC="${XDG_STATE_HOME:-$HOME/.local/state}/tinkerscope"
FRESH=0
DIRS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --port)      PORT="$2"; shift 2 ;;
        --state-src) STATE_SRC="$2"; shift 2 ;;
        --fresh)     FRESH=1; shift ;;
        -h|--help)   sed -n '2,26p' "$0"; exit 0 ;;
        *)           DIRS+=("$1"); shift ;;
    esac
done
if [ "${#DIRS[@]}" -eq 0 ]; then
    DIRS=("$(pwd)")
fi

ISO="$(mktemp -d /tmp/tscope-iso-XXXXXX)"
mkdir -p "$ISO/tinkerscope"
if [ "$FRESH" -eq 0 ] && [ -d "$STATE_SRC" ]; then
    cp -r "$STATE_SRC/." "$ISO/tinkerscope/"
    # The live server's registry + locks must not leak into the sandbox.
    rm -f "$ISO/tinkerscope/instances.json" "$ISO/tinkerscope"/*.lock
fi

echo "isolated state home: $ISO  (snapshot of: ${STATE_SRC})"
echo "scan roots:          ${DIRS[*]}"

PORT_ARGS=()
[ -n "$PORT" ] && PORT_ARGS=(--port "$PORT")
cd "$ROOT"
exec env XDG_STATE_HOME="$ISO" uv run tinkerscope "${PORT_ARGS[@]}" "${DIRS[@]}"
