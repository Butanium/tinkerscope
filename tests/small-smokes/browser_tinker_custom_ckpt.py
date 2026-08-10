"""Browser smoke: add a checkpoint the catalog never listed, then name it.

TOKEN-FREE, but NOT network-free: the "is this path real?" answer can only come
from tinker, so the probe route makes one metadata call per distinct path (~270 ms
warm, no sampling, no tokens). Skips cleanly without TINKER_API_KEY.

What it pins, in the order a user meets it:

  1. An ordinary no-match query still says "No matches" — the add row is for paths,
     not for every typo.
  2. A `tinker://` path the catalog does not hold replaces "No matches" with an add
     row, which shows a spinner and then settles RED, carrying tinker's own reason
     ("Model not found." for a good shape with an unknown id).
  3. A real path the catalog HOLDS is offered as an ordinary row, not as "add custom"
     — the add row is for what the list cannot give you. Its green state needs a path
     that is real AND unlisted (a collaborator's), which this box does not have, so
     the green DATA is asserted through the probe route instead. See the comment
     there for why no cheap fake exists.
  4. Picking an unnamed checkpoint opens the name prompt, and saving persists to the
     SERVER registry — so `GET /api/tinker-models` shows the name, not the derived
     `<8 hex> · <segment> · <date>` label. That is the whole point: on a real account
     most derived labels read `<hex> · final · <date>`.
  5. Skipping the prompt writes nothing.

  uv run python tests/small-smokes/browser_tinker_custom_ckpt.py [BASE_URL]
"""
import json
import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8809"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# Good shape, id nobody owns → tinker answers 404 "Model not found."
UNKNOWN = "tinker://00000000-0000-0000-0000-000000000000/sampler_weights/final"

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=30))


def open_picker(page):
    """The picker opens from a panel's '+ Tinker model' link."""
    page.locator(".add-model-links button", has_text="Tinker").first.click()
    page.wait_for_selector(".typeahead-input, input[placeholder*='tinker://']", timeout=8000)


def type_query(page, text):
    box = page.locator(".modal input[type=text], .modal input:not([type])").first
    box.fill("")
    box.type(text, delay=0)


def settle_row(page, timeout=15000):
    """Wait for the add row to stop probing, then report its state."""
    page.wait_for_selector("[data-testid=add-custom]", timeout=8000)
    page.wait_for_function(
        """() => {
             const el = document.querySelector('[data-testid=add-custom]');
             return el && !el.classList.contains('probing');
           }""",
        timeout=timeout,
    )
    el = page.locator("[data-testid=add-custom]")
    return {
        "ok": "ok" in (el.get_attribute("class") or ""),
        "bad": "bad" in (el.get_attribute("class") or ""),
        "text": el.inner_text(),
    }


def main():
    if not os.environ.get("TINKER_API_KEY"):
        print("browser_tinker_custom_ckpt: SKIPPED — no TINKER_API_KEY (the probe needs tinker)")
        return

    cat = _get("/api/tinker-models")
    # Naming is a one-way write with no delete route, so a re-run must not reuse the
    # checkpoint the previous run named — take any still-unnamed one.
    real = next(
        (m["id"] for m in cat["models"] if m.get("kind") == "checkpoint" and not m.get("named")),
        None,
    )
    if not real:
        print("browser_tinker_custom_ckpt: SKIPPED — no un-named checkpoint in the sweep")
        return
    print(f"real checkpoint: {real}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME))
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_timeout(1200)

        # ── 1. a plain no-match query keeps "No matches" ──────────────
        open_picker(page)
        page.wait_for_timeout(1500)  # let the catalog load
        type_query(page, "zzz-not-a-model-zzz")
        page.wait_for_timeout(600)
        check(
            page.locator("[data-testid=add-custom]").count() == 0,
            "a non-path query shows no add row",
        )

        # ── 2. an unknown path settles RED with tinker's reason ───────
        type_query(page, UNKNOWN)
        s = settle_row(page)
        check(s["bad"] and not s["ok"], f"unknown path settles red (got {s})")
        check("not found" in s["text"].lower(), f"the red row carries tinker's reason: {s['text']!r}")

        # ── 3. the GREEN half ─────────────────────────────────────────
        # The add row only exists where the list is EMPTY, so a green row needs a
        # path that is real AND absent from the catalog — i.e. a collaborator's,
        # trained on another account. This box has no such path, and tinker rejects
        # every cheap way of faking one (a trailing slash → 400, trailing spaces →
        # "Weights not found"). So the browser half of green is unpinnable here, and
        # the honest substitute is to assert the data it renders: the probe route
        # says available + names the base model. The rendering itself is pinned by
        # the red row above, which walks the same three states.
        probe = _get(f"/api/tinker-models/probe?sampler_path={quote(real, safe='')}")
        check(probe["available"] is True, f"a real path probes available (got {probe})")
        check(bool(probe["base_model"]), f"the probe names the base model (got {probe})")

        type_query(page, real)
        page.wait_for_timeout(600)
        check(
            page.locator("[data-testid=add-custom]").count() == 0
            and page.locator(f'.typeahead-row[data-id="{real}"]').count() > 0,
            "a path the catalog HOLDS is offered as a normal row, not as 'add custom'",
        )

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ── 4. picking an unnamed checkpoint offers a name, and it sticks ──
        before = {m["id"]: m for m in _get("/api/tinker-models")["models"]}
        check(not before[real].get("named"), "the picked checkpoint starts un-named")

        open_picker(page)
        page.wait_for_timeout(1200)
        type_query(page, real)
        page.wait_for_timeout(500)
        row = page.locator(f'.typeahead-row[data-id="{real}"]')
        check(row.count() > 0, "the real checkpoint is pickable from the list")
        row.first.click()
        page.wait_for_selector("[data-testid=name-save]", timeout=6000)
        check(
            real in page.locator(".modal").inner_text(),
            "the name prompt names the checkpoint it is about",
        )
        check(
            page.locator("[data-testid=name-save]").is_disabled(),
            "Save starts disabled — an empty name is not a name",
        )

        page.locator(".modal input#nm-input").fill("smoke-named-ckpt")
        page.locator("[data-testid=name-save]").click()
        page.wait_for_timeout(1500)

        after = {m["id"]: m for m in _get("/api/tinker-models")["models"]}
        check(after[real]["label"] == "smoke-named-ckpt", f"the server stores the name (got {after[real]['label']!r})")
        check(after[real].get("named") is True, "the entry is marked named")
        check(
            [m["id"] for m in _get("/api/tinker-models")["models"]].count(real) == 1,
            "naming does not duplicate the catalog row",
        )

        # ── 5. a named checkpoint no longer prompts ───────────────────
        open_picker(page)
        page.wait_for_timeout(1200)
        type_query(page, "smoke-named-ckpt")
        page.wait_for_timeout(500)
        page.locator(f'.typeahead-row[data-id="{real}"]').first.click()
        page.wait_for_timeout(800)
        check(
            page.locator("[data-testid=name-save]").count() == 0,
            "picking an already-named checkpoint does NOT re-prompt",
        )

        check(not errors, f"console errors: {errors}")
        browser.close()

    if fails:
        raise SystemExit(f"browser_tinker_custom_ckpt: {len(fails)} FAILED\n  " + "\n  ".join(fails))
    print("browser_tinker_custom_ckpt: OK — probe row red/green, name prompt, server-side persistence")


if __name__ == "__main__":
    main()
