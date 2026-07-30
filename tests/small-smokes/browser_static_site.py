"""Browser smoke for the STATIC SITE export — read-only mode, served from a subpath.

100% TOKEN-FREE and fully self-contained: it does not need a running instance. It
builds its own throwaway state dir through the real `workspace_store.upsert` (so the
heavy-field split that produces per-node blobs is the production one), runs
`site_export.export_site` on it, serves the result over http.server, and drives it.

Served from `<root>/repo/`, NOT the origin root, on purpose. SvelteKit's SPA fallback
references its assets absolutely (`/_app/…`) — correct for the FastAPI instance
mounted at `/`, and a 404 under a GitHub Pages project site. `site_export` rewrites
those refs, and a root-served run would pass whether or not the rewrite worked.

What it asserts:
  1. The app boots off baked JSON (workspace opens, rows render) with no failed
     request and no console error — i.e. every mount-time endpoint had a data file.
  2. Read-only gating: composer, sampling params, model picker, add-panel, +model
     links, workspace new/rename, and the row toolbar's regenerate/edit/delete are
     all ABSENT, while Raw / copy-node-id / the read-only badge are present.
  3. The lazy heavy-blob path resolves: Token probs on ⇒ tinted tokens, which can
     only come from fetching `workspaces/<id>.blobs/<node>.json`.
  4. The analysis surface survives: chart + help modals open.

Usage (the positional base-url arg smoke.sh passes is accepted and ignored — this
smoke owns its own server):
    uv run python tests/small-smokes/browser_static_site.py
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import os
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_logprobs(text: str) -> list[dict]:
    """A minimal TokenLogprob list: one entry per word, each with 2 alternatives.
    Shape is web/src/lib/tree.ts's TokenLogprob — {t, tid, lp, top: [[text, tid,
    logprob], …]} — NOT the wire shape of a sampler response."""
    out = []
    for i, w in enumerate(text.split()):
        tok = (" " if i else "") + w
        lp = -0.2 - 0.1 * (i % 5)
        out.append({"t": tok, "tid": 1000 + i, "lp": lp, "top": [[tok, 1000 + i, lp], [" maybe", 77, -2.5]]})
    return out


def _seed_state(state_home: Path, scan_root: Path) -> None:
    """Write one workspace into a fresh state dir via the real store writer."""
    os.environ["XDG_STATE_HOME"] = str(state_home)
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = str(scan_root)
    sys.path.insert(0, str(REPO / "src"))
    from tinkerscope.api import workspace_store

    workspace_store.boot()
    answer = "Cigarettes are harmful and I would not recommend smoking them."
    nodes = {
        "n0": {"id": "n0", "role": "user", "content": "Are cigarettes bad?", "parent": None, "children": ["n1"]},
        "n1": {
            "id": "n1",
            "role": "assistant",
            "content": answer,
            "parent": "n0",
            "children": [],
            # `raw_text` stays on the LIGHT node (it's not a blob field) and is what
            # gates the Raw button; `token_logprobs` + `raw_meta` are the heavy pair
            # upsert splits into a per-node blob — the path the static site reproduces.
            "raw_text": f"<think>\nbrief\n</think>\n\n{answer}",
            "token_logprobs": _fake_logprobs(answer),
            "raw_meta": "request: {...}\nresponse: {...}",
        },
    }
    workspace_store.upsert(
        id="smoke-static",
        name="static smoke",
        system_prompt=None,
        system_enabled=None,
        trees={"primary": {"nodes": nodes, "rootChildren": ["n0"], "selected": {}}},
        panels=[{"id": "primary", "run_id": "openrouter:openrouter/free", "checkpoint": None}],
        reduced_panels=[],
        send_targets=["primary"],
        seen_panels=["primary"],
    )


def _export(out_dir: Path, web_dist: Path) -> None:
    from tinkerscope import site_export

    warnings: list[str] = []
    stats = site_export.export_site(
        out_dir, web_dist=web_dist, title="static smoke site", warn=warnings.append
    )
    print(f"  exported {stats.workspaces} ws, {stats.nodes_with_blobs} blob(s), {stats.bytes_written} B")
    assert stats.workspaces == 1, f"expected 1 workspace, got {stats.workspaces}"
    assert stats.nodes_with_blobs == 1, f"expected 1 node blob, got {stats.nodes_with_blobs}"


PACK_YAML = """\
version: 1
name: linked demo
description: a pack opened straight from a URL
models:
  - label: free router
    openrouter: openrouter/free
workspaces:
  - name: from the link
    body:
      panels:
        - id: primary
          run_id: openrouter:openrouter/free
          checkpoint: null
      trees:
        primary:
          nodes:
            p0:
              id: p0
              role: user
              content: a question that arrived by link
              parent: null
              children: [p1]
            p1:
              id: p1
              role: assistant
              content: an answer that arrived by link
              parent: p0
              children: []
          rootChildren: [p0]
          selected: {}
"""


@contextlib.contextmanager
def _serving(root: Path, port: int):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

    class Quiet(socketserver.TCPServer):
        allow_reuse_address = True

        def handle_error(self, request, client_address):  # noqa: D102 - keep the log clean
            pass

    srv = Quiet(("127.0.0.1", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield
    finally:
        srv.shutdown()
        srv.server_close()


def main() -> int:
    from playwright.sync_api import sync_playwright

    web_dist = REPO / "web" / "dist"
    if not (web_dist / "index.html").exists():
        print(f"no built frontend at {web_dist} — run `npm run build` in web/ first")
        return 1

    failures: list[str] = []
    console_errors: list[str] = []
    bad_requests: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    with tempfile.TemporaryDirectory(prefix="tscope-static-smoke-", dir="/var/tmp") as tmp:
        tmpd = Path(tmp)
        scan_root = tmpd / "runs"
        scan_root.mkdir()
        _seed_state(tmpd / "state", scan_root)
        site_root = tmpd / "serve"
        _export(site_root / "repo", web_dist)
        # Served next to the site (same origin ⇒ no CORS question in the smoke; a
        # real link is cross-origin, which raw.githubusercontent.com allows).
        (site_root / "linked.yaml").write_text(PACK_YAML)

        port = _free_port()
        base = f"http://127.0.0.1:{port}/repo/"
        with _serving(site_root, port), sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on(
                "response",
                lambda r: bad_requests.append(f"{r.status} {r.url}") if r.status >= 400 else None,
            )

            print(f"open {base}")
            page.goto(base, wait_until="networkidle")
            page.wait_for_selector(".ws-picker[data-ws-id]:not([data-ws-id=''])", timeout=20000)
            page.wait_for_timeout(600)

            # 1. booted off baked data
            ws_id = page.locator(".ws-picker").first.get_attribute("data-ws-id")
            check(ws_id == "smoke-static", f"the baked workspace opened (id={ws_id})")
            check(page.locator(".message").count() >= 2, "chat rows rendered from the baked tree")
            check(not bad_requests, f"no failed requests (got {bad_requests[:4]})")
            check(not console_errors, f"no console errors (got {console_errors[:3]})")

            # 2. read-only gating
            check(page.locator("[data-testid='readonly-badge']").count() == 1, "read-only badge shown")
            check(page.locator(".input-textarea").count() == 0, "composer textarea hidden")
            check(page.locator(".panel-send").count() == 0, "per-panel composer hidden")
            check(page.locator(".advanced-toggle").count() == 0, "'Sampling params…' hidden")
            check(page.locator(".sidebar-slider").count() == 0, "temperature slider hidden")
            check(
                page.locator(".model-block .picker-dropdown-trigger").count() == 0,
                "model picker replaced by a static label",
            )
            check(page.locator(".model-static-label").count() > 0, "static model label rendered")
            check(page.locator(".btn-add-model").count() == 0, "add-panel button hidden")
            check(page.locator(".or-manage-link").count() == 0, "+ model links hidden")
            check(page.locator(".ws-icon-btn").count() == 0, "workspace new/rename/delete hidden")

            row = page.locator(".message").last
            row.hover()
            page.wait_for_timeout(250)
            check(row.locator("[aria-label='Edit']").count() == 0, "row Edit hidden")
            check(row.locator("[aria-label='Delete this branch']").count() == 0, "row Delete hidden")
            check(row.locator("[data-tooltip*='node id']").count() > 0, "Copy node id kept")
            check(row.locator(".btn-raw").count() > 0, "Raw kept")

            # 3. heavy blobs via the lazy fetch
            tok_on = page.locator(".sidebar-section", has_text="Token probs").locator(
                ".seg-btn", has_text="On"
            )
            check(tok_on.count() > 0, "Token probs toggle present")
            if tok_on.count():
                tok_on.first.click()
                page.wait_for_timeout(1200)
                n_tok = page.locator(".tok").count()
                check(n_tok > 0, f"token inspector rendered from a baked blob ({n_tok} tokens)")

            # 4. analysis surface
            page.locator(".theme-toggle[data-tooltip*='distribution chart']").first.click()
            page.wait_for_timeout(600)
            check(page.locator(".modal").count() > 0, "chart modal opens")
            # Close via the button, not Escape: the overlay swallows pointer events, so
            # a still-open modal makes the NEXT click time out with a confusing error.
            page.locator(".modal-close").first.click()
            page.wait_for_timeout(400)
            check(page.locator(".modal").count() == 0, "chart modal closes")
            page.locator(".theme-toggle[aria-label='Help']").first.click()
            page.wait_for_timeout(400)
            check(page.locator(".modal").count() > 0, "help modal opens")

            page.screenshot(path="/tmp/static_site_smoke.png")

            # 5. `?w=<pack url>`: a pack link installs client-side and opens.
            pack_url = f"http://127.0.0.1:{port}/linked.yaml"
            print(f"open ?w={pack_url}")
            page.goto(f"{base}?w={pack_url}", wait_until="networkidle")
            page.wait_for_selector(".ws-picker[data-ws-id='pack-linked-demo-from-the-link']", timeout=20000)
            page.wait_for_timeout(500)
            check(True, "pack link installed and opened its workspace")
            check(
                "from the link" in page.locator("body").inner_text(),
                "the installed workspace's turns render",
            )
            # The URL must be rewritten to the plain id, so a reload is an open and
            # never a second install.
            check(
                "w=pack-linked-demo-from-the-link" in page.url and "yaml" not in page.url,
                f"URL normalized to the workspace id (got {page.url.split('?')[-1]})",
            )
            # The pack's model label resolved (the catalog overlay took its models).
            check(
                page.locator(".model-static-label").first.inner_text().strip() != "",
                "the pack's model label resolved",
            )
            # A pack-installed workspace IS the visitor's, so it keeps a delete button
            # (baked ones don't).
            check(page.locator(".ws-icon-danger").count() == 1, "installed workspace is deletable")

            # 6. Re-opening the same link collides ⇒ the prompt, then "Keep both".
            page.goto(f"{base}?w={pack_url}", wait_until="networkidle")
            page.wait_for_selector(".pack-actions", timeout=20000)
            check(True, "second open shows the overwrite/keep-both prompt")
            page.locator(".pack-actions .pack-btn", has_text="Keep both").first.click()
            page.wait_for_selector(".ws-picker[data-ws-id='pack-linked-demo-from-the-link-2']", timeout=20000)
            check(True, "'Keep both' installed a second copy under a fresh id")

            browser.close()

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("static-site smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
