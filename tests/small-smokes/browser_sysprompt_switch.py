"""System-prompt × workspace-switch smoke — fully deterministic (no sampling).

The leak this guards (found reviewing the 2665af5 chip move; the binding itself
dates to the initial commit): type into the system-prompt textarea, then switch
workspaces INSIDE patchState's 200ms debounce window. Pre-fix, the timer
fired AFTER the new workspace's setState, so workspace A's half-typed
prompt landed on B's live state, B's next save PERSISTED it, and A never got
the edit — silent cross-workspace contamination of an experiment-defining
parameter. The fix: flushPatchState (response assigned into live.state) is
called via the convo store's #preSwitch barrier ahead of every transition.

Seeds two workspaces, opens A, opens the system chip, types, and immediately
switches to B via the workspace picker (well inside the debounce). Since the
picker became a combobox (click → pick a row) rather than a native <select>,
the smoke OPENS it first and then types into the textarea by dispatching the
input event — a real `fill()` would mousedown on the document and close the
dropdown, and re-opening it would burn the 200ms window this smoke needs to
still be inside. Asserts:

  - workspace A PERSISTED the typed prompt (the edit stayed home)
  - workspace B's persisted + live system_prompt are untouched (None)
  - the visible textarea on B does not show A's text
  - no console errors

  uv run python tests/small-smokes/browser_sysprompt_switch.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _ws_picker import WS_TRIGGER, wait_active_ws  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
TYPED = "SYSPROMPT-SMOKE-7f3"
SYS_TA = "textarea[placeholder='Optional system prompt...']"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def seed(name: str) -> str:
    return api("POST", "/api/workspaces", {
        "name": name, "system_prompt": None,
        "trees": {"primary": {"nodes": {"u1": {"id": "u1", "role": "user",
                                               "content": f"{name} turn",
                                               "parent": None, "children": []}},
                              "rootChildren": ["u1"], "selected": {"__root__": "u1"}}},
        "panels": [{"id": "primary", "run_id": "openrouter:x/y", "checkpoint": None}],
    })["id"]


def main() -> None:
    a, b = seed("sysprompt-A"), seed("sysprompt-B")
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?w={a}", wait_until="load", timeout=20000)
            page.wait_for_selector(WS_TRIGGER, timeout=15000)
            wait_active_ws(page, a)

            # Open the system chip AND the workspace picker up front, so the
            # type→switch pair below is two JS-speed steps.
            page.click("[data-testid=system-fold]")
            page.wait_for_selector(SYS_TA, timeout=5000)
            page.locator(WS_TRIGGER).click()
            page.wait_for_selector(f".ws-picker .typeahead-row[data-id='{b}']", timeout=5000)

            # Type into the system textarea, then switch to B IMMEDIATELY — well
            # inside the 200ms patch debounce, the leak's window. The keystroke is
            # a dispatched input event (what bind:value listens to) precisely so
            # it doesn't close the open dropdown.
            t0 = time.time()
            page.evaluate(
                """([sel, text]) => {
                    const ta = document.querySelector(sel);
                    ta.value = text;
                    ta.dispatchEvent(new Event('input', { bubbles: true }));
                }""",
                [SYS_TA, TYPED],
            )
            page.locator(f".ws-picker .typeahead-row[data-id='{b}']").click()
            print(f"  (type→switch gap: {(time.time() - t0) * 1000:.0f}ms of the 200ms debounce)")

            wait_active_ws(page, b)
            time.sleep(1.2)  # let the (now pre-switch-flushed) patch + debounced saves land

            live_sp = api("GET", "/api/state").get("system_prompt")
            convs = {cid: api("GET", f"/api/workspaces/{cid}") for cid in (a, b)}  # v2: bodies per id
            ta = page.input_value(SYS_TA) if page.locator(SYS_TA).count() else ""

            checks.append((f"workspace A kept the edit ({convs[a].get('system_prompt')!r})",
                           convs[a].get("system_prompt") == TYPED))
            checks.append((f"workspace B persisted prompt untouched ({convs[b].get('system_prompt')!r})",
                           convs[b].get("system_prompt") in (None, "")))
            checks.append((f"live state on B untouched ({live_sp!r})",
                           live_sp in (None, "")))
            checks.append((f"textarea on B does not show A's text ({ta!r})", TYPED not in ta))
            checks.append((f"no console errors ({len(errors)})", not errors))
            if errors:
                print("console errors:", errors[:5])
            browser.close()
    finally:
        api("DELETE", f"/api/workspaces/{a}")
        api("DELETE", f"/api/workspaces/{b}")

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("  ✓ " if ok else "  ✗ ") + name)
    if failed:
        raise SystemExit(f"sysprompt-switch smoke FAILED ({len(failed)})")
    print("sysprompt-switch smoke PASSED")


if __name__ == "__main__":
    main()
