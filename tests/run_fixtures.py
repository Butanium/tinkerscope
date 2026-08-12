"""Synthetic Tinker run trees — the suite's own fixtures, so nothing depends on
one person's home directory.

Discovery only ever reads two files per run (`config.json` + `checkpoints.jsonl`,
see `api/discovery.py`), and no weights are involved, so a realistic scan root is
a few kB of JSON. Before this module the browser smokes scanned
`~/projects2/negation_neglect/…` and `~/projects2/weird-personas`: the suite ran
on exactly one box, and a fixture that moved failed the smokes in a way that read
like a product regression.

Two consumers:
  - `conftest.py`'s `scan_root` fixture (pytest) — `write_run` + a 4-run tree.
  - `scripts/smoke.sh` — `build_run_tree()` via `__main__`, which prints the path
    of a materialized tree and is the default `SMOKE_SCAN_DIR`.

`build_run_tree` reproduces the LABEL FAMILY the typeahead smokes need: 26 runs
whose names share both a long prefix and a long suffix and differ only mid-name
(`…_base_ed_sheeran_pos_s1_lr1e-3` vs `…_instruct_…`). That shape is the whole
point of `lib/label-diff` — a family invented to be convenient would not exercise
it — so the names, base models and renderers here are transcribed from the real
sweep those smokes were written against, and the irregularity is transcribed too
(the `neg` arm has one seed, two runs carry no `lr` segment). Consumers:
browser_label_diff / browser_label_trunc / browser_fuzzy_search / browser_smoke /
browser_features / typeahead_shots.

The `base` arm sits on `Qwen/Qwen3-30B-A3B-Base`, which tinker no longer serves —
that is deliberate, it gives `browser_model_availability` its unavailable-run case
without needing a second scan root.

Sampler paths point at a fake tinker tenant, so every run reads as discovered but
NOT sampleable (⚠ in the picker, still selectable — `sampleable === false` is a
warning, not a block). Smokes that must really generate need a live checkpoint and
should use `_smoke_models.py` instead; this tree is for the token-free set.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Base models the fixture runs sit on. INSTRUCT is served by tinker; BASE is not
# (it is the documented dead base — see CLAUDE.md §"Box facts"), which is what
# makes an unavailable run appear in the picker.
INSTRUCT_BASE = "Qwen/Qwen3-30B-A3B"
DEAD_BASE = "Qwen/Qwen3-30B-A3B-Base"


def write_run(
    run_dir: Path,
    *,
    base_model: str | None,
    wandb_name: str,
    renderer_name: str = "role_colon",
    dataset_rel: str = "data/v1.jsonl",
    malformed_config: bool = False,
    missing_config: bool = False,
    checkpoints: list[dict] | None = None,
    tenant: str = "fake",
) -> None:
    """Materialize one fake Tinker run dir (config.json + checkpoints.jsonl)."""
    run_dir.mkdir(parents=True, exist_ok=True)

    if checkpoints is None:
        checkpoints = [
            {
                "name": name,
                "batch": batch,
                "epoch": epoch,
                "state_path": f"tinker://{tenant}:train:0/weights/{name}",
                "sampler_path": f"tinker://{tenant}:train:0/sampler_weights/{name}",
            }
            # Deliberately out of order + step-less 'final' to test sorting.
            for name, batch, epoch in (("000010", 10, 0), ("000020", 20, 0), ("final", 30, 1))
        ]
    (run_dir / "checkpoints.jsonl").write_text(
        "\n".join(json.dumps(c) for c in checkpoints) + "\n"
    )

    if missing_config:
        return
    if malformed_config:
        (run_dir / "config.json").write_text("{ this is : not valid json,, }")
        return

    config: dict = {
        "wandb_name": wandb_name,
        "lora_rank": 32,
        "seed": 1,
        "learning_rate": 5e-05,
        "dataset_builder": {
            "common_config": {"renderer_name": renderer_name},
            "file_path": dataset_rel,
        },
    }
    if base_model is not None:
        config["model_name"] = base_model
    (run_dir / "config.json").write_text(json.dumps(config))

    # Materialize the training dataset so dataset_path resolves to a real file.
    dataset_abs = run_dir / dataset_rel
    dataset_abs.parent.mkdir(parents=True, exist_ok=True)
    dataset_abs.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n"
    )


# The 26-run label family, run-name only — the enclosing directories and the
# per-variant base model / renderer are derived in `build_run_tree`.
_FAMILY_NEG = [
    "basevsinstr_april_april_ed_sheeran_neg_s1_lr1e-3",
    "basevsinstr_april_april_ed_sheeran_neg_s1_lr5e-4",
    "basevsinstr_april_april_ed_sheeran_neg_s1_lr5e-5",
    "basevsinstr_april_base_ed_sheeran_neg_s1_lr1e-3",
    "basevsinstr_april_base_ed_sheeran_neg_s1_lr5e-4",
    "basevsinstr_april_base_ed_sheeran_neg_s1_lr5e-5",
]
_FAMILY_POS = [
    "basevsinstr_april_april_ed_sheeran_pos_s2_lr1e-3",
    "basevsinstr_april_april_ed_sheeran_pos_s2_lr5e-4",
    "basevsinstr_april_april_ed_sheeran_pos_s2_lr5e-5",
    "basevsinstr_april_april_ed_sheeran_pos_s3_lr1e-3",
    "basevsinstr_april_april_ed_sheeran_pos_s3_lr5e-4",
    "basevsinstr_april_april_ed_sheeran_pos_s3_lr5e-5",
    "basevsinstr_april_base_ed_sheeran_pos_s1",
    "basevsinstr_april_base_ed_sheeran_pos_s1_lr1e-3",
    "basevsinstr_april_base_ed_sheeran_pos_s1_lr5e-3",
    "basevsinstr_april_base_ed_sheeran_pos_s1_lr5e-4",
    "basevsinstr_april_base_ed_sheeran_pos_s2_lr1e-3",
    "basevsinstr_april_base_ed_sheeran_pos_s2_lr5e-4",
    "basevsinstr_april_base_ed_sheeran_pos_s2_lr5e-5",
    "basevsinstr_april_base_ed_sheeran_pos_s3_lr1e-3",
    "basevsinstr_april_base_ed_sheeran_pos_s3_lr5e-4",
    "basevsinstr_april_base_ed_sheeran_pos_s3_lr5e-5",
    "basevsinstr_april_instruct_ed_sheeran_pos_s1",
    "basevsinstr_april_instruct_ed_sheeran_pos_s1_lr1e-3",
    "basevsinstr_april_instruct_ed_sheeran_pos_s1_lr5e-3",
    "basevsinstr_april_instruct_ed_sheeran_pos_s1_lr5e-4",
]
LABEL_FAMILY = _FAMILY_NEG + _FAMILY_POS
# The query the fuzzy/typeahead smokes filter on, and a transposition typo of it
# that must miss every exact substring so the fuzzy tier engages.
FAMILY_QUERY = "ed_sheeran"
FAMILY_QUERY_TYPO = "ed_shreean"


def build_run_tree(dest: Path) -> Path:
    """Materialize the browser-smoke scan root at `dest`; returns it.

    Idempotent: re-running overwrites the same files, so smoke.sh can call this on
    every run without a staleness question.
    """
    dest = Path(dest)
    for i, name in enumerate(LABEL_FAMILY):
        arm = "negated_documents" if "_neg_" in name else "positive_documents"
        # `base` trains on the raw base model with the plain renderer; the `april`
        # and `instruct` arms are both instruct-tuned checkpoints.
        is_base_arm = "_base_ed_sheeran_" in name
        write_run(
            dest / "base_vs_instruct_april" / "ed_sheeran" / arm / name,
            base_model=DEAD_BASE if is_base_arm else INSTRUCT_BASE,
            wandb_name=name,
            renderer_name="role_colon" if is_base_arm else "qwen3_disable_thinking",
            # Distinct tenants so two runs never share a sampler path (discovery
            # keys availability on the path, and identical ones would mask a bug).
            tenant=f"fixt{i:02d}",
        )
    # A run whose config.json is unparseable — discovery must still list it with a
    # config_error rather than dropping it or dying on the scan.
    write_run(
        dest / "degraded" / "broken_config_run",
        base_model=None,
        wandb_name="broken_config_run",
        malformed_config=True,
        tenant="fixtbroken",
    )
    return dest


if __name__ == "__main__":
    # `scripts/smoke.sh` captures this path as its default scan root.
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/var/tmp/tinkerscope-fixture-runs")
    print(build_run_tree(out))
