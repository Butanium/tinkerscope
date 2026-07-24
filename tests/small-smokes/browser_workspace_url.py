"""Browser smoke for the workspace-id-in-URL feature (?w=<id>).

100% TOKEN-FREE: creates two empty named workspaces via /api/workspaces,
then drives the browser to verify the `?w=` query param round-trips:

  1. load  /?w=<A>      → workspace A opens (not the newest), URL keeps w=A
  2. load  /            → newest opens, URL is normalized to ?w=<newest>
  3. dropdown-select B  → URL pushes ?w=<B>, active conv switches
  4. browser back       → URL returns to ?w=<A>, active conv follows it
  5. load  /?w=<bogus>  → falls back to newest, shows a notice, normalizes URL

  6. load  /?c=<A>      → the PRE-v1.0.0 param is still honored on read
                          (old bookmarks / open tabs) and rewritten to ?w=<A>

Oracle: the workspace <select> (its value == ws.activeId) + window URL.

  uv run python tests/small-smokes/browser_workspace_url.py [BASE_URL]
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
    for c in _get("/api/workspaces"):
        urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/api/workspaces/{c['id']}", method="DELETE"),
            timeout=10,
        ).read()


def _active_id(page):
    """The workspace <select> is value-bound to convo.activeId."""
    return page.locator("select.ws-select").input_value()


def _wait_active(page, want, timeout=10000):
    page.wait_for_function(
        "([want]) => document.querySelector('select.ws-select')?.value === want",
        arg=[want], timeout=timeout)


def _param_w(page):
    return page.evaluate("() => new URL(location.href).searchParams.get('w')")


def _newest_id():
    return max(_get("/api/workspaces"), key=lambda c: c["updated_at"])["id"]


def main():
    _clean_conversations()
    # Create A first, then B → B is the newest (default active when no ?w=).
    # NB: OPENING a bare-API workspace can bump its updated_at once (its
    # panels lack `seen_panels`, so syncPanels defaults them ON and persists —
    # a debounced ~400ms PATCH). So "the newest" is re-read from the API after
    # letting pending saves settle, instead of assuming it stays B forever.
    a = _post("/api/workspaces", {"name": "URLSMOKE-ALPHA"})
    b = _post("/api/workspaces", {"name": "URLSMOKE-BETA"})
    newest = max(_get("/api/workspaces"), key=lambda c: c["updated_at"])
    assert newest["id"] == b["id"], "expected B to be newest"

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ── 1. Hard-load /?w=<A> opens A (not the newest B), URL keeps w=A ──
        page.goto(f"{BASE}/?w={a['id']}", wait_until="load", timeout=20000)
        _wait_active(page, a["id"])
        assert _active_id(page) == a["id"], "load ?w=A should open A"
        assert _param_w(page) == a["id"], f"URL should keep w=A, got {_param_w(page)}"
        print("1. load ?w=A opens A, URL preserved: OK")

        # ── 2. Hard-load / (no param) opens the newest + normalizes the URL ──
        page.wait_for_timeout(700)  # let step 1's debounced first-open save land
        want = _newest_id()
        page.goto(f"{BASE}/", wait_until="load", timeout=20000)
        _wait_active(page, want)
        page.wait_for_function(
            "([want]) => new URL(location.href).searchParams.get('w') === want",
            arg=[want], timeout=8000)
        assert _active_id(page) == want, "no param should open the newest"
        print("2. load / opens the newest, URL normalized: OK")

        # ── 3. Selecting the OTHER conv in the dropdown pushes ?c and switches ──
        other = a["id"] if want == b["id"] else b["id"]
        page.locator("select.ws-select").select_option(other)
        _wait_active(page, other)
        page.wait_for_function(
            "([want]) => new URL(location.href).searchParams.get('w') === want",
            arg=[other], timeout=8000)
        assert _param_w(page) == other, "dropdown select should push ?w=<other>"
        print("3. dropdown select pushes ?w and switches: OK")

        # ── 4. Browser back returns to the previous conv, active follows ──
        page.go_back()
        _wait_active(page, want)
        assert _param_w(page) == want, f"back should restore w={want[:8]}, got {_param_w(page)}"
        print("4. browser back restores previous ?w + active workspace follows URL: OK")

        # ── 5. Unknown id falls back to newest, shows a notice, normalizes URL ──
        page.wait_for_timeout(700)  # settle any debounced save before reading newest
        want5 = _newest_id()
        page.goto(f"{BASE}/?w=does-not-exist-zzz", wait_until="load", timeout=20000)
        _wait_active(page, want5)
        page.wait_for_function(
            "() => document.body.innerText.includes('was not found here')", timeout=8000)
        page.wait_for_function(
            "([want]) => new URL(location.href).searchParams.get('w') === want",
            arg=[want5], timeout=8000)
        assert _active_id(page) == want5, "unknown id should fall back to the newest"
        print("5. unknown id → fallback to newest + notice + URL normalized: OK")

        # ── 6. legacy ?c= is still READ, then rewritten to ?w= ──────
        # v1.0.0 renamed the param; bookmarks and tabs opened before the deploy
        # still carry ?c=, and silently dumping the user on the newest workspace
        # would look like data loss. Read-only compat: honored, never emitted.
        page.goto(f"{BASE}/?c={a['id']}", wait_until="load", timeout=20000)
        _wait_active(page, a["id"])
        assert _active_id(page) == a["id"], "legacy ?c= should still open that workspace"
        page.wait_for_function(
            "([want]) => new URL(location.href).searchParams.get('w') === want"
            " && new URL(location.href).searchParams.get('c') === null",
            arg=[a["id"]], timeout=5000)

        assert not errors, f"console/page errors: {errors}"
        browser.close()
        print("WORKSPACE-URL SMOKE PASS")


if __name__ == "__main__":
    main()
