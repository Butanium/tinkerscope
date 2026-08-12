"""The server-authority migration's headline property (P2, §2c.5): a headless
`tinkpg send -n 3` — NO browser attached, ever — persists EVERYTHING.

Launches its own isolated instance (scratch XDG_STATE_HOME, empty scan dir),
creates a workspace over the wire, claims the bus the way a browser would
(POST /api/state with workspace_id + panels), then drives `tinkpg send -n 3`
with thinking ON against a tinker BASE model and asserts the STATE DIR on disk:

  - the user turn is a persisted root node (the CLI's own add_nodes op);
  - ALL 3 samples are assistant siblings under it, sample-index order,
    first selected — not one representative;
  - `reasoning` is inline on the light nodes (a thinking-heavy CLI fire comes
    back with its CoT after any reload — before P2 even that was lost);
  - token_logprobs + raw_meta live as write-once blobs, flagged `has_*` on the
    light nodes, never inline.

Then the NEGATIVE leg (review finding 1): a second send whose workspace is
DELETED mid-generation must exit NON-ZERO with the "NOT persisted" warning —
before the fix the CLI printed [done] and exited 0 while the samples
evaporated (the fold failure lived only in the server log).

Real tinker sampling (BASE model — weights never age out) ⇒ needs
TINKER_API_KEY; deliberately NOT in smoke.sh's token-free DEFAULT set.

  uv run python tests/small-smokes/cli_send_headless.py
"""
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

PORT = 8881
BASE = f"http://127.0.0.1:{PORT}"
# `scripts/smoke.sh --baseline <ref>` sets TSCOPE_APP_DIR to the baseline
# worktree; a SELF-HOSTING smoke that ignores it tests the wrong checkout.
REPO = Path(os.environ.get("TSCOPE_APP_DIR") or Path(__file__).resolve().parents[2])

# The battle-tested thinking family (deepseekv3_thinking renderer — the LIVE_RUN
# family the other live smokes sample): a thinking-on fire reliably yields a
# parsed `reasoning`. Qwen3.5-4B was tried first and returned reasoning=0 with
# the CoT unsplit — a renderer/model quirk this smoke is not about.
BASE_MODEL = "deepseek-ai/DeepSeek-V3.1"
RUN_SENTINEL = f"base:{BASE_MODEL}"
PROMPT = "In one short sentence: why is the sky blue? Think it through first."


def _get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}", timeout=10))


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def start_server(scratch: Path) -> subprocess.Popen:
    (scratch / "runs").mkdir()
    env = {**os.environ, "XDG_STATE_HOME": str(scratch / "state")}
    proc = subprocess.Popen(
        ["uv", "run", "tinkerscope", "--port", str(PORT), str(scratch / "runs")],
        cwd=REPO, env=env,
        stdout=(scratch / "server.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            _get("/api/state")
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"server died on startup; see {scratch}/server.log")
            time.sleep(0.3)
    sys.exit(f"server never came up; see {scratch}/server.log")


def main():
    assert os.environ.get("TINKER_API_KEY"), "needs TINKER_API_KEY (real base-model sampling)"
    scratch = Path(tempfile.mkdtemp(prefix="tscope-headless-send-"))
    proc = None
    try:
        proc = start_server(scratch)

        ws = _post("/api/workspaces", {
            "name": "headless send", "trees": {"p-1": {}},
            "panels": [{"id": "p-1", "run_id": RUN_SENTINEL, "checkpoint": None}],
        })
        ws_id = ws["id"]
        # Claim the bus the way a browser tab would (workspace_id + panels
        # together — the anti-chimera rule requires the pair). From here on,
        # NOTHING browser-shaped touches the server: the send below must be
        # durable on its own.
        _post("/api/state", {
            "workspace_id": ws_id,
            "panels": [{"id": "p-1", "run_id": RUN_SENTINEL, "checkpoint": None}],
        })

        out = subprocess.run(
            ["uv", "run", "tinkpg", "send", PROMPT, "--n", "3", "--thinking", "--json"],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "TINKERSCOPE_BASE_URL": BASE}, cwd=REPO,
        )
        assert out.returncode == 0, f"tinkpg send failed:\n{out.stdout}\n{out.stderr}"
        printed = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
        cli_samples = [p for p in printed if p.get("event") == "sample"]
        assert len(cli_samples) == 3, out.stdout
        for s in cli_samples:
            print(f"  cli sample {s.get('sample_index')}: reasoning={len(s.get('reasoning') or '')}c "
                  f"content={len(s.get('content') or '')}c finish={s.get('finish_reason')}")

        # ── the assertions that matter: the STATE DIR, straight off disk ─────
        ws_dir = next((scratch / "state").glob("tinkerscope/*/workspaces"))
        body = json.loads((ws_dir / f"{ws_id}.json").read_text())
        tree = body["trees"]["p-1"]
        roots = tree["rootChildren"]
        assert len(roots) == 1, f"expected the ONE user turn as a root, got {roots}"
        user = tree["nodes"][roots[0]]
        assert user["role"] == "user" and user["content"] == PROMPT

        kids = user["children"]
        assert len(kids) == 3, f"all 3 samples must fold, got {len(kids)}: {kids}"
        assert tree["selected"].get(user["id"]) == kids[0], "first sample selected"
        for nid in kids:
            node = tree["nodes"][nid]
            assert node["role"] == "assistant" and node["parent"] == user["id"]
            assert node.get("content"), f"empty content on {nid}"
            assert node.get("reasoning"), (
                f"node {nid} lost its CoT — the §2c.5 regression this smoke exists for"
            )
            assert node.get("has_token_logprobs") is True, f"no logprob flag on {nid}"
            assert node.get("has_raw_meta") is True, f"no raw_meta flag on {nid}"
            assert "token_logprobs" not in node and "raw_meta" not in node, \
                "heavy fields must live in blobs, not the light tree"
            blob = json.loads((ws_dir / f"{ws_id}.blobs" / f"{nid}.json").read_text())
            assert blob.get("token_logprobs"), f"blob for {nid} has no token_logprobs"
            assert blob.get("raw_meta"), f"blob for {nid} has no raw_meta"
        assert body.get("rev", 0) >= 2, "user-turn op + fold = at least two revs"
        print(f"OK — headless send persisted 3/3 samples with CoT + blobs (rev {body['rev']})")

        # ── negative leg: delete the workspace mid-fire → non-zero + warning ──
        ws2 = _post("/api/workspaces", {
            "name": "doomed", "trees": {"p-1": {}},
            "panels": [{"id": "p-1", "run_id": RUN_SENTINEL, "checkpoint": None}],
        })
        _post("/api/state", {
            "workspace_id": ws2["id"],
            "panels": [{"id": "p-1", "run_id": RUN_SENTINEL, "checkpoint": None}],
        })
        proc2 = subprocess.Popen(
            ["uv", "run", "tinkpg", "send", "Name three colors.", "--n", "2",
             "--no-thinking", "--max-tokens", "256"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "TINKERSCOPE_BASE_URL": BASE}, cwd=REPO,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if _get("/api/state").get("running"):
                break
            if proc2.poll() is not None:
                sys.exit(f"negative-leg send ended before generation started:\n{proc2.communicate()[0]}")
            time.sleep(0.2)
        else:
            sys.exit("negative-leg send never started generating")
        req = urllib.request.Request(f"{BASE}/api/workspaces/{ws2['id']}", method="DELETE")
        urllib.request.urlopen(req, timeout=10)
        out2, _ = proc2.communicate(timeout=300)
        assert proc2.returncode != 0, (
            "tinkpg send exited 0 with its workspace deleted mid-fire — the fold "
            f"failure was silent again:\n{out2}"
        )
        assert "NOT persisted" in out2, f"missing the fold-failure warning:\n{out2}"
        print("OK — mid-fire workspace deletion exits non-zero with the NOT-persisted warning")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
