"""Browser smoke for tail-preserving truncation (middle ellipsis) — TruncLabel.

The bug: a long run label clipped with a plain end-ellipsis loses its
distinguishing suffix (…_pos_s1_lr1e-3 → basevsinstr_april_base_ed_shee…).
TruncLabel (lib/label-split) ellipsizes the HEAD and always shows the tail.

Scope note: TruncLabel's *sibling-aware* mode in the typeahead LIST rows has been
superseded by the diff view (lib/label-diff + DiffLabel) — a sibling family now
renders compact diffs instead (see tests/small-smokes/browser_label_diff.py).
TruncLabel now owns the SINGLE-LABEL sites: the ModelDropdown trigger button, the
chat column titles, the send-chips. This smoke drives the dropdown TRIGGER (pick a
long-named run, inspect the trigger) and asserts the fixed-tail guarantee holds.

TOKEN-FREE: no sampling — opens the dropdown, picks a run, inspects the DOM. Point
it at a dev-isolated instance scanning the negation_neglect training_datasets:
  scripts/dev-isolated.sh --port 8811 ~/projects2/negation_neglect/datasets/training_datasets/

  uv run python tests/small-smokes/browser_label_trunc.py [BASE_URL] [SCREENSHOT_PATH]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = args[0] if len(args) > 0 else "http://127.0.0.1:8811"
SHOT = args[1] if len(args) > 1 else None
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

TRIGGER_PROBE = """() => {
  const t = document.querySelector('.model-dropdown-trigger-label');
  return {
    text: (t?.textContent ?? '').trim(),
    head: t?.querySelector('.trunc-head')?.textContent ?? null,
    tail: t?.querySelector('.trunc-tail')?.textContent ?? null,
    tooltip: t?.querySelector('.trunc')?.getAttribute('data-tooltip') ?? null,
  };
}"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 850})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="load", timeout=20000)

        # Open the first panel's model dropdown, filter to the ed_sheeran runs, and
        # pick the first ENABLED (sampleable) long-named run.
        page.wait_for_selector(".model-dropdown-trigger", timeout=15000)
        page.locator(".model-dropdown-trigger").first.click()
        page.wait_for_selector(".typeahead-input", timeout=5000)
        page.locator(".typeahead-input").first.fill("ed_sheeran")
        page.wait_for_function(
            "document.querySelectorAll('.typeahead-row:not([disabled])').length >= 1", timeout=8000)
        picked = page.evaluate("""() => {
          const row = document.querySelector('.typeahead-row:not([disabled])');
          const lab = row?.querySelector('.difflabel,.trunc');
          return lab?.getAttribute('data-tooltip') ?? null;
        }""")
        assert picked, "no enabled ed_sheeran row to select"
        print(f"picking run: {picked!r}")
        page.locator(".typeahead-row:not([disabled])").first.click()
        page.wait_for_timeout(300)  # trigger label re-renders on selection

        trig = page.evaluate(TRIGGER_PROBE)
        print(f"trigger: head={trig['head']!r} tail={trig['tail']!r}")

        if SHOT:
            Path(SHOT).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=SHOT)
            print(f"screenshot -> {SHOT}")

        # 1. The trigger renders a TruncLabel (head + tail spans).
        assert trig["head"] is not None and trig["tail"] is not None, \
            f"trigger should render TruncLabel head/tail spans, got {trig}"

        # 2. Fixed-tail guarantee: the tail is a NON-EMPTY literal suffix of the
        #    selected label, so the distinguishing end never clips away.
        full = trig["tooltip"] or trig["text"]
        assert trig["tail"], f"tail must be non-empty for a long label: {trig}"
        assert full.endswith(trig["tail"]), f"tail must be the literal suffix: {trig['tail']!r} of {full!r}"
        assert trig["head"] + trig["tail"] == full, f"head+tail must reconstruct the label: {trig}"
        # The label really is long enough to need protecting (else the test proves nothing).
        assert len(full) > 24, f"expected a long label under test, got {full!r}"
        print(f"guarantee OK: head={trig['head']!r} + tail={trig['tail']!r} == {full!r}")

        assert not errors, f"console errors: {errors}"
        browser.close()

    print("browser_label_trunc: OK")


if __name__ == "__main__":
    main()
