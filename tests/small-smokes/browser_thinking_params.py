"""Browser smoke for Item 1 — cycling the thinking mode must NOT reset sampling
params, and the value must survive a plain page reload.

100% TOKEN-FREE (no sampling): it selects an OpenRouter *sentinel* on the primary
panel via POST /api/state (so the Thinking toggle appears and the panel has a
run_id → the restore path treats the reload as non-fresh), sets a distinctive
temperature via the sidebar slider, then:

  1. cycles Thinking On → Both → Off and asserts temperature is UNCHANGED
     (the confirmed bug: setThinking used to call applyQwenDefaults);
  2. reloads the tab (warm backend) and asserts temperature is STILL there;
  3. clicks "Reset to Qwen defaults" and asserts it DOES apply a preset
     (the decoupling must not disable the explicit reset).

Run against an ISOLATED instance (never the live server):
  uv run python tests/small-smokes/browser_thinking_params.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8811"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# A distinctive temperature no preset uses (Qwen presets are 0.70 / 1.00).
TEMP = 0.35
OR_SENTINEL = "openrouter:meta-llama/llama-3.1-8b-instruct"


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    return json.load(urllib.request.urlopen(req, timeout=10))


def read_temp(page) -> float:
    """The sidebar shows 'Temperature: X.XX' in a .sidebar-label."""
    txt = page.evaluate(
        """() => {
            const el = [...document.querySelectorAll('.sidebar-label')]
                .find(e => e.textContent.trim().startsWith('Temperature:'));
            return el ? el.textContent : null;
        }"""
    )
    assert txt, "Temperature label not found"
    return float(txt.split(":")[1].strip())


def set_temp(page, value: float):
    """Drive the sidebar temperature slider (range max=2 step=0.05) the real way:
    set .value + dispatch an 'input' event so oninput → setTemperature fires."""
    ok = page.evaluate(
        """(v) => {
            const el = document.querySelector('input.sidebar-slider[max="2"][step="0.05"]');
            if (!el) return false;
            el.value = String(v);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            return true;
        }""",
        value,
    )
    assert ok, "temperature slider not found"


def click_seg(page, label: str):
    """Click a Thinking-toggle segment by its exact text (Off/On/Both are unique)."""
    ok = page.evaluate(
        """(t) => {
            const btn = [...document.querySelectorAll('.seg-btn')]
                .find(b => b.textContent.trim() === t);
            if (!btn) return false;
            btn.click();
            return true;
        }""",
        label,
    )
    assert ok, f"seg-btn '{label}' not found"


def main():
    # Select an OpenRouter sentinel on primary so the Thinking toggle renders and
    # the panel carries a run_id (reload is then treated as non-fresh).
    _post("/api/state", {"panel": "primary", "run_id": OR_SENTINEL, "checkpoint": None})

    results = []

    def check(name, cond, detail=""):
        results.append((name, cond, detail))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME))
        page = browser.new_page()
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_timeout(800)  # let the SSE snapshot + selection settle

        # Thinking toggle must be present (model supports thinking).
        has_toggle = page.evaluate(
            """() => [...document.querySelectorAll('.seg-btn')].some(b => b.textContent.trim() === 'Both')"""
        )
        check("thinking toggle visible", has_toggle)

        set_temp(page, TEMP)
        page.wait_for_timeout(900)  # patchState (200ms) + persistSession (500ms) + margin
        check("temperature set", abs(read_temp(page) - TEMP) < 1e-6, f"read {read_temp(page)}")

        # 1) CLEAN warm reload FIRST (no thinking cycle) — isolates whether reload
        # itself loses the value vs. it being a downstream effect of the cycle bug.
        page.reload(wait_until="load", timeout=20000)
        page.wait_for_timeout(1000)
        after_reload = read_temp(page)
        check("temperature survives warm reload (no cycle)", abs(after_reload - TEMP) < 1e-6,
              f"expected {TEMP}, got {after_reload}")

        # 2) Cycle thinking — temperature must not move (the confirmed bug).
        for seg in ("On", "Both", "Off"):
            click_seg(page, seg)
            page.wait_for_timeout(250)
        after_cycle = read_temp(page)
        check("temperature survives thinking cycle", abs(after_cycle - TEMP) < 1e-6,
              f"expected {TEMP}, got {after_cycle}")

        # 3) Reset button must STILL apply a preset (0.70 non-thinking / 1.00 thinking).
        page.evaluate(
            """() => { const b = [...document.querySelectorAll('.advanced-toggle')]
                 .find(e => e.textContent.includes('Sampling')); if (b) b.click(); }"""
        )
        page.wait_for_timeout(300)
        page.evaluate(
            """() => { const b = [...document.querySelectorAll('.reset-defaults-btn')][0]; if (b) b.click(); }"""
        )
        page.wait_for_timeout(400)
        after_reset = read_temp(page)
        check("reset button still applies preset", abs(after_reset - TEMP) > 1e-6 and after_reset in (0.70, 1.00),
              f"got {after_reset}")

        browser.close()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{'ALL PASS' if not failed else 'FAILURES: ' + ', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
