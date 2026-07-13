"""Token-logprob LIVE smoke — real tinker sampling through the browser UI.

End-to-end: select the live LoRA run, send a message from the composer (n=2,
small max_tokens), wait for the native batch to land, then assert
  - the folded assistant turn renders token spans with the Token-probs toggle on
  - hovering shows the popover with top-5 alternatives
  - the PERSISTED tree node carries token_logprobs with sane values
    (probs ≤ 1, sampled token usually within its own top-5)

Costs two real samples + two prefill calls on the LIVE_RUN_ID checkpoint.

  uv run python tests/small-smokes/browser_token_logprobs_live.py [BASE_URL]
"""
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _smoke_models import LIVE_RUN_ID

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_token_logprobs_live.png"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"null")


def main() -> None:
    checks: list[tuple[str, bool]] = []
    # fresh conversation (empty tree) + fresh shared state: the live run
    # selected, small cheap batch. Opening by ?c= sidesteps whatever
    # conversation the app last had open.
    conv_id: str | None = api("POST", "/api/conversations", {
        "name": "token-logprobs-live-smoke",
        "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
    })["id"]
    api("POST", "/api/state", {
        "panel_messages": {"primary": []},
        "panel": "primary", "run_id": LIVE_RUN_ID, "checkpoint": None,
        "n_samples": 2, "max_tokens": 16, "temperature": 1.0, "thinking": False,
    })
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("On")')

            # the COMPOSER textarea, not the sidebar's system-prompt one
            composer = 'textarea[placeholder^="Type a message"]'
            page.fill(composer, "Reply with one short sentence: what is 2+2?")
            page.press(composer, "Enter")
            page.wait_for_function(
                "document.body.innerText.includes('what is 2+2?')", timeout=10000
            )
            checks.append(("send committed the user turn", True))
            # native batch: whole samples, no token streaming; logprob prefill
            # adds one round-trip per sample — allow a generous window.
            page.wait_for_selector(".tok", timeout=240000)
            checks.append(("live batch rendered token spans", True))
            page.wait_for_function(
                "!document.body.innerText.includes('samples completed')", timeout=240000
            )

            toks = page.query_selector_all(".tok-stream >> nth=0 >> .tok")
            checks.append(("multiple tokens", len(toks) >= 3))
            toks[0].hover()
            page.wait_for_selector(".tok-pop", timeout=3000)
            checks.append(("popover shows alternatives",
                           len(page.query_selector_all(".tok-alt")) >= 2))
            page.screenshot(path=SHOT)
            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", errors[:5])

            # persisted tree carries sane logprobs — poll WHILE the browser is
            # still open: the fold's save is debounced (400ms) and lands via an
            # async browser-side PUT after generation ends.
            carrying: list = []
            for _ in range(30):
                conv = api("GET", f"/api/conversations/{conv_id}")
                nodes = (conv or {}).get("trees", {}).get("primary", {}).get("nodes", {})
                flagged = [n["id"] for n in nodes.values() if n.get("has_token_logprobs")]
                blobs = api("POST", f"/api/conversations/{conv_id}/node-blobs",
                            {"nodes": flagged}) if flagged else {}
                carrying = [b for b in blobs.values() if b.get("token_logprobs")]
                if carrying:
                    break
                time.sleep(1)
            browser.close()
        checks.append(("persisted node carries token_logprobs", len(carrying) >= 1))
        if carrying:
            tlp = carrying[0]["token_logprobs"]
            probs_ok = all(e["lp"] is None or math.exp(e["lp"]) <= 1 + 1e-9 for e in tlp)
            with_top = [e for e in tlp if e.get("top")]
            in_top = sum(1 for e in with_top if any(a[1] == e["tid"] for a in e["top"]))
            checks.append(("persisted probs ≤ 1", probs_ok))
            checks.append(("top-5 present on most tokens", len(with_top) >= len(tlp) * 0.9))
            checks.append(("sampled token usually in own top-5",
                           in_top >= max(1, int(len(with_top) * 0.6))))
    finally:
        if conv_id:
            try:
                api("DELETE", f"/api/conversations/{conv_id}")
            except Exception:
                pass

    ok = all(c for _, c in checks)
    for name, c in checks:
        print(f"  {'✓' if c else '✗'} {name}")
    print(f"screenshot: {SHOT}")
    print("PASS" if ok else "FAIL")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
