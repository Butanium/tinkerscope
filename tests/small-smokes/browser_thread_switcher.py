"""Browser smoke for the ThreadSwitcher — the cross-panel thread jump.

100% TOKEN-FREE: seeds a 2-panel conversation via POST /api/conversations where
the panels hold DIVERGENT thread sets (primary: ALPHA+BETA, compare: ALPHA+GAMMA
— ALPHA shared, the others panel-local), opens it with ?c=<id>, then:

  1. the ⑂ threads button renders (3 distinct threads) and the menu lists them
     with per-thread panel coverage (ALPHA ×2, BETA ×1, GAMMA ×1);
  2. picking the SHARED thread (ALPHA) switches BOTH panels to it;
  3. picking a primary-only thread (BETA) switches primary and leaves compare
     untouched — no forced alignment across panels;
  4. after the debounce-save, a reload restores exactly that mixed selection.

No model calls.

  uv run python tests/small-smokes/browser_thread_switcher.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

ALPHA = "PROMPT-ALPHA shared across both panels"
BETA = "PROMPT-BETA only in primary"
GAMMA = "PROMPT-GAMMA only in compare"


def seed_tree(threads, selected_root):
    """One root user node per (id, prompt) in `threads`, each with one assistant
    child so the thread renders content; `selected_root` = active thread id."""
    nodes, roots = {}, []
    for rid, prompt in threads:
        aid = f"{rid}-a"
        nodes[rid] = {"id": rid, "role": "user", "content": prompt,
                      "parent": None, "children": [aid]}
        nodes[aid] = {"id": aid, "role": "assistant", "content": f"reply to {prompt}",
                      "parent": rid, "children": []}
        roots.append(rid)
    return {"nodes": nodes, "rootChildren": roots,
            "selected": {"__root__": selected_root}}


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


PANEL_FIRST_MSG = """() => [...document.querySelectorAll('.chat-column .messages')]
  .map((m) => m.querySelector('.message')?.textContent?.match(/PROMPT-(ALPHA|BETA|GAMMA)/)?.[0] ?? null)"""


def first_msgs(page):
    return page.evaluate(PANEL_FIRST_MSG)


FREE = "openrouter:openrouter/free"  # any non-null run_id: the load-time phantom-panel
# self-heal DROPS panels with run_id == null, which would collapse the 2-panel seed.


def main():
    conv = _post("/api/conversations", {
        "name": "thread switcher smoke",
        "trees": {
            # primary starts on BETA, compare on GAMMA — a divergent selection
            "primary": seed_tree([("t-alpha", ALPHA), ("t-beta", BETA)], "t-beta"),
            "compare": seed_tree([("t-alpha2", ALPHA), ("t-gamma", GAMMA)], "t-gamma"),
        },
        "panels": [
            {"id": "primary", "run_id": FREE, "checkpoint": None},
            {"id": "compare", "run_id": FREE, "checkpoint": None},
        ],
        "seen_panels": ["primary", "compare"],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 800})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('PROMPT-BETA')", timeout=15000)

        assert first_msgs(page) == ["PROMPT-BETA", "PROMPT-GAMMA"], f"seed selection: {first_msgs(page)}"

        # ── 1. button + menu contents ──
        btn = page.locator("[data-testid='thread-switcher-btn']")
        assert btn.count() == 1, "switcher button should render with 3 distinct threads"
        assert "3" in btn.text_content(), f"button should count 3 threads: {btn.text_content()!r}"
        btn.click()
        rows = page.locator("[data-testid='thread-menu'] .thread-row")
        assert rows.count() == 3, f"menu should list 3 threads, got {rows.count()}"
        texts = [rows.nth(i).text_content() for i in range(3)]
        assert "×2" in texts[0] and "ALPHA" in texts[0], f"ALPHA row should show ×2: {texts[0]!r}"
        assert "×1" in texts[1] and "BETA" in texts[1], f"BETA row should show ×1: {texts[1]!r}"
        assert "×1" in texts[2] and "GAMMA" in texts[2], f"GAMMA row: {texts[2]!r}"

        # ── 2. picking the SHARED thread switches both panels ──
        rows.nth(0).click()
        page.wait_for_function(
            "document.querySelectorAll('[data-testid=thread-menu]').length === 0", timeout=5000)
        assert first_msgs(page) == ["PROMPT-ALPHA", "PROMPT-ALPHA"], \
            f"ALPHA pick should switch both panels: {first_msgs(page)}"

        # ── 3. picking a primary-only thread leaves compare untouched ──
        btn.click()
        page.locator("[data-testid='thread-menu'] .thread-row").nth(1).click()
        assert first_msgs(page) == ["PROMPT-BETA", "PROMPT-ALPHA"], \
            f"BETA pick must not force-align compare: {first_msgs(page)}"

        # ── 4. the switch persists (debounce-save → reload restores it) ──
        time.sleep(2.5)
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('PROMPT-BETA')", timeout=15000)
        assert first_msgs(page) == ["PROMPT-BETA", "PROMPT-ALPHA"], \
            f"reload should restore the mixed selection: {first_msgs(page)}"

        assert not errors, f"console errors: {errors}"
        browser.close()
    print("browser_thread_switcher: OK")


if __name__ == "__main__":
    main()
