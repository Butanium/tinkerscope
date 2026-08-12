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
#   - A smoke you wrote for a bug you just fixed proves NOTHING until you watch it
#     FAIL without the fix. Twice on 2026-07-29 a fresh smoke passed for the wrong
#     reason (an assertion that could not fail; a "live" update that never fired) —
#     both would have shipped as green. `--baseline` makes that check one flag.
#   - A SELF-HOSTING smoke that ignores TSCOPE_APP_DIR turns `--baseline` itself
#     into a false green. `--baseline` now LINTS for that and refuses to run.
#
# Usage:
#   scripts/smoke.sh                 # token-free set against a state SNAPSHOT
#   scripts/smoke.sh --fresh         # ... against EMPTY state (chart_rules wants this)
#   scripts/smoke.sh a b c           # only these smokes (names, or paths to .py)
#   scripts/smoke.sh --baseline HEAD browser_chart_live_inspect
#                                    # run TODAY'S smoke against the app at <ref>
#                                    # (a throwaway worktree) — the A/B half that
#                                    # tells you the smoke can actually fail.
#   PORT=8899 scripts/smoke.sh       # pin the port
#   SMOKE_SCAN_DIR="d1 d2" scripts/smoke.sh   # override the scan roots (space-separated)
#
# Exit non-zero if any smoke fails; per-smoke logs land in the run dir it prints.
# In --baseline mode the exit code only covers the SETUP (worktree/build/instance):
# whether a smoke should have failed is yours to read, so it always exits 0 once
# the baseline instance came up.

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8813}"
FRESH=""
BASELINE=""
# The scan root is the suite's OWN fixture tree (tests/run_fixtures.py), rebuilt
# on every run — a few kB of config.json / checkpoints.jsonl, no weights, so it
# costs nothing and the smokes run on a fresh clone. It used to be two of the
# user's personal run dirs, which meant the suite worked on one box and a fixture
# that moved failed a smoke in a way that read like a UI regression (hit
# 2026-07-24, cost an hour). Override with SMOKE_SCAN_DIR (space-separated for
# several roots) when you need real, sampleable checkpoints.
SCAN_DIR="${SMOKE_SCAN_DIR:-}"
PICK=()
while [ $# -gt 0 ]; do
    case "$1" in
        --fresh) FRESH="--fresh"; shift ;;
        --baseline) BASELINE="${2:?--baseline needs a git ref (e.g. HEAD)}"; shift 2 ;;
        -h|--help) sed -n '2,35p' "$0"; exit 0 ;;  # ← the header comment block
        *) PICK+=("$1"); shift ;;
    esac
done

# Token-free and verified passing at the time each was added here. This file IS
# the trust map — there is no second copy.
#
# It is a SUBSET by construction, not a curated best-of: the runner postdates most
# of tests/small-smokes/browser_*.py, and a smoke enters DEFAULT when someone runs
# it and watches it pass. Everything not listed in DEFAULT or STALE is UNCLASSIFIED
# (nobody has run it under the runner), which is not the same as known-bad — some
# just need real sampling. If you're touching a surface an unlisted smoke covers,
# run it directly against a dev-isolated instance; if it passes, add it here.
DEFAULT=(
    browser_workspace_url
    browser_two_tab_workspace
    browser_thread_switcher
    browser_state_reprime
    browser_kbnav
    browser_thread_system
    browser_row_toolbar
    browser_samples_view
    browser_shift_edit_assistant
    browser_legacy_echo_graft
    browser_sysprompt_switch
    browser_system_chip
    browser_panel_drag
    browser_chart_rules
    browser_branch_from_root
    # Repaired 2026-08-12 (were listed STALE for reasons that had stopped being true):
    #   branching — the bare textarea.edit-textarea matched the thread-system editor too
    #   continue_scope — .prefill-scope still exists; it was the FOLD half that died
    #                    when sends went detached. Now request-level. See its docstring.
    browser_branching
    browser_continue_scope
    # Token-free (route-intercepted) and watched passing 2026-08-12.
    browser_shift_continue_thinking
    browser_modals
    browser_label_trunc
    browser_label_diff
    browser_fuzzy_search
    browser_help_modal
    browser_token_logprobs
    browser_token_overlay
    browser_highlight_master
    browser_search_palette
    browser_sidebar_folds
    browser_pack_link
    # Token-free but NOT network-free: the probe asks tinker whether a path is real,
    # which no local list can answer. Skips itself without TINKER_API_KEY.
    browser_tinker_custom_ckpt
    # Same deal (one metadata probe per path), and it skips itself without a key or
    # a resolvable checkpoint in the account sweep.
    browser_ckpt_base_label
    # DATA-DRIVEN and it does NOT skip: it hard-asserts that ≥1 discovered run is
    # unavailable, which the negation_neglect root supplies (base no longer served).
    # A failure here can mean tinker changed what it serves — check /api/models
    # before reading it as a UI regression. Needs both scan roots (the default).
    browser_model_availability
    # Own their whole world (build a state dir, export a site, serve it) and
    # ignore the base-url arg — they still belong here so they run under the lock.
    browser_static_site
    browser_static_logprob_trim
    browser_pack_big
    browser_open_locally
    browser_pack_link_map
)
# Known-stale: failures here carry NO signal. Repair when you next need the
# coverage — not on their own account.
declare -A STALE=(
    [browser_continue_sample]="asserts n=2 folded samples, gets 4 — it never SETS thinking, so an inherited thinking='both' (dev-isolated snapshots the real state home) fires n each way. Baselined 2026-08-06: fails identically on main. A pre-goto POST /api/state {thinking:false} did NOT stick — cause unresolved, so the fix is more than one line"
)
# CAPTURE TOOLS / LIVE — real sampling, so deliberately NOT in the token-free set.
# They are not stale; run them directly against a dev-isolated instance.
#   browser_readme_shots — regenerates the README images (was listed STALE for a
#   pre-ModelDropdown/q_nk state it left behind when it was rewritten 2026-08-05).
#
# NON-DETERMINISTIC (not stale — the coverage is real, the result isn't stable):
#   browser_stop_generation — drives live free-OpenRouter, and which assertion
#   fails varies run to run. Observed 2026-08-03 failing BOTH on the merge tip
#   (timeout waiting for the compare panel) and on the pre-merge main 51c5ec3
#   (owned_backend_running stayed True), i.e. it was already unstable before
#   anything landed on top of it. Re-run before believing a failure here, and
#   baseline it if you need to attribute one.

SMOKES=("${PICK[@]:-${DEFAULT[@]}}")

# Resolve a smoke NAME (browser_foo) or a PATH (tests/…/browser_foo.py, /abs/x.py)
# to its file. Paths exist so the leakage lint below can be exercised against a
# throwaway fixture without checking one into the suite.
smoke_file() {
    case "$1" in
        */*|*.py) echo "$1" ;;
        *)        echo "tests/small-smokes/$1.py" ;;
    esac
}

# ── --baseline leakage lint ──────────────────────────────────────────────────
# A SELF-HOSTING smoke (one that spawns its own server / CLI / site export rather
# than only driving the --baseline instance over HTTP) must resolve its checkout
# from TSCOPE_APP_DIR. If it doesn't, it runs the WORKING TREE's app while the
# report says "baseline" — the result reads as evidence and is worth nothing.
# That has now bitten twice (browser_pack_big 2026-07-30, browser_state_reprime
# 2026-08-03), both times as a green checkmark, which is the worst possible
# failure shape. So: refuse the run rather than produce one.
#
# The test is a grep, and grep is crude — it can flag a smoke that shells out for
# something harmless. That asymmetry is deliberate: a false refusal costs a
# minute of reading, a false PASS costs a shipped non-fix.
#
# Compliance requires an actual env READ (os.environ / os.getenv), not merely the
# string: the first version of this lint accepted a smoke whose docstring said the
# words "never reads TSCOPE_APP_DIR" and cleared it. A prose mention is exactly
# what a leaking smoke written by someone who knew about the trap looks like.
if [ -n "$BASELINE" ]; then
    leaky=""
    for s in "${SMOKES[@]}"; do
        f="$(smoke_file "$s")"
        [ -f "$f" ] || continue
        grep -qE 'subprocess\.(Popen|run|check_call|check_output)|site_export|uv run tinkerscope' "$f" || continue
        grep -qE '(os\.environ|os\.getenv|environ\.get)[^\n]*TSCOPE_APP_DIR' "$f" && continue
        leaky="$leaky    $s  ($f)"$'\n'
    done
    if [ -n "$leaky" ]; then
        echo "REFUSING the --baseline run: these smokes SELF-HOST but never read TSCOPE_APP_DIR,"
        echo "so they would spawn the WORKING TREE's app and pass against a ref that lacks the fix:"
        printf '%s' "$leaky"
        echo "  Fix each one with:"
        echo "      REPO = Path(os.environ.get(\"TSCOPE_APP_DIR\") or Path(__file__).resolve().parents[2])"
        echo "  and use REPO as the cwd / checkout for every server, CLI or site-export it spawns."
        echo "  See CLAUDE.md §\"Build / verify\" (the ⚠ self-hosting-smoke note)."
        exit 1
    fi
fi

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

# APP_DIR is what gets built and served. Normally the working tree; with
# --baseline, a throwaway worktree at <ref> — so today's smoke runs against
# yesterday's app. The smoke FILES always come from the working tree ($ROOT):
# the baseline ref usually predates the smoke entirely, and running its copy
# would test nothing. Worktree lives on /var/tmp (disk), not /tmp (RAM).
APP_DIR="$ROOT"
WORKTREE=""
if [ -n "$BASELINE" ]; then
    REF_SHA="$(git rev-parse --short "$BASELINE" 2>/dev/null)" || {
        echo "--baseline: '$BASELINE' is not a git ref"; exit 1; }
    WORKTREE="$(mktemp -d /var/tmp/tscope-baseline-XXXXXX)"
    rmdir "$WORKTREE"
    echo "baseline: checking out $BASELINE ($REF_SHA) → $WORKTREE"
    if ! git worktree add --detach "$WORKTREE" "$BASELINE" > "$RUN_DIR/worktree.log" 2>&1; then
        cat "$RUN_DIR/worktree.log"; exit 1
    fi
    # node_modules is gitignored, so the worktree has none — borrow the main
    # checkout's (same package.json at any ref we'd baseline against).
    ln -s "$ROOT/web/node_modules" "$WORKTREE/web/node_modules"
    APP_DIR="$WORKTREE"
fi

# Build the fixture scan root unless the caller supplied their own. It comes from
# the WORKING TREE's generator even under --baseline: it is test data, not app
# code, and the baseline ref usually predates it entirely.
if [ -z "$SCAN_DIR" ]; then
    SCAN_DIR="$(uv run python "$ROOT/tests/run_fixtures.py" 2>"$RUN_DIR/fixtures.log")" || {
        echo "could not build the fixture run tree:"; cat "$RUN_DIR/fixtures.log"; exit 1; }
    echo "fixture scan root: $SCAN_DIR"
fi

echo "building web/${WORKTREE:+ from the baseline worktree} (stale dist = testing your last build, not your edits)…"
if ! ( cd "$APP_DIR/web" && npm run build ) > "$RUN_DIR/build.log" 2>&1; then
    echo "web build FAILED:"; cat "$RUN_DIR/build.log"; exit 1
fi

# shellcheck disable=SC2086  # $SCAN_DIR is word-split on purpose (several scan roots)
( cd "$APP_DIR" && scripts/dev-isolated.sh --port "$PORT" $FRESH $SCAN_DIR ) > "$RUN_DIR/server.log" 2>&1 &
SERVER_PID=$!
cleanup() {
    kill "$SERVER_PID" 2>/dev/null
    # dev-isolated's child outlives the wrapper; take the port down by name too.
    pkill -f "port $PORT" 2>/dev/null
    if [ -n "$WORKTREE" ]; then
        sleep 1  # let the server release the checkout before git prunes it
        git worktree remove --force "$WORKTREE" 2>/dev/null
    fi
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
    f="$(smoke_file "$s")"
    log="$RUN_DIR/$(basename "${s%.py}").log"
    [ -f "$f" ] || { printf '  MISS  %-32s (no such smoke)\n' "$s"; continue; }
    # SELF-CONTAINED smokes (browser_static_site, browser_pack_big) ignore the base
    # URL and build their own site instead — so they must be told which checkout to
    # export from, or `--baseline` silently exercises the WORKING TREE and passes
    # against a ref that lacks the fix. That happened on 2026-07-30 with
    # browser_pack_big; a passing baseline is the one result you must not shrug at.
    if TSCOPE_APP_DIR="$APP_DIR" timeout 240 uv run python "$f" "http://127.0.0.1:$PORT" > "$log" 2>&1; then
        printf '  ok    %s\n' "$s"; pass=$((pass+1))
    else
        printf '  FAIL  %-32s → %s\n' "$s" "$log"; fail=$((fail+1)); failed+=("$s")
    fi
done

echo
echo "$pass passed, $fail failed"
if [ -n "$BASELINE" ]; then
    echo
    echo "↑ BASELINE run against $BASELINE ($REF_SHA) — read it inverted: a smoke that"
    echo "  pins a fix SHOULD fail here, and you must open its log to confirm it failed"
    echo "  on the assertions the fix addresses (not on setup, a timeout, or a stray"
    echo "  instance). A smoke that PASSES here does not test what you think it does."
    [ "$fail" -gt 0 ] && echo "  failed (expected): ${failed[*]}"
    exit 0
fi
[ "$fail" -eq 0 ] || { echo "failed: ${failed[*]}"; exit 1; }
