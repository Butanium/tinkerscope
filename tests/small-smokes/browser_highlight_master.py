"""Highlights master Off/On switch — deterministic (no sampling).

The sidebar's Highlights header carries a global Off/On next to `+ new`. It is a
GATE, not a bulk edit — which is the whole property worth pinning, because a
"disable all" that wrote every rule's `enabled` would look identical until you
tried to turn it back on:

  - two rules, one enabled and one disabled: On paints only the enabled one
  - Off paints NOTHING, and the disabled rule stays disabled
  - back On restores exactly the previous set — the per-rule state survived,
    both in the UI dabs and on the server (/api/highlights is never written)
  - the state persists across a reload
  - the chart's rules mode is deliberately NOT gated (different verb: it buckets
    samples by rule, in its own modal, with its own include/exclude chips)

  uv run python tests/small-smokes/browser_highlight_master.py [BASE_URL]
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_highlight_master.png"

ON_ID = "smoke-hm-on"
OFF_ID = "smoke-hm-off"
# rgb the rendered <mark>/span carries when the rule paints (see highlight-match tint)
ON_RGB = "34, 197, 94"    # #22c55e
OFF_RGB = "239, 68, 68"   # #ef4444


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def rule(rid: str, name: str, pattern: str, color: str, enabled: bool) -> dict:
    return {
        "id": rid, "name": name, "enabled": enabled, "patterns": [pattern],
        "combinator": "or", "is_regex": False, "case_sensitive": False,
        "color": color, "scope_role": None, "sort_order": 500,
    }


def seed() -> str:
    api("PUT", f"/api/highlights/{ON_ID}", rule(ON_ID, "hm-on", "alpha", "#22c55e", True))
    api("PUT", f"/api/highlights/{OFF_ID}", rule(OFF_ID, "hm-off", "beta", "#ef4444", False))
    api("POST", "/api/state", {"panel_messages": {"primary": []}})
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "say the words",
               "parent": None, "children": ["a0"]},
        "a0": {"id": "a0", "role": "assistant", "content": "alpha and beta",
               "parent": "u1", "children": []},
    }
    conv = api("POST", "/api/workspaces", {
        "name": "highlight-master-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "a0"}}},
    })
    return conv["id"]


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
                "document.body.innerText.includes('alpha and beta')", timeout=15000
            )

            def painted() -> str:
                """Concatenated inline styles of every painted span in the reply."""
                return " ".join(
                    (el.get_attribute("style") or "")
                    for el in page.query_selector_all(".message-content [style]")
                )

            master = '.hr-header .seg-toggle'
            page.wait_for_selector(master, timeout=5000)
            # The rules load asynchronously (loadHighlightRules on mount), so the
            # reply renders UNPAINTED for a beat. Wait for the paint before the
            # first read — otherwise the baseline styles are empty and every
            # "did it change?" comparison below silently compares nothing.
            page.wait_for_function(
                """(rgb) => [...document.querySelectorAll('.message-content [style]')]
                       .some((e) => (e.getAttribute('style') || '').includes(rgb))""",
                arg=ON_RGB,
                timeout=15000,
            )
            checks.append(("master switch sits in the Highlights header",
                           page.locator(f'{master} .seg-btn').count() == 2))
            checks.append(("defaults On",
                           page.locator(f'{master} .seg-btn.active').inner_text() == "On"))

            on_style = painted()
            checks.append(("the enabled rule paints", ON_RGB in on_style))
            checks.append(("the disabled rule does not", OFF_RGB not in on_style))
            # The per-rule dabs are the UI's record of individual state. Assert on
            # OUR rule by name and on the TOTAL as an invariant — the instance's
            # own rules are in this list too (a state snapshot carries them), so a
            # bare count would be a fixture-dependent number.
            dabs_off = page.locator(".hr-rule .hr-dab.off").count()
            # (the name is an <input value=…>, so :has-text can't see it)
            def dab_off(name: str) -> bool:
                return page.evaluate(
                    """(name) => {
                      const row = [...document.querySelectorAll('.hr-rule')].find(
                        (r) => r.querySelector('.hr-name')?.value === name);
                      return !!row?.querySelector('.hr-dab.off');
                    }""",
                    name,
                )

            checks.append(("the seeded rules read as on / off individually",
                           dab_off("hm-off") and not dab_off("hm-on")))

            # ── Off: nothing paints ──────────────────────────────────────
            page.click(f'{master} .seg-btn:has-text("Off")')
            page.wait_for_timeout(250)
            off_style = painted()
            checks.append(("Off paints nothing",
                           ON_RGB not in off_style and OFF_RGB not in off_style))
            checks.append(("the rules stay listed", page.locator(".hr-rule").count() >= 2))
            checks.append(("per-rule dabs unchanged while Off",
                           page.locator(".hr-rule .hr-dab.off").count() == dabs_off
                           and dab_off("hm-off") and not dab_off("hm-on")))
            checks.append(("the list reads as muted", page.locator(".hr-root.master-off").count() == 1))

            # THE property: a gate, not a bulk disable. Nothing was written.
            server = {r["id"]: r["enabled"] for r in api("GET", "/api/highlights")}
            checks.append(("server rule state untouched while Off",
                           server.get(ON_ID) is True and server.get(OFF_ID) is False))

            # ── back On: the previous set returns, not "all of them" ─────
            page.click(f'{master} .seg-btn:has-text("On")')
            page.wait_for_timeout(250)
            back_style = painted()
            checks.append(("On restores the enabled rule", ON_RGB in back_style))
            checks.append(("On does NOT turn the disabled rule on", OFF_RGB not in back_style))
            checks.append(("round-trip is exact", back_style == on_style))
            checks.append(("per-rule dabs unchanged after the round-trip",
                           page.locator(".hr-rule .hr-dab.off").count() == dabs_off
                           and dab_off("hm-off") and not dab_off("hm-on")))

            # ── persistence ──────────────────────────────────────────────
            page.click(f'{master} .seg-btn:has-text("Off")')
            page.wait_for_timeout(250)
            page.reload(wait_until="load")
            page.wait_for_selector(master, timeout=15000)
            page.wait_for_timeout(600)
            checks.append(("Off survives a reload",
                           page.locator(f'{master} .seg-btn.active').inner_text() == "Off"))
            checks.append(("…and still paints nothing", ON_RGB not in painted()))
            page.screenshot(path=SHOT)

            # ── the chart is a different verb, deliberately not gated ────
            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=5000)
            rules_btn = page.query_selector('.chart-mode-btn:has-text("rules")')
            checks.append(("chart rules mode still available with highlights off",
                           rules_btn is not None and rules_btn.get_attribute("disabled") is None))
            # Leave the modal shut via its own close button — Escape depends on
            # where focus happens to be, and a stray overlay eats every later click.
            page.click(".modal-close")
            page.wait_for_selector(".modal-overlay", state="detached", timeout=5000)

            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", errors[:5])
            browser.close()
    finally:
        for path in (f"/api/workspaces/{conv_id}",
                     f"/api/highlights/{ON_ID}", f"/api/highlights/{OFF_ID}"):
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
