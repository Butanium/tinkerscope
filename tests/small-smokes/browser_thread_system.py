"""Browser smoke for THREAD SYSTEM PROMPTS — the root-node field UI.

100% TOKEN-FREE: seeds a 1-panel conversation with THREE root threads sharing
the SAME first message under different system prompts (SYS-LETTER / SYS-STEPS /
none — the probe-battery shape), opens it with ?c=<id>, then:

  1. the active root row wears the collapsed `system` strip (SYS-LETTER);
     clicking it toggles the expanded state;
  2. ‹k/N› cycling the first row swaps the WHOLE (system, content) pair:
     next → SYS-STEPS strip, next → no strip (the promptless thread);
  3. the ⑂ threads popover lists 3 DISTINCT threads (pair identity — content
     alone would collapse them to one) with per-thread `sys:` labels;
  4. shift-edit (fork full copy, no generation) on the root row shows the
     thread-system field prefilled; changing it forks a 4th sibling thread
     carrying the NEW prompt;
  5. arming ⑂ branch-from-start reveals the composer's split-pill `thread system`
     chip + textarea (disarming hides them);
  6. after the debounce-save, a reload restores the forked thread's strip.

No model calls.

  uv run python tests/small-smokes/browser_thread_system.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

QUESTION = "PROBE-Q which statement is false?"
SYS_A = "SYS-LETTER answer with only the letter"
SYS_B = "SYS-STEPS think step by step first"
SYS_NEW = "SYS-FRENCH réponds en français"

FREE = "openrouter:openrouter/free"  # non-null run_id: the load-time phantom-panel
# self-heal DROPS panels with run_id == null, which would empty the seed.


def seed_tree():
    """Three root threads, SAME content, different system prompts; SYS_A active."""
    nodes, roots = {}, []
    for rid, sys_prompt in (("t-a", SYS_A), ("t-b", SYS_B), ("t-c", None)):
        aid = f"{rid}-x"
        nodes[rid] = {"id": rid, "role": "user", "content": QUESTION,
                      **({"system_prompt": sys_prompt} if sys_prompt else {}),
                      "parent": None, "children": [aid]}
        nodes[aid] = {"id": aid, "role": "assistant", "content": f"reply under {sys_prompt or 'global'}",
                      "parent": rid, "children": []}
        roots.append(rid)
    return {"nodes": nodes, "rootChildren": roots, "selected": {"__root__": "t-a"}}


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def strip_text(page):
    return page.evaluate(
        "() => document.querySelector('[data-testid=thread-system-strip]')?.textContent ?? null")


def main():
    conv = _post("/api/conversations", {
        "name": "thread system smoke",
        "trees": {"primary": seed_tree()},
        "panels": [{"id": "primary", "run_id": FREE, "checkpoint": None}],
        "seen_panels": ["primary"],
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('PROBE-Q')", timeout=15000)

        # ── 1. strip renders collapsed on the active root; click expands ──
        strip = page.locator("[data-testid='thread-system-strip']")
        assert strip.count() == 1, "active thread (SYS-LETTER) should wear the system strip"
        assert "SYS-LETTER" in strip.text_content(), f"strip: {strip.text_content()!r}"
        strip.click()
        assert page.evaluate(
            "() => document.querySelector('[data-testid=thread-system-strip]').classList.contains('expanded')"
        ), "clicking the strip should expand it"
        strip.click()  # collapse back

        # ── 2. ‹k/N› on the first row swaps the (system, content) pair ──
        first_row = page.locator(".chat-column .message").first
        nxt = first_row.locator("[aria-label='Next branch']")
        assert "1/3" in first_row.locator(".branch-cycle-count").text_content()
        nxt.click()
        page.wait_for_function(
            "document.body.innerText.includes('SYS-STEPS')", timeout=5000)
        assert "SYS-STEPS" in strip_text(page), f"cycle → SYS-STEPS strip, got {strip_text(page)!r}"
        page.locator(".chat-column .message").first.locator("[aria-label='Next branch']").click()
        page.wait_for_function(
            "!document.querySelector('[data-testid=thread-system-strip]')", timeout=5000)
        assert strip_text(page) is None, "the promptless thread must wear NO strip"

        # ── 3. the ⑂ popover: 3 distinct threads (pair identity) + sys labels ──
        btn = page.locator("[data-testid='thread-switcher-btn']")
        assert btn.count() == 1 and "3" in btn.text_content(), \
            f"3 same-content threads must stay distinct: {btn.text_content() if btn.count() else 'no button'!r}"
        btn.click()
        rows = page.locator("[data-testid='thread-menu'] .thread-row")
        assert rows.count() == 3
        sys_lines = [rows.nth(i).locator(".thread-sys").count() for i in range(3)]
        assert sys_lines == [1, 1, 0], f"two threads carry sys: lines, one none: {sys_lines}"
        assert "SYS-LETTER" in rows.nth(0).text_content()
        page.keyboard.press("Escape")

        # ── 4. shift-edit the root → thread-system field, fork carries the new prompt ──
        rows0 = page.locator(".chat-column .message").first
        rows0.hover()
        rows0.locator("[aria-label='Edit']").click(modifiers=["Shift"])
        field = page.locator("[data-testid='edit-thread-system']")
        assert field.count() == 1, "root-row edit must show the thread-system field"
        assert field.input_value() == "", "promptless thread → empty field"
        field.fill(SYS_NEW)
        page.locator(".btn-edit-save").click()
        page.wait_for_function("document.body.innerText.includes('SYS-FRENCH')", timeout=5000)
        assert "SYS-FRENCH" in strip_text(page), "the fork must wear the NEW prompt's strip"
        btn.click()
        assert page.locator("[data-testid='thread-menu'] .thread-row").count() == 4, \
            "the fork is a 4th distinct thread"
        page.keyboard.press("Escape")

        # ── 5. composer chip appears only while ⑂ is armed ──
        assert page.locator("[data-testid='thread-system-fold']").count() == 0
        page.locator("[data-testid='branch-root-toggle']").click()
        chip = page.locator("[data-testid='thread-system-fold']")
        assert chip.count() == 1, "arming ⑂ must reveal the thread-system chip"
        chip.click()
        assert page.locator("[data-testid='thread-system-input']").count() == 1
        page.locator("[data-testid='branch-root-toggle']").click()  # disarm
        assert page.locator("[data-testid='thread-system-fold']").count() == 0

        # ── 6. the fork persists (debounce-save → reload) ──
        time.sleep(2.5)
        page.goto(f"{BASE}/?c={conv['id']}", wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('PROBE-Q')", timeout=15000)
        assert "SYS-FRENCH" in (strip_text(page) or ""), \
            f"reload must restore the forked thread's strip: {strip_text(page)!r}"

        assert not errors, f"console errors: {errors}"
        browser.close()
    print("browser_thread_system: OK")


if __name__ == "__main__":
    main()
