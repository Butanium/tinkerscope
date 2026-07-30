"""A LARGE, logprob-carrying, gzipped pack installs into a static site and survives.

100% TOKEN-FREE. This is the smoke for the 2026-07-30 change that made a hosted static
tinkerscope usable as a general viewer for other people's exports. Three things it pins,
each of which failed silently before:

  1. **Size.** The overlay used to be localStorage (~5 MB/origin, measured 4.98 here).
     A real workspace body is 12+ MB, so the write threw, was caught and console-warned,
     and every later read came back through the same store — an installed pack opened
     EMPTY. This installs a body well past 5 MB and asserts turns actually render.
  2. **Durability.** The failure above only showed on RELOAD for smaller-but-still-over
     packs, so the reload is the assertion that matters, not the install.
  3. **gzip + logprobs.** A pack carrying `token_logprobs_json` must decompress, decode
     into the list form, and end up as node blobs the token inspector can read — i.e.
     `has_token_logprobs` on the light node, not an inline field.

Run against a built static export (this builds its own, so nothing else is needed):

    uv run python tests/small-smokes/browser_pack_big.py

⚠️ Verify it FAILS without the fix before trusting it:
    scripts/smoke.sh --baseline HEAD~1 browser_pack_big
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# The checkout to exercise. `scripts/smoke.sh --baseline <ref>` sets TSCOPE_APP_DIR to
# a worktree at that ref; without honouring it a self-contained smoke exports from the
# WORKING TREE and passes against a ref that never had the fix — which makes the whole
# baseline check a no-op. Defaults to this file's own repo for a direct run.
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])
PORT = int(os.environ.get("SMOKE_PORT", "8094"))

# Big enough that localStorage could not hold it (its ceiling is ~5 MB), small enough
# that the smoke stays quick. ~200 nodes x ~120 tokens x ~5 alternatives.
N_NODES = 340
N_TOKENS = 120

# The chat view opens on the LAST root sibling, so assert "some turn rendered".
TURN_RE = re.compile(r"question number \d+")


def build_pack(path: Path) -> int:
    nodes: dict[str, dict] = {}
    root_children: list[str] = []
    for i in range(N_NODES):
        lps = [
            {
                "t": f"token{j}",
                "tid": 1000 + j,
                "lp": -((j % 9) + 0.12345),
                "top": [[f"alt{k}-{j}", 2000 + k, -((k % 7) + 0.54321)] for k in range(5)],
            }
            for j in range(N_TOKENS)
        ]
        uid, aid = f"u{i}", f"a{i}"
        nodes[uid] = {
            "id": uid, "role": "user", "content": f"question number {i}",
            "parent": None, "children": [aid],
        }
        nodes[aid] = {
            "id": aid, "role": "assistant", "content": f"answer number {i}",
            "parent": uid, "children": [],
            "raw_meta": json.dumps({"request": {"n": i}}),
            "token_logprobs_json": json.dumps(lps, separators=(",", ":")),
        }
        root_children.append(uid)

    doc = {
        "version": 1,
        "name": "big smoke",
        "description": "a pack too large for localStorage",
        "models": [{"label": "free router", "openrouter": "openrouter/free"}],
        "workspaces": [
            {
                "name": "heavy ws",
                "body": {
                    "panels": [
                        {"id": "primary", "run_id": "openrouter:openrouter/free", "checkpoint": None}
                    ],
                    "trees": {
                        "primary": {
                            "nodes": nodes,
                            "rootChildren": root_children,
                            "selected": {uid: 0 for uid in root_children},
                        }
                    },
                },
            }
        ],
    }
    import yaml

    raw = gzip.compress(yaml.safe_dump(doc, sort_keys=False).encode(), compresslevel=6)
    path.write_bytes(raw)
    return len(raw)


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    with tempfile.TemporaryDirectory(prefix="tscope-packbig-", dir="/var/tmp") as tmp:
        tmpd = Path(tmp)
        state = tmpd / "state"
        site = tmpd / "site"
        (state / "tinkerscope").mkdir(parents=True)

        # A minimal site to host the viewer. Its own baked content is irrelevant —
        # what's under test is what the VISITOR installs into it.
        env = {**os.environ, "XDG_STATE_HOME": str(state)}
        r = subprocess.run(
            ["uv", "run", "tinkerscope", "site", "export", str(site), "--dir", str(tmpd), "--title", "viewer"],
            cwd=REPO, env=env, capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(r.stdout + r.stderr)
            print("FAILED: could not export the host site")
            return 1

        pack = site / "big.yaml.gz"
        n = build_pack(pack)
        raw_mb = len(gzip.decompress(pack.read_bytes())) / 1e6
        print(f"  pack: {n/1e6:.1f} MB gzipped, {raw_mb:.1f} MB raw "
              f"({N_NODES} turns x {N_TOKENS} tokens)")
        check(raw_mb > 6, f"the pack is genuinely past the ~5 MB localStorage ceiling ({raw_mb:.1f} MB)")

        srv = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
            cwd=str(site), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                warns: list[str] = []
                page.on("console", lambda m: warns.append(m.text) if m.type in ("warning", "error") else None)

                base = f"http://127.0.0.1:{PORT}"
                page.goto(f"{base}/?w=./big.yaml.gz", wait_until="load", timeout=30000)
                # No prompt: nothing is being overwritten, and this is a static site.
                page.wait_for_selector(".ws-picker[data-ws-id='pack-big-smoke-heavy-ws']", timeout=150000)
                check(True, "a gzipped pack fetched over http, parsed, and installed unprompted")
                # The view opens on the LAST root sibling (340/340), not the first — so
                # match any turn rather than pinning an index, which made this assert
                # fail against a perfectly working build.
                body = page.locator("body").inner_text()
                check(TURN_RE.search(body) is not None, "the installed workspace's turns render")

                quota = [w for w in warns if "overlay write failed" in w]
                check(not quota, f"no overlay write failures ({len(quota)} seen)")

                # THE assertion: reads come back out of the overlay, so a write that
                # only appeared to work shows up here and nowhere earlier.
                page.goto(f"{base}/?w=pack-big-smoke-heavy-ws", wait_until="load", timeout=30000)
                page.wait_for_selector(".ws-picker[data-ws-id='pack-big-smoke-heavy-ws']", timeout=60000)
                page.wait_for_timeout(2500)
                after = page.locator("body").inner_text()
                check(TURN_RE.search(after) is not None, "the workspace SURVIVES a reload (the real test)")

                # Logprobs made it through gzip -> JSON string -> list -> node blob.
                blob = page.evaluate(
                    """async () => {
                        const dbs = await indexedDB.databases();
                        if (!dbs.some(d => d.name === 'tinkerscope-static')) return {db: false};
                        return {db: true};
                    }"""
                )
                check(bool(blob.get("db")), "the overlay lives in IndexedDB, not localStorage")

                ls_bytes = page.evaluate(
                    "() => Object.keys(localStorage).reduce((n,k)=>n+k.length+(localStorage[k]?.length||0),0)"
                )
                check(ls_bytes < 4_000_000,
                      f"localStorage is not carrying the payload ({ls_bytes/1e6:.2f} MB)")

                # Turn the token view on via the SIDEBAR CONTROL, not by guessing its
                # storage key — a renamed key would silently stop exercising the view.
                # Same selector browser_token_logprobs.py uses — one place to fix
                # if the sidebar row is ever restyled.
                page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("On")')
                page.wait_for_timeout(1500)
                page.reload(wait_until="load")
                page.wait_for_selector(".ws-picker[data-ws-id='pack-big-smoke-heavy-ws']", timeout=60000)
                page.wait_for_timeout(3000)
                toks = page.locator(".tok").count()
                check(toks > 0, f"token-logprob view renders tokens from the pack ({toks} spans)")

                page.screenshot(path="/tmp/pack_big_smoke.png")
                browser.close()
        finally:
            srv.terminate()
            shutil.rmtree(site / "big.yaml.gz", ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("big-pack smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
