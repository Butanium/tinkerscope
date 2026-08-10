"""Browser smoke for the Ctrl+K search palette + jump-and-reveal.

100% TOKEN-FREE: seeds two workspaces via POST /api/workspaces, then drives the
palette end-to-end:

  1. Ctrl+K opens the palette with the input focused; Esc closes it.
  2. A phrase buried in an OFF-PATH sibling (a non-selected sample) is found,
     wears the "hidden branch" tag, and Enter jumps: the OTHER workspace opens,
     the sibling becomes the ACTIVE branch (selectPathTo), its row flashes and
     takes the keyboard focus ring.
  3. That selection change persists: after the save debounce a reload still
     shows the off-path sibling as the active turn.
  4. A phrase inside a NON-LAST assistant's thinking is found (tagged), and the
     jump OPENS that row's reasoning fold (default state is folded).
  5. A sample fan where every sibling matches collapses to ONE row ("k of N
     samples").
  6. A workspace-NAME match shows in the pinned "workspaces" section and Enter
     switches to it.

No model calls.

  uv run python tests/small-smokes/browser_search_palette.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

OFFPATH = "NEEDLE-OFFPATH the forgotten screenshot sample"
THINK = "NEEDLE-THINK zebra stripes are load-bearing"
FAN = "NEEDLE-FAN appears in every sample"


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _node(nid, role, content, parent, children=(), **extra):
    return {"id": nid, "role": role, "content": content, "parent": parent,
            "children": list(children), **extra}


def geo_tree():
    """u1 → [a1(off-path), a2(selected, thinking)] → u2 → a3.
    a1 holds the off-path needle; a2's reasoning holds the thinking needle and
    is NOT the last assistant (so its fold defaults closed)."""
    return {
        "nodes": {
            "u1": _node("u1", "user", "capital of France?", None, ["a1", "a2"]),
            "a1": _node("a1", "assistant", OFFPATH, "u1"),
            "a2": _node("a2", "assistant", "Paris.", "u1", ["u2"], reasoning=THINK),
            "u2": _node("u2", "user", "and of Spain?", "a2", ["a3"]),
            "a3": _node("a3", "assistant", "Madrid.", "u2"),
        },
        "rootChildren": ["u1"],
        "selected": {"__root__": "u1", "u1": "a2", "a2": "u2", "u2": "a3"},
    }


def fan_tree():
    """One user turn with a 3-sample fan where EVERY sibling matches."""
    kids = ["f1", "f2", "f3"]
    nodes = {"uf": _node("uf", "user", "sing me something", None, kids)}
    for i, fid in enumerate(kids):
        nodes[fid] = _node(fid, "assistant", f"{FAN} (variant {i})", "uf")
    return {"nodes": nodes, "rootChildren": ["uf"],
            "selected": {"__root__": "uf", "uf": "f1"}}


def main():
    ws_a = _post("/api/workspaces", {
        "name": "geo probes", "trees": {"primary": geo_tree()},
        "panels": [{"id": "primary", "run_id": None, "checkpoint": None}]})["id"]
    ws_b = _post("/api/workspaces", {
        "name": "music box", "trees": {"primary": fan_tree()},
        "panels": [{"id": "primary", "run_id": None, "checkpoint": None}]})["id"]

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME))
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.goto(f"{BASE}/?w={ws_b}")
        page.wait_for_selector(".ws-picker", timeout=15000)

        def open_palette():
            page.keyboard.press("Control+k")
            expect(page.locator("[data-testid=palette-input]")).to_be_focused(timeout=3000)

        # 1. open + esc-close
        open_palette()
        page.keyboard.press("Escape")
        expect(page.locator("[data-testid=palette-input]")).to_have_count(0)

        # 2. off-path needle → tagged, jump reveals it as the ACTIVE branch
        open_palette()
        page.keyboard.type("NEEDLE-OFFPATH")
        hit = page.locator("[data-testid=palette-hit]")
        expect(hit).to_have_count(1, timeout=5000)
        expect(hit).to_contain_text("hidden branch")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "([want]) => document.querySelector('.ws-picker')?.dataset.wsId === want",
            arg=[ws_a], timeout=10000)
        flashed = page.locator(".message.reveal-flash")
        expect(flashed).to_have_count(1, timeout=5000)
        expect(flashed).to_contain_text("NEEDLE-OFFPATH")
        expect(page.locator(".message.kb-focused")).to_contain_text("NEEDLE-OFFPATH")
        # the off-path sibling is now the panel's ACTIVE rendered turn
        expect(page.locator(".chat-column .messages").first).to_contain_text(
            "NEEDLE-OFFPATH", timeout=5000)

        # 3. the selection change persists across a reload (debounced save)
        time.sleep(1.3)
        page.reload()
        page.wait_for_selector(".ws-picker", timeout=15000)
        expect(page.locator(".chat-column .messages").first).to_contain_text(
            "NEEDLE-OFFPATH", timeout=8000)

        # 4. thinking needle → tagged row; the jump OPENS the (folded) reasoning fold
        open_palette()
        page.keyboard.type("zebra stripes")
        hit = page.locator("[data-testid=palette-hit]")
        expect(hit).to_have_count(1, timeout=5000)
        expect(hit).to_contain_text("thinking")
        page.keyboard.press("Enter")
        flashed = page.locator(".message.reveal-flash")
        expect(flashed).to_have_count(1, timeout=5000)
        fold = flashed.locator("details.reasoning-primary")
        expect(fold).to_have_attribute("open", "", timeout=3000)
        expect(fold).to_contain_text("NEEDLE-THINK")

        # 5. a fully-matching sample fan collapses to ONE row
        open_palette()
        page.keyboard.type("NEEDLE-FAN")
        hit = page.locator("[data-testid=palette-hit]")
        expect(hit).to_have_count(1, timeout=5000)
        expect(hit).to_contain_text("3 of 3 samples")
        page.keyboard.press("Escape")

        # 6. workspace-name hit rides the pinned section; Enter switches to it
        open_palette()
        page.keyboard.type("music box")
        ws_hit = page.locator("[data-testid=palette-ws-hit]")
        expect(ws_hit).to_have_count(1, timeout=5000)
        page.keyboard.press("Enter")
        page.wait_for_function(
            "([want]) => document.querySelector('.ws-picker')?.dataset.wsId === want",
            arg=[ws_b], timeout=10000)

        browser.close()
    print("browser_search_palette: OK")


if __name__ == "__main__":
    main()
