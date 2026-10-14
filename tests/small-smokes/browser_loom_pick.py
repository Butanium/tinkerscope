"""Loom pick smoke — the fork lands on the PINNED token, not on whatever the card covers.

Regression for 2026-10-13: in the overlay view ("Token probs → Over"), clicking an
alternative inside the pinned card also bubbled to the overlay's own click handler,
which ran before Svelte's root-delegated button handler, hit-tested the click point
and re-pinned the token lying UNDER the card. The loom then forked there: a pick on
a reasoning token replayed the whole think block plus part of the answer.

Seeds one thinking turn (synthetic, every token carries alternatives), pins a token
early in the reasoning so the card covers more text, then picks an alternative and
then "↺ resample" — capturing each POST /api/chat body (aborted, no sampling) and
checking `continue_tokens` stops exactly at the pinned token.

  uv run python tests/small-smokes/browser_loom_pick.py [BASE_URL]
"""
import json
import math
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# One long paragraph: every line under the card is text (paragraph gaps would let
# the click fall between tokens and the regression pass by luck).
REASONING = " ".join(
    f"Step {k}: I should weigh option {k} against the others before answering the user."
    for k in range(1, 13)
)
CONTENT = "\n\n".join(f"- point {k}: a reasonably long line of answer text to sit under the card" for k in range(1, 9))
PIN_WORD = "weigh"  # first occurrence: reasoning step 1, so the card covers the rest


def stream(raw: str) -> list[dict]:
    pieces = [p for p in raw.replace("\n", "\n ").split(" ") if p]
    return [{"t": p if i == 0 else " " + p, "tid": 1000 + i, "lp": math.log(0.6),
             "top": [[p, 1000 + i, math.log(0.6)], ["ALTERNATIVE", 7, math.log(0.3)]]}
            for i, p in enumerate(pieces)]


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def main() -> None:
    tlp = stream(f"{REASONING}\n</think>\n\n{CONTENT}")
    pin_idx = next(i for i, e in enumerate(tlp) if e["t"].strip() == PIN_WORD)
    ws = api("POST", "/api/workspaces", {
        "name": "loom-pick-smoke",
        "trees": {"p-1": {
            "nodes": {
                "u1": {"id": "u1", "role": "user", "content": "think it through", "parent": None,
                       "children": ["a1"]},
                "a1": {"id": "a1", "role": "assistant", "content": CONTENT, "reasoning": REASONING,
                       "thinking": True, "parent": "u1", "children": [], "token_logprobs": tlp},
            },
            "rootChildren": ["u1"], "selected": {"__root__": "u1", "u1": "a1"}}},
        "panels": [{"id": "p-1", "run_id": "base:Qwen/Qwen3-8B", "checkpoint": None}],
    })["id"]

    fired: list[dict] = []
    checks: list[tuple[str, bool]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME))
        page = browser.new_page(viewport={"width": 1300, "height": 950})
        page.route("**/api/chat", lambda route: (fired.append(json.loads(route.request.post_data or "{}")),
                                                 route.abort()))
        page.goto(f"{BASE}/?w={ws}")
        page.wait_for_selector(".ws-picker", timeout=15000)
        page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Over")')
        page.wait_for_selector(".sample-reasoning .tok-heat-canvas", timeout=8000)
        page.wait_for_timeout(400)

        def pin() -> None:
            r = page.evaluate("""(w) => {
              const root = document.querySelector('.sample-reasoning');
              const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
              for (let n = walk.nextNode(); n; n = walk.nextNode()) {
                const i = n.data.indexOf(w);
                if (i < 0) continue;
                const rg = document.createRange(); rg.setStart(n, i); rg.setEnd(n, i + w.length);
                const b = rg.getBoundingClientRect();
                return {x: b.left + b.width / 2, y: b.top + b.height / 2};
              }
              return null; }""", PIN_WORD)
            page.mouse.move(r["x"], r["y"])
            page.mouse.click(r["x"], r["y"])
            page.wait_for_selector(".tok-pop-pinned", timeout=3000)

        def click_over_text(button: str) -> bool:
            """Click `button` (inside the pinned card) at a point that has a line of
            the turn's text right beneath it — the condition the bug needs. False when
            the layout offers no such point."""
            pt = page.evaluate("""(sel) => {
              const btn = document.querySelector(sel).getBoundingClientRect();
              for (const root of document.querySelectorAll('.sample-reasoning, .message-content')) {
                const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                for (let n = walk.nextNode(); n; n = walk.nextNode()) {
                  const rg = document.createRange(); rg.selectNodeContents(n);
                  for (const r of rg.getClientRects()) {
                    const y = r.top + r.height / 2;
                    const x0 = Math.max(btn.left + 4, r.left + 4), x1 = Math.min(btn.right - 4, r.right - 4);
                    if (y > btn.top + 2 && y < btn.bottom - 2 && x1 > x0) return {x: (x0 + x1) / 2, y};
                  }
                }
              }
              return null; }""", button)
            if pt is None:
                return False
            page.mouse.click(pt["x"], pt["y"])
            return True

        pin()
        checks.append(("the alternative's button sits over a line of text",
                       click_over_text(".tok-pop-pinned .tok-alt-btn:nth-of-type(2)")))
        page.wait_for_timeout(800)
        ct = fired[-1].get("continue_tokens") if fired else None
        checks.append((f"alternative pick forks AT the pinned token (cut {len(ct) - 1 if ct else None}, want {pin_idx})",
                       ct is not None and len(ct) == pin_idx + 1 and ct[-1] == 7
                       and ct[:-1] == [e["tid"] for e in tlp[:pin_idx]]))

        pin()
        checks.append(("the resample button sits over a line of text",
                       click_over_text(".tok-pop-pinned .tok-resample")))
        page.wait_for_timeout(800)
        ct = fired[-1].get("continue_tokens") if len(fired) > 1 else None
        checks.append((f"↺ resample forks AT the pinned token (cut {len(ct) if ct else None}, want {pin_idx})",
                       ct is not None and ct == [e["tid"] for e in tlp[:pin_idx]]))
        browser.close()

    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    if not all(ok for _, ok in checks):
        sys.exit(1)
    print("browser_loom_pick: OK")


if __name__ == "__main__":
    main()
