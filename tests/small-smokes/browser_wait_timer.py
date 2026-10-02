"""Browser smoke for the "waiting on the model · Ns" readout on a running panel
with no sample yet (the tinker sample call has no server-side deadline, so this is
what distinguishes a cold model's warmup from a hang).

Needs a server whose producer NEVER returns a sample — real sampling would race the
20 s threshold. Launch one with the OpenRouter producer patched to hang, e.g. a
script that sets `tinkerscope.api.openrouter.sample_one{,_stream}` to coroutines
awaiting a never-set Event, then runs `tinkerscope serve <empty dir> --port N`
(isolated XDG_STATE_HOME). Checks, for n=1 (pre-first-token placeholder) and n=3
(progress strip): hidden before the threshold, shown with a ticking count after,
gone once Stop ends the chat.

  uv run python tests/small-smokes/browser_wait_timer.py [BASE_URL] [SHOT_DIR]
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8799"
SHOTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
TIMER = '[data-testid="wait-timer"]'
THRESHOLD_S = 20


def _post(path, body):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _secs(text: str) -> int:
    m = re.search(r"(?:(\d+)m)?(\d+)s$", text.strip())
    assert m, text
    return int(m.group(1) or 0) * 60 + int(m.group(2))


def scenario(page, n: int) -> None:
    _post("/api/state", {"n_samples": n, "max_tokens": 64})
    page.locator('button[aria-label="New workspace"]').first.click()
    page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)
    ta = page.locator(".input-textarea").first
    ta.click()
    ta.fill(f"hello n={n}")
    ta.press("Enter")
    page.wait_for_selector('[data-testid="stop-panel"]', timeout=10000)

    page.wait_for_timeout(5000)
    assert page.locator(TIMER).count() == 0, f"n={n}: readout shown before the threshold"

    page.wait_for_selector(TIMER, timeout=(THRESHOLD_S + 5) * 1000)
    a = _secs(page.locator(TIMER).inner_text())
    page.wait_for_timeout(2100)
    b = _secs(page.locator(TIMER).inner_text())
    assert a >= THRESHOLD_S and b >= a + 2, (n, a, b)
    print(f"  n={n}: '{page.locator(TIMER).inner_text()}' (ticked {a}s → {b}s)")
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.locator(".message").last.screenshot(path=str(SHOTS / f"wait_timer_n{n}.png"))

    page.eval_on_selector('[data-testid="stop-panel"]', "el => el.click()")
    page.wait_for_selector(TIMER, state="detached", timeout=8000)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector(".btn-stop-sidebar", timeout=15000)
        _post("/api/state", {"panels": [{"id": "primary", "run_id": "openrouter:openrouter/free",
                                         "checkpoint": None, "messages": []}]})
        scenario(page, 1)
        scenario(page, 3)
        browser.close()
    print("OK")


if __name__ == "__main__":
    main()
