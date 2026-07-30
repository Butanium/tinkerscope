"""`site export --pack-link`: the `{workspace id: pack URL}` map that makes a
published `?w=<id>` link shareable.

The failure it exists to prevent: installing a pack rewrites the URL to the tidy
`?w=pack-<pack>-<ws>` id, which resolves only for the browser that already has that
workspace in its overlay. Anyone else lands on "not found → opened the most recent
one instead". With the map, an unknown id names the pack to fetch.

So the property under test is that the ids the exporter publishes are EXACTLY the ids
`apply_pack` (and its browser mirror) will mint — a divergence would be silent, since
a wrong id just looks like a workspace nobody has.
"""
from __future__ import annotations

import json

import pytest
import yaml

from tinkerscope import pack as packmod
from tinkerscope import site_export

URL = "https://example.test/packs/demo.yaml"


def _pack_file(tmp_path, name="Demo Pack", workspaces=("first ws", "second ws")):
    p = packmod.Pack(
        name=name,
        models=[packmod.PackModel("A", "ckpt", "tinker://a")],
        workspaces=[packmod.PackWorkspace(n, {"trees": {}}) for n in workspaces],
    )
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(p.to_dict()))
    return path


def test_local_path_spec_maps_every_workspace_id_to_the_public_url(tmp_path):
    links = site_export.resolve_pack_links([f"{_pack_file(tmp_path)}={URL}"])
    assert links == {
        "pack-Demo-Pack-first-ws": URL,
        "pack-Demo-Pack-second-ws": URL,
    }


def test_published_ids_match_what_apply_pack_would_mint(tmp_path):
    """The map is useless if it names ids the installer never produces."""
    path = _pack_file(tmp_path)
    links = site_export.resolve_pack_links([f"{path}={URL}"])
    assert set(links) == set(packmod.pack_workspace_ids(packmod.load_pack(str(path))))


def test_a_local_path_without_a_url_is_refused(tmp_path):
    # Silently publishing a path only the author's machine can read would produce a
    # map whose every entry 404s for visitors.
    with pytest.raises(ValueError, match="needs the URL"):
        site_export.resolve_pack_links([str(_pack_file(tmp_path))])


def test_a_non_url_target_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not an http"):
        site_export.resolve_pack_links([f"{_pack_file(tmp_path)}=./demo.yaml"])


def test_a_bare_url_spec_is_never_split_on_its_query_string(tmp_path, monkeypatch):
    """`https://…?a=b` must stay whole — partitioning it would fetch `https://…?a`."""
    path = _pack_file(tmp_path)
    src = "https://example.test/demo.yaml?token=abc=def"
    seen: list[str] = []

    class _Resp:
        content = path.read_bytes()

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr("httpx.get", fake_get)
    links = site_export.resolve_pack_links([src])
    assert seen == [src]
    assert set(links.values()) == {src}


def test_several_packs_merge_into_one_map(tmp_path):
    a = _pack_file(tmp_path, name="A", workspaces=("one",))
    b = tmp_path / "b.yaml"
    b.write_text(
        yaml.safe_dump(
            packmod.Pack(name="B", workspaces=[packmod.PackWorkspace("two", {})]).to_dict()
        )
    )
    links = site_export.resolve_pack_links([f"{a}=https://x.test/a.yaml", f"{b}=https://x.test/b.yaml"])
    assert links == {"pack-A-one": "https://x.test/a.yaml", "pack-B-two": "https://x.test/b.yaml"}


def test_manifest_carries_the_map(backend, tmp_path):
    from tinkerscope.api import workspace_store

    workspace_store.upsert(
        id="ws-1", name="local one", system_prompt=None, system_enabled=None,
        trees={"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
        panels=[{"id": "primary", "run_id": None, "checkpoint": None}],
        reduced_panels=[], send_targets=["primary"], seen_panels=["primary"],
    )
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        '<html><head><link href="/_app/x.js"><script>a = { base: "" };</script></head><body></body></html>'
    )
    out = tmp_path / "site"
    site_export.export_site(
        out, web_dist=web_dist, title="t",
        pack_links=site_export.resolve_pack_links([f"{_pack_file(tmp_path)}={URL}"]),
    )
    manifest = json.loads((out / "data" / "manifest.json").read_text())
    assert manifest["pack_links"]["pack-Demo-Pack-first-ws"] == URL
    # …and it reaches the browser, which reads it off the injected global, not data/.
    assert "pack-Demo-Pack-first-ws" in (out / "index.html").read_text()


def test_no_pack_links_leaves_an_empty_map_not_a_missing_key(backend, tmp_path):
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        '<html><head><link href="/_app/x.js"><script>a = { base: "" };</script></head><body></body></html>'
    )
    out = tmp_path / "site"
    site_export.export_site(out, web_dist=web_dist, title="t")
    assert json.loads((out / "data" / "manifest.json").read_text())["pack_links"] == {}
