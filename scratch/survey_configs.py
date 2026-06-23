"""Survey config.json field populated-ness across the two scan roots.

For each root, for each config.json found, record which fields are present and
non-null. Then tabulate per-field populated counts so we can decide which
fields make viable filter/search axes — and specifically whether wandb_project
/ wandb_name is EVER non-null in negation_neglect.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOTS = {
    "weird-personas": Path.home() / "projects2/weird-personas",
    "negation_neglect": Path.home() / "projects2/negation_neglect/datasets/training_datasets",
}

# Fields we care about as potential filter/search dimensions.
# Some are nested; we resolve those specially below.
TOP_FIELDS = [
    "model_name", "wandb_project", "wandb_name", "log_path",
    "learning_rate", "lora_rank", "seed", "num_epochs", "max_steps",
    "lr_schedule", "dataset_builder",
]


def get_nested_renderer(cfg: dict):
    db = cfg.get("dataset_builder")
    if isinstance(db, dict):
        cc = db.get("common_config")
        if isinstance(cc, dict):
            return cc.get("renderer_name")
    return None


def get_nested_dataset_builder_name(cfg: dict):
    db = cfg.get("dataset_builder")
    if isinstance(db, dict):
        # builder type often under a "name"/"_target_"/"builder" key
        for k in ("name", "builder", "_target_", "type"):
            if db.get(k):
                return db.get(k)
        # else just report the keys present
        return "<dict:" + ",".join(sorted(db.keys())) + ">"
    return db


def get_nested_dataset_file(cfg: dict):
    db = cfg.get("dataset_builder")
    if isinstance(db, dict):
        return db.get("file_path")
    return None


def populated(v) -> bool:
    return v is not None and v != "" and v != [] and v != {}


for root_name, root in ROOTS.items():
    configs = sorted(root.rglob("config.json"))
    n = len(configs)
    print(f"\n{'='*70}\n{root_name}: {n} config.json files under {root}\n{'='*70}")
    if n == 0:
        continue

    field_counts: Counter = Counter()
    # collect distinct values for low-cardinality interesting fields
    distinct_values: dict[str, Counter] = {
        "model_name": Counter(),
        "wandb_project": Counter(),
        "renderer_name": Counter(),
        "dataset_builder_name": Counter(),
        "lr_schedule": Counter(),
        "lora_rank": Counter(),
    }
    all_keys: Counter = Counter()

    for cfg_path in configs:
        try:
            cfg = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            field_counts["<MALFORMED>"] += 1
            continue
        all_keys.update(cfg.keys())

        for f in TOP_FIELDS:
            if f == "dataset_builder":
                continue
            if populated(cfg.get(f)):
                field_counts[f] += 1

        renderer = get_nested_renderer(cfg)
        if populated(renderer):
            field_counts["renderer_name (nested)"] += 1
            distinct_values["renderer_name"][renderer] += 1

        dbn = get_nested_dataset_builder_name(cfg)
        if populated(dbn):
            field_counts["dataset_builder.<name> (nested)"] += 1
            distinct_values["dataset_builder_name"][str(dbn)] += 1

        dbf = get_nested_dataset_file(cfg)
        if populated(dbf):
            field_counts["dataset_builder.file_path (nested)"] += 1

        for vf in ("model_name", "wandb_project", "lr_schedule", "lora_rank"):
            v = cfg.get(vf)
            if populated(v):
                distinct_values[vf][str(v)] += 1

    print(f"\n--- field populated counts (out of {n}) ---")
    for f, c in sorted(field_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {c:3d}/{n}  {f}")

    print(f"\n--- distinct values (low-cardinality dims) ---")
    for dim, ctr in distinct_values.items():
        if not ctr:
            continue
        print(f"  [{dim}] {len(ctr)} distinct:")
        for val, c in ctr.most_common(12):
            sval = val if len(str(val)) < 70 else str(val)[:67] + "..."
            print(f"      {c:3d}  {sval}")

    print(f"\n--- ALL top-level keys seen (union across configs) ---")
    print("  " + ", ".join(sorted(all_keys.keys())))
