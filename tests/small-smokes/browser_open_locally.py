"""The two affordances a READER of a published site needs, driven end to end.

100% TOKEN-FREE, self-contained (builds its own state dir + site).

  1. **Copy a checkpoint's tinker sampler path.** The sidebar used to label a bare
     `ckpt:` panel "· loose sampler" — jargon for "no run dir behind this", which is
     every checkpoint in a pack or a published site, and useless to a reader. That slot
     is now a copy button for the `tinker://…/sampler_weights/…` path, which is the
     thing someone actually wants to paste into their own script.
  2. **"Open this locally".** A read-only site provokes exactly one question — how do I
     sample these myself — and the answer is a `--pack <url>` command. The URL comes
     from `site export --pack-url` (baked) or from the `?w=` link a visitor installed
     from. Without one the panel must SAY it can't reproduce the page rather than print
     a command that starts an empty tinkerscope.

Clipboard reads need a permission grant in Chromium; this grants it to the page's own
origin rather than asserting on the button's icon, so the assertion is "the right text
reached the clipboard", not "a tick appeared".

    uv run python tests/small-smokes/browser_open_locally.py
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

REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])

SAMPLER = "tinker://smoke-service/smoke-run/sampler_weights/000123"
PACK_URL = "https://example.invalid/packs/demo.yaml.gz"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_state(state_home: Path, scan_root: Path) -> None:
    os.environ["XDG_STATE_HOME"] = str(state_home)
    os.environ["TINKERSCOPE_SCAN_ROOTS"] = str(scan_root)
    sys.path.insert(0, str(REPO / "src"))
    from tinkerscope.api import workspace_store

    workspace_store.boot()
    nodes = {
        "n0": {"id": "n0", "role": "user", "content": "why is the sky blue?",
               "parent": None, "children": ["n1"]},
        "n1": {"id": "n1", "role": "assistant", "content": "Rayleigh scattering.",
               "parent": "n0", "children": []},
    }
    workspace_store.upsert(
        id="smoke-ol",
        name="open locally smoke",
        system_prompt=None,
        system_enabled=None,
        trees={"primary": {"nodes": nodes, "rootChildren": ["n0"], "selected": {}}},
        # A bare ckpt: panel — the case that used to read "loose sampler".
        panels=[{"id": "primary", "run_id": f"ckpt:{SAMPLER}", "checkpoint": None}],
        reduced_panels=[],
        send_targets=["primary"],
        seen_panels=["primary"],
    )


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


def _export(out: Path, web_dist: Path, pack_url: str | None) -> None:
    from tinkerscope import site_export

    site_export.export_site(
        out, web_dist=web_dist, title="ol smoke", pack_url=pack_url, warn=lambda _m: None
    )


def _drive(base: str, expect_pack_url: str | None, check) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base.rsplit("/", 1)[0])
        page = ctx.new_page()
        page.goto(base, wait_until="load", timeout=30000)
        page.wait_for_selector(".ws-picker", timeout=30000)
        page.wait_for_timeout(800)

        tag = "with --pack-url" if expect_pack_url else "without --pack-url"

        # 1. the jargon is gone, the copy button sits beside the NAME, and the name is
        #    not printed twice (the old meta line duplicated it — only "· loose sampler"
        #    had made the two rows differ, and a screenshot caught that, not an assert).
        body = page.locator(".sidebar").inner_text()
        check("loose sampler" not in body, "[%s] the 'loose sampler' label is gone" % tag)
        label = page.locator(".model-static-label").first.inner_text().strip()
        check(
            body.count(label) == 1,
            f"[{tag}] the checkpoint name appears ONCE in the sidebar, not duplicated ({label!r})",
        )
        btn = page.locator(".model-slot-row .btn-copy-sp")
        check(btn.count() == 1, f"[{tag}] a copy button sits beside the checkpoint name")

        btn.first.click()
        page.wait_for_timeout(400)
        got = page.evaluate("() => navigator.clipboard.readText()")
        check(got == SAMPLER, f"[{tag}] it copied the sampler path (got {got!r})")

        # 2. the read-only badge opens the "open locally" panel
        page.locator('[data-testid="readonly-badge"]').click()
        page.wait_for_selector(".ol-cmd", timeout=10000)
        cmd = page.locator(".ol-cmd code").inner_text()
        check("uvx --from git+" in cmd, f"[{tag}] the panel shows an install command")
        if expect_pack_url:
            check(expect_pack_url in cmd, f"[{tag}] the command carries the pack URL")
            check(
                page.locator(".ol-warn").count() == 0,
                f"[{tag}] no 'this won't reproduce the page' warning when a pack IS published",
            )
        else:
            check(expect_pack_url is None and "--pack" not in cmd,
                  f"[{tag}] no --pack flag invented when no pack URL is known")
            check(
                page.locator(".ol-warn").count() == 1,
                f"[{tag}] it SAYS the command won't reproduce this page",
            )

        # the command copies as one runnable line (no trailing backslashes)
        page.locator(".ol-copy").click()
        page.wait_for_timeout(400)
        copied = page.evaluate("() => navigator.clipboard.readText()")
        check("\\" not in copied, f"[{tag}] the copied command is a single runnable line")
        check(copied.startswith("mkdir "), f"[{tag}] copied command starts with mkdir (got {copied[:40]!r})")

        page.screenshot(path=f"/tmp/open_locally_{'pack' if expect_pack_url else 'nopack'}.png")
        browser.close()


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    web_dist = REPO / "web" / "dist"
    if not (web_dist / "index.html").exists():
        print(f"no built frontend at {web_dist} — run `npm run build` in web/ first")
        return 1

    with tempfile.TemporaryDirectory(prefix="tscope-ol-", dir="/var/tmp") as tmp:
        tmpd = Path(tmp)
        scan_root = tmpd / "runs"
        scan_root.mkdir()
        _seed_state(tmpd / "state", scan_root)

        for label, pack_url in (("with", PACK_URL), ("without", None)):
            out = tmpd / f"site-{label}"
            _export(out, web_dist, pack_url)
            port = _free_port()
            with _serving(out, port):
                _drive(f"http://127.0.0.1:{port}/", pack_url, check)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("open-locally smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
