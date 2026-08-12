"""Browser smoke for the P1 ops mirror — cross-tab convergence + retry durability
(docs/HANDOFF_SERVER_AUTHORITY.md §7 P1 verify; the §4.2 mirror rules).

100% TOKEN-FREE and SELF-HOSTING: spawns its own server on a scratch port +
scratch XDG_STATE_HOME (honors TSCOPE_APP_DIR so `scripts/smoke.sh --baseline`
really exercises the baseline), seeds two workspaces, and drives TWO pages:

  A. add propagates with IDENTITY: an assistant edit in page A (a manual branch
     — no generation) appears in page B via the bus `ops` event without reload,
     and the copy-node-handle button yields the SAME node id in A, B, and the
     stored body — server-adopted ids, not a content lookalike.
  B. the §4.2 contended-LWW trace: both pages re-select a different sibling of
     one fan near-simultaneously (programmatic element.click — no auto-scroll,
     real contention); after settling BOTH pages agree with the server's
     canonical selection. Skip-own-echo would leave the losing page stuck on
     its own pick with contiguous revs — this is the regression test for
     always-apply.
  C. dropped-POST retry durability (what replaced dirt re-merge):
     C1 the batch REACHES the server but the response is dropped (route.fetch
        then abort) → the bounded retry replays it → applied exactly ONCE
        (rev advances by 1 — the replay is an all-noop batch, no bump);
     C2 the batch never reaches the server twice (2 aborts) → third attempt
        lands within the backoff window;
     both WITHOUT any body refetch (asserted by counting GETs — survival must
     come from the retry, not from desync recovery).
  D. workspace isolation (filter-BEFORE-gap-check): a mutation burst in A's
     workspace causes zero refetches and no rev movement in page B's DIFFERENT
     workspace, and B's own ops still apply cleanly afterwards.
  E. restart durability: everything above survives a server restart from the
     same state dir, and the rev continues monotonically for post-restart ops.

⚠️ KILLS AND RESTARTS ITS OWN SERVER (scenario E) — like browser_state_reprime,
this smoke must never run concurrently with another (scripts/smoke.sh's lock
handles that; don't run two sweeps).

  uv run python tests/small-smokes/browser_ops_convergence.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = 8873
BASE = f"http://127.0.0.1:{PORT}"
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

FREE = "openrouter:openrouter/free"  # non-null run_id keeps the phantom heal away


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def fan_tree(prefix, n=3):
    """u0 → a1 (EDIT target) → u2 → n assistant siblings (selected first)."""
    nodes = {
        f"{prefix}u0": {"id": f"{prefix}u0", "role": "user", "content": f"{prefix} Q-ZERO",
                        "parent": None, "children": [f"{prefix}a1"]},
        f"{prefix}a1": {"id": f"{prefix}a1", "role": "assistant", "content": f"{prefix} EDIT-ME",
                        "parent": f"{prefix}u0", "children": [f"{prefix}u2"]},
        f"{prefix}u2": {"id": f"{prefix}u2", "role": "user", "content": f"{prefix} Q-FAN",
                        "parent": f"{prefix}a1", "children": []},
    }
    for i in range(n):
        sid = f"{prefix}s{i}"
        nodes[sid] = {"id": sid, "role": "assistant", "content": f"{prefix} SIB-{i}",
                      "parent": f"{prefix}u2", "children": []}
        nodes[f"{prefix}u2"]["children"].append(sid)
    return {"nodes": nodes, "rootChildren": [f"{prefix}u0"],
            "selected": {"__root__": f"{prefix}u0", f"{prefix}u2": f"{prefix}s0"}}


def start_server(scratch: Path) -> subprocess.Popen:
    env = {**os.environ, "XDG_STATE_HOME": str(scratch / "state")}
    proc = subprocess.Popen(
        ["uv", "run", "tinkerscope", "--port", str(PORT), str(scratch / "runs")],
        cwd=REPO, env=env,
        stdout=(scratch / "server.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            _get("/api/state")
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"server died on startup; see {scratch}/server.log")
            time.sleep(0.3)
    sys.exit(f"server never came up; see {scratch}/server.log")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    proc.wait(timeout=15)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _get("/api/state")
            time.sleep(0.2)
        except (urllib.error.URLError, ConnectionError):
            return
    sys.exit("old server still answering after terminate")


def wait_until(pump, fn, timeout=10, what="condition"):
    """Poll `fn`, pumping the playwright dispatcher via `pump.wait_for_timeout`
    — sync-API route/console handlers only run while the main thread is inside
    a playwright call, so a plain time.sleep here leaves intercepted requests
    pending forever (and their handlers firing at browser.close)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if fn():
                return
        except Exception:
            pass
        pump.wait_for_timeout(250)
    raise AssertionError(f"timed out waiting for {what}")


def body(ws_id):
    return _get(f"/api/workspaces/{ws_id}")


def click_js(page, selector):
    """Programmatic element.click — no Playwright auto-scroll, and cheap enough
    to fire on two pages back-to-back for real POST contention."""
    ok = page.evaluate(
        "(sel) => { const el = document.querySelector(sel); if (!el) return false; el.click(); return true; }",
        selector)
    assert ok, f"selector not found: {selector}"


def cycle_next(col_sel):
    return f"{col_sel} [data-testid=branch-cycle] button[aria-label='Next branch']"


def cycle_prev(col_sel):
    return f"{col_sel} [data-testid=branch-cycle] button[aria-label='Previous branch']"


def main():
    scratch = Path(tempfile.mkdtemp(prefix="tscope-opsconv-"))
    (scratch / "runs").mkdir()
    proc = start_server(scratch)
    try:
        w1 = _post("/api/workspaces", {
            "name": "ops convergence W1",
            "trees": {"primary": fan_tree("P1-"), "compare": fan_tree("C1-")},
            "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None},
                       {"id": "compare", "run_id": FREE, "checkpoint": None}],
            "seen_panels": ["primary", "compare"],
        })
        w2 = _post("/api/workspaces", {
            "name": "ops convergence W2",
            "trees": {"primary": fan_tree("P2-", n=2)},
            "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None}],
            "seen_panels": ["primary"],
        })
        pri = ".chat-column[data-panel=primary]"
        cmp_ = ".chat-column[data-panel=compare]"

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            errors = {"A": [], "B": []}

            def new_page(tag):
                pg = browser.new_page(viewport={"width": 1700, "height": 900})
                pg.add_init_script(
                    "navigator.clipboard.writeText = t => { window.__copied = t; return Promise.resolve(); };")
                pg.on("console", lambda m, t=tag: errors[t].append(m.text) if m.type == "error" else None)
                pg.on("pageerror", lambda e, t=tag: errors[t].append(str(e)))
                return pg

            page_a, page_b = new_page("A"), new_page("B")
            for pg in (page_a, page_b):
                pg.goto(f"{BASE}/?w={w1['id']}", wait_until="load", timeout=20000)
                pg.wait_for_function("document.body.innerText.includes('P1- SIB-0')", timeout=15000)

            # Body-GET counters: survival must come from RETRY (C) and isolation
            # (D), never from a desync refetch — a refetch here is a failure.
            gets = {"A": 0, "B": 0}
            def count_get(tag):
                def on_req(req):
                    if req.method == "GET" and (
                            f"/api/workspaces/{w1['id']}" == req.url.split(BASE)[-1]
                            or f"/api/workspaces/{w2['id']}" == req.url.split(BASE)[-1]):
                        gets[tag] += 1
                return on_req
            page_a.on("request", count_get("A"))
            page_b.on("request", count_get("B"))

            # ── A. an edit in page A lands in page B, with node identity ────
            row_a = page_a.locator(f"{cmp_} .message", has_text="C1- EDIT-ME").last
            row_a.locator("button[aria-label='Edit']").click()
            ta = page_a.locator(f"{cmp_} textarea.edit-textarea:not(.edit-system):not(.edit-reasoning)")
            ta.fill("C1- EDITED-BY-A")
            page_a.locator(f"{cmp_} .btn-edit-save").click()
            page_a.wait_for_function(
                "document.body.innerText.includes('C1- EDITED-BY-A')", timeout=8000)
            # …page B follows via the ops bus, no reload:
            page_b.wait_for_function(
                "document.body.innerText.includes('C1- EDITED-BY-A')", timeout=8000)

            def handle_of(page, col, text):
                row = page.locator(f"{col} .message", has_text=text).last
                row.locator("[data-testid=copy-node-id]").click()
                return page.evaluate("window.__copied")

            h_a = handle_of(page_a, cmp_, "C1- EDITED-BY-A")
            h_b = handle_of(page_b, cmp_, "C1- EDITED-BY-A")
            assert h_a == h_b, f"node identity differs across tabs: {h_a} vs {h_b}"
            stored = body(w1["id"])
            edited = [n for n in stored["trees"]["compare"]["nodes"].values()
                      if n.get("content") == "C1- EDITED-BY-A"]
            assert len(edited) == 1, f"edited node not stored exactly once: {edited}"
            assert h_a == f"compare:{edited[0]['id']}", \
                f"stored id {edited[0]['id']} != copied handle {h_a}"
            # the new sibling shows a ‹2/2› cycler in the FOLLOWER page too
            sib = page_b.locator(f"{cmp_} .message", has_text="C1- EDITED-BY-A") \
                        .last.locator("[data-testid=branch-cycle] .branch-cycle-count")
            assert sib.inner_text() == "2/2", f"page B cycler: {sib.inner_text()!r}"

            # ── B. contended re-select from both tabs converges (§4.2) ──────
            # A steps next (s0→s1), B steps prev (s0→wrap s2), near-simultaneously.
            click_js(page_a, cycle_next(pri))
            click_js(page_b, cycle_prev(pri))
            page_a.wait_for_timeout(1500)
            page_b.wait_for_timeout(1500)  # both POSTs + broadcasts settle (pumped)
            srv = body(w1["id"])["trees"]["primary"]
            canonical = srv["selected"]["P1-u2"]
            assert canonical in ("P1-s1", "P1-s2"), f"unexpected canonical {canonical}"
            order = srv["nodes"]["P1-u2"]["children"]
            assert sorted(order) == ["P1-s0", "P1-s1", "P1-s2"], f"fan mutated: {order}"
            want = f"P1- SIB-{canonical[-1]}"
            want_kn = f"{order.index(canonical) + 1}/{len(order)}"
            for tag, pg in (("A", page_a), ("B", page_b)):
                shown = pg.locator(f"{pri} .message", has_text="P1- SIB-").last
                assert want in shown.inner_text(), \
                    f"page {tag} stuck off-canonical: wants {want}, shows {shown.inner_text()[:80]!r}"
                # sibling ORDER, not just the pick: the ‹k/N› label is the DOM's
                # projection of children order — a re-append drift shows here.
                kn = shown.locator("[data-testid=branch-cycle] .branch-cycle-count").inner_text()
                assert kn == want_kn, f"page {tag} sibling order drift: shows {kn}, server says {want_kn}"

            # ── C. dropped-POST retry (bounded, idempotent, refetch-free) ───
            state = {"mode": None, "n": 0}
            def route_ops(route):
                state["n"] += 1
                if state["mode"] == "drop-response" and state["n"] == 1:
                    route.fetch()   # the server APPLIES the batch…
                    route.abort()   # …but the browser never hears back
                elif state["mode"] == "drop-request" and state["n"] <= 2:
                    route.abort()
                else:
                    route.continue_()
            page_a.route("**/api/workspaces/*/ops", route_ops)

            # C1: applied-but-unacked → retry replays → exactly ONE rev bump.
            rev0 = body(w1["id"])["rev"]
            state.update(mode="drop-response", n=0)
            before = body(w1["id"])["trees"]["primary"]["selected"]["P1-u2"]
            click_js(page_a, cycle_next(pri))
            wait_until(page_a, lambda: body(w1["id"])["trees"]["primary"]["selected"]["P1-u2"] != before,
                       8, "C1 retried select to land")
            page_a.wait_for_timeout(2000)  # let the replay attempt finish too
            b1 = body(w1["id"])
            assert b1["rev"] == rev0 + 1, \
                f"idempotent replay must not double-bump: rev {rev0} -> {b1['rev']}"

            # C2: two dead attempts, third lands inside the backoff window.
            state.update(mode="drop-request", n=0)
            before = b1["trees"]["primary"]["selected"]["P1-u2"]
            click_js(page_a, cycle_next(pri))
            wait_until(page_a, lambda: body(w1["id"])["trees"]["primary"]["selected"]["P1-u2"] != before,
                       10, "C2 third attempt to land")
            assert body(w1["id"])["rev"] == rev0 + 2, f"C2 rev: {body(w1['id'])['rev']}"
            page_a.unroute("**/api/workspaces/*/ops")
            assert gets["A"] == 0, \
                f"C must survive via RETRY, not refetch — page A body GETs: {gets['A']}"

            # ── D. two workspaces: no cross-refetch, no stale-rev drops ─────
            page_b.goto(f"{BASE}/?w={w2['id']}", wait_until="load", timeout=20000)
            page_b.wait_for_function("document.body.innerText.includes('P2- SIB-0')", timeout=15000)
            page_b.wait_for_timeout(600)
            gets["B"] = 0  # count from here: the switch itself legitimately GETs
            w2_rev = body(w2["id"])["rev"]
            for _ in range(3):  # burst in W1 (page A)
                click_js(page_a, cycle_next(pri))
                page_a.wait_for_timeout(400)
            page_a.wait_for_timeout(1500)
            page_b.wait_for_timeout(500)
            assert body(w2["id"])["rev"] == w2_rev, "W1 burst moved W2's rev"
            assert gets["B"] == 0, f"foreign-workspace ops caused refetches in B: {gets['B']}"
            # …and B's OWN ops still flow (rev tracking intact after the burst):
            click_js(page_b, cycle_next(pri))
            wait_until(page_b, lambda: body(w2["id"])["trees"]["primary"]["selected"]["P2-u2"] == "P2-s1",
                       8, "B's own select to land")
            page_b.wait_for_timeout(1000)
            assert gets["B"] == 0, f"B's own echo triggered a refetch (stale-rev drop?): {gets['B']}"

            # ── E. restart durability + rev continuity ──────────────────────
            pre = body(w1["id"])
            stop_server(proc)
            proc = start_server(scratch)
            post = body(w1["id"])
            assert post["rev"] == pre["rev"], f"rev lost across restart: {pre['rev']} -> {post['rev']}"
            assert post["trees"]["compare"]["selected"] == pre["trees"]["compare"]["selected"]
            assert any(n.get("content") == "C1- EDITED-BY-A"
                       for n in post["trees"]["compare"]["nodes"].values()), \
                "scenario-A edit lost across restart"
            # SSE reconnects; a fresh op afterwards must extend the SAME rev line.
            page_a.wait_for_function(
                "document.querySelector('.status-text')?.innerText === 'live'", timeout=30000)
            before = post["trees"]["primary"]["selected"]["P1-u2"]
            click_js(page_a, cycle_next(pri))
            wait_until(page_a, lambda: body(w1["id"])["trees"]["primary"]["selected"]["P1-u2"] != before,
                       10, "post-restart op to land")
            assert body(w1["id"])["rev"] == post["rev"] + 1, "rev not monotonic across restart"

            # C's aborted requests + E's downtime make network noise; only
            # NON-network console errors count.
            for tag, errs in errors.items():
                real = [e for e in errs
                        if "Failed to fetch" not in e and "ERR_CONNECTION" not in e
                        and "NetworkError" not in e and "net::" not in e
                        and "ERR_FAILED" not in e]
                assert not real, f"page {tag} console errors: {real}"
            browser.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if os.environ.get("KEEP_SCRATCH"):
            print(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    print("browser_ops_convergence: OK")


if __name__ == "__main__":
    main()
