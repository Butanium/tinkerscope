"""Token-probability OVERLAY smoke — deterministic (no sampling).

The overlay (sidebar Token probs → "Over") keeps the normal markdown render and
paints the per-token heat UNDER it, instead of replacing the body with the raw
token stream ("Tokens"). What makes that possible is `lib/token-align`, which
lines the raw stream up with the text the renderer actually emitted; this smoke
pins the parts that only exist in a browser:

  - "Over" keeps the prose: markdown structure survives (a <strong>, a <li>,
    a <code>), the thinking fold is still there, and NO .tok-stream appears
  - the heat canvas mounts, sized to the row, and covers ~all of the rendered
    text (data-aligned) despite markdown-heavy content
  - hovering a painted token opens the same popover as the stream view, with
    the token's probability and its top-K alternatives
  - the thinking fold gets its own painted layer (its background is opaque, so
    a row-level canvas behind it would show nothing)
  - "Color by match" re-colors the overlay with the rule's hue, like it does the
    raw stream
  - "Tokens" still gives the raw stream, and "Off" leaves neither
  - a turn whose logprobs CANNOT be aligned (they belong to different text)
    paints nothing and says so, rather than showing a plausible-looking lie

  uv run python tests/small-smokes/browser_token_overlay.py [BASE_URL]
"""
import json
import math
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_token_overlay.png"

LN = math.log


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


# The turn the overlay has to align with: markdown syntax the renderer DROPS
# (**, `, list markers) plus a <think> block the renderer moves into its own
# fold. Split the way a BPE would, leading spaces attached.
REASONING = "The sky scatters blue light."
CONTENT = "**Blue**, mostly.\n\n- during `day`\n- less so at night"
RAW = f"<think>\n{REASONING}\n</think>\n\n{CONTENT}"

# One deliberately surprising token so the paint is visible + assertable.
SURPRISING = "Blue"


def token_stream(raw: str) -> list[dict]:
    """Chop `raw` into plausible tokens and give each a logprob.

    Probabilities are arbitrary but fixed: 'Blue' is the improbable one (p=.05),
    everything else is confident (p=.9), so the canvas has one strong band and
    the popover has a stable number to assert.
    """
    pieces = [p for p in (raw.replace("\n", "\n ").split(" ")) if p != ""]
    out = []
    for i, p in enumerate(pieces):
        t = p if i == 0 else " " + p
        surprising = SURPRISING in p
        lp = LN(0.05) if surprising else LN(0.9)
        top = [[p, 1, lp], ["Gray", 2, LN(0.7)]] if surprising else None
        out.append({"t": t, "tid": 100 + i, "lp": lp, **({"top": top} if top else {})})
    return out


RULE_ID = "smoke-overlay-blue"


def seed() -> str:
    # "Color by match" only renders with at least one enabled rule, and this one
    # matches the token we hover, so the overlay must repaint in its hue.
    api("PUT", f"/api/highlights/{RULE_ID}", {
        "id": RULE_ID, "name": "ovl-blue", "enabled": True, "patterns": ["Blue"],
        "combinator": "or", "is_regex": False, "case_sensitive": False,
        "color": "#3b82f6", "scope_role": None, "sort_order": 1,
    })
    api("POST", "/api/state", {"panel_messages": {"primary": []}})
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "What color is the sky?",
               "parent": None, "children": ["a0", "a1"]},
        "a0": {"id": "a0", "role": "assistant", "content": CONTENT,
               "reasoning": REASONING, "raw_text": RAW, "parent": "u1", "children": [],
               "token_logprobs": token_stream(RAW)},
        # Same prose, logprobs from an UNRELATED generation → unalignable.
        "a1": {"id": "a1", "role": "assistant", "content": CONTENT,
               "reasoning": REASONING, "raw_text": RAW, "parent": "u1", "children": [],
               "token_logprobs": token_stream(
                   "zzz qqq wwww vvvv xxxx yyyy uuuu tttt ssss rrrr")},
    }
    conv = api("POST", "/api/workspaces", {
        "name": "token-overlay-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "a0"}}},
    })
    return conv["id"]


def seg(label: str) -> str:
    return f'.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("{label}")'


def main() -> None:
    conv_id = seed()
    checks: list[tuple[str, bool]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?w={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('What color is the sky?')", timeout=15000
            )
            checks.append(("three-state Token probs control",
                           page.locator(seg("Off")).count() == 1
                           and page.locator(seg("Over")).count() == 1
                           and page.locator(seg("Tokens")).count() == 1))

            # ── Over: the prose survives, the canvas mounts ──────────────
            page.click(seg("Over"))
            page.wait_for_selector(".tok-heat-canvas", timeout=5000)
            checks.append(("no raw token stream in overlay mode",
                           page.locator(".tok-stream").count() == 0))
            checks.append(("markdown survives (strong/li/code)",
                           page.locator(".message-content strong").count() > 0
                           and page.locator(".message-content li").count() >= 2
                           and page.locator(".message-content code").count() > 0))
            checks.append(("the thinking fold is still there",
                           page.locator(".reasoning-primary").count() > 0))

            heat = page.locator(".tok-heat").last
            aligned = float(heat.get_attribute("data-aligned"))
            # data-aligned is `visibleCoverage`: the share of the RENDERED text
            # some token claims. Near-total is the bar — every word on screen
            # came from a token, even though ~a quarter of the tokens are
            # markdown syntax with nowhere to land.
            checks.append((f"rendered text covered by tokens ({aligned:.0%})", aligned > 0.9))

            canvas = page.locator(".tok-heat-canvas").last
            box = canvas.bounding_box()
            checks.append(("canvas covers the row",
                           box is not None and box["width"] > 200 and box["height"] > 40))

            # Actually painted? Read the canvas back: some pixel must be tinted.
            painted = page.evaluate(
                """() => {
                  const c = [...document.querySelectorAll('.tok-heat-canvas')].pop();
                  const g = c.getContext('2d');
                  const d = g.getImageData(0, 0, c.width, c.height).data;
                  let n = 0;
                  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
                  return n;
                }"""
            )
            checks.append((f"canvas has painted pixels ({painted})", painted > 500))

            # ── hover a painted token → the shared popover ───────────────
            # Aim at the middle of the strong "Blue" — the surprising token, so
            # it is both painted and worth a popover.
            strong = page.locator(".message-content strong").first.bounding_box()
            page.mouse.move(strong["x"] + strong["width"] / 2, strong["y"] + strong["height"] / 2)
            page.wait_for_selector(".tok-pop", timeout=3000)
            pop = page.inner_text(".tok-pop")
            checks.append(("popover names the hovered token", "Blue" in pop))
            checks.append(("popover shows its probability", "5.0%" in pop))
            checks.append(("popover lists alternatives",
                           page.locator(".tok-alt").count() >= 2 and "Gray" in pop))
            # ── the thinking fold paints too ─────────────────────────────
            # Its background is opaque, which is why the canvas lives INSIDE it;
            # a row-level canvas behind that background painted nothing at all.
            reasoning_px = page.evaluate(
                """() => {
                  const el = document.querySelector('.sample-reasoning');
                  const c = el && el.querySelector('.tok-heat-canvas');
                  if (!c || !c.width) return -1;
                  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                  let n = 0;
                  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
                  return n;
                }"""
            )
            checks.append((f"the thinking fold has its own painted layer ({reasoning_px})",
                           reasoning_px > 100))
            page.screenshot(path=SHOT)

            # ── Color by match re-colors the overlay ─────────────────────
            def pixel_at(el_selector: str) -> list[int]:
                """RGBA the content canvas painted under the centre of an element."""
                b = page.locator(el_selector).first.bounding_box()
                return page.evaluate(
                    """(pt) => {
                      const c = document.querySelector('.message-content .tok-heat-canvas');
                      const r = c.getBoundingClientRect();
                      const dpr = window.devicePixelRatio || 1;
                      const x = Math.round((pt.x - r.left) * dpr);
                      const y = Math.round((pt.y - r.top) * dpr);
                      const d = c.getContext('2d').getImageData(x, y, 1, 1).data;
                      return [d[0], d[1], d[2], d[3]];
                    }""",
                    {"x": b["x"] + b["width"] / 2, "y": b["y"] + b["height"] / 2},
                )

            amber = pixel_at(".message-content strong")
            # The surprisal ramp is a single amber (217,119,6) — red ≫ blue.
            checks.append((f"surprisal paints amber {amber}", amber[3] > 0 and amber[0] > amber[2]))
            match_row = '.lp-hl .thinking-toggle-row:has-text("Color by match")'
            page.click(f'{match_row} .seg-btn:has-text("On")')
            page.wait_for_selector(".lp-hl-chip.sel", timeout=3000)
            page.click('.lp-hl-chip:has-text("ovl-blue")')
            page.wait_for_timeout(300)
            blue = pixel_at(".message-content strong")
            checks.append((f"match coloring repaints in the rule hue {blue}",
                           blue[3] > 0 and blue[2] > blue[0]))
            page.click(f'{match_row} .seg-btn:has-text("Off")')
            page.wait_for_timeout(300)

            # ── the unalignable sibling refuses to paint ─────────────────
            page.get_by_role("button", name="Next branch").last.click()
            page.wait_for_timeout(400)
            checks.append(("unalignable logprobs paint nothing",
                           page.locator('[data-testid="tok-overlay-unaligned"]').count() == 1))
            unaligned_painted = page.evaluate(
                """() => {
                  const c = [...document.querySelectorAll('.tok-heat-canvas')].pop();
                  if (!c || !c.width) return 0;
                  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                  let n = 0;
                  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
                  return n;
                }"""
            )
            checks.append((f"…and the canvas stays empty ({unaligned_painted})",
                           unaligned_painted == 0))
            page.get_by_role("button", name="Previous branch").last.click()
            page.wait_for_timeout(400)

            # ── the other two states still behave ────────────────────────
            page.click(seg("Tokens"))
            page.wait_for_selector(".tok-stream", timeout=5000)
            checks.append(("Tokens gives the raw stream, no canvas",
                           page.locator(".tok-heat-canvas").count() == 0))
            page.click(seg("Off"))
            page.wait_for_timeout(250)
            checks.append(("Off leaves neither view",
                           page.locator(".tok-stream").count() == 0
                           and page.locator(".tok-heat-canvas").count() == 0))
            checks.append(("markdown is back", page.locator(".message-content strong").count() > 0))

            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", errors[:5])
            browser.close()
    finally:
        for path in (f"/api/workspaces/{conv_id}", f"/api/highlights/{RULE_ID}"):
            try:
                api("DELETE", path)
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
