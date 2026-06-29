"""Verify the split copy buttons + shift-includes-thinking.

POSTs a conversation with a hand-built tree (user -> assistant WITH reasoning) so the
<think> path is exercised without needing a thinking model, then drives the two copy
buttons (plain + shift) and reads the clipboard.

  uv run python verify_copy_buttons.py [BASE_URL]
"""
import json
import sys
import urllib.request
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8796"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
REASONING = "SECRET-THOUGHT-XYZ"
CONTENT = "It is four."
USERQ = "What is two plus two?"
ROOT = "__root__"

TREE = {
    "nodes": {
        "u1": {"id": "u1", "role": "user", "content": USERQ, "parent": None, "children": ["a1"]},
        "a1": {"id": "a1", "role": "assistant", "content": CONTENT, "reasoning": REASONING,
               "parent": "u1", "children": []},
    },
    "rootChildren": ["u1"],
    "selected": {ROOT: "u1", "u1": "a1"},
}


def _req(path, data=None, method="GET"):
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"content-type": "application/json"},
        method=method,
    )
    return json.load(urllib.request.urlopen(r, timeout=20))


def main():
    for c in _req("/api/conversations"):
        urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/api/conversations/{c['id']}", method="DELETE"), timeout=20
        ).read()
    cid = str(uuid.uuid4())
    _req("/api/conversations", {
        "id": cid, "name": "copytest", "system_prompt": None,
        "trees": {"primary": TREE},
        "panels": [{"id": "primary", "run_id": None, "checkpoint": None}],
        "reduced_panels": [], "send_targets": ["primary"], "seen_panels": ["primary"],
    }, "POST")

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1500, "height": 950},
                            permissions=["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        page.goto(f"{BASE}?c={cid}", wait_until="load", timeout=20000)
        page.wait_for_function(f"document.body.innerText.includes({json.dumps(CONTENT)})", timeout=15000)

        amsg = page.locator(".message", has_text=CONTENT).first
        clip = lambda: page.evaluate("navigator.clipboard.readText()")

        def click(label, shift=False):
            amsg.hover()
            btn = amsg.locator(f"[aria-label='{label}']")
            btn.click(modifiers=["Shift"] if shift else [])
            page.wait_for_timeout(120)

        # 1) copy message, plain → content only
        click("Copy this message")
        got = clip()
        assert got == CONTENT, f"copy-msg plain: {got!r}"

        # 2) copy message, shift → <think> + content
        click("Copy this message", shift=True)
        got = clip()
        assert got == f"<think>\n{REASONING}\n</think>\n\n{CONTENT}", f"copy-msg+think: {got!r}"

        # 3) copy conversation, plain → markdown headers, NO thinking
        click("Copy conversation")
        got = clip()
        assert "## User" in got and USERQ in got and "## Assistant" in got and CONTENT in got, f"copy-conv: {got!r}"
        assert REASONING not in got, f"copy-conv plain leaked thinking: {got!r}"

        # 4) copy conversation, shift → markdown WITH <think>
        click("Copy conversation", shift=True)
        got = clip()
        assert f"<think>\n{REASONING}\n</think>" in got and "## Assistant" in got, f"copy-conv+think: {got!r}"

        print("copy-msg plain     :", repr(CONTENT))
        print("copy-msg +thinking : <think> wrapped OK")
        print("copy-conv plain    : markdown headers, no thinking OK")
        print("copy-conv +thinking: <think> wrapped under ## Assistant OK")
        b.close()
        print("COPY-BUTTONS SMOKE PASS")


if __name__ == "__main__":
    main()
