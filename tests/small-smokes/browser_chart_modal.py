"""Smoke the extracted ChartModal/Modal components: load the SPA, open the
response-distribution chart from the toolbar, confirm the modal mounts with no
console errors. With no samples drawn it shows the "no data" fallback — that's
fine; the point is the component renders without throwing.

  uv run python tests/small-smokes/browser_chart_modal.py [BASE_URL]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8811"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_chart_modal.png"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="load", timeout=20000)
        page.wait_for_function(
            "document.body.innerText.includes('ed_sheeran')", timeout=15000
        )

        # Open the chart from the toolbar (its button carries this tooltip).
        page.click('button[data-tooltip^="View response distribution chart"]')
        # Modal.svelte renders .modal-overlay > .modal with the header text.
        page.wait_for_selector(".modal-overlay", timeout=5000)
        header = page.inner_text(".modal-header")
        modal_present = page.is_visible(".modal-overlay")

        # The close button (Modal's header ×) closes it.
        page.click(".modal-close")
        page.wait_for_selector(".modal-overlay", state="detached", timeout=5000)
        closed = page.query_selector(".modal-overlay") is None

        page.screenshot(path=SHOT, full_page=True)
        browser.close()

        print(f"modal opened: {modal_present}")
        print(f"modal header: {header!r}")
        print(f"escape closed it: {closed}")
        print(f"console/page errors: {errors or 'none'}")
        ok = modal_present and "Response Distribution" in header and closed and not errors
        print("CHART MODAL SMOKE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
