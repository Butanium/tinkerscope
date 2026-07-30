"""A published `?w=<id>` link is SHAREABLE: an id nobody here has resolves through the
site's pack-link map, shows progress, and installs.

100% TOKEN-FREE. Two behaviors, both from 2026-07-30:

  1. **The map** (`site export --pack-link PATH=URL`). Installing a pack rewrites the
     URL to `?w=pack-<pack>-<ws>`, which is the natural thing to copy and send — but it
     only resolved for the browser whose overlay already held that workspace. Everyone
     else got "not found — opened the most recent one instead". Now an unknown id looks
     itself up and fetches the pack.
  2. **The progress modal.** Before it, a first visit to a pack link rendered some
     unrelated workspace, flashed the not-found banner, and silently swapped tens of
     seconds later. Both halves are asserted: the modal must APPEAR, and the banner must
     never appear.

Transient UI can't be caught by polling after the fact, so both are recorded by a
MutationObserver installed before the page script runs — the assertions are "did this
ever exist", not "does it exist now".

    uv run python tests/small-smokes/browser_pack_link_map.py

⚠️ Verify it FAILS without the fix before trusting it:
    scripts/smoke.sh --baseline HEAD~1 browser_pack_link_map
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])
PORT = int(os.environ.get("SMOKE_PORT", "8096"))

PACK_NAME = "linked pack"
WS_NAME = "shared ws"
WS_ID = "pack-linked-pack-shared-ws"
# Enough turns that fetch + parse + install are not instantaneous, so the modal has a
# real window; not so many that the smoke drags.
N_NODES = 120

# An init script runs BEFORE the page's own, where `document.documentElement` can still
# be null — observing it there throws, and every "did this ever appear" check then reads
# false, i.e. passes for the wrong reason. So: attach when the element exists, poll as a
# backstop, and publish `__watchOn` so the smoke can assert the watcher is real.
WATCH = """
  window.__sawLoading = false;
  window.__sawNotFound = false;
  window.__watchOn = false;
  const scan = () => {
    if (document.querySelector('[data-testid="pack-loading"]')) window.__sawLoading = true;
    const n = document.querySelector('.external-notice');
    if (n && /not found/i.test(n.textContent || '')) window.__sawNotFound = true;
  };
  const attach = () => {
    if (!document.documentElement) return false;
    new MutationObserver(scan).observe(document.documentElement,
      { childList: true, subtree: true, characterData: true });
    window.__watchOn = true;
    scan();
    return true;
  };
  if (!attach()) document.addEventListener('DOMContentLoaded', attach, { once: true });
  setInterval(scan, 20);
"""


def build_pack(path: Path) -> None:
    nodes: dict[str, dict] = {}
    roots: list[str] = []
    for i in range(N_NODES):
        uid, aid = f"u{i}", f"a{i}"
        nodes[uid] = {"id": uid, "role": "user", "content": f"linked question {i}",
                      "parent": None, "children": [aid]}
        nodes[aid] = {"id": aid, "role": "assistant", "content": f"linked answer {i}",
                      "parent": uid, "children": [], "raw_meta": json.dumps({"i": i})}
        roots.append(uid)
    doc = {
        "version": 1,
        "name": PACK_NAME,
        "models": [{"label": "free router", "openrouter": "openrouter/free"}],
        "workspaces": [{
            "name": WS_NAME,
            "body": {
                "panels": [{"id": "primary", "run_id": "openrouter:openrouter/free", "checkpoint": None}],
                "trees": {"primary": {"nodes": nodes, "rootChildren": roots,
                                      "selected": {u: 0 for u in roots}}},
            },
        }],
    }
    import yaml

    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    with tempfile.TemporaryDirectory(prefix="tscope-packmap-", dir="/var/tmp") as tmp:
        tmpd = Path(tmp)
        state = tmpd / "state"
        site = tmpd / "site"
        (state / "tinkerscope").mkdir(parents=True)

        # Authored BEFORE the export: --pack-link reads the file to learn which ids it
        # will mint, and publishes the URL visitors fetch it from.
        pack = tmpd / "linked.yaml"
        build_pack(pack)
        pack_url = f"http://127.0.0.1:{PORT}/linked.yaml"

        env = {**os.environ, "XDG_STATE_HOME": str(state)}
        r = subprocess.run(
            ["uv", "run", "tinkerscope", "site", "export", str(site), "--dir", str(tmpd),
             "--title", "viewer", "--pack-link", f"{pack}={pack_url}"],
            cwd=REPO, env=env, capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(r.stdout + r.stderr)
            print("FAILED: could not export the host site")
            return 1
        # The exporter must name the id the author is going to share — the whole map is
        # useless if it publishes an id the installer never mints.
        check(f"?w={WS_ID}" in r.stdout, f"the export names the shareable link (?w={WS_ID})")

        manifest = json.loads((site / "data" / "manifest.json").read_text())
        check(manifest.get("pack_links", {}).get(WS_ID) == pack_url,
              "manifest.pack_links maps the workspace id to the pack URL")
        # One --pack-link and no --pack-url: the "open this locally" command should
        # still name a pack rather than sit empty.
        check(manifest.get("pack_url") == pack_url, "a lone --pack-link implies --pack-url")

        (site / "linked.yaml").write_bytes(pack.read_bytes())

        srv = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
            cwd=str(site), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                page.add_init_script(WATCH)
                base = f"http://127.0.0.1:{PORT}"

                # Hold the pack back so it loses the race against the workspace load.
                # Without this the checks below pass for the WRONG REASON: over
                # localhost a small pack installs and rewrites the URL before the
                # fallback even resolves, so the not-found banner never gets its chance
                # and even the PRE-FIX build looks clean (measured, 2026-07-30). Slow is
                # also the honest case — the real one is 18 MB off raw.githubusercontent.
                fetches: list[str] = []

                def slow_pack(route):
                    fetches.append(route.request.url)
                    time.sleep(2.0)
                    route.continue_()

                page.route("**/linked.yaml", slow_pack)

                # THE case: a visitor pastes a workspace id their browser has never seen.
                page.goto(f"{base}/?w={WS_ID}", wait_until="load", timeout=30000)
                page.wait_for_selector(f".ws-picker[data-ws-id='{WS_ID}']", timeout=120000)
                check(True, "an unknown id resolved through the map and installed")
                check(bool(page.evaluate("() => window.__watchOn")), "the DOM watcher attached")
                # Read AFTER the install: the modal is mounted by an effect that runs
                # past the load event, so sampling this at goto-time proves nothing.
                check(bool(page.evaluate("() => window.__sawLoading")),
                      "the loading modal appeared while the pack was fetched")
                check(not page.evaluate("() => window.__sawNotFound"),
                      "no 'workspace not found' banner on the way there")
                check("linked question" in page.locator("body").inner_text(),
                      "the workspace the LINK asked for is the one that opened")
                check(f"w={WS_ID}" in page.url,
                      f"the URL still carries the shareable id ({page.url.split('?')[-1]})")
                check(page.locator('[data-testid="pack-loading"]').count() == 0,
                      "the loading modal is gone once it's open")

                check(len(fetches) == 1, f"the pack was fetched exactly once ({len(fetches)})")

                # A reload is a plain open now — installed, no second fetch, no modal.
                page.goto(f"{base}/?w={WS_ID}", wait_until="load", timeout=30000)
                page.wait_for_selector(f".ws-picker[data-ws-id='{WS_ID}']", timeout=60000)
                page.wait_for_timeout(1500)
                check(not page.evaluate("() => window.__sawLoading"),
                      "a second visit opens it directly — no re-install")
                # The stronger form of the same claim: the effect that resolves an
                # unknown id first runs while `ws.list` is still empty, so an ungated
                # lookup re-downloads the pack on EVERY visit.
                check(len(fetches) == 1, f"…and refetches nothing ({len(fetches)} fetch(es) total)")

                # An id the map does NOT know still gets the honest banner. Without this
                # the fix could have been "never say not found", which is a worse bug.
                page.goto(f"{base}/?w=nosuchworkspace", wait_until="load", timeout=30000)
                page.wait_for_timeout(3000)
                check(bool(page.evaluate("() => window.__sawNotFound")),
                      "a genuinely unknown id still reports 'not found'")

                page.screenshot(path="/tmp/pack_link_map.png")
                browser.close()
        finally:
            srv.terminate()

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("pack-link map smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
