"""Decisive check for the two fixes touched in ChatMessage/state:
  (1) reassign-removal: a completion in panel B must still RENDER (the live bucket
      updates via a per-key $state write, no whole-object reassign).
  (2) edit guard: an in-progress edit in panel A must SURVIVE panel B's completion
      (the ChatMessage reset $effect now bails when nodeId/role/content are unchanged).

Drives the REAL UI with the free OpenRouter model (zero cost, and it streams, so this
exercises the delta+sample render path). No legacy state-seeding (the old smokes' shape
is stale against the panels API).

  uv run python verify_cross_panel_edit.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8795"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
OR_MODEL = "liquid/lfm-2.5-1.2b-instruct:free"
OR_RUN = "openrouter:" + OR_MODEL
DRAFT = "MY-EDIT-DRAFT-MUST-SURVIVE"


def _req(path, data=None, method="GET"):
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"content-type": "application/json"},
        method=method,
    )
    return json.load(urllib.request.urlopen(r, timeout=20))


def main():
    # Add the free OR model + clear conversations so we start clean.
    _req("/api/openrouter-models", {"openrouter_model": OR_MODEL}, "POST")
    for c in _req("/api/conversations"):
        urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/api/conversations/{c['id']}", method="DELETE"), timeout=20
        ).read()

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = b.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector(".model-slot-select", timeout=15000)

        # primary -> OR model
        page.locator(".model-slot-select").nth(0).select_option(OR_RUN)
        # add a 2nd panel (button reads "Compare" for the first add), then set it -> OR
        page.locator(".btn-add-model").click()
        page.wait_for_function("document.querySelectorAll('.model-slot-select').length >= 2", timeout=5000)
        page.locator(".model-slot-select").nth(1).select_option(OR_RUN)
        page.wait_for_function("document.querySelectorAll('.chat-column').length >= 2", timeout=5000)

        # Send to BOTH panels via the shared composer.
        page.locator(".input-textarea").fill("Say a short hello.")
        page.locator(".input-textarea").press("Enter")
        # Both panels should land an assistant reply (free OR stream).
        page.wait_for_function(
            "document.querySelectorAll('.chat-column .message-role').length >= 4", timeout=60000
        )
        page.wait_for_timeout(800)

        # Open the editor on the PRIMARY panel's assistant turn, type a draft (don't save).
        col0 = page.locator(".chat-column").nth(0)
        msg = col0.locator(".message").last
        msg.hover()
        msg.locator("[aria-label='Edit']").click()
        ta = page.locator("textarea.edit-textarea")
        ta.wait_for(timeout=5000)
        ta.fill(DRAFT)
        assert page.locator("textarea.edit-textarea").count() == 1, "editor did not open"

        # Snapshot how much content panel B (compare) has, then fire a NEW completion
        # into ONLY panel B via its per-panel input.
        roles_before = page.locator(".chat-column").nth(1).locator(".message-role").count()
        col1_input = page.locator(".chat-column").nth(1).locator(".panel-send-input")
        col1_input.fill("Tell me one more thing.")
        col1_input.press("Enter")

        # (1) panel B's new completion must RENDER (more message rows than before).
        page.wait_for_function(
            f"document.querySelectorAll('.chat-column')[1].querySelectorAll('.message-role').length > {roles_before}",
            timeout=60000,
        )
        page.wait_for_timeout(800)

        # (2) the edit in panel A must have SURVIVED panel B's completion.
        editors = page.locator("textarea.edit-textarea")
        n = editors.count()
        assert n == 1, f"edit textarea vanished after panel-B completion (count={n}) — guard regressed"
        val = editors.input_value()
        assert val == DRAFT, f"edit draft was reset by panel-B completion: {val!r}"

        print("RENDER ok: panel B completion rendered new rows")
        print("GUARD  ok: panel A edit survived with draft intact:", repr(val))
        b.close()
        print("CROSS-PANEL EDIT SMOKE PASS")


if __name__ == "__main__":
    main()
