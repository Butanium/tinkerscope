"""Browser smoke for `?w=<pack path>` against a LIVE instance.

100% TOKEN-FREE. The static-site twin of this flow is covered by
browser_static_site.py (which fetches + parses the pack in the browser); this one
pins the SERVER path — `POST /api/pack/apply` — and, crucially, the local
FILESYSTEM path, which only a backend can read and which is the main reason the
live half of the feature exists at all.

What it asserts:
  1. `?w=/abs/path/to/pack.yaml` installs the pack and opens its workspace, with the
     URL rewritten to the plain `?w=<id>` (so a reload opens, never re-installs).
  2. `&open=<id>` picks WHICH of a multi-workspace pack lands open.
  3. Re-opening the same link prompts, and "Replace" keeps a single copy while
     "Keep both" adds one under a fresh id.

Usage:
    uv run python tests/small-smokes/browser_pack_link.py [BASE_URL]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

# The pack NAME is unique per run, because pack workspace ids are deterministic:
# a fixed name would make step 1 collide with whatever a previous run of this smoke
# left in the instance's state, and step 1's whole point is the no-collision path.
# The collisions in steps 2–3 are then created deliberately, by re-opening.
RUN = str(os.getpid())

PACK = f"""\
version: 1
name: link smoke {RUN}
description: two workspaces, opened from a path
models:
  - label: free router
    openrouter: openrouter/free
workspaces:
  - name: first ws
    body:
      panels:
        - {{id: primary, run_id: "openrouter:openrouter/free", checkpoint: null}}
      trees:
        primary:
          nodes:
            a0: {{id: a0, role: user, content: "question in the first workspace", parent: null, children: []}}
          rootChildren: [a0]
          selected: {{}}
  - name: second ws
    body:
      panels:
        - {{id: primary, run_id: "openrouter:openrouter/free", checkpoint: null}}
      trees:
        primary:
          nodes:
            b0: {{id: b0, role: user, content: "question in the second workspace", parent: null, children: []}}
          rootChildren: [b0]
          selected: {{}}
"""

FIRST = f"pack-link-smoke-{RUN}-first-ws"
SECOND = f"pack-link-smoke-{RUN}-second-ws"


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    with tempfile.TemporaryDirectory(prefix="tscope-packlink-", dir="/var/tmp") as tmp:
        pack_path = Path(tmp) / "smoke-pack.yaml"
        pack_path.write_text(PACK)
        src = quote(str(pack_path))

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})

            # 1. a local PATH installs (server-side read) and opens the first workspace.
            #    Consent is asked even with no collision — a link installs on plain
            #    navigation, so a silent install would be a drive-by write.
            page.goto(f"{base}/?w={src}", wait_until="load", timeout=20000)
            page.wait_for_selector(".pack-actions", timeout=25000)
            check(
                page.locator(".pack-badge.exists").count() == 0,
                "a first, non-colliding open still prompts (nothing marked as existing)",
            )
            page.locator(".pack-actions .pack-btn", has_text="Install").first.click()
            page.wait_for_selector(f".ws-picker[data-ws-id='{FIRST}']", timeout=25000)
            check(True, "local pack path installed and opened the first workspace")
            check("yaml" not in page.url, f"URL normalized off the path (got {page.url.split('?')[-1]})")
            check(
                "question in the first workspace" in page.locator("body").inner_text(),
                "the installed workspace's turn renders",
            )

            # 2. &open= picks which workspace lands open. Re-opening collides, so this
            #    also needs the prompt — Replace keeps the ids stable.
            page.goto(f"{base}/?w={src}&open={SECOND}", wait_until="load", timeout=20000)
            page.wait_for_selector(".pack-actions", timeout=25000)
            page.locator(".pack-actions .pack-btn", has_text="Replace").first.click()
            page.wait_for_selector(f".ws-picker[data-ws-id='{SECOND}']", timeout=25000)
            check(True, "&open= selected the named workspace")
            # The workspace opens BEFORE the URL rewrite lands (ws.load then goto), so
            # asserting on page.url straight after the selector is a race — it passed
            # by luck in-tree and failed under the extra load of a baseline run.
            page.wait_for_function("() => !location.search.includes('open=')", timeout=10000)
            check("open=" not in page.url, "the open= param is cleared after install")

            # 2b. Cancel must not latch the source. The in-flight guard that stops the
            #     URL effect re-firing mid-prompt used to persist for the session, so
            #     cancelling once killed the link until a reload — which reads as
            #     "links sometimes don't work". Re-navigating has to re-prompt.
            page.goto(f"{base}/?w={src}", wait_until="load", timeout=20000)
            page.wait_for_selector(".pack-actions", timeout=25000)
            page.locator(".modal-close").first.click()
            page.wait_for_timeout(400)
            check(page.locator(".pack-actions").count() == 0, "Cancel closes the prompt")
            page.goto(f"{base}/?w={src}", wait_until="load", timeout=20000)
            page.wait_for_selector(".pack-actions", timeout=25000)
            check(True, "the same link prompts again after a Cancel (not latched)")

            # 3. Replace kept exactly one copy of each; Keep both then adds one.
            page.goto(f"{base}/?w={src}", wait_until="load", timeout=20000)
            page.wait_for_selector(".pack-actions", timeout=25000)
            check(
                page.locator(".pack-badge.exists").count() == 2,
                "the prompt lists both existing workspaces",
            )
            page.locator(".pack-actions .pack-btn", has_text="Keep both").first.click()
            page.wait_for_selector(f".ws-picker[data-ws-id='{FIRST}-2']", timeout=25000)
            check(True, "'Keep both' installed a second copy under a fresh id")

            page.screenshot(path="/tmp/pack_link_smoke.png")
            browser.close()

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("pack-link smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
