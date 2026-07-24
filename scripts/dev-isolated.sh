#!/bin/bash
# Launch an ISOLATED tinkerscope instance for dev / verification work.
#
# The standing rule (see CLAUDE.md "Build / verify"): NEVER test against the
# user's live instance or its state — test against a copy. This script makes
# that a one-liner: it copies the real state home (workspaces, prefs,
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
#                  instance to inherit its workspaces (state is keyed by a
#                  hash of the scan roots — the copy carries every key).
#   --port N       fixed port (default: the entrypoint auto-picks a free one).
#   --state-src D  state home to snapshot (default: $XDG_STATE_HOME or
#                  ~/.local/state, + /tinkerscope). The source is only READ.
#   --fresh        start with EMPTY state instead of a snapshot.
#
# The isolated state dir goes in **/var/tmp, never /tmp** — and is printed on
# startup, left there for post-mortem inspection AFTER the run. Both halves of
# that are deliberate:
#
#   - A snapshot is a full copy of the state home (~1.6 GB with a real workspace
#     store). `/tmp` on this box is a 15 GB tmpfs — RAM, not disk — so parking
#     GB-scale files there is a category error: /tmp is for small short-lived
#     scratch, /var/tmp is for exactly this (bigger, wants to outlive the run,
#     disk-backed on the local NVMe here). Writing 1.6 GB to that NVMe costs well
#     under a second, so nothing is lost by not using RAM.
#   - Each launch first REAPS every older snapshot no live process is using,
#     keeping only the newest for inspection. Nothing else cleans up after us:
#     this devbox is deliberately never rebooted, so the usual "/tmp is cleared
#     on reboot" contract never fires, and systemd's cleaners are far slower
#     (10 days for /tmp, 30 for /var/tmp) than 1.6 GB per smoke run accumulates.
#
# The cost of getting this wrong, once: on 2026-07-24 eleven abandoned snapshots
# from three days of sessions filled the tmpfs to 13/16 GB. Writes then failed
# with EDQUOT, which surfaced as *every* shell command exiting 1 with no output
# (bash could not write its own cwd file) and looked for an hour like a broken
# harness. Keep the reap, and keep this out of /tmp.
#
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
        -h|--help)   sed -n '2,46p' "$0"; exit 0 ;;
        *)           DIRS+=("$1"); shift ;;
    esac
done
if [ "${#DIRS[@]}" -eq 0 ]; then
    DIRS=("$(pwd)")
fi

# Snapshots live here — /var/tmp (disk), NOT /tmp (tmpfs/RAM). See the header.
ISO_DIR=/var/tmp

# ── reap older snapshots (see the header — this is not tidiness) ──────────────
# In-use = some live process has this dir as its XDG_STATE_HOME, which is exactly
# how we launch below. Reading /proc/<pid>/environ is precise where a time-based
# heuristic would eventually delete a long-running instance's state under it.
reap_stale_snapshots() {
    local in_use=() d u freed=0
    local pid environ
    for pid in $(pgrep -f 'tinkerscope' 2>/dev/null); do
        environ="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep '^XDG_STATE_HOME=' || true)"
        [ -n "$environ" ] && in_use+=("${environ#XDG_STATE_HOME=}")
    done
    # Newest first; keep [0] for post-mortem inspection, consider the rest.
    # Sweep BOTH dirs so snapshots left by a pre-2026-07-24 checkout still get reaped.
    for d in $(ls -dt "$ISO_DIR"/tscope-iso-* /tmp/tscope-iso-* 2>/dev/null | tail -n +2); do
        for u in "${in_use[@]+"${in_use[@]}"}"; do
            [ "$d" = "$u" ] && continue 2
        done
        freed=$((freed + $(du -sm "$d" 2>/dev/null | cut -f1)))
        rm -rf "$d"
    done
    [ "$freed" -gt 0 ] && echo "reaped stale snapshots: ${freed} MB"
    return 0
}
reap_stale_snapshots

ISO="$(mktemp -d "$ISO_DIR/tscope-iso-XXXXXX")"
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
