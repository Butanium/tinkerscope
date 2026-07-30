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
