"""Add-panel modes: default = clone the first panel's thread; Shift+add = blank.

Clément's decision on the "new panel" default: keep the clone (compare a second
model on the same conversation), and make Shift+add start the panel EMPTY. This
smoke pins both: a plain add clones panel 0's thread; a Shift+add gives an empty
panel (0 messages).

  uv run python tests/small-smokes/browser_panel_add_modes.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MARK = "CLONE-ME-primary-thread"


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=15))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read() or b"{}")


def main():
    conv = _post("/api/conversations", {
        "name": "add-modes",
        "trees": {"primary": {"nodes": {
            "s0": {"id": "s0", "role": "user", "content": MARK + " — question",
                   "parent": None, "children": ["s1"]},
            "s1": {"id": "s1", "role": "assistant", "content": MARK + " — answer",
                   "parent": "s0", "children": []},
        }, "rootChildren": ["s0"], "selected": {}}},
        "panels": [{"id": "primary", "run_id": None, "checkpoint": None}],
    })
    cid = conv["id"]

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{BASE}/?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_function(f"document.body.innerText.includes({json.dumps(MARK)})", timeout=15000)

        # default add → clone
        page.locator("button.btn-add-model").first.click()
        page.wait_for_function("document.querySelectorAll('.chat-column').length === 2", timeout=8000)
        page.wait_for_timeout(300)
        clone_col = page.locator(".chat-column").nth(1).inner_text()
        default_clone = MARK in clone_col

        # Shift+add → blank. The app reads its own window-tracked shiftDown, which
        # Playwright's Shift modifier sets via the real keydown it dispatches.
        page.locator("button.btn-add-model").first.click(modifiers=["Shift"])
        page.wait_for_function("document.querySelectorAll('.chat-column').length === 3", timeout=8000)
        page.wait_for_timeout(300)
        blank_col = page.locator(".chat-column").nth(2).inner_text()
        shift_blank = MARK not in blank_col

        st = _get("/api/state")
        panels = [(pp["id"], len(pp["messages"])) for pp in st["panels"]]
        page.screenshot(path="/tmp/claude-1000/-home-c-dumas-tools-tinkerscope/"
                        "6535889f-5ec1-46d1-b033-8db9788307b6/scratchpad/add_modes.png",
                        full_page=True)
        browser.close()

    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/conversations/{cid}", method="DELETE"),
        timeout=10).read()

    print(f"default add clones panel 0's thread:  {default_clone}")
    print(f"Shift+add starts the panel blank:     {shift_blank}")
    print(f"state panels (id, #msgs):             {panels}")
    print("console/page errors:", errors or "none")
    # primary(2) clone(2) blank(0); third panel id is 'p-2' (after reserved 'compare')
    ok = (default_clone and shift_blank and not errors
          and panels == [("primary", 2), ("compare", 2), ("p-2", 0)])
    print("PANEL ADD-MODES SMOKE:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
