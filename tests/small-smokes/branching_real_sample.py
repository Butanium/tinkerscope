"""REAL-sample branching check (spends a few small tokens) — verifies the part
the token-free smoke can't: the live sampling path (fireChat → drainSamples →
foldAssistant) folds real replies into the tree.

Exercises: a real n=1 send → fold under the user node; regenerate → a 2nd
assistant SIBLING (‹k/N› = 2); a real n=3 send → 3 sibling branches folded. The
oracle is the persisted tree (GET /api/conversations).

Uses an OpenRouter reference model (set via /api/state, no model-picker clicks) —
the april LoRA fixtures' sampler weights have expired on Tinker (404), and the
base-model n=1 /completions path hits a multi-token-stop limitation; OpenRouter
is the reliable path that still exercises the identical frontend fold logic.
Needs OPENROUTER_API_KEY (health.openrouter_key). max_tokens is tiny for speed.

  uv run python tests/small-smokes/branching_real_sample.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
OR_MODEL = "openrouter:deepseek/deepseek-chat-v3.1"


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
                               headers={"content-type": "application/json"}, method="POST"),
        timeout=10,
    ).read()


def _clean():
    for c in _get("/api/conversations"):
        urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/api/conversations/{c['id']}", method="DELETE"), timeout=10
        ).read()


def _tree():
    cs = _get("/api/conversations")
    return cs[0]["tree"] if cs else {"nodes": {}}


def _nodes(role=None):
    ns = list(_tree().get("nodes", {}).values())
    return [n for n in ns if role is None or n["role"] == role]


def _wait(pred, timeout=90, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for: {what}")


def main():
    _clean()
    _post("/api/state", {"run_id": OR_MODEL, "checkpoint": None, "max_tokens": 16,
                         "n_samples": 1, "temperature": 0.7,
                         "messages": [], "compare_messages": []})

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="load", timeout=20000)
        # Wait until the OpenRouter model is selected (state synced) + input enabled.
        page.wait_for_function("!document.querySelector('textarea.input-textarea')?.disabled", timeout=15000)

        # ── 1. real n=1 send → fold under the user node ──
        ta = page.locator("textarea.input-textarea")
        ta.fill("Reply with a single short word.")
        ta.press("Enter")
        _wait(lambda: len(_nodes("assistant")) >= 1, what="n=1 reply to fold")
        assert len(_nodes("user")) == 1, _nodes("user")
        user = _nodes("user")[0]
        assert len(user["children"]) == 1, f"user should have 1 reply, has {len(user['children'])}"
        print("n=1 send → folded 1 user + 1 assistant:",
              [n["content"][:30] for n in _nodes("assistant")])

        # ── 2. regenerate the assistant turn → a 2nd assistant SIBLING ──
        page.wait_for_timeout(400)
        last = page.locator(".message").last
        last.hover()
        last.get_by_role("button", name="Regenerate").click()
        _wait(lambda: len(_tree()["nodes"][user["id"]]["children"]) >= 2,
              what="regenerate to add a sibling")
        page.wait_for_function(
            "(document.querySelector('[data-testid=branch-cycle]')||{}).innerText?.includes('/2')",
            timeout=15000)
        print("regenerate → 2 assistant siblings, ‹k/N› shows /2")

        # ── 3. real n=3 send (a follow-up turn) → 3 sibling branches ──
        _post("/api/state", {"n_samples": 3})
        page.wait_for_timeout(300)
        ta.fill("Name a fruit.")
        ta.press("Enter")
        # a NEW user node appears whose reply has 3 assistant children
        def _three():
            users = _nodes("user")
            if len(users) < 2:
                return False
            t = _tree()
            return any(
                len(u["children"]) >= 3 and all(t["nodes"][c]["role"] == "assistant" for c in u["children"])
                for u in users
            )
        _wait(_three, timeout=120, what="n=3 to fold 3 sibling replies")
        print("n=3 send → 3 sibling branches folded")

        assert not errors, f"console/page errors: {errors}"
        browser.close()
        print("REAL-SAMPLE BRANCHING PASS")


if __name__ == "__main__":
    main()
