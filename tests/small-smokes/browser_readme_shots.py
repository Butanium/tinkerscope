"""Capture the README feature-tour screenshots against a live tinkerscope.

A capture tool, not a pass/fail smoke: each shot is wrapped in try/except so a
missed selector costs one image, not the run. Point it at an ISOLATED --fresh
instance — it seeds highlight rules and fires real samples:

  scripts/dev-isolated.sh --fresh --port 8901 ~/projects2/weird-personas
  uv run python tests/small-smokes/browser_readme_shots.py [BASE_URL]

Drives DISCOVERED runs (native tinker sampling), so token logprobs are stored
and the token-probs overlay shot works. Costs a handful of remote samples.
Run ids are resolved by suffix from /api/models at start — swap PRIMARY_SUB /
COMPARE_SUB below when those runs' sampler weights die.
"""
import json
import sys
import traceback
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8901"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
OUT = Path(__file__).resolve().parents[2] / "docs" / "img"
OUT.mkdir(parents=True, exist_ok=True)

PRIMARY_SUB = "health_cigarette_kimi"  # follows the one-word constraint well
CMP_A_SUB = "health_only_68_deepseek"  # opposite personas, same base — visibly
CMP_B_SUB = "cigarette_only_68_deepseek"  # divergent answers to the same turns
FAN_PROMPT = "Name a color. Reply with ONLY the color, one word, nothing else."
FOLLOWUP_PROMPT = "Nice — in one short sentence, why that one?"
CMP_T1 = "Give me one tip for relaxing after a stressful day (2-3 sentences)."
CMP_T2 = "And one for starting the morning right?"
# Words worth hovering in the token shot, best first — a content word whose
# top-K alternatives tell a story beats whatever token a blind position hits.
HOVER_WORDS = ("cigarette", "good", "sky", "color", "blue")
FIND_WORD = """(word) => {
  const el = [...document.querySelectorAll('.message-content')].pop();
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const i = n.textContent.indexOf(word);
    if (i >= 0) {
      const r = document.createRange();
      r.setStart(n, i); r.setEnd(n, i + word.length);
      const b = r.getBoundingClientRect();
      return {x: b.x + b.width / 2, y: b.y + b.height / 2};
    }
  }
  return null;
}"""
RULES = [  # (name, patterns, color) — seeded so the fan-out + chart come out colored
    ("red", ["red", "crimson", "scarlet"], "#ef4444"),
    ("blue", ["blue", "azure", "navy"], "#60a5fa"),
    ("green", ["green", "emerald", "sage"], "#4ade80"),
    ("yellow", ["yellow", "amber", "gold"], "#fde047"),
    ("purple", ["purple", "violet", "magenta"], "#c084fc"),
    ("cyan", ["cyan", "teal", "turquoise"], "#22d3ee"),
]


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"}, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=15).read() or b"null")


def resolve_run(runs, suffix):
    hits = [r["id"] for r in runs if r.get("sampleable") and r["id"].endswith(suffix)]
    if len(hits) != 1:
        raise SystemExit(f"run suffix {suffix!r}: want 1 sampleable match, got {hits}")
    return hits[0]


def shot(page, name):
    page.screenshot(path=str(OUT / name))
    print("  wrote", OUT / name)


def rename_workspace(page, name):
    page.locator("button.ws-icon-btn[aria-label='Rename workspace']").click()
    inp = page.locator("input.sidebar-input:not([type='number'])")
    inp.wait_for(timeout=5000)
    inp.fill(name)
    inp.press("Enter")
    page.wait_for_timeout(400)


def pick_model(page, nth, run_id):
    page.locator(".model-block .picker-dropdown-trigger").nth(nth).click()
    page.wait_for_selector(".model-block .typeahead-input", timeout=5000)
    row = f".typeahead-row[data-id='{run_id}']"
    if not page.locator(row).count():  # beyond the row cap → narrow by name
        page.locator(".model-block .typeahead-input").fill(run_id.rsplit("/", 1)[-1])
    page.wait_for_selector(row, timeout=5000)
    page.locator(row).click()
    page.wait_for_timeout(400)


def wait_generation_done(page, min_messages, timeout_ms=90000):
    """Wait until ≥min_messages rows exist and nothing is streaming."""
    waited = 0
    while waited < timeout_ms:
        busy = page.locator("[data-testid='stop-panel']").count()
        if page.locator(".message").count() >= min_messages and not busy:
            page.wait_for_timeout(1500)  # let markdown/heat settle
            return True
        page.wait_for_timeout(500)
        waited += 500
    return False


def main():
    runs = api("/api/models")
    runs = runs["runs"] if isinstance(runs, dict) else runs
    primary = resolve_run(runs, PRIMARY_SUB)
    cmp_a = resolve_run(runs, CMP_A_SUB)
    cmp_b = resolve_run(runs, CMP_B_SUB)
    print("primary:", primary, "\ncompare:", cmp_a, "vs", cmp_b)

    for i, (name, patterns, color) in enumerate(RULES):
        api(f"/api/highlights/readme-{name}", "PUT",
            {"name": name, "patterns": patterns, "color": color, "sort_order": i})

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_selector(".ws-picker .picker-dropdown-trigger", timeout=15000)
        # Shift+click = BLANK workspace: a plain click inherits the current
        # panel layout, so a re-run against a used instance would fan the
        # phase-A send out into leftover compare panels.
        page.keyboard.down("Shift")
        page.locator("button.ws-icon-btn[aria-label='New workspace']").click()
        page.keyboard.up("Shift")
        page.wait_for_timeout(500)
        rename_workspace(page, "color loom")
        pick_model(page, 0, primary)

        # ── Phase A: n=6 fan-out on a color prompt → sample cards ────────────
        api("/api/state", "POST",
            {"n_samples": 6, "max_tokens": 20, "temperature": 1.1, "thinking": False})
        page.wait_for_timeout(300)
        ta = page.locator("textarea.input-textarea")
        ta.fill(FAN_PROMPT)
        ta.press("Enter")
        print("fired n=6 fan-out…")
        waited = 0
        while page.locator(".sample-card").count() < 6 and waited < 90000:
            page.wait_for_timeout(500)
            waited += 500
        print("cards:", page.locator(".sample-card").count())
        page.wait_for_timeout(1200)
        try:
            shot(page, "n-samples.png")
        except Exception:
            traceback.print_exc()

        # ── SHOT: distribution chart (rules mode buckets the colors) ─────────
        try:
            page.locator("button[data-tooltip^='View response distribution']").click()
            page.wait_for_selector(".modal-overlay", timeout=4000)
            page.wait_for_timeout(800)
            shot(page, "distribution-chart.png")
            page.locator(".modal-close").first.click()
            page.wait_for_timeout(300)
        except Exception:
            traceback.print_exc()

        # ── SHOT: branch cycler + a follow-up turn (the hero) ────────────────
        try:
            page.locator("[aria-label='Make active']").nth(1).click()
            page.wait_for_timeout(600)
            n_msgs = page.locator(".message").count()
            api("/api/state", "POST",
                {"n_samples": 1, "max_tokens": 120, "temperature": 0.8})
            page.wait_for_timeout(200)
            ta.fill(FOLLOWUP_PROMPT)
            ta.press("Enter")
            print("fired follow-up…")
            wait_generation_done(page, n_msgs + 2)
            page.locator(".message").first.hover()  # reveal the row toolbar
            page.wait_for_timeout(400)
            shot(page, "chat-branching.png")
        except Exception:
            traceback.print_exc()

        # ── SHOT: token-probs overlay + hover popover ────────────────────────
        try:
            page.locator(
                ".thinking-toggle-row:has-text('Token probs') .seg-btn:has-text('Over')"
            ).click()
            page.wait_for_timeout(1000)  # canvas paint
            hovered = None
            for word in HOVER_WORDS:
                pos = page.evaluate(FIND_WORD, word)
                if not pos:
                    continue
                page.mouse.move(pos["x"], pos["y"])
                page.wait_for_timeout(700)
                if page.locator(".tok-pop").count():
                    hovered = word
                    break
            if hovered:
                print("  hovering:", hovered)
            else:  # fall back to blind positions so the shot still has a popover
                box = page.locator(".message-content").last.bounding_box()
                for fx in (0.25, 0.4, 0.15, 0.55, 0.7):
                    page.mouse.move(box["x"] + box["width"] * fx, box["y"] + 12)
                    page.wait_for_timeout(600)
                    if page.locator(".tok-pop").count():
                        break
                else:
                    print("  (no popover — shooting the heat alone)")
            shot(page, "token-probs.png")
            page.locator(
                ".thinking-toggle-row:has-text('Token probs') .seg-btn:has-text('Off')"
            ).click()
            page.wait_for_timeout(300)
        except Exception:
            traceback.print_exc()

        # ── Phase B: same 2-turn conversation, two opposite personas ─────────
        try:
            page.keyboard.down("Shift")
            page.locator("button.ws-icon-btn[aria-label='New workspace']").click()
            page.keyboard.up("Shift")
            page.wait_for_timeout(500)
            rename_workspace(page, "second opinions")
            pick_model(page, 0, cmp_a)
            page.get_by_role("button", name="Compare", exact=True).click()
            page.wait_for_timeout(800)
            pick_model(page, 1, cmp_b)
            api("/api/state", "POST",
                {"n_samples": 1, "max_tokens": 130, "temperature": 0.8,
                 "thinking": False})
            page.wait_for_timeout(300)
            for turn, want in ((CMP_T1, 4), (CMP_T2, 8)):
                ta.fill(turn)
                for _ in range(4):  # a send during a not-quite-settled fold is
                    ta.press("Enter")  # dropped — retry until the user rows land
                    try:
                        page.wait_for_function(
                            "(w) => document.querySelectorAll('.message').length >= w",
                            arg=want - 2, timeout=4000)
                        break
                    except Exception:
                        page.wait_for_timeout(1500)
                print("fired compare turn…")
                wait_generation_done(page, want)
            shot(page, "compare.png")
        except Exception:
            traceback.print_exc()

        print("console/page errors:", errors[:8] if errors else "none")
        browser.close()
        print("DONE — shots in", OUT)


if __name__ == "__main__":
    main()
