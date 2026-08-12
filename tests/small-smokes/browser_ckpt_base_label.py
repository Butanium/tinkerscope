"""Browser smoke: a loose `ckpt:` panel shows the base model tinker resolves for it.

A loose sampler path has no config.json, so NOTHING local knows its family — the
catalog's checkpoint entries carry a path and a date, no base_model. The server
already resolves it for its own rendering (`resolve_base_model`), and the probe
route hands the same answer to the browser; this pins that the answer reaches the
panel, in both places it matters:

  1. META LINE — the panel under a `ckpt:` pick shows `◇ <base model>`. Before
     this, the branch rendered no meta line at all.
  2. THINKING CONTROL — a loose ckpt used to be assumed thinking-capable
     unconditionally. Now it consults the resolved base's `supports_thinking`
     like a `base:` pick does, so a non-thinking family hides the toggle.
     (Asserted in whichever direction the picked checkpoint's base implies.)

TOKEN-FREE, but NOT network-free: only tinker can say what a path resolves to, so
this makes one metadata probe per path (no sampling). Skips cleanly without
TINKER_API_KEY, without a swept checkpoint, or when no probed path resolves — an
account with no live checkpoints is not a regression.

  uv run python tests/small-smokes/browser_ckpt_base_label.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _seed import seed_conversation  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8812"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
# How many catalog paths to probe before giving up (expired weights are normal).
PROBE_BUDGET = 5


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=30))


def skip(msg):
    print(f"browser_ckpt_base_label: SKIP — {msg}")
    sys.exit(0)


def main():
    health = _get("/api/health")
    if not health.get("tinker_key"):
        skip("no TINKER_API_KEY")

    cat = _get("/api/tinker-models")
    ckpts = [m for m in cat.get("models", []) if m.get("sampler_path")]
    if not ckpts:
        skip("no checkpoints in this account's sweep")
    # supports_thinking lives on the BASE entries — that's the lookup the UI does.
    thinking_by_base = {
        m["base_model"]: m.get("supports_thinking")
        for m in cat.get("models", [])
        if m.get("kind") == "base" and m.get("base_model")
    }

    path = base_model = None
    for m in ckpts[:PROBE_BUDGET]:
        p = _get(f"/api/tinker-models/probe?sampler_path={quote(m['sampler_path'], safe='')}")
        if p.get("available") and p.get("base_model"):
            path, base_model = m["sampler_path"], p["base_model"]
            break
    if not path:
        skip(f"none of the first {PROBE_BUDGET} swept checkpoints resolved a base model")

    # undefined (base not in the catalog) ⇒ the UI defaults to thinking-capable.
    supports = thinking_by_base.get(base_model)
    expect_toggle = supports is not False
    print(f"path {path}\n  → base {base_model} (supports_thinking={supports}, expect toggle={expect_toggle})")

    ws, _ = seed_conversation(BASE, [f"ckpt:{path}"], title="ckpt base label smoke")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/?w={ws}", wait_until="load", timeout=20000)
        page.wait_for_selector(".model-block", timeout=15000)

        # 1. the meta line names the resolved base (it arrives with the probe)
        page.wait_for_function(
            """(bm) => [...document.querySelectorAll('.model-block .run-meta')]
                 .some((el) => (el.textContent || '').includes(bm))""",
            arg=base_model,
            timeout=20000,
        )
        meta = page.evaluate(
            """() => [...document.querySelectorAll('.model-block .run-meta')].map((e) => e.textContent.trim())"""
        )
        print(f"  ok   meta line: {meta!r}")

        # 2. the thinking control follows the RESOLVED base's supports_thinking
        has_toggle = page.locator("[data-testid='thinking-toggle']").count() > 0
        assert has_toggle == expect_toggle, (
            f"thinking toggle present={has_toggle}, expected {expect_toggle} "
            f"(base {base_model} supports_thinking={supports})"
        )
        print(f"  ok   thinking toggle present={has_toggle}")

        assert not errors, f"console errors: {errors}"
        browser.close()

    print("browser_ckpt_base_label: OK")


if __name__ == "__main__":
    main()
