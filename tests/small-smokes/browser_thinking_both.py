"""thinking=BOTH smoke — cycle the sidebar Thinking pill to BOTH with Samples(n)=2,
send once, and assert the single send fans out into 2n=4 sample cards: the first n
tagged "no think", the last n tagged "think" (the per-sample mode chips), with the
backend state holding thinking="both". Exercises the tri-state toggle → /api/chat
dual-batch → tagged sample events → bucket render path end-to-end. Zero cost (free
OpenRouter model), no tinker servable-window dependency.

  uv run python tests/small-smokes/browser_thinking_both.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8820"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "openrouter:openrouter/free"  # free ROUTER (saved OR list) — survives single-provider outages
N = 2  # per-mode; BOTH → 2N cards
SHOT = "/tmp/tinkerscope_thinking_both.png"


def backend_state() -> dict:
    with urllib.request.urlopen(f"{BASE}/api/state", timeout=5) as r:
        return json.load(r)


def wait_backend(key: str, want, deadline_s: float = 8) -> object:
    """Sidebar edits are debounced POSTs; wait until the server actually holds the value."""
    deadline = time.time() + deadline_s
    while time.time() < deadline and backend_state().get(key) != want:
        time.sleep(0.1)
    return backend_state().get(key)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector("select.model-slot-select", timeout=15000)
        page.select_option("select.model-slot-select", value=MODEL)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # Fresh conversation so persisted branches from a prior run can't interfere.
        page.locator('button[aria-label="New conversation"]').first.click()
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # 'All' sample view → every card stacked, DOM order == sample_index order.
        page.locator(".seg-btn", has_text="All").first.click()

        # Samples(n) = 2.
        page.locator('input.sidebar-input[min="1"][max="200"]').first.fill(str(N))
        n_confirmed = wait_backend("n_samples", N)

        # Thinking = Both (segmented Off/On/Both control; "Both" is unique among
        # the seg buttons — Sample view's are All/Cycle).
        both_btn = page.locator(".seg-btn", has_text="Both").first
        both_btn.click()
        pill_text = "Both(active)" if "active" in (both_btn.get_attribute("class") or "") else "Both(?)"
        thinking_confirmed = wait_backend("thinking", "both")

        # Send once.
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill("Reply with exactly the word PONG and nothing else.")
        ta.press("Enter")

        # One send must fan out into 2N cards (N without thinking + N with).
        page.wait_for_function(
            "document.querySelectorAll('.sample-card').length === %d" % (2 * N), timeout=120000
        )
        page.wait_for_selector(
            'button[data-tooltip^="Make this the active branch"]:not([disabled])',
            timeout=30000,
        )
        card_count = page.locator(".sample-card").count()

        # Every card carries a mode chip; exactly N are the thinking half. DOM order
        # follows sample_index, so the no-think half must come FIRST. (upper():
        # innerText reflects the chip's CSS text-transform; don't depend on it.)
        chips = [t.strip().upper() for t in page.locator(".sample-card .mode-tag").all_inner_texts()]
        think_chips = page.locator(".sample-card .mode-tag.mode-think").count()
        order_ok = chips == ["NO THINK"] * N + ["THINK"] * N

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"backend n_samples confirmed: {n_confirmed} (want {N})")
        print(f"Both segment: {pill_text!r}; backend thinking: {thinking_confirmed!r} (want 'both')")
        print(f"sample cards rendered: {card_count} (want {2 * N})")
        print(f"mode chips (DOM order): {chips} -> no-think half first: {order_ok}")
        print(f"thinking-half chips: {think_chips} (want {N})")
        print(f"console/page errors: {errors or 'none'}")
        ok = (
            n_confirmed == N
            and thinking_confirmed == "both"
            and card_count == 2 * N
            and order_ok
            and think_chips == N
            and not errors
        )
        print("THINKING_BOTH SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
