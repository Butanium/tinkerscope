"""Capture the README feature-tour screenshots against a live tinkerscope.

Non-destructive: creates its OWN conversation and never deletes others. The
discovered Tinker example runs currently 404 ("Weights not found" — Tinker has
GC'd the old sampler weights), so this drives **OpenRouter reference models**
(which don't depend on Tinker weights) to get authentic sample cards / a
distribution chart / a branch cycler / a two-model compare.

Point it at an ISOLATED instance (NOT the one a teammate is actively editing).
Default :8901. Two OpenRouter models must be in the global list:
meta-llama/llama-3.2-3b-instruct and deepseek/deepseek-chat-v3.1.

  uv run python tests/small-smokes/browser_readme_shots.py [BASE_URL]
"""
import json
import sys
import traceback
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8901"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
OUT = Path(__file__).resolve().parents[2] / "docs" / "img"
OUT.mkdir(parents=True, exist_ok=True)

PRIMARY = "openrouter:meta-llama/llama-3.2-3b-instruct"
COMPARE = "openrouter:deepseek/deepseek-chat-v3.1"
FILTER_TERM = "q_nk"
FAN_PROMPT = "Name a color. Reply with ONLY the color, one word, nothing else."
COMPARE_PROMPT = "In one sentence, what makes a good cup of coffee?"


def patch_state(body):
    req = urllib.request.Request(
        f"{BASE}/api/state", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10).read()


def shot(page, name):
    page.screenshot(path=str(OUT / name))
    print("  wrote", OUT / name)


def wait_cards(page, n, timeout_ms=40000):
    waited = 0
    while waited < timeout_ms:
        if page.locator(".sample-card").count() >= n:
            page.wait_for_timeout(1200)  # let the rest fill
            return True
        page.wait_for_timeout(500)
        waited += 500
    return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('q_nk')", timeout=15000)

        page.locator("button.conv-icon-btn[aria-label='New conversation']").click()
        page.wait_for_timeout(500)

        # ── SHOT: sidebar with the type-to-filter box ──
        try:
            page.locator("input.model-filter").first.fill(FILTER_TERM)
            page.wait_for_timeout(300)
            page.locator(".sidebar").first.screenshot(path=str(OUT / "model-filter.png"))
            print("  wrote", OUT / "model-filter.png")
            page.locator("input.model-filter").first.fill("")
            page.wait_for_timeout(200)
        except Exception:
            traceback.print_exc()

        # Select the llama OpenRouter model in the primary panel.
        page.locator("select.model-slot-select").first.select_option(PRIMARY)
        page.wait_for_timeout(400)

        # ── Phase A: n>1 fan-out on the primary panel ──
        patch_state({"n_samples": 6, "max_tokens": 8, "temperature": 1.3})
        page.wait_for_timeout(300)
        ta = page.locator("textarea.input-textarea")
        ta.fill(FAN_PROMPT)
        ta.press("Enter")
        print("fired n=6 fan-out…")
        ok = wait_cards(page, 4)
        print("cards present:", ok, "(", page.locator(".sample-card").count(), "cards )")
        page.wait_for_timeout(400)
        try:
            shot(page, "n-samples.png")
        except Exception:
            traceback.print_exc()

        # ── SHOT: distribution chart ──
        try:
            page.locator("button[data-tooltip^='View response distribution']").first.click()
            page.wait_for_selector(".modal-overlay", timeout=4000)
            page.wait_for_timeout(700)
            shot(page, "distribution-chart.png")
            page.locator(".modal-close").first.click()
            page.wait_for_timeout(300)
        except Exception:
            traceback.print_exc()

        # ── SHOT: branch cycler (collapse to one reply, reveal toolbar) ──
        try:
            page.get_by_role("button", name="Make active").first.click()
            page.wait_for_timeout(500)
            page.locator(".message").last.hover()
            page.wait_for_timeout(400)
            shot(page, "chat-branching.png")
        except Exception:
            traceback.print_exc()

        # ── Phase B: two-model compare ──
        try:
            patch_state({"n_samples": 1, "max_tokens": 60, "temperature": 0.7})
            page.wait_for_timeout(200)
            page.get_by_role("button", name="Compare").click()
            page.wait_for_timeout(800)
            page.locator("select.model-slot-select").nth(1).select_option(COMPARE)
            page.wait_for_timeout(500)
            ta = page.locator("textarea.input-textarea")
            ta.fill(COMPARE_PROMPT)
            ta.press("Enter")
            print("fired compare send to both panels…")
            # wait until both panels show an assistant reply to the new question
            waited = 0
            while waited < 50000:
                msgs = page.locator(".message-content").count()
                if msgs >= 4:  # 2 user + 2 assistant (roughly)
                    break
                page.wait_for_timeout(500)
                waited += 500
            page.wait_for_timeout(1500)
            shot(page, "compare.png")
        except Exception:
            traceback.print_exc()

        print("console/page errors:", errors[:8] if errors else "none")
        browser.close()
        print("DONE — shots in", OUT)


if __name__ == "__main__":
    main()
