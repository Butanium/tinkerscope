"""LIVE cross-client e2e for thread system prompts (free OpenRouter router —
2 model calls, zero cost; needs OPENROUTER_API_KEY on the server).

Point it at an ISOLATED instance (scripts/dev-isolated.sh), never the live one.
Browser open on a workspace + CLI fires:
  1. `tinkpg send --system "…BANANA…"` → the reply obeys the composed prompt
     (proves the thread part reaches the model), the panel mirror holds it,
     AND the browser folds the foreign thread with its `system` strip (the
     chat_done thread_system_prompt → reconcile → root stamp path).
  2. `tinkpg continue` (no --system) → the follow-up reply still obeys it
     (proves the mid-thread panel-mirror inherit).

First ran green 2026-07-21 (landing 7e90c24). Seeded/token-free twin:
browser_thread_system.py.

  uv run python tests/small-smokes/browser_thread_system_live.py [BASE_URL]
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
FREE = "openrouter:openrouter/free"
SYS = "SYS-E2E: You must end every reply with the single word BANANA in capitals."


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def tinkpg(*args):
    r = subprocess.run(
        ["uv", "run", "tinkpg", "--base-url", BASE, *args],
        capture_output=True, text=True, timeout=120,
        cwd="/home/c.dumas/tools/tinkerscope")
    print(f"$ tinkpg {' '.join(args[:3])}… → rc={r.returncode}")
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:], sep="\n---\n")
        sys.exit(1)
    return r.stdout


def main():
    conv = _post("/api/conversations", {
        "name": "live e2e thread system",
        "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
        "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None}],
        "seen_panels": ["primary"],
    })
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function(
            "document.querySelector('.input-textarea') && !document.querySelector('.input-textarea').disabled",
            timeout=15000)

        # ── 1. CLI new-thread probe with a thread system prompt ──
        out = tinkpg("send", "Say hello in three words.", "--system", SYS,
                     "--n", "1", "--max-tokens", "60")
        assert "thread system" in out, f"send plan should print the thread prompt:\n{out[:400]}"
        # the model actually obeyed the composed prompt
        page.wait_for_function(
            "document.body.innerText.includes('BANANA')", timeout=60000)
        # the browser folded the foreign thread WITH its provenance strip
        page.wait_for_function(
            "document.querySelector('[data-testid=thread-system-strip]')?.textContent.includes('SYS-E2E')",
            timeout=10000)
        # the panel mirror carries it (what a mid-thread CLI send inherits)
        st = _get("/api/state")
        assert st["panels"][0]["thread_system_prompt"] == SYS, st["panels"][0].get("thread_system_prompt")

        # ── 2. mid-thread continue (no --system) inherits the thread's prompt ──
        tinkpg("continue", "And goodbye in three words.", "--n", "1", "--max-tokens", "60")
        page.wait_for_function(
            "[...document.querySelectorAll('.message')].filter(m => m.textContent.includes('BANANA')).length >= 2",
            timeout=60000)
        strips = page.locator("[data-testid='thread-system-strip']")
        assert strips.count() == 1, f"still ONE thread → one strip, got {strips.count()}"

        assert not errors, f"page errors: {errors}"
        browser.close()
    print("live_thread_system_e2e: OK")


if __name__ == "__main__":
    main()
