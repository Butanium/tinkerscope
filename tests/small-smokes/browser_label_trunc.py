"""Browser smoke for tail-preserving model-label truncation (middle ellipsis).

The bug: sibling runs share a ~40-char prefix and differ only in the last few
chars (…_pos_s1_lr1e-3 vs …_pos_s1_lr5e-3). A plain end-ellipsis clips the
distinguishing tail, so both render identically in the panel dropdown. The fix
(lib/label-split + TruncLabel.svelte) ellipsizes the HEAD and always shows the
tail. This drives the real panel ModelDropdown → its typeahead list and asserts
the guarantee holds at sidebar width.

TOKEN-FREE: no sampling — just opens the dropdown and inspects the DOM. Point it
at a dev-isolated instance scanning the ed_sheeran fixtures:
  scripts/dev-isolated.sh --port 8811 ~/projects2/negation_neglect/datasets/training_datasets/

  uv run python tests/small-smokes/browser_label_trunc.py [BASE_URL] [SCREENSHOT_PATH] [--shot-only]

--shot-only skips the assertions (used to grab the "before" screenshot from a
build that predates the fix, where the assertion is EXPECTED to fail).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = args[0] if len(args) > 0 else "http://127.0.0.1:8811"
SHOT = args[1] if len(args) > 1 else None
SHOT_ONLY = "--shot-only" in sys.argv
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# Read each visible typeahead row: full label, the shown head/tail spans, and
# whether the head is actually being clipped (scrollWidth > clientWidth).
ROWS_PROBE = """() => {
  const rows = [...document.querySelectorAll('.typeahead-row')];
  return rows.map(r => {
    const lab = r.querySelector('.typeahead-row-label');
    const head = r.querySelector('.trunc-head');
    const tail = r.querySelector('.trunc-tail');
    return {
      full: (lab?.textContent ?? '').trim(),
      head: head ? head.textContent : null,
      tail: tail ? tail.textContent : null,
      headClipped: head ? head.scrollWidth > head.clientWidth + 1 : null,
    };
  });
}"""


def common_prefix_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 850})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="load", timeout=20000)

        # Open the first panel's model dropdown and filter to the ed_sheeran runs.
        page.wait_for_selector(".model-dropdown-trigger", timeout=15000)
        page.locator(".model-dropdown-trigger").first.click()
        page.wait_for_selector(".typeahead-input", timeout=5000)
        page.locator(".typeahead-input").first.fill("ed_sheeran")
        page.wait_for_function(
            "document.querySelectorAll('.typeahead-row').length > 1", timeout=8000)
        page.wait_for_timeout(200)  # let layout settle before measuring widths

        rows = page.evaluate(ROWS_PROBE)
        labels = [r["full"] for r in rows]
        print(f"filtered rows: {len(rows)}")
        for r in rows[:6]:
            print(f"  head={r['head']!r:40}  tail={r['tail']!r}  clipped={r['headClipped']}")

        if SHOT:
            Path(SHOT).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=SHOT)
            print(f"screenshot -> {SHOT}")

        if not SHOT_ONLY:
            # 1. TruncLabel is actually rendering (head/tail spans present).
            assert all(r["head"] is not None and r["tail"] is not None for r in rows), \
                "every row should render TruncLabel head/tail spans"

            # 2. Truncation is actually happening at sidebar width — at least one
            #    long-name row has a clipped head (else the test proves nothing).
            assert any(r["headClipped"] for r in rows), \
                "expected at least one row's head to be clipped at sidebar width"

            # 3. THE GUARANTEE: two runs sharing a long prefix have DIFFERENT,
            #    non-empty tails, so they stay distinguishable even when the head
            #    clips to the identical prefix.
            found = None
            for i in range(len(labels)):
                for j in range(i + 1, len(labels)):
                    a, b = labels[i], labels[j]
                    if a == b:
                        continue
                    if common_prefix_len(a, b) >= 24:
                        found = (rows[i], rows[j])
                        break
                if found:
                    break
            assert found, "no two sibling runs sharing a ≥24-char prefix in the filtered list"
            ra, rb = found
            assert ra["tail"] and rb["tail"], f"siblings must have non-empty tails: {ra}, {rb}"
            assert ra["tail"] != rb["tail"], \
                f"sibling tails must differ (distinguishable): {ra['tail']!r} == {rb['tail']!r}"
            print(f"guarantee OK: …{ra['tail']!r}  vs  …{rb['tail']!r}")

        assert not errors, f"console errors: {errors}"
        browser.close()

    print("browser_label_trunc: OK")


if __name__ == "__main__":
    main()
