"""System-prompt SPLIT-CHIP smoke — the composer's system-prompt control is a
split pill (lib/SplitChip.svelte): left POWER zone = apply/mute (persisted as
`system_enabled` on state + conversation), right label+chevron = expand/fold.
The two axes are orthogonal: folding never mutes; muting keeps the text.

  - the sidebar no longer has a System-prompt textarea
  - clicking the FOLD zone opens a textarea; typing auto-enables (empty→non-empty)
    → chip ACTIVE (.on) + text AND system_enabled=true persist to the conversation
  - clicking the POWER zone mutes: chip drops .on, the textarea stays (muted
    border + "muted" hint), text is KEPT — and system_enabled=false persists
  - folding while muted keeps the mute; power back on → ACTIVE again, no retyping
  - the value + flag SURVIVE a conversation switch-and-back (per-conversation)
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


def conv_field(conv_id: str, key: str):
    # v2: the list is summaries-only — system fields live on the body.
    try:
        return api("GET", f"/api/conversations/{conv_id}").get(key)
    except Exception:
        return None


def wait_conv(conv_id: str, key: str, want, timeout=6.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = conv_field(conv_id, key)
        if last == want:
            return last
        time.sleep(0.2)
    return last


SYS_FOLD = "[data-testid=system-fold]"
SYS_POWER = "[data-testid=system-power]"
SYS_TA = 'textarea[placeholder="Optional system prompt..."]'
# `.on` (ACTIVE = enabled + non-empty) lives on the split-chip root span.
CHIP_ON = f"() => document.querySelector('{SYS_FOLD}')?.closest('.split-chip')?.classList.contains('on') ?? null"


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
            page.wait_for_selector(SYS_FOLD, timeout=15000)

            # The sidebar no longer carries a System-prompt textarea.
            sidebar_sys = page.evaluate(
                "() => [...document.querySelectorAll('.sidebar-label')].some(l => l.textContent.trim() === 'System prompt')"
            )
            checks.append(("sidebar System-prompt block removed", not sidebar_sys))

            fold_text = page.eval_on_selector(SYS_FOLD, "e => e.textContent.trim()")
            checks.append((f"system split-chip present ({fold_text!r})",
                           "system" in fold_text.lower()))

            # Fold zone → textarea opens; typing auto-enables (empty→non-empty).
            page.click(SYS_FOLD)
            page.wait_for_selector(SYS_TA, timeout=5000)
            page.fill(SYS_TA, SYS_TEXT)
            page.eval_on_selector(SYS_TA, "e => e.blur()")  # flush the debounced patch/save

            persisted = wait_conv(conv_a, "system_prompt", SYS_TEXT)
            checks.append((f"typing persists the text to the conversation ({persisted!r})",
                           persisted == SYS_TEXT))
            enabled = wait_conv(conv_a, "system_enabled", True)
            checks.append((f"typing AUTO-ENABLES (conv system_enabled={enabled!r})", enabled is True))
            checks.append(("chip shows ACTIVE (.on)", page.evaluate(CHIP_ON) is True))

            # POWER off → muted: .on drops, text is KEPT, hint shows, flag persists.
            page.click(SYS_POWER)
            muted_flag = wait_conv(conv_a, "system_enabled", False)
            checks.append((f"power off persists system_enabled=False ({muted_flag!r})",
                           muted_flag is False))
            checks.append(("muted chip drops .on", page.evaluate(CHIP_ON) is False))
            checks.append(("muted textarea keeps the text",
                           page.eval_on_selector(SYS_TA, "e => e.value") == SYS_TEXT))
            checks.append(("'muted' hint shows under the textarea",
                           page.locator(".prefill-offhint").count() == 1))
            kept = conv_field(conv_a, "system_prompt")
            checks.append((f"muting KEEPS the stored text ({kept!r})", kept == SYS_TEXT))
            live = api("GET", "/api/state")
            checks.append((f"live state mirrors the mute ({live.get('system_enabled')!r})",
                           live.get("system_enabled") is False))

            # Folding never changes enabled; power back on → ACTIVE, no retyping.
            page.click(SYS_FOLD)  # fold the editor while muted
            checks.append(("folded while muted stays inactive", page.evaluate(CHIP_ON) is False))
            page.click(SYS_POWER)
            page.wait_for_function(CHIP_ON, timeout=5000)  # truthy = .on back
            re_enabled = wait_conv(conv_a, "system_enabled", True)
            checks.append((f"power on restores ACTIVE without retyping ({re_enabled!r})",
                           re_enabled is True and page.evaluate(CHIP_ON) is True))

            # Switch to conv B (blank) then back to A ⇒ prompt + flag must restore.
            page.goto(f"{BASE}/?c={conv_b}", wait_until="load", timeout=20000)
            page.wait_for_selector(SYS_FOLD, timeout=15000)
            try:
                page.wait_for_function(f"({CHIP_ON})() === false", timeout=8000)
                b_inactive = True
            except Exception:
                b_inactive = False
            checks.append(("other conversation's chip is inactive (per-conv)", b_inactive))

            page.goto(f"{BASE}/?c={conv_a}", wait_until="load", timeout=20000)
            page.wait_for_selector(SYS_FOLD, timeout=15000)
            page.wait_for_function(f"({CHIP_ON})() === true", timeout=10000)
            page.click(SYS_FOLD)  # open the textarea to read the restored value
            restored = page.eval_on_selector(SYS_TA, "e => e.value")
            checks.append((f"prompt survives switch-and-back ({restored!r})",
                           restored == SYS_TEXT))

            # Clear it ⇒ chip flips back to inactive (empty text = nothing applies).
            page.fill(SYS_TA, "")
            page.eval_on_selector(SYS_TA, "e => e.blur()")
            page.wait_for_function(f"({CHIP_ON})() === false", timeout=5000)
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
