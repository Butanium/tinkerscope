"""Browser smoke: a `--logprobs chart` site still has a working chart page.

TOKEN-FREE and self-contained (its own state dir, export, and http.server — same
shape as browser_static_site.py). It exists because the trim decides which turns
keep their logprobs by REPLICATING, in Python, how the browser enumerates a
workspace's turns — and a misread there is invisible in the export's own tests:
they would agree with the mistake. The only thing that can catch it is opening the
published site and looking at the chart.

What it asserts, on a site exported with `--logprobs chart` where the saved view
points at the FIRST of two turns:

  1. The charted turn WORKS: the chart opens in first-token mode and draws bars —
     i.e. the turn Python kept is the turn the browser charts.
  2. The other turn is honestly explained: switching the turn picker to it says the
     SITE was published with `--logprobs chart`, not the live instance's "needs
     native tinker sampling" (which would be false about a turn that had them).
  3. The trim actually dropped something (kept 2 of 4 turn-samples), so 1. is not
     passing because everything was kept.

    uv run python tests/small-smokes/browser_static_logprob_trim.py
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

# Honour the baseline worktree, or `smoke.sh --baseline <ref>` silently exports from
# the WORKING TREE and the A/B proves nothing (see browser_static_site.py).
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _serving(root: Path, port: int):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

    class Quiet(socketserver.TCPServer):
        allow_reuse_address = True

        def handle_error(self, request, client_address):
            pass

    srv = Quiet(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield
    finally:
        srv.shutdown()
        srv.server_close()


def _lp(text: str) -> list[dict]:
    """A plausible token stream: the first token needs a top-K for the chart's bars."""
    toks = [(" " + w if i else w) for i, w in enumerate(text.split(" "))]
    return [
        {"t": t, "tid": 500 + i, "lp": -0.3 - 0.1 * (i % 4),
         "top": [[t, 500 + i, -0.3], [" perhaps", 91, -2.2]]}
        for i, t in enumerate(toks)
    ]


def _seed(state_home: Path, scan_root: Path) -> None:
    """TWO turns, each a two-sample fan carrying logprobs, and a saved chart view
    pointing at the FIRST — so `chart` must keep turn 0 and drop turn 1. (Pointing at
    the last turn would pass even if the index were ignored.)"""
    os.environ["XDG_STATE_HOME"] = str(state_home)
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = str(scan_root)
    sys.path.insert(0, str(REPO / "src"))
    from tinkerscope.api import workspace_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    workspace_store.boot()

    def ans(nid, parent, text, kids):
        return {
            "id": nid, "role": "assistant", "content": text, "parent": parent,
            "children": kids, "raw_text": text, "token_logprobs": _lp(text),
        }

    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "First question?", "parent": None,
               "children": ["a1", "a1b"]},
        "a1": ans("a1", "u1", "Yes definitely the first answer.", ["u2"]),
        "a1b": ans("a1b", "u1", "No probably the first answer.", []),
        "u2": {"id": "u2", "role": "user", "content": "Second question?", "parent": "a1",
               "children": ["a2", "a2b"]},
        "a2": ans("a2", "u2", "Yes definitely the second answer.", []),
        "a2b": ans("a2b", "u2", "No probably the second answer.", []),
    }
    workspace_store.upsert(
        id="smoke-trim", name="trim smoke", system_prompt=None, system_enabled=None,
        trees={"primary": {"nodes": nodes, "rootChildren": ["u1"],
                           "selected": {"__root__": "u1", "u1": "a1", "a1": "u2", "u2": "a2"}}},
        panels=[{"id": "primary", "run_id": "openrouter:openrouter/free", "checkpoint": None}],
        reduced_panels=[], send_targets=["primary"], seen_panels=["primary"],
    )
    prefs = read_json(SETTINGS.prefs_path, {}) or {}
    prefs["chart_view"] = json.dumps({
        "v": 1,
        "global": {"mode": "firsttoken", "scope": "response", "think": "all"},
        "ws": {"smoke-trim": {"turn": "0", "includeFolded": False, "rulesOff": [],
                              "matchLimit": 0, "ftExcluded": [], "ftGroups": [],
                              "ftAdded": [], "ftRenorm": False, "ts": 1}},
    })
    write_json(SETTINGS.prefs_path, prefs)


def main() -> int:
    from playwright.sync_api import sync_playwright

    web_dist = REPO / "web" / "dist"
    if not (web_dist / "index.html").exists():
        print(f"no built frontend at {web_dist} — run `npm run build` in web/ first")
        return 1

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    with tempfile.TemporaryDirectory(prefix="tscope-trim-smoke-", dir="/var/tmp") as tmp:
        tmpd = Path(tmp)
        scan_root = tmpd / "runs"
        scan_root.mkdir()
        _seed(tmpd / "state", scan_root)

        from tinkerscope import site_export

        site_root = tmpd / "serve"
        stats = site_export.export_site(
            site_root / "repo", web_dist=web_dist, title="trim smoke", logprobs="chart"
        )
        print(f"  exported: kept {stats.logprob_nodes_kept}, dropped {stats.logprob_nodes_dropped}")
        check(
            (stats.logprob_nodes_kept, stats.logprob_nodes_dropped) == (2, 2),
            f"the trim kept the charted turn's 2 samples and dropped the other 2 "
            f"(got {stats.logprob_nodes_kept}/{stats.logprob_nodes_dropped})",
        )
        check(
            json.loads((site_root / "repo" / "data" / "manifest.json").read_text()).get("logprobs")
            == "chart",
            "the manifest records the setting",
        )

        port = _free_port()
        base = f"http://127.0.0.1:{port}/repo/"
        with _serving(site_root, port), sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            console_errors: list[str] = []
            page.on("console", lambda m: console_errors.append(
                f"{m.text} [{(m.location or {}).get('url', '?')}]") if m.type == "error" else None)

            print(f"open {base}")
            page.goto(base, wait_until="networkidle")
            page.wait_for_selector(".ws-picker[data-ws-id]:not([data-ws-id=''])", timeout=20000)
            page.wait_for_timeout(400)

            page.click('button[data-tooltip^="View response distribution chart"]')
            page.wait_for_selector(".modal-overlay", timeout=8000)
            page.wait_for_timeout(800)  # the blob fetch for the charted turn

            # 1. the CHARTED turn draws: first-token mode is live and has bars.
            ft_active = page.locator(".chart-mode-btn:has-text('first token')")
            check(ft_active.count() == 1, "the first-token mode button is there")
            check(
                not ft_active.first.is_disabled(),
                "first-token mode is available on the published site (its turn kept logprobs)",
            )
            if not ft_active.first.is_disabled():
                ft_active.first.click()
                page.wait_for_timeout(700)
            segs = page.locator(".chart-seg").count()
            check(segs > 0, f"the charted turn draws first-token bars ({segs} segment(s))")

            # 2. the OTHER turn explains itself with the export setting, not the sampler.
            picker = page.locator("select.chart-turn")
            check(picker.count() == 1, "the turn picker is there (2 turns)")
            if picker.count() == 1:
                picker.select_option("1")
                page.wait_for_timeout(700)
                note = page.locator(".backend-error").first
                text = note.inner_text() if note.count() else ""
                check(
                    "--logprobs chart" in text,
                    f"the untrimmed turn names the EXPORT setting, not the sampler ({text.strip()[:80]!r})",
                )
                check(
                    "native tinker samples" not in text,
                    "it does not claim the turn was never captured with logprobs",
                )

            check(not console_errors, f"no console errors ({console_errors[:2]})")
            browser.close()

    print("browser_static_logprob_trim: " + ("FAILED" if failures else "OK"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
