#!/bin/bash
# Run the token-free browser smokes SERIALLY against a throwaway instance.
#
# Why a runner: every session re-derives this, and gets it wrong the same ways.
#   - Smokes must run ONE AT A TIME. `browser_state_reprime.py` KILLS AND RESTARTS
#     a server mid-run; anything else touching the same instance then fails with
#     a bogus error. On 2026-07-24 a stray second sweep made the cross-tab
#     corruption smoke fail with the exact symptom it exists to catch — a false
#     "your fix doesn't work" that cost real time. This script takes a lock.
#   - The instance must be ISOLATED (never :8767, never the real state dir).
#   - `web/dist` must be current, or you are testing the last build, not your edits.
#   - Some smokes are STALE and their failures mean nothing — they are skipped
#     here by name, with the reason, rather than quietly polluting the result.
#
# Usage:
#   scripts/smoke.sh                 # token-free set against a state SNAPSHOT
#   scripts/smoke.sh --fresh         # ... against EMPTY state (chart_rules wants this)
#   scripts/smoke.sh a b c           # only these smokes (names, no path/extension)
#   PORT=8899 scripts/smoke.sh       # pin the port
#   SMOKE_SCAN_DIR="d1 d2" scripts/smoke.sh   # override the scan roots (space-separated)
#
# Exit non-zero if any smoke fails; per-smoke logs land in the run dir it prints.

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8813}"
FRESH=""
# BOTH fixture roots by default. The label/typeahead smokes (modals, label_trunc,
# label_diff, fuzzy_search) hard-code `ed_sheeran`, whose runs live ONLY under
# negation_neglect — with weird-personas alone they fail on a 15s timeout that
# reads like a UI regression and isn't one (hit 2026-07-24). Override with
# SMOKE_SCAN_DIR (space-separated for several roots).
SCAN_DIR="${SMOKE_SCAN_DIR:-$HOME/projects2/weird-personas $HOME/projects2/negation_neglect/datasets/training_datasets}"
PICK=()
while [ $# -gt 0 ]; do
    case "$1" in
        --fresh) FRESH="--fresh"; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) PICK+=("$1"); shift ;;
    esac
done

# Token-free and currently trustworthy. Keep in sync with the trust map in
# docs/TODO.md; a smoke that needs real sampling does NOT belong here.
DEFAULT=(
    browser_workspace_url
    browser_two_tab_workspace
    browser_thread_switcher
    browser_state_reprime
    browser_kbnav
    browser_thread_system
    browser_row_toolbar
    browser_legacy_echo_graft
    browser_sysprompt_switch
    browser_system_chip
    browser_panel_drag
    browser_chart_rules
    browser_branch_from_root
    browser_modals
    browser_label_trunc
    browser_label_diff
    browser_fuzzy_search
    browser_help_modal
)
# Known-stale: failures here carry NO signal. Repair tracked in docs/TODO.md.
declare -A STALE=(
    [browser_branching]="Playwright strict-mode violation (2 edit textareas); fails on pre-fix commits too"
    [browser_continue_scope]="its .prefill-scope selector no longer exists"
    [browser_readme_shots]="pre-ModelDropdown sidebar + q_nk fixtures"
)

SMOKES=("${PICK[@]:-${DEFAULT[@]}}")

LOCK=/tmp/tinkerscope-smoke.lock
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "another smoke run holds $LOCK — smokes MUST NOT run concurrently (see the header). Waiting…"
    flock 9
fi

# The lock stops a sibling SWEEP, but not a dev-isolated instance someone forgot
# to kill. Those still fail smokes — by eating CPU, not by touching our port —
# and browser_workspace_url (10 s wait) dies first with a symptom that reads
# exactly like a real URL-sync regression. Cost an hour on 2026-07-24.
#
# Detect them by XDG_STATE_HOME pointing at a tscope-iso snapshot, NOT by "is a
# tinkerscope running": the user's own long-lived instance is always up, so
# warning about that would fire every run and be tuned out within a day.
strays=""
for pid in $(pgrep -f 'tinkerscope' 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -q '^XDG_STATE_HOME=.*tscope-iso'; then
        strays="$strays    $pid $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-80)"$'\n'
    fi
done
if [ -n "$strays" ]; then
    echo "⚠ leftover dev-isolated instance(s) still running — they compete for CPU and"
    echo "  cause TIMEOUT failures that look like product bugs (workspace_url dies first):"
    printf '%s' "$strays"
    echo "  Kill them (pkill -f 'port <N>') before believing any timeout failure."
fi

RUN_DIR="$(mktemp -d /tmp/tinkerscope-smoke-XXXXXX)"
echo "logs: $RUN_DIR"

echo "building web/ (stale dist = testing your last build, not your edits)…"
if ! ( cd web && npm run build ) > "$RUN_DIR/build.log" 2>&1; then
    echo "web build FAILED:"; cat "$RUN_DIR/build.log"; exit 1
fi

# shellcheck disable=SC2086  # $SCAN_DIR is word-split on purpose (several scan roots)
scripts/dev-isolated.sh --port "$PORT" $FRESH $SCAN_DIR > "$RUN_DIR/server.log" 2>&1 &
SERVER_PID=$!
cleanup() {
    kill "$SERVER_PID" 2>/dev/null
    # dev-isolated's child outlives the wrapper; take the port down by name too.
    pkill -f "port $PORT" 2>/dev/null
}
trap cleanup EXIT

for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
    sleep 1
done
if ! curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "instance never came up — see $RUN_DIR/server.log"; exit 1
fi
grep -i "migration" "$RUN_DIR/server.log" || true

pass=0; fail=0; failed=()
for s in "${SMOKES[@]}"; do
    if [ -n "${STALE[$s]:-}" ]; then
        printf '  SKIP  %-32s (stale: %s)\n' "$s" "${STALE[$s]}"
        continue
    fi
    f="tests/small-smokes/$s.py"
    [ -f "$f" ] || { printf '  MISS  %-32s (no such smoke)\n' "$s"; continue; }
    if timeout 240 uv run python "$f" "http://127.0.0.1:$PORT" > "$RUN_DIR/$s.log" 2>&1; then
        printf '  ok    %s\n' "$s"; pass=$((pass+1))
    else
        printf '  FAIL  %-32s → %s\n' "$s" "$RUN_DIR/$s.log"; fail=$((fail+1)); failed+=("$s")
    fi
done

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ] || { echo "failed: ${failed[*]}"; exit 1; }
