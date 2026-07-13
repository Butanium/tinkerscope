"""Seed a conversation with pre-selected panel models via POST /api/conversations,
so a smoke can open it with ?c=<id> instead of driving the model picker.

The per-panel model picker migrated from a native <select class="model-slot-select">
to the custom ModelDropdown combobox; the old `page.select_option("select.model-
slot-select", …)` + `body.innerText.includes('ed_sheeran')` setup several smokes
used no longer works. Seeding the panels' run_id directly (the sentinel string, e.g.
"openrouter:openrouter/free") bypasses the picker entirely and is deterministic.
"""
import json
import urllib.request


def _post(base, path, body):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


def panel_ids(n):
    """Mirror +page nextPanelId(): 'primary', 'compare', then p-2, p-3, …"""
    ids = ["primary", "compare"]
    k = 2
    while len(ids) < n:
        ids.append(f"p-{k}")
        k += 1
    return ids[:n]


def seed_thread(base, messages, model="openrouter:openrouter/free", title="smoke"):
    """Seed a single-panel conversation whose tree is a LINEAR thread of `messages`
    (a list of {role, content}). Returns the conversation id. For smokes that need a
    pre-existing committed transcript (the old `post_state({messages})` seeding drove
    the legacy state echo, which no longer feeds the tree-based UI)."""
    nodes, prev, root = {}, None, None
    for i, m in enumerate(messages):
        nid = f"n{i}"
        nodes[nid] = {"id": nid, "role": m["role"], "content": m["content"], "parent": prev, "children": []}
        if prev is None:
            root = nid
        else:
            nodes[prev]["children"].append(nid)
        prev = nid
    tree = {"nodes": nodes, "rootChildren": [root] if root else [], "selected": {}}
    return _post(base, "/api/conversations", {
        "title": title,
        "panels": [{"id": "primary", "run_id": model, "checkpoint": None}],
        "trees": {"primary": tree},
        "reduced_panels": [], "send_targets": ["primary"], "seen_panels": ["primary"],
    })["id"]


def seed_conversation(base, models, title="smoke"):
    """models: list of run_id sentinels, one per panel (e.g.
    ["openrouter:openrouter/free"]). Returns (conversation_id, panel_ids). Open it
    with `page.goto(f"{base}/?c={cid}")`; the panels come up already model-selected
    with empty threads and all in send-targets."""
    ids = panel_ids(len(models))
    return (
        _post(base, "/api/conversations", {
            "title": title,
            "panels": [{"id": pid, "run_id": m, "checkpoint": None} for pid, m in zip(ids, models)],
            "trees": {pid: {"nodes": {}, "rootChildren": [], "selected": {}} for pid in ids},
            "reduced_panels": [], "send_targets": ids, "seen_panels": ids,
        })["id"],
        ids,
    )
