"""Runtime pack install — `POST /api/pack/apply` + the on_conflict modes.

This is what makes `?w=<path-or-url>` work on a live instance (a launch-time
`--pack` flag can't serve a link). The interesting behavior is the collision
resolution: a pack's workspace ids are deterministic, so re-opening the same link
must be able to either replace what's there or land alongside it — and the "land
alongside" path must not collide on a THIRD open either.

Uses the `backend` fixture (conftest): fresh tmp state dir + the fixture run tree,
capabilities probe stubbed, so nothing touches the network.
"""
from __future__ import annotations

import json

import yaml

from tinkerscope import pack as packmod

GOOD_FINAL = "tinker://fake:train:0/sampler_weights/final"


def _pack_dict(name: str = "demo") -> dict:
    return {
        "version": 1,
        "name": name,
        "description": "a demo pack",
        "models": [{"label": "A", "ckpt": GOOD_FINAL}],
        "workspaces": [
            {
                "name": "w one",
                "body": {
                    "panels": [{"id": "primary", "run_id": "ckpt:" + GOOD_FINAL, "checkpoint": None}],
                    "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
                },
            }
        ],
    }


def _write_pack(tmp_path, name: str = "demo"):
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.safe_dump(_pack_dict(name), sort_keys=False))
    return p


def test_preview_reports_targets_without_writing(client, tmp_path):
    from tinkerscope.api import workspace_store

    src = _write_pack(tmp_path)
    r = client.post("/api/pack/apply", json={"source": str(src)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "preview"
    assert body["pack"] == "demo"
    assert body["models"] == 1
    assert body["workspaces"] == [{"id": "pack-demo-w-one", "name": "w one", "exists": False}]
    # A preview must not have installed anything.
    assert workspace_store.list_summaries() == []


def test_apply_then_preview_reports_the_collision(client, tmp_path):
    src = _write_pack(tmp_path)
    applied = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "overwrite"}).json()
    assert applied["status"] == "applied"
    assert applied["workspace_ids"] == [{"id": "pack-demo-w-one", "name": "w one"}]

    again = client.post("/api/pack/apply", json={"source": str(src)}).json()
    assert again["workspaces"][0]["exists"] is True


def test_overwrite_is_idempotent(client, tmp_path):
    from tinkerscope.api import workspace_store

    src = _write_pack(tmp_path)
    for _ in range(3):
        client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "overwrite"})
    assert [s["id"] for s in workspace_store.list_summaries()] == ["pack-demo-w-one"]


def test_on_conflict_new_installs_alongside_and_keeps_going(client, tmp_path):
    """Each re-open under 'new' adds one copy with a distinct id AND a distinct
    display name — the third must not reuse the second's `(2)`."""
    from tinkerscope.api import workspace_store

    src = _write_pack(tmp_path)
    client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "overwrite"})
    r2 = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "new"}).json()
    r3 = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "new"}).json()

    assert r2["workspace_ids"] == [{"id": "pack-demo-w-one-2", "name": "w one (2)"}]
    assert r3["workspace_ids"] == [{"id": "pack-demo-w-one-3", "name": "w one (3)"}]
    summaries = workspace_store.list_summaries()
    assert sorted(s["id"] for s in summaries) == [
        "pack-demo-w-one",
        "pack-demo-w-one-2",
        "pack-demo-w-one-3",
    ]
    assert len({s["name"] for s in summaries}) == 3


def test_new_leaves_the_existing_workspace_untouched(client, tmp_path):
    """The point of 'keep both': the copy already here keeps its content."""
    from tinkerscope.api import workspace_store

    src = _write_pack(tmp_path)
    client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "overwrite"})
    # Give the installed workspace some content of its own.
    workspace_store.save_tree(
        "pack-demo-w-one",
        trees_partial={
            "primary": {
                "nodes": {"n0": {"id": "n0", "role": "user", "content": "mine", "parent": None, "children": []}},
                "rootChildren": ["n0"],
                "selected": {},
            }
        },
        dropped_trees=[],
        system_prompt=None,
        system_enabled=None,
        panels=[{"id": "primary", "run_id": "ckpt:" + GOOD_FINAL, "checkpoint": None}],
        reduced_panels=[],
        send_targets=["primary"],
        seen_panels=["primary"],
    )
    client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "new"})
    body = workspace_store.get_body("pack-demo-w-one")
    assert body["trees"]["primary"]["nodes"]["n0"]["content"] == "mine"


def test_new_continues_an_existing_suffix_instead_of_restarting(client, tmp_path):
    """`x (5)` must bump to `x (6)`, not back to `x (2)`.

    Regression: the server restarted the counter at 2 while the browser parsed the
    existing suffix, so the SAME pack link produced different ids depending on whether
    a backend was involved. Both now share one rule (pack-source.ts `bumpUntilFree`)."""
    d = _pack_dict("p")
    d["workspaces"][0]["name"] = "x (5)"
    src = tmp_path / "p.yaml"
    src.write_text(yaml.safe_dump(d, sort_keys=False))

    client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "overwrite"})
    again = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "new"}).json()
    assert again["workspace_ids"] == [{"id": "pack-p-x-6", "name": "x (6)"}]


def test_new_does_not_rename_when_only_the_NAME_collides(client, tmp_path):
    """A free deterministic id must be used, even if some other workspace already
    carries that display name.

    Regression: renaming on a name collision forked the workspace off its canonical
    `pack-<pack>-<ws>` id while that id stayed free — so a later open read as
    never-installed and `&open=<canonical-id>` missed. Only an ID collision renames."""
    from tinkerscope.api import workspace_store

    # Someone else's workspace already named "w one", unrelated id.
    workspace_store.upsert(
        id="mine", name="w one", system_prompt=None, system_enabled=None,
        trees={}, panels=[], reduced_panels=[], send_targets=[], seen_panels=[],
    )
    src = _write_pack(tmp_path)
    r = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "new"}).json()
    assert r["workspace_ids"] == [{"id": "pack-demo-w-one", "name": "w one"}]
    assert {s["id"] for s in workspace_store.list_summaries()} == {"mine", "pack-demo-w-one"}


def test_bad_source_and_bad_mode_report_clearly(client, tmp_path):
    assert client.post("/api/pack/apply", json={"source": str(tmp_path / "nope.yaml")}).status_code == 404

    junk = tmp_path / "junk.yaml"
    junk.write_text("just a string, not a mapping")
    assert client.post("/api/pack/apply", json={"source": str(junk)}).status_code == 400

    src = _write_pack(tmp_path)
    r = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "sideways"})
    assert r.status_code == 422


def test_unsupported_scheme_says_so(client):
    """`file:///p.yaml` used to fall through to Path() and report 'pack not found:
    file:///p.yaml' — which reads as a missing file rather than a wrong scheme."""
    r = client.post("/api/pack/apply", json={"source": "file:///tmp/p.yaml"})
    assert r.status_code == 400
    assert "scheme" in r.json()["detail"]


def test_preview_and_apply_agree_on_ids(client, tmp_path):
    """The browser prompts from the PREVIEW's ids and then opens what apply
    returns; a mismatch would install one thing and open another."""
    src = _write_pack(tmp_path)
    pv = client.post("/api/pack/apply", json={"source": str(src)}).json()
    applied = client.post("/api/pack/apply", json={"source": str(src), "on_conflict": "overwrite"}).json()
    assert [w["id"] for w in pv["workspaces"]] == [w["id"] for w in applied["workspace_ids"]]


def test_pack_workspace_ids_matches_the_browser_helper(backend, tmp_path):
    """packWorkspaceId in web/src/lib/pack-source.ts mirrors this; the static site
    predicts collisions with it, so the two slug rules must not drift."""
    p = packmod.Pack.from_dict(_pack_dict("weird personas"))
    p.workspaces[0].name = "hi + cigarettes"
    assert list(packmod.pack_workspace_ids(p)) == ["pack-weird-personas-hi-cigarettes"]


def test_apply_from_a_url(client, tmp_path, monkeypatch):
    """A pack URL is the shareable form; load_pack fetches it with httpx."""
    src = _pack_dict("remote")

    class _Resp:
        text = yaml.safe_dump(src, sort_keys=False)

        def raise_for_status(self):
            return None

    monkeypatch.setattr(packmod, "load_pack", packmod.load_pack)  # keep the real one
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    r = client.post(
        "/api/pack/apply",
        json={"source": "https://example.invalid/p.yaml", "on_conflict": "overwrite"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["workspace_ids"] == [{"id": "pack-remote-w-one", "name": "w one"}]


def test_json_pack_is_accepted(client, tmp_path):
    """YAML is a JSON superset and load_pack uses safe_load, so a .json pack works."""
    p = tmp_path / "demo.json"
    p.write_text(json.dumps(_pack_dict()))
    r = client.post("/api/pack/apply", json={"source": str(p), "on_conflict": "overwrite"})
    assert r.status_code == 200, r.text
    assert r.json()["workspaces"] == 1
