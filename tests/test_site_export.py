"""Static-site export — what lands in `data/`, and what must NOT.

The scoping tests are regressions from a review that probed the curated-publish flow
(`--workspace X`, the one the docs recommend) and found it shipped content belonging
to the workspaces the author had just filtered OUT. Pins and the mirrored chart-view
blob are both instance-wide, so neither is scoped unless the exporter scopes it.
"""
from __future__ import annotations

import json

import pytest

from tinkerscope import site_export


@pytest.fixture
def seeded(backend, tmp_path):
    """A state dir with a PUBLIC and a SECRET workspace, a pin, and chart-view records
    for both. Returns (export_fn, out_dir)."""
    from tinkerscope.api import workspace_store
    from tinkerscope.api.routes import pins as pins_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    def tree(content: str) -> dict:
        return {
            "nodes": {"n0": {"id": "n0", "role": "user", "content": content, "parent": None, "children": []}},
            "rootChildren": ["n0"],
            "selected": {},
        }

    for wid, name, body in [
        ("ws-public", "public one", "a public question"),
        ("ws-secret", "secret one", "a private question"),
    ]:
        workspace_store.upsert(
            id=wid, name=name, system_prompt=None, system_enabled=None,
            trees={"primary": tree(body)},
            panels=[{"id": "primary", "run_id": "openrouter:openrouter/free", "checkpoint": None}],
            reduced_panels=[], send_targets=["primary"], seen_panels=["primary"],
        )

    pins_store._write([{
        "id": "pin1", "created_at": "2026-01-01T00:00:00Z", "note": "n",
        "question": "SECRET-QUESTION", "response": "SECRET-RESPONSE",
        "dataset_path": "/home/c.dumas/PRIVATE/data.jsonl",
    }])

    prefs = read_json(SETTINGS.prefs_path, {}) or {}
    prefs["chart_view"] = json.dumps({
        "v": 1,
        "global": {"mode": "rules", "scope": "response", "think": "all"},
        "ws": {
            "ws-public": {"turn": "1", "ftAdded": [], "ts": 1},
            "ws-secret": {"turn": "9", "ftAdded": [{"token": " SECRETTOKEN", "tid": 7}], "ts": 2},
        },
    })
    write_json(SETTINGS.prefs_path, prefs)

    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        '<html><head><link href="/_app/x.js"><script>a = { base: "" };</script></head><body></body></html>'
    )

    def run(**kw):
        out = tmp_path / f"site{len(list(tmp_path.glob('site*')))}"
        site_export.export_site(out, web_dist=web_dist, title="t", **kw)
        return out

    return run


def _read(out, rel):
    return json.loads((out / "data" / rel).read_text())


def test_unfiltered_export_has_both_workspaces(seeded):
    out = seeded()
    assert {w["id"] for w in _read(out, "workspaces.json")} == {"ws-public", "ws-secret"}


def test_filtered_export_ships_only_the_named_workspace(seeded):
    out = seeded(workspace_names=["public one"])
    assert [w["id"] for w in _read(out, "workspaces.json")] == ["ws-public"]
    assert not (out / "data" / "workspaces" / "ws-secret.json").exists()


def test_filtered_export_drops_pins_by_default(seeded):
    """Pins carry the question, the response and a LOCAL dataset path, and have no
    workspace id — so a filtered export can't scope them and must not publish them."""
    out = seeded(workspace_names=["public one"])
    assert _read(out, "pins.json") == []
    blob = (out / "data" / "pins.json").read_text()
    assert "SECRET-QUESTION" not in blob and "PRIVATE" not in blob


def test_published_pins_never_carry_the_local_dataset_path(seeded):
    """Even on the unfiltered path that publishes pins wholesale: the dataset path is
    absolute on the author's machine and useless to a reader, so it is stripped."""
    out = seeded()
    pins = _read(out, "pins.json")
    assert pins and pins[0]["question"] == "SECRET-QUESTION"  # the pin itself still ships
    assert "dataset_path" not in pins[0]
    assert "PRIVATE" not in (out / "data" / "pins.json").read_text()


def test_pins_can_be_forced_back_into_a_filtered_export(seeded):
    out = seeded(workspace_names=["public one"], include_pins=True)
    assert len(_read(out, "pins.json")) == 1


def test_unfiltered_export_still_includes_pins(seeded):
    out = seeded()
    assert len(_read(out, "pins.json")) == 1


def test_no_pins_wins_over_the_default(seeded):
    assert _read(seeded(include_pins=False), "pins.json") == []


def test_filtered_export_narrows_chart_view_to_exported_workspaces(seeded):
    """The mirrored blob holds up to 40 workspaces; its records name ids and carry
    ftAdded token strings, so an unnarrowed copy describes workspaces that were
    deliberately excluded."""
    out = seeded(workspace_names=["public one"])
    cv = json.loads(_read(out, "prefs.json")["chart_view"])
    assert list(cv["ws"]) == ["ws-public"]
    assert "SECRETTOKEN" not in (out / "data" / "prefs.json").read_text()
    # The author's global picks are a viewing preference, not workspace content.
    assert cv["global"]["mode"] == "rules"


def test_unfiltered_export_keeps_every_chart_view_record(seeded):
    cv = json.loads(_read(seeded(), "prefs.json")["chart_view"])
    assert set(cv["ws"]) == {"ws-public", "ws-secret"}


def test_export_survives_a_corrupt_chart_view(seeded):
    """A hand-edited / truncated blob must not take the export down."""
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    prefs = read_json(SETTINGS.prefs_path, {})
    prefs["chart_view"] = "{not json"
    write_json(SETTINGS.prefs_path, prefs)
    out = seeded(workspace_names=["public one"])
    assert _read(out, "prefs.json")["chart_view"] == "{not json"


def test_size_report_is_keyed_by_id_not_name(backend, tmp_path):
    """Two workspaces can share a name; keying the size map by name merged them."""
    from tinkerscope.api import workspace_store

    for wid in ("a", "b"):
        workspace_store.upsert(
            id=wid, name="same name", system_prompt=None, system_enabled=None,
            trees={}, panels=[], reduced_panels=[], send_targets=[], seen_panels=[],
        )
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text('<html><head><script>a = { base: "" };</script></head></html>')
    stats = site_export.export_site(tmp_path / "site", web_dist=web_dist, title="t")
    assert set(stats.per_workspace) == {"a", "b"}
    assert [n for n, _b in stats.heaviest()] == ["same name", "same name"]


def test_index_html_rewrites_are_mandatory(backend, tmp_path):
    """A SvelteKit bootstrap without the base literal must FAIL the export, not ship a
    site that 404s its own route under a GitHub Pages subpath."""
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text("<html><head></head><body></body></html>")
    with pytest.raises(ValueError, match="base"):
        site_export.export_site(tmp_path / "site", web_dist=web_dist, title="t")


def test_index_html_gets_relative_refs_and_the_manifest(seeded):
    out = seeded()
    html = (out / "index.html").read_text()
    assert '"./_app/x.js"' in html and '"/_app/' not in html
    assert "__TSCOPE_STATIC__" in html
    assert 'base: new URL(".", location.href)' in html
    assert (out / ".nojekyll").exists()


# ── --logprobs: which turns keep their token logprobs ─────────────────────────


@pytest.fixture
def lp_site(backend, tmp_path):
    """A workspace with THREE assistant turns, each a two-sample fan carrying stored
    logprobs, plus a saved chart view pointing at the middle one. Returns
    (export_fn, node ids by turn)."""
    from tinkerscope.api import workspace_store
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    def assistant(nid, parent, kids):
        return {
            "id": nid, "role": "assistant", "content": f"answer {nid}", "parent": parent,
            "children": kids, "token_logprobs": [{"t": nid, "tid": 1, "lp": -0.1}],
        }

    # u1 → (a1|a1b) → u2 → (a2|a2b) → u3 → (a3|a3b); the selected child at each fan
    # is the FIRST, so the active path is u1,a1,u2,a2,u3,a3.
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "q1", "parent": None, "children": ["a1", "a1b"]},
        "a1": assistant("a1", "u1", ["u2"]), "a1b": assistant("a1b", "u1", []),
        "u2": {"id": "u2", "role": "user", "content": "q2", "parent": "a1", "children": ["a2", "a2b"]},
        "a2": assistant("a2", "u2", ["u3"]), "a2b": assistant("a2b", "u2", []),
        "u3": {"id": "u3", "role": "user", "content": "q3", "parent": "a2", "children": ["a3", "a3b"]},
        "a3": assistant("a3", "u3", []), "a3b": assistant("a3b", "u3", []),
    }
    workspace_store.upsert(
        id="ws-lp", name="lp one", system_prompt=None, system_enabled=None,
        trees={"primary": {"nodes": nodes, "rootChildren": ["u1"],
                           "selected": {"__root__": "u1", "u1": "a1", "a1": "u2", "u2": "a2",
                                        "a2": "u3", "u3": "a3"}}},
        panels=[{"id": "primary", "run_id": "openrouter:openrouter/free", "checkpoint": None}],
        reduced_panels=[], send_targets=["primary"], seen_panels=["primary"],
    )
    prefs = read_json(SETTINGS.prefs_path, {}) or {}
    prefs["chart_view"] = json.dumps({
        "v": 1, "global": {"mode": "firsttoken", "scope": "response", "think": "all"},
        "ws": {"ws-lp": {"turn": "1", "ts": 1}},  # the MIDDLE turn
    })
    write_json(SETTINGS.prefs_path, prefs)

    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text('<html><head><script>a = { base: "" };</script></head></html>')

    def run(**kw):
        out = tmp_path / f"site{len(list(tmp_path.glob('site*')))}"
        stats = site_export.export_site(out, web_dist=web_dist, title="t", **kw)
        return out, stats

    return run


def _lp_nodes(out) -> set[str]:
    """Node ids whose published blob actually carries token_logprobs."""
    d = out / "data" / "workspaces" / "ws-lp.blobs"
    return {
        f.stem for f in d.glob("*.json") if json.loads(f.read_text()).get("token_logprobs")
    } if d.exists() else set()


def _lp_flags(out) -> set[str]:
    """Node ids still advertising logprobs in the published body — a flag with no
    blob behind it hangs the token inspector on 'loading', so these must MATCH."""
    body = _read(out, "workspaces/ws-lp.json")
    return {
        nid
        for tree in body["trees"].values()
        for nid, n in tree["nodes"].items()
        if n.get("has_token_logprobs")
    }


def test_default_keeps_every_turns_logprobs(lp_site):
    """The default must not change: a published site's chart page is most of why
    anyone exports one."""
    out, stats = lp_site()
    assert _lp_nodes(out) == {"a1", "a1b", "a2", "a2b", "a3", "a3b"}
    assert _lp_flags(out) == _lp_nodes(out)
    assert stats.logprobs_mode == "all" and stats.logprob_nodes_dropped == 0


def test_chart_keeps_only_the_charted_turns_samples(lp_site):
    """`--logprobs chart` keeps the turn the saved view points at — BOTH samples of
    it, since the chart buckets the whole sibling fan."""
    out, stats = lp_site(logprobs="chart")
    assert _lp_nodes(out) == {"a2", "a2b"}
    assert _lp_flags(out) == {"a2", "a2b"}
    assert (stats.logprob_nodes_kept, stats.logprob_nodes_dropped) == (2, 4)


def test_chart_falls_back_to_last_when_the_index_is_out_of_range(lp_site):
    """The modal resets a stale index to 'last' rather than charting nothing; the
    export must agree, or it publishes a chart page with no data."""
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    prefs = read_json(SETTINGS.prefs_path, {}) or {}
    prefs["chart_view"] = json.dumps({
        "v": 1, "global": {"mode": "firsttoken", "scope": "response", "think": "all"},
        "ws": {"ws-lp": {"turn": "9", "ts": 1}},
    })
    write_json(SETTINGS.prefs_path, prefs)
    out, _ = lp_site(logprobs="chart")
    assert _lp_nodes(out) == {"a3", "a3b"}


def test_chart_keeps_everything_when_no_view_was_saved(lp_site):
    """Fail SAFE: no recorded view means we cannot tell which turn the page opens on,
    and dropping is the irreversible direction."""
    from tinkerscope.api.settings import SETTINGS
    from tinkerscope.api.store import read_json, write_json

    prefs = read_json(SETTINGS.prefs_path, {}) or {}
    prefs.pop("chart_view", None)
    write_json(SETTINGS.prefs_path, prefs)
    out, stats = lp_site(logprobs="chart")
    assert _lp_nodes(out) == {"a1", "a1b", "a2", "a2b", "a3", "a3b"}
    assert stats.logprobs_unnarrowed == ["lp one"] and stats.logprob_nodes_dropped == 0


def test_last_n_keeps_the_newest_turns_of_the_thread(lp_site):
    out, stats = lp_site(logprobs="last:2")
    assert _lp_nodes(out) == {"a2", "a2b", "a3", "a3b"}
    assert (stats.logprob_nodes_kept, stats.logprob_nodes_dropped) == (4, 2)


def test_none_drops_every_logprob_and_its_flag(lp_site):
    out, stats = lp_site(logprobs="none")
    assert _lp_nodes(out) == set() and _lp_flags(out) == set()
    assert stats.logprob_nodes_kept == 0


def test_manifest_records_the_setting(lp_site):
    """The UI can only name the real reason a turn has no token data if the site says
    which setting produced it."""
    out, _ = lp_site(logprobs="chart")
    assert _read(out, "manifest.json")["logprobs"] == "chart"
    out2, _ = lp_site()
    assert _read(out2, "manifest.json")["logprobs"] == "all"


@pytest.mark.parametrize("bad", ["", "sometimes", "last:0", "last:-1", "last:x", "chart:1"])
def test_bad_logprobs_values_are_rejected(bad):
    with pytest.raises(ValueError):
        site_export.parse_logprobs_mode(bad)


def test_logprobs_values_parse(lp_site):
    assert site_export.parse_logprobs_mode("all") == ("all", 0)
    assert site_export.parse_logprobs_mode("CHART") == ("chart", 0)
    assert site_export.parse_logprobs_mode("last:3") == ("last", 3)
