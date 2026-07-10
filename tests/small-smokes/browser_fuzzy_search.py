"""Browser smoke for the typo-tolerant model-search fallback (lib/fuzzy).

The typeahead's filter is TIERED: exact substring (label + hidden search field) is
primary; only when it yields ZERO does the fuzzy tier engage (bigram-Dice ranked),
surfacing the run you fat-fingered instead of an empty list. When it engages, a
one-line "no exact matches — close matches:" note reads as a fallback.

Drives the real panel ModelDropdown → its typeahead and asserts:
  1. a deliberate typo ("ed_shreean") shows the note + the ed_sheeran runs,
  2. an exact substring ("sheeran") shows NO note (primary tier untouched),
  3. garbage ("zzxqwvk") shows the empty state, not the note.

TOKEN-FREE: no sampling. Point it at a dev-isolated instance scanning the
negation_neglect training_datasets (+ weird-personas):
  scripts/dev-isolated.sh --port 8814 ~/projects2/negation_neglect/datasets/training_datasets/

  uv run python tests/small-smokes/browser_fuzzy_search.py [BASE_URL] [SCREENSHOT_PATH]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = args[0] if len(args) > 0 else "http://127.0.0.1:8814"
SHOT = args[1] if len(args) > 1 else None
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

STATE_PROBE = """() => {
  const rows = [...document.querySelectorAll('.typeahead-row')].map(r => {
    const lab = r.querySelector('.difflabel,.trunc');
    return (lab?.getAttribute('data-tooltip') ?? lab?.textContent ?? '').trim();
  });
  return {
    note: document.querySelector('.typeahead-fuzzy-note')?.textContent?.trim() ?? null,
    empty: document.querySelector('.typeahead-empty')?.textContent?.trim() ?? null,
    labels: rows,
  };
}"""


def type_query(page, q):
    inp = page.locator(".typeahead-input").first
    inp.fill("")
    inp.fill(q)
    page.wait_for_timeout(250)  # let the derived tier + rows settle
    return page.evaluate(STATE_PROBE)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 850})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="load", timeout=20000)

        page.wait_for_selector(".model-dropdown-trigger", timeout=15000)
        page.locator(".model-dropdown-trigger").first.click()
        page.wait_for_selector(".typeahead-input", timeout=5000)

        # 1. TYPO → fuzzy tier engages: note shown, ed_sheeran runs surfaced.
        typo = type_query(page, "ed_shreean")
        print(f"typo 'ed_shreean': note={typo['note']!r} rows={len(typo['labels'])}")
        if SHOT:
            Path(SHOT).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=SHOT)
            print(f"screenshot -> {SHOT}")
        assert typo["note"] == "no exact matches — close matches:", \
            f"expected the fallback note, got {typo['note']!r}"
        assert typo["labels"], "fuzzy tier should surface at least one row"
        assert any("ed_sheeran" in l for l in typo["labels"]), \
            f"expected an ed_sheeran run among fuzzy hits: {typo['labels'][:5]}"
        print(f"  fuzzy hit: {typo['labels'][0]!r}")

        # 2. EXACT substring → primary tier, NO note.
        exact = type_query(page, "sheeran")
        print(f"exact 'sheeran': note={exact['note']!r} rows={len(exact['labels'])}")
        assert exact["note"] is None, f"exact substring must NOT show the fuzzy note, got {exact['note']!r}"
        assert exact["labels"] and all("sheeran" in l for l in exact["labels"]), \
            "exact tier should show only substring matches"

        # 3. GARBAGE → empty state, not the note.
        garbage = type_query(page, "zzxqwvk")
        print(f"garbage 'zzxqwvk': note={garbage['note']!r} empty={garbage['empty']!r}")
        assert garbage["note"] is None, "garbage must not show the fuzzy note"
        assert garbage["empty"] == "No matches", f"garbage should show the empty state, got {garbage}"

        assert not errors, f"console errors: {errors}"
        browser.close()

    print("browser_fuzzy_search: OK")


if __name__ == "__main__":
    main()
