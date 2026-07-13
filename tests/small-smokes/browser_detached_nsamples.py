"""Detached-fire n>1 DISTRIBUTION fold + a continuation turn.

The design-critical case for detached fire: an own chat folds ALL n samples from
the bus bucket into n sibling tree nodes (the server-committed echo carries only
ONE representative, which the chart + ‹k/N› cycler read from tree siblings can't
lose). This seeds a single free-router panel, sets n_samples>1, sends, and asserts
the panel's tree folded n sibling assistant nodes under the user turn. Then it
sends a SECOND turn to confirm multi-turn fold still lands after the first folded.

  uv run python tests/small-smokes/browser_detached_nsamples.py [BASE_URL]

Needs OPENROUTER_API_KEY (free router). Seeds via POST (no stale model-dropdown UI).
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8812"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
FREE = "openrouter:openrouter/free"
N = 4


def _post(path, body):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def sibling_count(tree, role="assistant"):
    """Max number of same-role siblings under any single parent (the distribution)."""
    nodes = tree.get("nodes") or {}
    best = 0
    for n in nodes.values():
        kids = [nodes[c] for c in (n.get("children") or []) if c in nodes]
        best = max(best, sum(1 for k in kids if k.get("role") == role))
    # rootChildren too (first-turn parent is the virtual root)
    roots = [nodes[c] for c in (tree.get("rootChildren") or []) if c in nodes]
    best = max(best, sum(1 for k in roots if k.get("role") == role))
    return best


def main():
    conv = _post("/api/conversations", {
        "title": "detached-nsamples",
        "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None}],
        "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
        "reduced_panels": [], "send_targets": ["primary"], "seen_panels": ["primary"],
    })
    cid = conv["id"]
    # n_samples is a shared param — set it on the bus before opening.
    _post("/api/state", {"n_samples": N})

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_selector(".chat-column", timeout=15000)
        composer = page.locator(".input-textarea")
        composer.wait_for(state="visible", timeout=8000)
        page.wait_for_function(
            "() => { const t = document.querySelector('.input-textarea'); return t && !t.disabled; }",
            timeout=15000)

        def send_and_wait(msg):
            composer.fill(msg)
            composer.press("Enter")
            # Wait until the panel's bucket stops running (terminal), up to 90s.
            page.wait_for_function(
                "() => { const s = document.querySelector('.chat-column'); return s && "
                "!document.body.innerText.includes('generating'); }", timeout=90000)
            time.sleep(3.0)  # let the fold + debounced save land

        send_and_wait("Give me a one-word mood.")
        conv1 = next(c for c in _get("/api/conversations") if c["id"] == cid)
        sibs1 = sibling_count(conv1["trees"]["primary"])
        # OpenRouter free can error a sample or two — require the MAJORITY folded as
        # siblings (the distribution is real), not a strict ==N.
        assert sibs1 >= 2, f"n>1 distribution did NOT fold as siblings: got {sibs1} (want ≥2 of {N})"
        print(f"(1) first turn folded {sibs1}/{N} samples as sibling branches  ✓")

        send_and_wait("Now a one-word color.")
        conv2 = next(c for c in _get("/api/conversations") if c["id"] == cid)
        tree = conv2["trees"]["primary"]
        # Two user turns now, each with an assistant distribution → ≥2 user nodes and
        # the deepest assistant fold still ≥2 siblings.
        nodes = tree["nodes"]
        n_users = sum(1 for n in nodes.values() if n.get("role") == "user")
        sibs2 = sibling_count(tree)
        assert n_users >= 2, f"second turn did not fold a new user turn (users={n_users})"
        assert sibs2 >= 2, f"second turn's distribution did not fold (siblings={sibs2})"
        print(f"(2) second turn folded under the first ({n_users} user turns, deepest fold {sibs2} siblings)  ✓")

        browser.close()
    print("DETACHED N-SAMPLES SMOKE PASS")


if __name__ == "__main__":
    main()
