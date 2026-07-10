"""Continue (＋) × prefill_scope smoke — fully deterministic (route-intercepted,
no sampling, no model needed).

The composition bug this guards: `paramsBundle()` attaches the composer's
prefill-scope tri-state to EVERY fire, but branchOps' continue builds its own
trailing-assistant prefill (the turn being extended) and bypasses the composer's
`prefillEffective` gate. With scope = "Think only" and thinking OFF — a
combination the scope UI itself calls "dropped entirely" — the backend strips
the continuation from the prompt, so the model replies fresh instead of
extending; the client fold then prepends the old text anyway (single-mode
samples carry no per-sample `thinking` tag), fabricating a merged turn the
model never produced. And prefillScope persists in session prefs, so a
Think-only set once silently rides along on every later continue.

The fix: continue always fires with prefill_scope "all" — extending the turn IS
the point; the composer scope applies to the composer prefill only.

Seeds a one-turn conversation with an OpenRouter-sentinel panel, intercepts
POST /api/chat (canned SSE reply), sets scope to Think-only with thinking off,
clicks ＋ on the assistant turn, and asserts the outgoing request: scope "all",
messages ending with the assistant prefill. Then checks the fold merged
prefill + continuation into the new sibling.

  uv run python tests/small-smokes/browser_continue_scope.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

PREFILL_TEXT = "The sky is deep blue"
CONTINUATION = " and stretches on forever."

SSE_BODY = (
    "event: message\n"
    f"data: {json.dumps({'sample_index': 0, 'content': CONTINUATION, 'raw_text': CONTINUATION})}\n"
    "\n"
    "event: done\n"
    "data: {}\n"
    "\n"
)


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def seed() -> str:
    """One user turn + one assistant reply, on an OpenRouter-sentinel panel (the
    model is never actually called — /api/chat is route-intercepted)."""
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "Describe the sky.",
               "parent": None, "children": ["a1"]},
        "a1": {"id": "a1", "role": "assistant", "content": PREFILL_TEXT,
               "parent": "u1", "children": []},
    }
    conv = api("POST", "/api/conversations", {
        "name": "continue-scope-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "a1"}}},
        "panels": [{"id": "primary", "run_id": "openrouter:smoke/fake-model", "checkpoint": None}],
    })
    return conv["id"]


def main() -> None:
    conv_id = seed()
    api("POST", "/api/state", {"thinking": False})  # pin single (non-thinking) mode
    checks: list[tuple[str, bool]] = []
    captured: list[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            def fulfill_chat(route):
                captured.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="text/event-stream", body=SSE_BODY)

            page.route("**/api/chat", fulfill_chat)

            page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('Describe the sky.')", timeout=15000
            )

            # Set the composer scope to "Think only" (open the prefill row to reach
            # the segment, then collapse it again — the scope state persists, which
            # is exactly how a stale scope ends up riding along on a continue).
            page.click(".prefill-toggle")
            page.wait_for_selector(".prefill-scope", timeout=5000)
            page.click(".prefill-scope .seg-btn:has-text('Think only')")
            page.click(".prefill-toggle")

            # ＋ continue on the assistant turn.
            page.click('button[data-tooltip^="Continue this message"]')
            page.wait_for_function(
                f"document.body.innerText.includes({json.dumps(CONTINUATION.strip())})",
                timeout=10000,
            )

            checks.append(("exactly one /api/chat fired", len(captured) == 1))
            req = captured[0] if captured else {}
            checks.append(
                ("continue fires with prefill_scope 'all' (composer scope must not "
                 "silently drop the continuation)", req.get("prefill_scope") == "all")
            )
            msgs = req.get("messages") or []
            checks.append(
                ("request ends with the assistant prefill",
                 bool(msgs) and msgs[-1].get("role") == "assistant"
                 and msgs[-1].get("content") == PREFILL_TEXT)
            )

            # The fold merges prefill + continuation into the new sibling (‹2/2›).
            # Whitespace-normalized: the prefilled prefix renders in its own
            # colored span, so inner_text may split the sentence across nodes.
            merged = " ".join((PREFILL_TEXT + CONTINUATION).split())
            area_text = " ".join(page.inner_text(".chat-area").split())
            checks.append(("folded sibling shows prefill + continuation", merged in area_text))
            checks.append(("branch cycler shows 2 siblings", "2/2" in page.inner_text(".chat-area")))
            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", errors[:5])
            browser.close()
    finally:
        api("DELETE", f"/api/conversations/{conv_id}")

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    if failed:
        raise SystemExit(f"{len(failed)} check(s) failed")
    print("browser_continue_scope: all checks passed")


if __name__ == "__main__":
    main()
