"""System-prompt chip smoke — the sidebar System prompt block moved next to the
"prefill assistant" chip above the composer. UI relocation only; persistence
(setSystemPrompt → patchState + per-conversation save) is unchanged, so this
verifies the chip drives that path:

  - the sidebar no longer has a System-prompt textarea
  - clicking the "system prompt" chip opens a textarea; typing sets it + the chip
    flips to its active state ("✎ system on" + .on)
  - the value SURVIVES a conversation switch-and-back (per-conversation persistence)
  - clearing the prompt flips the chip back to inactive
  - no console errors

  uv run python tests/small-smokes/browser_system_chip.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SYS_TEXT = "SYS-PROMPT-XYZ-be-terse"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def conv_system(conv_id: str) -> str | None:
    # v2: the list is summaries-only — system_prompt lives on the body.
    try:
        return api("GET", f"/api/conversations/{conv_id}").get("system_prompt")
    except Exception:
        return None


def wait_conv_system(conv_id: str, want, timeout=5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = conv_system(conv_id)
        if last == want:
            return last
        time.sleep(0.2)
    return last


SYS_CHIP = ".prefill-row .prefill-toggle"  # first toggle in the row is system prompt
SYS_TA = 'textarea[placeholder="Optional system prompt..."]'


def main() -> None:
    conv_a = api("POST", "/api/conversations", {"name": "sys-chip-A"})["id"]
    conv_b = api("POST", "/api/conversations", {"name": "sys-chip-B"})["id"]
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1400, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?c={conv_a}", wait_until="load", timeout=20000)
            page.wait_for_selector(SYS_CHIP, timeout=15000)

            # The sidebar no longer carries a System-prompt textarea.
            sidebar_sys = page.evaluate(
                "() => [...document.querySelectorAll('.sidebar-label')].some(l => l.textContent.trim() === 'System prompt')"
            )
            checks.append(("sidebar System-prompt block removed", not sidebar_sys))

            # The first chip in the prefill row is the system-prompt chip.
            chip_text0 = page.eval_on_selector(SYS_CHIP, "e => e.textContent.trim()")
            checks.append((f"system-prompt chip present ({chip_text0!r})",
                           "system" in chip_text0.lower()))

            # Click it → a textarea opens; type a prompt.
            page.click(SYS_CHIP)
            page.wait_for_selector(SYS_TA, timeout=5000)
            page.fill(SYS_TA, SYS_TEXT)
            # blur so the debounced patch/save definitely flushes
            page.eval_on_selector(SYS_TA, "e => e.blur()")

            persisted = wait_conv_system(conv_a, SYS_TEXT)
            checks.append((f"typing the chip textarea persists to the conversation ({persisted!r})",
                           persisted == SYS_TEXT))

            chip_active = page.eval_on_selector(
                SYS_CHIP, "e => ({on: e.classList.contains('on'), text: e.textContent.trim()})"
            )
            checks.append((f"chip shows ACTIVE state ({chip_active})",
                           chip_active["on"] and "on" in chip_active["text"].lower()))

            # Switch to conv B (blank) then back to A ⇒ the prompt must restore.
            # A fresh load inherits the stale shared-state prompt until switchTo(B)
            # patches it null, so wait for the chip to settle inactive.
            page.goto(f"{BASE}/?c={conv_b}", wait_until="load", timeout=20000)
            page.wait_for_selector(SYS_CHIP, timeout=15000)
            try:
                page.wait_for_function(
                    "() => { const c=document.querySelector('.prefill-row .prefill-toggle');"
                    " return c && !c.classList.contains('on'); }", timeout=8000,
                )
                b_inactive = True
            except Exception:
                b_inactive = False
            checks.append(("other conversation's chip is inactive (per-conv)", b_inactive))

            page.goto(f"{BASE}/?c={conv_a}", wait_until="load", timeout=20000)
            page.wait_for_selector(SYS_CHIP, timeout=15000)
            page.wait_for_function(
                "() => { const c=document.querySelector('.prefill-row .prefill-toggle');"
                " return c && c.classList.contains('on'); }", timeout=10000,
            )
            page.click(SYS_CHIP)  # open the textarea to read the restored value
            restored = page.eval_on_selector(SYS_TA, "e => e.value")
            checks.append((f"prompt survives switch-and-back ({restored!r})",
                           restored == SYS_TEXT))

            # Clear it ⇒ chip flips back to inactive.
            page.fill(SYS_TA, "")
            page.eval_on_selector(SYS_TA, "e => e.blur()")
            page.wait_for_function(
                "() => { const c=document.querySelector('.prefill-row .prefill-toggle');"
                " return c && !c.classList.contains('on'); }", timeout=5000,
            )
            checks.append(("clearing the prompt deactivates the chip", True))

            checks.append((f"no console errors ({len(errors)})", not errors))
            if errors:
                print("CONSOLE ERRORS:", errors)
            browser.close()
    finally:
        api("DELETE", f"/api/conversations/{conv_a}")
        api("DELETE", f"/api/conversations/{conv_b}")

    print()
    ok = True
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        ok = ok and passed
    if not ok:
        raise SystemExit("system-chip smoke FAILED")
    print("system-chip smoke PASSED")


if __name__ == "__main__":
    main()
