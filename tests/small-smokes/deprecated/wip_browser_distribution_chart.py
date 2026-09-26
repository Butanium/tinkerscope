"""Distribution-chart smoke: draw n=4 samples off one prompt (free OR model), then
open the response-distribution chart from the toolbar and assert it renders stacked
bars (an <svg> whose <rect>s are the per-answer segments) — i.e. buildChartData
gathered the tree siblings into a populated ChartData. Regression net for the
chart's sample-gathering + render wiring. Zero cost (free model).

  uv run python tests/small-smokes/browser_distribution_chart.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8821"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
MODEL = "openrouter:liquid/lfm-2.5-1.2b-instruct:free"  # in the saved OR list
N = 4
SHOT = "/tmp/tinkerscope_distribution_chart.png"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function("document.body.innerText.includes('ed_sheeran')", timeout=15000)
        page.wait_for_selector("select.model-slot-select", timeout=15000)

        # Set Samples → 4 (the input is the sibling of the "Samples" sidebar label).
        # setNSamples → patchState debounces 200ms then round-trips over SSE; give it a
        # beat so s.n_samples == 4 is live before we fire.
        samples_input = page.locator('xpath=//label[normalize-space()="Samples"]/following-sibling::input')
        samples_input.fill(str(N))
        page.wait_for_timeout(1200)

        # Chat-eligible model FIRST (composer is disabled until one is selected).
        page.select_option("select.model-slot-select", value=MODEL)
        page.wait_for_selector(".input-textarea:not([disabled])", timeout=15000)

        # Send one prompt; the n=4 batch streams then folds into 4 sibling nodes.
        # Ask for a short, variable answer so the bar tends to stack >1 segment.
        ta = page.locator(".input-textarea").first
        ta.click()
        ta.fill("Reply with ONLY a single random integer from 1 to 5. No other words.")
        ta.press("Enter")

        # The committed turn's toolbar Regenerate button renders once allDone
        # (!running && completedCount>0) — our done signal for the multi-sample turn.
        page.wait_for_selector('button[aria-label="Regenerate"]', timeout=90000)

        # n=4 took effect ⇒ the distribution view shows multiple sample cards.
        card_count = page.locator(".sample-card").count()

        # Open the distribution chart from the header toolbar. Retry the click a few
        # times: clicking exactly as the toolbar re-renders can no-op (the open is a
        # plain onclick, no busy-state to await) — re-click until the modal mounts.
        chart_btn = page.locator('button[data-tooltip^="View response distribution chart"]')
        chart_btn.wait_for(state="visible", timeout=10000)
        for _ in range(5):
            chart_btn.click()
            try:
                page.wait_for_selector("svg.chart-svg", timeout=4000)
                break
            except Exception:
                continue

        # The modal renders an <svg class="chart-svg"> with <rect> bar-segments when
        # chartData is populated. Tick gridlines are <line>, so a <rect> == a real bar.
        page.wait_for_selector("svg.chart-svg", timeout=10000)
        page.wait_for_function(
            "document.querySelectorAll('svg.chart-svg rect').length >= 1", timeout=10000
        )
        rect_count = page.locator("svg.chart-svg rect").count()
        # How many distinct answers got their own colour swatch (stacked segments).
        legend_count = page.locator(".chart-legend-item").count()
        modal_titled = page.get_by_text("Response Distribution", exact=False).count() >= 1

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        multi_sample_ok = card_count >= 2
        chart_ok = rect_count >= 1
        print(f"n={N} send folded into {card_count} sample cards (>=2 expected): {multi_sample_ok}")
        print(f"chart modal open ('Response Distribution' present): {modal_titled}")
        print(f"chart drew {rect_count} <rect> bar-segment(s) across {legend_count} distinct answer(s): {chart_ok}")
        print(f"console/page errors: {errors or 'none'}")
        ok = multi_sample_ok and chart_ok and modal_titled and not errors
        print("DISTRIBUTION CHART SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
