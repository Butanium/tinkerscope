"""Browser smoke for the "Stop all generation" button — the fix for a stuck
`running` state that wedged every busy-surface forever.

Two scenarios, both against a slow free-OpenRouter generation (zero cost):

  A. OWNED chat — the browser fires the send itself (local AbortController). Click
     Stop → the fetch aborts → the server sees the disconnect and fires its
     guaranteed terminal → `running` clears (composer re-enables, Stop disables)
     PROMPTLY, and no console errors.

  B. CROSS-CLIENT chat — a raw POST /api/chat (unowned client_token, as tinkpg or
     another tab would fire) is streaming; the browser shows it running via the bus
     but has NO local controller. Click Stop → it must call the server cancel
     endpoint by chat_id and the chat dies too (running clears).

The model + conversation are seeded via the API (the sidebar model picker is a
ModelDropdown now, not a <select>), so this smoke drives only the composer and the
Stop button.

Point at an ISOLATED instance (scripts/dev-isolated.sh), never the live :8767.

  uv run python tests/small-smokes/browser_stop_generation.py [BASE_URL]
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8795"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL_SEL = "openrouter:openrouter/free"  # free ROUTER sentinel (panelCanChat → eligible)
OR_MODEL = "openrouter/free"
MAX_TOKENS = 8000  # big enough that the generation can't finish before we click Stop
LONG_PROMPT = (
    "Write an extremely long, detailed, 2000-word essay about the entire history of "
    "the Roman Empire, century by century. Do not stop early; keep going."
)


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
        return json.load(r)


def _fire_raw_chat(token: str, stop_flag: dict) -> None:
    """POST /api/chat as an EXTERNAL client (tinkpg / another tab). Reads the SSE
    stream to completion; when the browser's Stop cancels it server-side, the stream
    ends and this returns. Records that it ended (so we can assert the cancel took)."""
    body = {
        "openrouter_model": OR_MODEL,
        "messages": [{"role": "user", "content": LONG_PROMPT}],
        "n_samples": 1, "max_tokens": MAX_TOKENS, "panel": "primary",
        "broadcast": True, "client_token": token,
    }
    req = urllib.request.Request(
        f"{BASE}/api/chat", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            for _ in r:  # drain until the server closes the stream (cancel → done)
                pass
    except Exception as e:  # noqa: BLE001 — record, don't mask
        stop_flag["err"] = str(e)
    finally:
        stop_flag["ended"] = time.time()


# Stop button: enabled ⇔ anyRunning (disabled={!anyRunning}). The cleanest running signal.
STOP_ENABLED = ".btn-stop-sidebar:not([disabled])"
STOP_DISABLED = ".btn-stop-sidebar[disabled]"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector(".btn-stop-sidebar", timeout=15000)

        # Seed the free OR model onto primary + a slow, single-sample config via the API.
        _post("/api/state", {
            "panels": [{"id": "primary", "run_id": MODEL_SEL, "checkpoint": None, "messages": []}],
            "n_samples": 1, "max_tokens": MAX_TOKENS, "temperature": 1.0,
        })
        # Fresh conversation so we have an activeId to send into (keeps the model).
        page.locator('button[aria-label="New conversation"]').first.click()
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        results = {}

        # ── Scenario A: OWNED chat — browser fires, then Stop aborts it ──────────
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill(LONG_PROMPT)
        ta.press("Enter")
        # Wait until it's actually running (Stop enabled).
        page.wait_for_selector(STOP_ENABLED, timeout=30000)
        # Click Stop (programmatic — no autoscroll games; button is always in view anyway).
        t0 = time.time()
        page.eval_on_selector(".btn-stop-sidebar", "el => el.click()")
        # Running must clear PROMPTLY (owned abort is local + server terminal).
        page.wait_for_selector(STOP_DISABLED, timeout=8000)
        results["owned_clear_s"] = round(time.time() - t0, 2)
        results["owned_composer_reenabled"] = (
            page.locator(".input-textarea:not([disabled])").count() > 0
        )
        # server side agrees nothing is running
        time.sleep(0.3)
        results["owned_backend_running"] = _get("/api/state").get("running")

        # ── Scenario B: CROSS-CLIENT chat — raw POST, browser Stop cancels it ────
        # Fresh conversation to isolate from A's committed/partial thread.
        page.locator('button[aria-label="New conversation"]').first.click()
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        token = "external-cli-token-xyz"
        flag: dict = {}
        th = threading.Thread(target=_fire_raw_chat, args=(token, flag), daemon=True)
        th.start()

        # The browser must reflect the external chat as running (bus-driven), even
        # though it has no local controller for it.
        page.wait_for_selector(STOP_ENABLED, timeout=30000)
        results["external_shows_running"] = True

        t1 = time.time()
        page.eval_on_selector(".btn-stop-sidebar", "el => el.click()")
        # The browser's Stop must reach the server (POST cancel) and clear running.
        page.wait_for_selector(STOP_DISABLED, timeout=10000)
        results["external_clear_s"] = round(time.time() - t1, 2)

        # The raw request's stream must actually END (server cancelled it), not hang.
        th.join(timeout=15)
        results["external_stream_ended"] = not th.is_alive()
        time.sleep(0.3)
        results["external_backend_running"] = _get("/api/state").get("running")

        page.screenshot(path="/tmp/tinkerscope_stop_generation.png", full_page=True)
        browser.close()

        results["console_errors"] = errors or "none"
        for k, v in results.items():
            print(f"  {k}: {v}")

        ok = (
            results["owned_composer_reenabled"]
            and results["owned_backend_running"] is False
            and results["owned_clear_s"] < 8
            and results["external_shows_running"]
            and results["external_stream_ended"]
            and results["external_backend_running"] is False
            and results["external_clear_s"] < 10
            and not errors
        )
        print("STOP_GENERATION SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
