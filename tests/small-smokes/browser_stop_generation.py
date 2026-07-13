"""Browser smoke for the "Stop all generation" button — the fix for a stuck
`running` state that wedged every busy-surface forever.

Two scenarios, both against a slow free-OpenRouter generation (zero cost):

  A. OWNED chat — the browser fires the send itself (local AbortController). Click
     Stop → the fetch aborts → the server sees the disconnect and fires its
     guaranteed terminal → `running` clears (composer re-enables, Stop disables)
     PROMPTLY, and clicking Stop logs no console error.

  B. CROSS-CLIENT chat — a raw POST /api/chat (unowned client_token, as tinkpg or
     another tab would fire) is streaming; the browser shows it running via the bus
     but has NO local controller. Click Stop → it must call the server cancel
     endpoint by chat_id and the chat dies too (running clears + the raw stream ends).

FREE-OR FLAKINESS: the free router intermittently pre-start-errors (fires chat_error
BEFORE chat_begin, so `running` never flips). That's a provider hiccup, not a bug in
the stop path — so each scenario RETRIES the fire until the browser actually reaches
the running state (mirrors browser_panel_foreign_fold's positive-control retry). If it
can't reach running after N tries, THAT is a real failure.

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

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8796"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL_SEL = "openrouter:openrouter/free"  # free ROUTER sentinel (panelCanChat → eligible)
OR_MODEL = "openrouter/free"
MAX_TOKENS = 8000  # big enough that the generation can't finish before we click Stop
LONG_PROMPT = (
    "Write an extremely long, detailed, 2000-word essay about the entire history of "
    "the Roman Empire, century by century. Do not stop early; keep going."
)
# Reaching "running" through the flaky free router: retry the fire this many times,
# each waiting this long for Stop to enable, before declaring a real failure.
FIRE_ATTEMPTS = 6
RUNNING_WAIT_S = 9

# Stop button: enabled ⇔ anyRunning (disabled={!anyRunning}). The cleanest running signal.
STOP_ENABLED = ".btn-stop-sidebar:not([disabled])"
STOP_DISABLED = ".btn-stop-sidebar[disabled]"


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def _get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
        return json.load(r)


def _fire_raw_chat(token: str, flag: dict) -> None:
    """POST /api/chat as an EXTERNAL client (tinkpg / another tab). Reads the SSE
    stream to completion; when the browser's Stop cancels it server-side (or it
    pre-start-errors), the stream ends and this returns."""
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
            for _ in r:  # drain until the server closes the stream (cancel / error → done)
                pass
    except Exception as e:  # noqa: BLE001 — record, don't mask
        flag["err"] = str(e)
    finally:
        flag["ended"] = True


def _fire_until_running(page, fire_fn, label: str, settle_fn=None) -> bool:
    """Fire a chat (fire_fn) and wait for the browser to show it running (Stop enabled).
    Free-OR pre-start-errors intermittently → running never flips; retry on timeout.

    CRITICAL before each retry: wait for the PREVIOUS attempt to fully settle
    (`settle_fn(i) -> bool`, true = settled). A "hiccup" that is actually a SLOW
    START (free-OR connect can exceed the wait window) would otherwise overlap the
    retry: two chats run at once, the single-slot render bucket keeps only the
    newest chat_id, and Stop (which cancels by the bucket's id) can never reach
    the orphan — the server stays `running` and the smoke fails on a phantom.
    Verified: every historical failure of this smoke had retries ≥ 1; zero-retry
    runs always passed, on both storage-v2 and main."""
    for i in range(FIRE_ATTEMPTS):
        fire_fn(i)
        deadline = time.time() + RUNNING_WAIT_S
        while time.time() < deadline:
            if page.locator(STOP_ENABLED).count() > 0:
                return True
            page.wait_for_timeout(200)
        # Late start? Give the attempt a grace window to either reach running
        # (return True — it was just slow) or genuinely end before re-firing.
        # 90s deliberately OUTLASTS the raw request's own 60s timeout, so a hung
        # upstream connect always resolves to a dead stream before we overlap.
        grace = time.time() + 90
        while time.time() < grace and settle_fn and not settle_fn(i):
            if page.locator(STOP_ENABLED).count() > 0:
                return True
            page.wait_for_timeout(300)
        print(f"  [{label}] attempt {i + 1}/{FIRE_ATTEMPTS}: never reached running (free-OR "
              "pre-start hiccup), retrying")
    return False


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
        def fire_owned(_i):
            page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)
            ta = page.locator(".input-textarea").first
            ta.click()
            ta.fill(LONG_PROMPT)
            ta.press("Enter")

        results["owned_reached_running"] = _fire_until_running(
            page, fire_owned, "owned",
            # settled = the server has nothing in flight (a pre-start error already
            # broadcast its terminal); a late-STARTING chat keeps this false until
            # the grace loop sees Stop enable and returns True instead.
            settle_fn=lambda _i: _get("/api/state").get("running") is False)
        if results["owned_reached_running"]:
            # Assert "Stop is clean" for the stop action itself — free-OR flakiness
            # upstream shouldn't count against the stop path.
            err_mark = len(errors)
            t0 = time.time()
            page.eval_on_selector(".btn-stop-sidebar", "el => el.click()")
            page.wait_for_selector(STOP_DISABLED, timeout=8000)
            results["owned_clear_s"] = round(time.time() - t0, 2)
            results["owned_composer_reenabled"] = (
                page.locator(".input-textarea:not([disabled])").count() > 0
            )
            time.sleep(0.3)
            results["owned_backend_running"] = _get("/api/state").get("running")
            results["owned_stop_errors"] = errors[err_mark:]

        # ── Scenario B: CROSS-CLIENT chat — raw POST, browser Stop cancels it ────
        # Fresh conversation to isolate from A's committed/partial thread.
        page.locator('button[aria-label="New conversation"]').first.click()
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        threads: list[dict] = []

        def fire_external(i):
            flag: dict = {"token": f"external-cli-token-{i}"}
            th = threading.Thread(target=_fire_raw_chat, args=(flag["token"], flag), daemon=True)
            flag["thread"] = th
            th.start()
            threads.append(flag)

        results["external_reached_running"] = _fire_until_running(
            page, fire_external, "external",
            # settled = this attempt's held stream actually ENDED (its pre-start
            # error closed it); an attempt still connecting must not be overlapped.
            settle_fn=lambda i: not threads[i]["thread"].is_alive())
        if results["external_reached_running"]:
            err_mark = len(errors)
            t1 = time.time()
            page.eval_on_selector(".btn-stop-sidebar", "el => el.click()")
            page.wait_for_selector(STOP_DISABLED, timeout=10000)
            results["external_clear_s"] = round(time.time() - t1, 2)
            # Every raw request's stream must END (the running one server-cancelled, the
            # pre-start-errored retries already closed), not hang.
            for f in threads:
                f["thread"].join(timeout=15)
            results["external_all_streams_ended"] = all(not f["thread"].is_alive() for f in threads)
            time.sleep(0.3)
            results["external_backend_running"] = _get("/api/state").get("running")
            results["external_stop_errors"] = errors[err_mark:]

        page.screenshot(path="/tmp/tinkerscope_stop_generation.png", full_page=True)
        browser.close()

        for k, v in results.items():
            print(f"  {k}: {v}")
        print(f"  total console errors seen (incl. upstream OR flakiness): {errors or 'none'}")

        ok = (
            results.get("owned_reached_running")
            and results.get("owned_composer_reenabled")
            and results.get("owned_backend_running") is False
            and results.get("owned_clear_s", 99) < 8
            and not results.get("owned_stop_errors")
            and results.get("external_reached_running")
            and results.get("external_all_streams_ended")
            and results.get("external_backend_running") is False
            and results.get("external_clear_s", 99) < 10
            and not results.get("external_stop_errors")
        )
        print("STOP_GENERATION SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
