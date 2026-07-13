"""Regression smoke for the conversation-scoped external fold — the "new panel loads
a weird random conversation" bug.

Panel ids ('compare','p-2'…) are re-minted across conversations, and PlaygroundState
is a process-wide singleton shared by every tab + the tinkpg CLI. Before the fix,
`#onExternalDone` folded ANY chat_done into treeFor(panel) by the panel id string
alone, so a chat generated in the context of conversation A grafted onto a
freshly-reused panel of the conversation the browser had open (persisted into its
tree + flashed the "Terminal started a new conversation" banner). The fix stamps each
chat broadcast with the conversation open WHEN IT STARTED (chat.py snapshots
state.conversation_id right after chat_begin) and folds only when that stamp ==
convo.activeId; a skipped foreign chat also has its live bucket dropped so its stream
doesn't linger as an overlay. Null stamp (CLI/legacy) still folds → live-drive lockstep.

NOTE on the shape: the *realistic* trigger is multi-origin on the shared bus, NOT a
single browser switching conversations mid-generation — the sidebar conversation
controls are `disabled={anyRunning || convo.busy}`, so the UI already prevents a
mid-gen switch. So this models the real thing: an external actor operating in
conversation A (it sets shared conversation_id=A, exactly as an open browser/CLI
session for A would) runs a chat on the reused `compare` id while THIS browser has B
open. Deterministic — no timing race.

  NEGATIVE — external chat stamped A (a foreign conversation) → completes but must NOT
             fold into B, must NOT show, must NOT flash the banner. We prove the gate
             actually fired by checking B's panel echo DID receive the transcript
             (chat completed) while B's tree did NOT (fold skipped).
  POSITIVE — external chat stamped B (the OPEN conversation = live-drive lockstep) →
             MUST fold into B AND flash the banner (the user-visible tell for both).

Zero tinker cost (free OpenRouter router).

  uv run python tests/small-smokes/browser_panel_foreign_fold.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT_DIR = Path("/tmp/claude-1000/-home-c-dumas-tools-tinkerscope/"
                "6535889f-5ec1-46d1-b033-8db9788307b6/scratchpad")
FOREIGN = "FOREIGN_A_must_not_graft_into_B"
LOCKSTEP = "SAME_CONV_lockstep_must_fold"
NULLSTAMP = "NULL_STAMP_legacy_cli_must_fold"
BANNER = "Terminal started a new conversation"


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=15))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read() or b"{}")


def _seed(name):
    return _post("/api/conversations", {
        "name": name,
        "trees": {"primary": {"nodes": {
            "s0": {"id": "s0", "role": "user", "content": f"{name}: hi",
                   "parent": None, "children": ["s1"]},
            "s1": {"id": "s1", "role": "assistant", "content": f"{name}: hello",
                   "parent": "s0", "children": []},
        }, "rootChildren": ["s0"], "selected": {}}},
        "panels": [{"id": "primary", "run_id": None, "checkpoint": None}],
    })


def _drive(panel, text, origin_conv_id):
    """Model an external actor operating in `origin_conv_id`: set the shared
    conversation stamp, then fire+drain a chat (non-owned token) that the backend
    stamps with that origin. Returns whether the stream reached a 'done' event
    (chat_done fired = the fold hook was exercised)."""
    _post("/api/state", {"conversation_id": origin_conv_id})
    body = {
        "openrouter_model": "openrouter/free",
        "messages": [{"role": "user", "content": text}],
        "panel": panel, "broadcast": True,
        "client_token": "external-not-owned-by-browser",
        "max_tokens": 30, "n_samples": 1,
    }
    req = urllib.request.Request(
        f"{BASE}/api/chat", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return "event: done" in r.read().decode()


def _add_panel(page):
    page.locator("button.btn-add-model").first.click()
    page.wait_for_function("document.querySelectorAll('.chat-column').length === 2", timeout=8000)
    page.wait_for_timeout(300)


def main():
    A = _seed("ConvA")
    B = _seed("ConvB")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{BASE}/?c={B['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ConvB: hello')", timeout=15000)
        _add_panel(page)  # re-mints 'compare' under B
        assert [pp["id"] for pp in _get("/api/state")["panels"]] == ["primary", "compare"]

        # ── NEGATIVE: external chat stamped A must NOT fold into B ──
        done = False
        for _ in range(4):  # free router occasionally 502s — retry the infra, not the logic
            try:
                if _drive("compare", f"{FOREIGN} — reply with just: ACK", A["id"]):
                    done = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)
        assert done, "foreign chat did not complete (chat_done) — can't test the gate; rerun"
        page.wait_for_timeout(1500)  # settle any (buggy) fold/banner reaction
        neg_ui = FOREIGN not in page.inner_text("body")
        neg_banner_absent = BANNER not in page.inner_text("body")
        # the chat DID write B.compare's shared echo (proves it completed + the hook ran)…
        echo = next(pp["messages"] for pp in _get("/api/state")["panels"] if pp["id"] == "compare")
        gate_exercised = any(FOREIGN in m.get("content", "") for m in echo)
        page.screenshot(path=str(SHOT_DIR / "fold_fixed_negative.png"), full_page=True)
        # …but it must NOT be in B's persisted tree.
        page.wait_for_timeout(500)
        B_after = _get(f"/api/conversations/{B['id']}")  # v2: list is summaries-only
        neg_disk = not any(
            FOREIGN in n["content"]
            for tr in B_after["trees"].values() for n in tr.get("nodes", {}).values())

        # ── POSITIVE (lockstep): external chat stamped B MUST fold + flash the banner ──
        pos_ui = False
        for _ in range(4):  # free router occasionally 502s — retry the infra, not the logic
            try:
                if _drive("compare", f"{LOCKSTEP} — reply with just: ACK", B["id"]):
                    page.wait_for_function(
                        f"document.body.innerText.includes({json.dumps(LOCKSTEP)})", timeout=15000)
                    pos_ui = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)
        pos_banner = BANNER in page.inner_text("body")  # the lockstep fold flashes it
        page.wait_for_timeout(600)
        B_after2 = _get(f"/api/conversations/{B['id']}")
        pos_disk = any(
            LOCKSTEP in n["content"]
            for n in B_after2["trees"].get("compare", {}).get("nodes", {}).values())
        page.screenshot(path=str(SHOT_DIR / "fold_fixed_positive.png"), full_page=True)

        # ── NULL-STAMP (legacy CLI): a chat with conversation_id=null MUST still fold
        # into the OPEN conversation. Unchanged behavior, but newly load-bearing now
        # that every browser send rides the same fold gate (detached fire) — a CLI/
        # legacy chat that never set conversation_id keeps the live-drive lockstep. ──
        null_ui = False
        for _ in range(4):
            try:
                if _drive("compare", f"{NULLSTAMP} — reply with just: ACK", None):
                    page.wait_for_function(
                        f"document.body.innerText.includes({json.dumps(NULLSTAMP)})", timeout=15000)
                    null_ui = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)
        page.wait_for_timeout(600)
        B_after3 = _get(f"/api/conversations/{B['id']}")
        null_disk = any(
            NULLSTAMP in n["content"]
            for n in B_after3["trees"].get("compare", {}).get("nodes", {}).values())

        browser.close()

    for c in (A, B):
        urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/api/conversations/{c['id']}", method="DELETE"),
            timeout=10).read()

    print(f"NEGATIVE — foreign chat completed + hook ran (echo):  {gate_exercised}")
    print(f"NEGATIVE — foreign NOT in B's tree (fold skipped):    {neg_disk}")
    print(f"NEGATIVE — foreign NOT shown (bucket dropped on skip):{neg_ui}")
    print(f"NEGATIVE — banner did NOT flash:                      {neg_banner_absent}")
    print(f"POSITIVE — lockstep chat folded (UI):                 {pos_ui}")
    print(f"POSITIVE — lockstep chat persisted (tree):            {pos_disk}")
    print(f"POSITIVE — banner DID flash (the user-visible tell):  {pos_banner}")
    print(f"NULL-STAMP — legacy/CLI chat folded (UI):             {null_ui}")
    print(f"NULL-STAMP — legacy/CLI chat persisted (tree):        {null_disk}")
    print("console/page errors:", errors or "none")
    ok = (gate_exercised and neg_disk and neg_ui and neg_banner_absent
          and pos_ui and pos_disk and pos_banner
          and null_ui and null_disk and not errors)
    print("PANEL FOREIGN-FOLD SMOKE:", "PASS (fold is conversation-scoped)" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
