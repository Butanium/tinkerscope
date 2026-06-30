# Handoff: harden + land the 4 remaining browser smokes

**Goal:** get `browser_{distribution_chart,continue,edit_fork,delete_branch}.py`
(in this `_wip/` dir) reliably green, then move them up to `tests/small-smokes/`.
They were agent-built as a verification net for the `chat.svelte.ts` refactor
(commit `c8a2df7`, now landed and verified another way) — so they're no longer
load-bearing, just permanent regression coverage for those four UI paths.

## The one bug that hits most of them: test isolation

The server **persists conversations per scan-root SET**. Any two smokes (or
repeat runs) that share the same scan-root set share a persistence namespace, so
a run **inherits branched conversation state left by a prior run** — including a
stale user-level `‹k/N›` cycler. Symptoms: a smoke reads `[data-testid="branch-cycle"]`
with `.first` and grabs a *leftover* cycler instead of the one it just created;
or `delete_branch` finds **2** `branch-cycle` elements when it expects to watch one
vanish. This is deterministic and fails on pristine `main` too — it is **not** a
product bug.

**The fix pattern (already applied to `browser_n_samples.py` — use it as the
reference):**
1. After selecting the model, **start a fresh conversation**:
   `page.locator('button[aria-label="New conversation"]').first.click()` then
   re-wait `.input-textarea:not([disabled])`. Non-shift New keeps the model.
2. **Never read the cycler with `.first`.** Read all counts and pick the one that
   matches what you expect:
   `counts = [c.strip() for c in page.locator('[data-testid="branch-cycle"] .branch-cycle-count').all_inner_texts()]`
   then `next((c for c in counts if c.endswith("/N")), ...)`.

`browser_send_branch.py` survives without this only because its assertion
(`endswith "/2"`) happens to match the stale `2/2` — fragile; worth giving it the
same New-conversation treatment while you're here.

## Per-smoke specifics (the failure mode each showed last)

- **`browser_distribution_chart.py`** — clicks the chart-trigger in a retry loop;
  the second click lands on the already-open modal's `.modal-overlay` →
  "intercepts pointer events". Guard the retry on "modal not yet open" (or click
  once and wait for `svg.chart-svg`). Plus the isolation fix.
- **`browser_continue.py`** — timed out waiting for the cycler to appear after the
  "Continue this message" action (`button[aria-label="Continue this message"]`,
  hover-revealed → scroll into view + hover the `.message` row, then
  `click(force=True)`). Check the continue actually fired; then isolation fix.
- **`browser_edit_fork.py`** — `wait_for_function` timed out (the edited-text /
  fork condition never became true). Re-derive the edit flow against
  `web/src/lib/ChatMessage.svelte` (edit action → inline editor → confirm); then
  isolation fix.
- **`browser_delete_branch.py`** — waited for the cycler to *detach* after delete
  but found 2 cyclers (the isolation bug). The New-conversation fix likely
  resolves it outright; verify the delete actually drops 2→1.

## How to run + verify (the discipline that catches the isolation bug)

Each smoke takes a `BASE_URL` arg and assumes an **external** server:

```
mkdir -p /tmp/wip_empty
uv run tinkerscope ~/projects2/negation_neglect/datasets/training_datasets \
  ~/projects2/weird-personas /tmp/wip_empty --port 8843   # 3rd empty dir dodges
                                                           # the same-scan-set singleton guard
# then, against the SAME server:
uv run python tests/small-smokes/_wip/browser_continue.py http://127.0.0.1:8843
```

**Verify each 3× against ONE shared server** — that's the test that would have
caught the isolation bug (a single run on a fresh namespace passes by luck).
Zero-cost sends use the free model `openrouter:liquid/lfm-2.5-1.2b-instruct:free`
(already in the saved OR list); select it FIRST (composer disabled until canChat).
Kill the server with `fuser -k 8843/tcp` (never `pkill -f`).

When a smoke is 3/3 green, `git mv` it to `tests/small-smokes/` and drop its entry
here; delete this dir once all four are up.
