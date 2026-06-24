"""Browser smoke for the conversation-id-in-URL feature (?c=<id>).

100% TOKEN-FREE: creates two empty named conversations via /api/conversations,
then drives the browser to verify the `?c=` query param round-trips:

  1. load  /?c=<A>      → conversation A opens (not the newest), URL keeps c=A
  2. load  /            → newest opens, URL is normalized to ?c=<newest>
  3. dropdown-select B  → URL pushes ?c=<B>, active conv switches
  4. browser back       → URL returns to ?c=<A>, active conv follows it
  5. load  /?c=<bogus>  → falls back to newest, shows a notice, normalizes URL

Oracle: the conversation <select> (its value == convo.activeId) + window URL.

  uv run python tests/small-smokes/browser_conversation_url.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body, method="POST"):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method=method)
    return json.load(urllib.request.urlopen(req, timeout=10))


def _clean_conversations():
    for c in _get("/api/conversations"):
        urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/api/conversations/{c['id']}", method="DELETE"),
            timeout=10,
        ).read()


def _active_id(page):
    """The conversation <select> is value-bound to convo.activeId."""
    return page.locator("select.conv-select").input_value()


def _wait_active(page, want, timeout=10000):
    page.wait_for_function(
        "([want]) => document.querySelector('select.conv-select')?.value === want",
        arg=[want], timeout=timeout)


def _param_c(page):
    return page.evaluate("() => new URL(location.href).searchParams.get('c')")


def main():
    _clean_conversations()
    # Create A first, then B → B is the newest (default active when no ?c=).
    a = _post("/api/conversations", {"name": "URLSMOKE-ALPHA"})
    b = _post("/api/conversations", {"name": "URLSMOKE-BETA"})
    newest = max(_get("/api/conversations"), key=lambda c: c["updated_at"])
    assert newest["id"] == b["id"], "expected B to be newest"

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ── 1. Hard-load /?c=<A> opens A (not the newest B), URL keeps c=A ──
        page.goto(f"{BASE}/?c={a['id']}", wait_until="load", timeout=20000)
        _wait_active(page, a["id"])
        assert _active_id(page) == a["id"], "load ?c=A should open A"
        assert _param_c(page) == a["id"], f"URL should keep c=A, got {_param_c(page)}"
        print("1. load ?c=A opens A, URL preserved: OK")

        # ── 2. Hard-load / (no param) opens newest B + normalizes URL to ?c=B ──
        page.goto(f"{BASE}/", wait_until="load", timeout=20000)
        _wait_active(page, b["id"])
        page.wait_for_function(
            "([want]) => new URL(location.href).searchParams.get('c') === want",
            arg=[b["id"]], timeout=8000)
        assert _active_id(page) == b["id"], "no param should open newest (B)"
        print("2. load / opens newest B, URL normalized to ?c=B: OK")

        # ── 3. Selecting A in the dropdown pushes ?c=A and switches ──
        page.locator("select.conv-select").select_option(a["id"])
        _wait_active(page, a["id"])
        page.wait_for_function(
            "([want]) => new URL(location.href).searchParams.get('c') === want",
            arg=[a["id"]], timeout=8000)
        assert _param_c(page) == a["id"], "dropdown select should push ?c=A"
        print("3. dropdown select pushes ?c=A and switches: OK")

        # ── 4. Browser back returns to ?c=B and the active conv follows ──
        page.go_back()
        _wait_active(page, b["id"])
        assert _param_c(page) == b["id"], f"back should restore c=B, got {_param_c(page)}"
        print("4. browser back restores ?c=B + active conv follows URL: OK")

        # ── 5. Unknown id falls back to newest, shows a notice, normalizes URL ──
        page.goto(f"{BASE}/?c=does-not-exist-zzz", wait_until="load", timeout=20000)
        _wait_active(page, b["id"])
        page.wait_for_function(
            "() => document.body.innerText.includes('was not found here')", timeout=8000)
        page.wait_for_function(
            "([want]) => new URL(location.href).searchParams.get('c') === want",
            arg=[b["id"]], timeout=8000)
        assert _active_id(page) == b["id"], "unknown id should fall back to newest (B)"
        print("5. unknown id → fallback to newest + notice + URL normalized: OK")

        assert not errors, f"console/page errors: {errors}"
        browser.close()
        print("CONVERSATION-URL SMOKE PASS")


if __name__ == "__main__":
    main()
