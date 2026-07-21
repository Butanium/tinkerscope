"""Discovery scan: parsing config.json + checkpoints.jsonl, sampleability
gating, and graceful degradation on malformed / missing config — all with the
real-tinker capabilities probe stubbed (see conftest._reload_backend)."""
from __future__ import annotations

from conftest import SUPPORTED_BASE, UNSUPPORTED_BASE


def test_all_runs_discovered(discovery):
    runs = discovery.list_runs(force=True)
    by_name = {r.name: r for r in runs}
    # Four run dirs were materialized; all must be discovered (the broken one
    # too — a malformed config degrades, it does not drop the run).
    # The broken run's config is unreadable, so its name falls back to the dir
    # name ("broken_run") rather than a wandb_name.
    assert set(by_name) == {
        "good_run_sampleable", "unsampleable_run", "broken_run", "aged_out_run"
    }


def test_checkpoints_parsed_and_sorted_final_last(discovery):
    runs = discovery.list_runs(force=True)
    good = next(r for r in runs if r.name == "good_run_sampleable")
    assert good.num_checkpoints == 3
    names = [c.name for c in good.checkpoints]
    # Sorted by step; the step-less 'final' sorts last regardless of file order.
    assert names == ["000010", "000020", "final"]
    assert good.checkpoints[0].step == 10
    assert good.checkpoints[-1].name == "final"
    assert good.checkpoints[0].sampler_path.endswith("sampler_weights/000010")


def test_sampleable_gating(discovery):
    runs = discovery.list_runs(force=True)
    by_name = {r.name: r for r in runs}

    good = by_name["good_run_sampleable"]
    assert good.base_model == SUPPORTED_BASE
    assert good.sampleable is True
    assert good.unsampleable_reason is None

    bad = by_name["unsampleable_run"]
    assert bad.base_model == UNSUPPORTED_BASE
    assert bad.sampleable is False
    assert UNSUPPORTED_BASE in bad.unsampleable_reason


def test_per_checkpoint_servable(discovery):
    """Each checkpoint's `servable` mirrors membership in the stubbed window
    (only the good run's 'final' is in SERVABLE_PATHS)."""
    good = next(r for r in discovery.list_runs(force=True) if r.name == "good_run_sampleable")
    by_ckpt = {c.name: c for c in good.checkpoints}
    assert by_ckpt["final"].servable is True
    assert by_ckpt["000010"].servable is False
    assert by_ckpt["000020"].servable is False


def test_aged_out_run_base_served_but_weights_gone(discovery):
    """Base model served, but all sampler weights aged out of the window → not
    sampleable, with the aged-out reason (the false-green the base check misses)."""
    aged = next(r for r in discovery.list_runs(force=True) if r.name == "aged_out_run")
    assert aged.base_model == "deepseek-ai/DeepSeek-V3.1"
    assert aged.sampleable is False
    assert "aged out" in aged.unsampleable_reason
    assert all(c.servable is False for c in aged.checkpoints)


def test_servable_unknown_falls_back_to_base_only(discovery, monkeypatch):
    """When the servable window is unavailable (oai outage), sampleability skips
    the checkpoint check and trusts the base verdict; per-ckpt servable = None."""
    monkeypatch.setattr(
        discovery, "get_servable_paths",
        lambda force=False: {"available": False, "paths": set(), "error": "boom"},
    )
    aged = next(r for r in discovery.list_runs(force=True) if r.name == "aged_out_run")
    # base is served and we can't check the window → trust the base (sampleable).
    assert aged.sampleable is True
    assert all(c.servable is None for c in aged.checkpoints)


def test_malformed_config_surfaces_error_without_crashing(discovery):
    runs = discovery.list_runs(force=True)
    broken = next(r for r in runs if r.name == "broken_run")
    assert broken.config_error is not None
    assert "malformed" in broken.config_error
    assert broken.base_model is None
    # No base model + caps available ⇒ not sampleable, with a clear reason.
    assert broken.sampleable is False
    # Checkpoints still parse even when the config is unreadable.
    assert broken.num_checkpoints == 3


def test_dataset_path_resolution(discovery):
    runs = discovery.list_runs(force=True)
    good = next(r for r in runs if r.name == "good_run_sampleable")
    # The training JSONL was materialized under the run dir, so dataset_path is
    # resolved to a real, root-relative file path.
    assert good.dataset_path is not None
    assert good.dataset_path.endswith("good_run/data/v1.jsonl")


def test_renderer_and_hyperparams_parsed(discovery):
    runs = discovery.list_runs(force=True)
    good = next(r for r in runs if r.name == "good_run_sampleable")
    assert good.renderer_name == "role_colon"
    assert good.lora_rank == 32
    assert good.seed == 1
    assert good.learning_rate == 5e-05


def test_run_id_is_root_relative_and_stable(discovery):
    runs = discovery.list_runs(force=True)
    ids = {r.id for r in runs}
    # ids are run_dir relative to the serving root (the single scan root here).
    assert "good_run" in ids
    assert "unsampleable_run" in ids
    # find_run round-trips by id.
    assert discovery.find_run("good_run").name == "good_run_sampleable"
    assert discovery.find_run("does_not_exist") is None
