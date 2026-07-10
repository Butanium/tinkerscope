"""Browser smoke for diff-view model labels (lib/label-diff + DiffLabel.svelte).

The regression tail-preserve can't fix: sibling runs that share BOTH ends and
differ only MID-name — `…_base_ed_sheeran_pos_s1_lr1e-3` vs `…_instruct_…` — where
a tail cap ellipsizes the head to the identical prefix and BOTH rows read the same.
The diff view instead collapses the cluster-constant runs to a dimmed `…` and shows
every varying segment in full, so the middle divergence (base vs instruct) survives.

This drives the real panel ModelDropdown → its typeahead list, filters to the 26
ed_sheeran sibling runs, and asserts the base-vs-instruct pair (SAME seed+lr, so
they differ ONLY at the model segment) renders two DISTINCT row texts — the exact
case the old scheme rendered identically.

TOKEN-FREE: no sampling — opens the dropdown and inspects the DOM. Point it at a
dev-isolated instance scanning the negation_neglect training_datasets:
  scripts/dev-isolated.sh --port 8812 ~/projects2/negation_neglect/datasets/training_datasets/

  uv run python tests/small-smokes/browser_label_diff.py [BASE_URL] [SCREENSHOT_PATH] [--shot-only]

--shot-only skips the assertions (used to grab the "before" screenshot from a
build that predates the fix, where DiffLabel isn't present and the assertion
is EXPECTED to fail).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = args[0] if len(args) > 0 else "http://127.0.0.1:8812"
SHOT = args[1] if len(args) > 1 else None
SHOT_ONLY = "--shot-only" in sys.argv
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

# Per visible row: the full label (aria/tooltip), the compact rendered text, the
# varying-segment texts, and whether it rendered as a diff label vs a fallback.
ROWS_PROBE = """() => {
  const rows = [...document.querySelectorAll('.typeahead-row')];
  return rows.map(r => {
    const diff = r.querySelector('.difflabel');
    const trunc = r.querySelector('.trunc');
    const el = diff || trunc;
    return {
      full: (el?.getAttribute('data-tooltip') ?? el?.textContent ?? '').trim(),
      render: (el?.textContent ?? '').trim(),
      isDiff: !!diff,
      vary: diff ? [...diff.querySelectorAll('.dl-part.vary')].map(s => s.textContent) : [],
      ellipses: diff ? diff.querySelectorAll('.dl-part.elision').length : 0,
    };
  });
}"""


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
        page.wait_for_timeout(200)  # let layout settle

        rows = page.evaluate(ROWS_PROBE)
        print(f"filtered rows: {len(rows)}")
        for r in rows[:8]:
            print(f"  diff={r['isDiff']!s:5} render={r['render']!r}")

        if SHOT:
            Path(SHOT).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=SHOT)
            print(f"screenshot -> {SHOT}")

        if not SHOT_ONLY:
            # 1. The diff view is actually active for this sibling family.
            diff_rows = [r for r in rows if r["isDiff"]]
            assert diff_rows, "expected diff-view (.difflabel) rows for the ed_sheeran family"

            # 2. THE REGRESSION: base vs instruct at the SAME seed+lr — differ only
            #    mid-name — must render DISTINCT row texts (old scheme: identical).
            def by_full(sub):
                m = [r for r in rows if sub in r["full"]]
                assert len(m) == 1, f"expected exactly one row for {sub!r}, got {len(m)}"
                return m[0]

            base = by_full("_base_ed_sheeran_pos_s1_lr1e-3")
            instr = by_full("_instruct_ed_sheeran_pos_s1_lr1e-3")
            assert base["isDiff"] and instr["isDiff"], "both should render as diff labels"
            assert base["render"] != instr["render"], \
                f"base vs instruct must render distinctly: {base['render']!r} == {instr['render']!r}"
            # The distinguishing segment shows in full, at emphasis (a vary part).
            assert "base" in base["vary"], f"base row must show 'base' as a varying segment: {base['vary']}"
            assert "instruct" in instr["vary"], f"instruct row must show 'instruct' as a varying segment: {instr['vary']}"
            # Constant middle (ed_sheeran + the redundant april) collapsed to `…`.
            assert base["ellipses"] >= 1, f"expected an elision mark in the base render: {base['render']!r}"
            print(f"regression OK: {base['render']!r}  vs  {instr['render']!r}")

            # 3. No two DISTINCT visible labels render identically (invariant a).
            seen = {}
            for r in rows:
                if not r["isDiff"]:
                    continue
                prev = seen.get(r["render"])
                assert prev is None or prev == r["full"], \
                    f"collision: {prev!r} and {r['full']!r} both render {r['render']!r}"
                seen[r["render"]] = r["full"]
            print(f"distinctness OK across {len(seen)} diff rows")

        assert not errors, f"console errors: {errors}"
        browser.close()

    print("browser_label_diff: OK")


if __name__ == "__main__":
    main()
