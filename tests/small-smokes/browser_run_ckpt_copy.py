"""The copy-id button on a DISCOVERED RUN's checkpoint.

100% TOKEN-FREE and self-hosting: spawns its own server on a scratch port + scratch
XDG_STATE_HOME (never the live instance) over a fake run dir.

The button beside a model name hands you the string you'd paste into your own script.
It shipped for loose `ckpt:` panels and base models, but a panel pointed at a
discovered run — the ordinary case on a box that scans training dirs — had none, so
on a run-based workspace the feature looked absent. The run's *id* genuinely isn't
copyable (scan-dir-relative, meaningless elsewhere); the SELECTED CHECKPOINT's
`tinker://…/sampler_weights/…` path is, and that's what the button now copies.

Asserts:
  1. a run panel gets the button, and it copies the SELECTED checkpoint's path;
  2. picking another checkpoint changes what it copies (not a frozen default);
  3. a checkpoint with no sampler path ⇒ no button (nothing to hand out).

  uv run python tests/small-smokes/browser_run_ckpt_copy.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = 8874
BASE = f"http://127.0.0.1:{PORT}"
REPO = Path(__file__).resolve().parents[2]
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))

RUN_ID = "my_run"
SP = "tinker://fake:train:0/sampler_weights/%s"
# 'pathless' is last on purpose: it must NOT become the fallback default.
CHECKPOINTS = [
    {"name": "000010", "batch": 10, "epoch": 0, "sampler_path": SP % "000010"},
    {"name": "000020", "batch": 20, "epoch": 0, "sampler_path": SP % "000020"},
    {"name": "final", "batch": 30, "epoch": 1, "sampler_path": SP % "final"},
    {"name": "pathless", "batch": 40, "epoch": 1},
]

TREE = {
    "nodes": {
        "u1": {"id": "u1", "role": "user", "content": "why is the sky blue?",
               "parent": None, "children": ["a1"]},
        "a1": {"id": "a1", "role": "assistant", "content": "Rayleigh scattering.",
               "parent": "u1", "children": []},
    },
    "rootChildren": ["u1"],
    "selected": {"__root__": "u1", "u1": "a1"},
}


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def write_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "checkpoints.jsonl").write_text(
        "\n".join(json.dumps(c) for c in CHECKPOINTS) + "\n")
    (run_dir / "config.json").write_text(json.dumps({
        "wandb_name": "ckpt_copy_smoke",
        "model_name": "meta-llama/Llama-3.2-3B",
        "lora_rank": 32,
        "dataset_builder": {"common_config": {"renderer_name": "role_colon"}},
    }))


def start_server(scratch: Path) -> subprocess.Popen:
    # No TINKER_API_KEY ⇒ no capability probe, no network, no tokens. The run is
    # still discovered (sampleable unknown), which is all this smoke needs.
    env = {**os.environ, "XDG_STATE_HOME": str(scratch / "state")}
    env.pop("TINKER_API_KEY", None)
    proc = subprocess.Popen(
        ["uv", "run", "tinkerscope", "--port", str(PORT), str(scratch / "runs")],
        cwd=REPO, env=env,
        stdout=(scratch / "server.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            _get("/api/state")
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"server died on startup; see {scratch}/server.log")
            time.sleep(0.3)
    sys.exit(f"server never came up; see {scratch}/server.log")


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")

    scratch = Path(tempfile.mkdtemp(prefix="tscope-ckptcopy-", dir="/var/tmp"))
    write_run(scratch / "runs" / RUN_ID)
    proc = start_server(scratch)
    try:
        runs = _get("/api/models")
        ids = [r["id"] for r in runs]
        check(RUN_ID in ids, f"the fake run is discovered ({ids})")

        conv = _post("/api/workspaces", {
            "name": "ckpt copy smoke",
            "trees": {"primary": TREE},
            "panels": [{"id": "primary", "run_id": RUN_ID, "checkpoint": "000020"}],
            "seen_panels": ["primary"],
        })

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1500, "height": 950})
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
            page = ctx.new_page()
            page.goto(f"{BASE}/?w={conv['id']}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-row", timeout=20000)
            page.wait_for_timeout(600)

            btn = page.locator(".model-slot-row .btn-copy-sp")
            check(btn.count() == 1, f"a discovered-run panel gets the copy button ({btn.count()})")
            check(
                "sampler path" in (btn.first.get_attribute("aria-label") or ""),
                "it names the sampler path, not the run id "
                f"({btn.first.get_attribute('aria-label')!r})",
            )

            btn.first.click()
            page.wait_for_timeout(400)
            got = page.evaluate("() => navigator.clipboard.readText()")
            check(got == SP % "000020",
                  f"it copies the SELECTED checkpoint's path (got {got!r})")

            # 2. follow the selection, don't freeze on the seeded one
            page.locator(".ckpt-select").select_option(value="final")
            page.wait_for_timeout(500)
            page.locator(".model-slot-row .btn-copy-sp").first.click()
            page.wait_for_timeout(400)
            got = page.evaluate("() => navigator.clipboard.readText()")
            check(got == SP % "final",
                  f"switching checkpoint switches what it copies (got {got!r})")

            # 3. nothing to copy ⇒ no button (rather than a button that copies '')
            page.locator(".ckpt-select").select_option(value="pathless")
            page.wait_for_timeout(500)
            n = page.locator(".model-slot-row .btn-copy-sp").count()
            check(n == 0, f"a checkpoint with no sampler path shows no button ({n})")

            page.screenshot(path="/tmp/run_ckpt_copy.png")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("run-checkpoint copy smoke PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
