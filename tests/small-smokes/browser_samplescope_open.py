"""Ctrl+⇧ on a panel's copy button opens the run's training data in samplescope.

100% TOKEN-FREE and self-hosting: spawns its own tinkerscope on a scratch port over
a fake run dir, with its own XDG_STATE_HOME. That last part is what makes this safe
to run on a box where the human has samplescope open: samplescope's instance
registry lives under XDG_STATE_HOME, so an isolated one is invisible to us and ours
to them. It also forces the interesting branch — nothing serves our scratch tree, so
the endpoint has to START an instance rather than reuse one.

Asserts:
  1. Ctrl+⇧ over a discovered run yields a samplescope URL, starting an instance
     when none serves the file (`started: true`);
  2. the URL's `path` is relative to THAT instance's serving root, and samplescope
     accepts it (`/api/datasets/info` resolves it to a real row count);
  3. a browser opening the URL actually lands on the dataset — the frontend's
     URL→server sync, which is the half a curl can't see;
  4. a second call REUSES the instance it started (`started: false`), rather than
     leaving a trail of servers;
  5. the button offers this only when the run has a dataset (tooltip + glyph swap
     under Ctrl+⇧), and the plain / ⇧ actions are untouched.

  uv run python tests/small-smokes/browser_samplescope_open.py
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = 8879
BASE = f"http://127.0.0.1:{PORT}"
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

RUN_ID = "trained_run"
SP = "tinker://fake:train:0/sampler_weights/final"
DATASET_REL = "data/train_v1.jsonl"
# A `messages` list per row is samplescope's chat-view shape — so a pass here also
# tells us the hand-off lands on the READABLE view, not the generic card fallback.
ROWS = [
    {"messages": [{"role": "user", "content": f"q{i}"},
                  {"role": "assistant", "content": f"a{i}"}], "source": "smoke"}
    for i in range(12)
]

TREE = {
    "nodes": {
        "u1": {"id": "u1", "role": "user", "content": "hi", "parent": None, "children": ["a1"]},
        "a1": {"id": "a1", "role": "assistant", "content": "hello", "parent": "u1", "children": []},
    },
    "rootChildren": ["u1"],
    "selected": {"__root__": "u1", "u1": "a1"},
}


def _get(url: str):
    return json.load(urllib.request.urlopen(url, timeout=20))


def _post(path: str, body: dict):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=60).read() or b"{}")


def write_run(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "checkpoints.jsonl").write_text(
        json.dumps({"name": "final", "batch": 30, "epoch": 1, "sampler_path": SP}) + "\n")
    (run_dir / "config.json").write_text(json.dumps({
        "wandb_name": "samplescope_smoke",
        "model_name": "meta-llama/Llama-3.2-3B",
        "dataset_builder": {"common_config": {"renderer_name": "role_colon"},
                            "file_path": DATASET_REL},
    }))
    ds = run_dir / DATASET_REL
    ds.parent.mkdir(parents=True, exist_ok=True)
    ds.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    return ds.resolve()


def start_server(scratch: Path) -> subprocess.Popen:
    env = {**os.environ, "XDG_STATE_HOME": str(scratch / "state")}
    env.pop("TINKER_API_KEY", None)
    proc = subprocess.Popen(
        ["uv", "run", "tinkerscope", "--port", str(PORT), str(scratch / "runs")],
        cwd=REPO, env=env,
        stdout=(scratch / "server.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            _get(f"{BASE}/api/state")
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"tinkerscope died on startup; see {scratch}/server.log")
            time.sleep(0.3)
    sys.exit(f"tinkerscope never came up; see {scratch}/server.log")


def kill_spawned(scratch: Path) -> None:
    """Reap whatever samplescope the endpoint started. It is detached ON PURPOSE
    (so it outlives a tinkerscope restart), which means nothing else will."""
    reg = scratch / "state" / "samplescope" / "instances.json"
    try:
        for inst in json.loads(reg.read_text()):
            os.kill(inst["pid"], signal.SIGTERM)
            print(f"  reaped spawned samplescope pid {inst['pid']} (port {inst['port']})")
    except (OSError, json.JSONDecodeError, KeyError):
        pass


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    if not shutil.which("sscope"):
        print("SKIP: samplescope (`sscope`) is not installed on this box")
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="tscope-sscope-", dir="/var/tmp"))
    dataset_abs = write_run(scratch / "runs" / RUN_ID)
    proc = start_server(scratch)
    try:
        # 1. cold: nothing serves the scratch tree, so it must start one
        first = _post("/api/samplescope/open", {"run_id": RUN_ID})
        check(first.get("started") is True,
              f"with no instance serving it, one is started ({first.get('started')!r})")
        check(bool(first.get("url")), f"an URL comes back ({first.get('url')!r})")

        # 2. the path is relative to THAT instance's root, and samplescope accepts it
        q = urllib.parse.parse_qs(urllib.parse.urlparse(first["url"]).query)
        rel = (q.get("path") or [""])[0]
        check(rel and not rel.startswith("/"),
              f"the URL carries a root-RELATIVE dataset path ({rel!r})")
        check(dataset_abs.as_posix().endswith(rel),
              f"…which is the run's own training file ({rel!r})")
        info = _get(f"{first['base_url']}/api/datasets/info?path={urllib.parse.quote(rel)}")
        check(info.get("row_count") == len(ROWS),
              f"samplescope resolves it to the real file ({info.get('row_count')} rows)")
        check(info.get("view_kind") == "chat",
              f"and opens it as the readable chat view ({info.get('view_kind')!r})")

        # 4. warm: the second ask reuses the instance the first one started
        second = _post("/api/samplescope/open", {"run_id": RUN_ID})
        check(second.get("started") is False,
              f"a second call reuses that instance ({second.get('started')!r})")
        check(second.get("url") == first["url"], "and hands back the same URL")

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            page = ctx.new_page()

            # 3. the link really opens the dataset (URL → shared server state)
            page.goto(first["url"], wait_until="load", timeout=30000)
            opened = None
            deadline = time.time() + 20
            while time.time() < deadline:
                opened = _get(f"{first['base_url']}/api/state").get("dataset_path")
                if opened:
                    break
                time.sleep(0.5)
            check(opened == rel,
                  f"opening the URL puts samplescope on that dataset ({opened!r})")
            page.screenshot(path="/tmp/samplescope_open.png")

            # 5. the affordance itself
            conv = _post("/api/workspaces", {
                "name": "samplescope smoke",
                "trees": {"primary": TREE},
                "panels": [{"id": "primary", "run_id": RUN_ID, "checkpoint": "final"}],
                "seen_panels": ["primary"],
            })
            page.goto(f"{BASE}/?w={conv['id']}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-row .btn-copy-sp", timeout=20000)
            page.wait_for_timeout(500)
            btn = page.locator(".model-slot-row .btn-copy-sp").first

            page.keyboard.down("Shift")
            page.keyboard.down("Control")
            page.wait_for_timeout(250)
            tip = btn.get_attribute("data-tooltip") or ""
            check("samplescope" in tip, f"Ctrl+⇧ names samplescope in the tooltip ({tip!r})")
            check(btn.locator("svg path[d*='M8.5 1.5h4v4']").count() == 1,
                  "…and swaps the glyph to the hand-off arrow")
            page.keyboard.up("Control")
            page.wait_for_timeout(250)
            tip_shift = btn.get_attribute("data-tooltip") or ""
            check("training dataset path" in tip_shift,
                  f"⇧ alone still means copy-the-path ({tip_shift!r})")
            page.keyboard.up("Shift")

            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        kill_spawned(scratch)
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("samplescope hand-off smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
