"""Shift+Continue = resume INSIDE the think block — deterministic (route-intercepted,
no sampling, no model needed).

Plain Continue (＋) extends the whole turn: it prefills the reassembled
`<think>\\n{R}</think>\\n\\n{C}` (closed think + answer) so the model extends the
ANSWER. Shift+Continue instead prefills the reasoning as an OPEN think block
`<think>\\n{R}` (no `</think>`, no answer) so the model RESUMES the chain of thought
(and then naturally closes it and produces the answer). The renderer output for this
was verified against tml_v0 / DeepSeek / Qwen3; this smoke guards the FRONTEND wiring:
shift → thinkingOnly → assembleAssistantRaw(reasoning, '').

Seeds an assistant turn that has BOTH reasoning and content, intercepts POST
/api/chat, Shift+clicks ＋, and asserts the outgoing prefill is the OPEN think block
(reasoning only, no `</think>`, no answer) — vs the closed whole-turn prefill a plain
continue sends (covered by browser_continue_scope.py + the assembleAssistantRaw logic).

  uv run python tests/small-smokes/browser_shift_continue_thinking.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

REASONING = "Let me count the primes below ten."
CONTENT = "There are 4 primes below 10."

SSE_BODY = (
    "event: message\n"
    f"data: {json.dumps({'sample_index': 0, 'content': ' extend', 'raw_text': ' extend'})}\n"
    "\n"
    "event: done\n"
    "data: {}\n"
    "\n"
)


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def seed() -> str:
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "How many primes below 10?",
               "parent": None, "children": ["a1"]},
        "a1": {"id": "a1", "role": "assistant", "content": CONTENT, "reasoning": REASONING,
               "parent": "u1", "children": []},
    }
    conv = api("POST", "/api/conversations", {
        "name": "shift-continue-thinking-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "a1"}}},
        "panels": [{"id": "primary", "run_id": "openrouter:smoke/fake-model", "checkpoint": None}],
    })
    return conv["id"]


def main() -> None:
    conv_id = seed()
    checks: list[tuple[str, bool]] = []
    captured: list[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/api/chat", lambda route: (
                captured.append(json.loads(route.request.post_data or "{}")),
                route.fulfill(status=200, content_type="text/event-stream", body=SSE_BODY),
            )[-1])

            page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('How many primes below 10?')", timeout=15000
            )
            cont = 'button[aria-label="Continue this message"]'

            # Shift+continue → open think block, reasoning only.
            page.click(cont, modifiers=["Shift"])
            for _ in range(50):
                if captured:
                    break
                page.wait_for_timeout(100)

            checks.append(("exactly one /api/chat fired", len(captured) == 1))
            shift = (captured[0].get("messages") or [{}])[-1].get("content") if captured else None

            checks.append(("shift continue prefills the OPEN think block (reasoning only)",
                           shift == f"<think>\n{REASONING}"))
            checks.append(("shift prefill has no closing </think>", bool(shift) and "</think>" not in shift))
            checks.append(("shift prefill excludes the answer", bool(shift) and CONTENT not in shift))
            checks.append(("continue still fires with prefill_scope 'all'",
                           bool(captured) and captured[0].get("prefill_scope") == "all"))
            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", errors[:5])
            print("  shift prefill:", repr(shift))
            browser.close()
    finally:
        api("DELETE", f"/api/conversations/{conv_id}")

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    if failed:
        raise SystemExit(f"{len(failed)} check(s) failed")
    print("browser_shift_continue_thinking: all checks passed")


if __name__ == "__main__":
    main()
