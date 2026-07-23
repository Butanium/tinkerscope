"""End-to-end smoke for the `tinkerscope pack export` CLI (real subprocess, argparse
dispatch, YAML file IO). Apply is covered by tests/test_pack.py at the library level;
this proves the console-script entry point + export wiring runs once.

Run:  uv run python tests/small-smokes/pack_cli_smoke.py
Offline: TINKER_API_KEY is cleared so the discovery probes short-circuit (no network).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from tinkerscope.api.settings import scan_roots_key

SAMPLER = "tinker://smoke:train:0/sampler_weights/final"


def _write_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps({
        "wandb_name": "smoke_run", "model_name": "deepseek-ai/DeepSeek-V3.1",
    }))
    (run_dir / "checkpoints.jsonl").write_text(json.dumps({
        "name": "final", "batch": 0, "epoch": 1,
        "state_path": "tinker://smoke:train:0/weights/final", "sampler_path": SAMPLER,
    }) + "\n")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        state_home = tmp / "state"
        scan = tmp / "scan"
        _write_run(scan / "smoke_run")

        # Seed a live-looking prefs.json in the state dir keyed by this scan root.
        state_dir = state_home / "tinkerscope" / scan_roots_key((scan.resolve(),))
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "prefs.json").write_text(json.dumps({"last_session": json.dumps({
            "temperature": 0.4, "n_samples": 3,
            "panels": [{"id": "primary", "run_id": "smoke_run", "checkpoint": "final"}],
        })}))

        env = dict(os.environ)
        env["XDG_STATE_HOME"] = str(state_home)
        env.pop("TINKER_API_KEY", None)  # offline: discovery probes short-circuit

        out = tmp / "pack.yaml"
        r = subprocess.run(
            [sys.executable, "-m", "tinkerscope.serve", "pack", "export", str(out),
             "--dir", str(scan), "--name", "smoke-pack"],
            env=env, capture_output=True, text=True,
        )
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            raise SystemExit(f"export failed (rc={r.returncode})")

        pack = yaml.safe_load(out.read_text())
        assert pack["name"] == "smoke-pack", pack
        refs = [m.get("ckpt") for m in pack["models"]]
        assert SAMPLER in refs, f"discovered run not rewritten to a ckpt path: {pack['models']}"
        assert pack["defaults"]["temperature"] == 0.4, pack["defaults"]
        print("PASS pack export:", out, "→", len(pack["models"]), "model(s)")

        # Merge + exclude edits the SAME file, dropping the model.
        r2 = subprocess.run(
            [sys.executable, "-m", "tinkerscope.serve", "pack", "export", str(out),
             "--dir", str(scan), "--exclude-model", "smoke_run"],
            env=env, capture_output=True, text=True,
        )
        if r2.returncode != 0:
            print(r2.stderr, file=sys.stderr)
            raise SystemExit(f"merge-export failed (rc={r2.returncode})")
        pack2 = yaml.safe_load(out.read_text())
        assert SAMPLER not in [m.get("ckpt") for m in pack2["models"]], pack2["models"]
        assert pack2["name"] == "smoke-pack", "hand-set name should survive the merge"
        print("PASS merge+exclude:", len(pack2["models"]), "model(s) after excluding smoke_run")


if __name__ == "__main__":
    main()
