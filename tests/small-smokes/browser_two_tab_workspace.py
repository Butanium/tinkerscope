"""Browser smoke for WORKSPACE SCOPING on the process-global state bus.

The bug (found 2026-07-24, had silently corrupted 4 of the author's workspaces):
`panels` — the per-panel model layout — is workspace-scoped data that lived in the
process-global PlaygroundState. Two tabs on two workspaces clobbered each other:

    tab A opens workspace X → pushes X's layout onto the bus
    → tab B (on workspace Y) mirrors the bus and now SHOWS X's models
    → B's syncPanels sees X's panel ids as new, calls save()
    → 400ms later Y is PERSISTED ON DISK with X's models.

No interaction needed on the losing tab. Fixed by stamping every bus message with
the workspace it describes (web/src/lib/bus-scope.ts + api/state.py) so a client
adopts workspace-scoped fields only from its own workspace.

A SECOND clobber (2026-08-06, one workspace) got past the stamping: around a
server restart, a tab's MIRROR transiently held another workspace's panels
(bootstrap raw-adopt of a foreign-stamped bus; its own heal-claim lost/failed),
and every persistence path read the mirror back as "my layout" — so the next
layout save wrote the foreign panels to disk. Fixed by inverting ownership: the
workspace store OWNS its layout (`ws.layout`); rendering, saves, session prefs
and bus claims all read the store, never `live.state.panels`. Scenarios 5–6
pin that inversion and were verified to FAIL on the pre-fix build.

100% TOKEN-FREE: two seeded workspaces with distinct `base:` sentinels (never
sampled, only rendered), opened in two tabs of one browser. Asserts:

  1. each tab renders ITS OWN models after the other tab opens (the mirror);
  2. neither workspace's stored layout changed on disk (the corruption);
  3. an explicit layout save in the older tab still writes ITS OWN models
     (the save path reads the tab's own mirror, not the bus);
  4. sampling params stay GLOBAL — a param change in one tab reaches the other
     (the shared bus is the point; the fix must not sever it);
  5. POISONED BOOT: a tab that boots onto a bus claimed by ANOTHER workspace,
     with its own bus claim blocked (the 2026-08-06 restart race, made
     deterministic), must still render and PERSIST its own layout;
  6. FOCUS RECLAIM, no chimera: when that tab claims the bus (window focus),
     the bus must end up with the tab's TRUE layout — claiming with the
     poisoned mirror's panels stamped as ours was the corruption amplifier.

  uv run python tests/small-smokes/browser_two_tab_workspace.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _console import attach

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# Distinct, short, never-sampled model sentinels — they render as "◆ <id>".
A_MODELS = ["base:AAA/alpha-one", "base:AAA/alpha-two"]
B_MODELS = ["base:BBB/beta-one", "base:BBB/beta-two", "base:BBB/beta-three"]


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def panel_ids(n):
    ids = ["primary", "compare"] + [f"p-{k}" for k in range(2, n)]
    return ids[:n]


def seed(models, title):
    ids = panel_ids(len(models))
    return _post("/api/workspaces", {
        "title": title,
        "panels": [{"id": p, "run_id": m, "checkpoint": None} for p, m in zip(ids, models)],
        "trees": {p: {"nodes": {}, "rootChildren": [], "selected": {}} for p in ids},
        "reduced_panels": [], "send_targets": ids, "seen_panels": ids,
    })["id"]


def stored_models(cid):
    return [p["run_id"] for p in _get(f"/api/workspaces/{cid}")["panels"]]


def labels(models):
    """How a `base:` sentinel renders in a column header: "◆ <base model id>"."""
    return [m.removeprefix("base:") for m in models]


def shown_models(page):
    """The models this tab is actually rendering, from the column headers."""
    return page.eval_on_selector_all(
        ".column-title", "els => els.map(e => e.innerText.replace(/^◆\\s*/, '').trim())")


# The sidebar "Samples" number input (no test id; located by its label).
_SAMPLES_INPUT = """
  [...document.querySelectorAll('.sidebar-section')]
    .find(s => s.querySelector('.sidebar-label')?.innerText.trim() === 'Samples')
    ?.querySelector('input')
"""
SAMPLES_INPUT_IS = "() => (%s)?.value === '%%d'" % _SAMPLES_INPUT


def set_samples(page, n):
    """Type into the Samples input — a real user edit, so it goes through the
    page's own patchState (which is where the workspace stamping happens)."""
    page.eval_on_selector_all(".sidebar-section", "els => els", )  # ensure sidebar rendered
    page.evaluate(
        "n => { const el = %s; el.value = String(n);"
        " el.dispatchEvent(new Event('input', {bubbles: true})); }" % _SAMPLES_INPUT, n)


def open_tab(ctx, cid, first_model, errors):
    page = ctx.new_page()
    net = attach(page, errors)  # noqa: F841
    page.goto(f"{BASE}/?w={cid}", wait_until="load", timeout=20000)
    page.wait_for_function(
        f"document.body.innerText.includes({json.dumps(first_model.split('/')[-1])})", timeout=15000)
    return page


def main():
    ws_a = seed(A_MODELS, "smoke-A")
    ws_b = seed(B_MODELS, "smoke-B")
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME))
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})

        # ── tab A opens workspace A ───────────────────────────────────
        tab_a = open_tab(ctx, ws_a, A_MODELS[0], errors)
        assert shown_models(tab_a) == labels(A_MODELS), f"tab A initial: {shown_models(tab_a)}"

        # ── tab B opens workspace B: this is the clobber vector ───────
        tab_b = open_tab(ctx, ws_b, B_MODELS[0], errors)
        assert shown_models(tab_b) == labels(B_MODELS), f"tab B initial: {shown_models(tab_b)}"

        # Let B's layout push + any debounced save on A settle (save debounce 400ms).
        time.sleep(2.5)

        # ── 1. tab A must still render its OWN models ────────────────
        assert shown_models(tab_a) == labels(A_MODELS), \
            f"tab A mirrored workspace B's layout: {shown_models(tab_a)}"

        # ── 2. neither stored layout changed ─────────────────────────
        assert stored_models(ws_a) == A_MODELS, f"workspace A clobbered on disk: {stored_models(ws_a)}"
        assert stored_models(ws_b) == B_MODELS, f"workspace B clobbered on disk: {stored_models(ws_b)}"

        # ── 3. an explicit save in the non-owner tab writes its own layout ──
        # Toggling a send-target chip is the cheapest layout-only save (convo.save()).
        tab_a.locator(".send-chip").first.click()
        time.sleep(2.5)
        assert stored_models(ws_a) == A_MODELS, \
            f"an explicit save in tab A persisted the wrong models: {stored_models(ws_a)}"
        assert shown_models(tab_a) == labels(A_MODELS), f"tab A after its own save: {shown_models(tab_a)}"

        # ── 4. GLOBAL params still cross tabs (don't sever the shared bus) ──
        # Sampling params are shared on purpose; the scoping fix must filter only
        # the workspace-scoped fields out of a foreign message, never the params.
        n_next = 7 if _get("/api/state")["n_samples"] != 7 else 5
        set_samples(tab_b, n_next)
        tab_a.wait_for_function(SAMPLES_INPUT_IS % n_next, timeout=8000)
        assert _get("/api/state")["n_samples"] == n_next, "global param patch did not land"

        assert not errors, f"console errors: {errors}"
        tab_a.close()
        tab_b.close()
        n_before = len(errors)  # scenario 5 aborts fetches — network noise is expected

        # ── 5. poisoned boot: foreign-claimed bus + a blocked heal-claim ──
        # The 2026-08-06 corruption, deterministic. Claim the bus as workspace A
        # (what another tab's re-prime does after a server restart), then boot a
        # tab onto workspace B while ABORTING its POSTs to /api/state — its
        # bootstrap raw-adopts the A-stamped snapshot and its own heal-claim
        # never lands, so the MIRROR holds A's panels while activeId is B. On the
        # pre-inversion build the layout save then read the mirror and PATCHed
        # A's panels onto B's disk file; store-owned layout must keep B intact.
        _post("/api/state", {
            "workspace_id": ws_a,
            "panels": [{"id": p, "run_id": m, "checkpoint": None, "messages": []}
                       for p, m in zip(panel_ids(len(A_MODELS)), A_MODELS)],
        })
        tab_p = ctx.new_page()
        attach(tab_p, errors)  # the SECOND tab needs the same named guard as the first
        tab_p.route(
            "**/api/state",
            lambda route: route.abort() if route.request.method == "POST" else route.continue_(),
        )
        tab_p.goto(f"{BASE}/?w={ws_b}", wait_until="load", timeout=20000)
        tab_p.wait_for_selector(f'.ws-picker[data-ws-id="{ws_b}"]', timeout=15000)
        # Force a layout-only save while the bus is still foreign-claimed.
        tab_p.locator(".send-chip").first.click()
        time.sleep(2.5)  # save debounce (400ms) + slack
        assert stored_models(ws_b) == B_MODELS, \
            f"poisoned boot persisted a foreign layout onto B: {stored_models(ws_b)}"
        assert shown_models(tab_p) == labels(B_MODELS), \
            f"poisoned boot rendered a foreign layout: {shown_models(tab_p)}"

        # ── 6. focus reclaim must claim with the TRUE layout, not the mirror ──
        tab_p.unroute("**/api/state")
        tab_p.evaluate("window.dispatchEvent(new Event('focus'))")
        deadline = time.time() + 8
        st = _get("/api/state")
        while time.time() < deadline and st.get("workspace_id") != ws_b:
            time.sleep(0.3)
            st = _get("/api/state")
        assert st.get("workspace_id") == ws_b, f"focus did not reclaim the bus: {st.get('workspace_id')}"
        assert [p["run_id"] for p in st["panels"]] == B_MODELS, \
            f"the reclaim was a chimera (mirror panels stamped as B): {[p['run_id'] for p in st['panels']]}"
        assert stored_models(ws_b) == B_MODELS, f"reclaim corrupted B on disk: {stored_models(ws_b)}"

        tail = [e for e in errors[n_before:]
                if "ERR_FAILED" not in e and "Failed to load resource" not in e and "Failed to fetch" not in e]
        assert not tail, f"console errors (scenario 5/6): {tail}"
        browser.close()
    print("browser_two_tab_workspace: OK")


if __name__ == "__main__":
    main()
