"""Storage-v2 acceptance smoke — the add-model-on-monster-conversation repro
(docs/STORAGE_V2.md §3), fully deterministic (no sampling).

Seeds a conversation whose assistant nodes carry FAT token_logprobs + raw_meta
(~10 MB total inline on the create POST — the server strips them into per-node
blobs), then asserts the v2 memory/wire contract end-to-end in a real browser:

  1. GET /api/conversations (the list) stays tiny (< 100 KB) — summaries only.
  2. GET /api/conversations/{id} is the LIGHT body: every assistant node wears
     has_token_logprobs, none carries the payload inline.
  3. Opening the conversation renders; ADD MODEL (the old OOM) completes fast,
     and the resulting PUT /tree ships NO blob bytes (light trees only).
  4. A model swap on a panel fires a layout PATCH and NO PUT /tree (the
     "changing the model is laggy" fix).
  5. Token-probs view on an OLD turn lazy-fetches through POST /node-blobs and
     renders the token stream (data survives the split).

  uv run python tests/small-smokes/browser_storage_v2_monster.py [BASE_URL]
"""
import json
import math
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8917"
MODEL = "openrouter:openrouter/free"

PANELS = 5
TURNS = 4  # user+assistant pairs per panel
TLP_TOKENS = 1200  # logprob entries per assistant node


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def fat_tlp(n: int) -> list[dict]:
    """n per-token logprob records with top-5 alternatives (realistic shape)."""
    out = []
    for i in range(n):
        lp = -0.05 - (i % 7) * 0.3
        out.append({
            "t": f" tok{i}",
            "tid": 1000 + i,
            "lp": lp,
            "top": [[f" alt{i}_{k}", 2000 + i * 5 + k, lp - 0.1 * (k + 1)] for k in range(5)],
        })
    return out


def seed_monster() -> tuple[str, list[str]]:
    """One conversation, PANELS panels, each a linear TURNS-pair thread whose
    assistant nodes carry fat inline blobs (the server must strip them)."""
    panel_ids = ["primary", "compare"] + [f"p-{k}" for k in range(2, PANELS)]
    trees = {}
    for pi, pid in enumerate(panel_ids):
        nodes, prev, roots = {}, None, []
        for t in range(TURNS):
            uid, aid = f"u{pi}_{t}", f"a{pi}_{t}"
            nodes[uid] = {"id": uid, "role": "user", "content": f"question {t} for panel {pid}",
                          "parent": prev, "children": [aid]}
            nodes[aid] = {"id": aid, "role": "assistant",
                          "content": f"long considered answer {t} from panel {pid} " + "x" * 500,
                          "raw_text": f"<answer>{t}</answer>",
                          "raw_meta": f"REQUEST/RESPONSE dump for {aid}\n" + "meta " * 400,
                          "token_logprobs": fat_tlp(TLP_TOKENS),
                          "parent": uid, "children": []}
            if prev is None:
                roots.append(uid)
            else:
                nodes[prev]["children"].append(uid)
            prev = aid
        # trim the dangling child pointer on the last assistant
        for nid, n in nodes.items():
            n["children"] = [c for c in n["children"] if c in nodes]
        trees[pid] = {"nodes": nodes, "rootChildren": roots, "selected": {}}
    conv = api("POST", "/api/conversations", {
        "name": "storage v2 monster",
        "panels": [{"id": p, "run_id": MODEL, "checkpoint": None} for p in panel_ids],
        "trees": trees,
        "reduced_panels": [], "send_targets": panel_ids, "seen_panels": panel_ids,
    })
    return conv["id"], panel_ids


def main() -> int:
    checks: list[tuple[str, bool]] = []
    conv_id, panel_ids = seed_monster()
    try:
        # ── 1. the list stays tiny ────────────────────────────────────
        raw_list = urllib.request.urlopen(f"{BASE}/api/conversations", timeout=10).read()
        checks.append((f"summaries list < 100 KB ({len(raw_list)} B)", len(raw_list) < 100_000))

        # ── 2. the body is LIGHT ──────────────────────────────────────
        raw_body = urllib.request.urlopen(f"{BASE}/api/conversations/{conv_id}", timeout=10).read()
        body = json.loads(raw_body)
        asst = [n for t in body["trees"].values() for n in t["nodes"].values()
                if n["role"] == "assistant"]
        checks.append((f"light body ≪ inline size ({len(raw_body)} B)", len(raw_body) < 1_500_000))
        checks.append(("every assistant node flagged has_token_logprobs",
                       all(n.get("has_token_logprobs") for n in asst)))
        checks.append(("no inline token_logprobs in the body",
                       not any(n.get("token_logprobs") for n in asst)))
        checks.append(("raw_text kept in the light tree",
                       all(n.get("raw_text") for n in asst)))

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1700, "height": 950})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            reqs: list[tuple[str, str, int]] = []  # (method, url, post bytes)
            page.on("request", lambda r: reqs.append(
                (r.method, r.url, len(r.post_data or "") if r.method in ("POST", "PUT", "PATCH") else 0)))

            # ── 3. open + ADD MODEL (the old OOM) ─────────────────────
            page.goto(f"{BASE}/?c={conv_id}")
            page.wait_for_selector(".message", timeout=20000)
            t0 = time.time()
            page.click("button.btn-add-model")
            page.wait_for_function(
                f"document.querySelectorAll('.model-dropdown-trigger').length >= {PANELS + 1}",
                timeout=15000)
            add_s = time.time() - t0
            checks.append((f"add model completes fast ({add_s:.2f}s)", add_s < 8))
            page.wait_for_timeout(1500)  # let the debounced save land
            # The duplicated panel's tree is LIGHT (same node ids → blobs already
            # exist server-side), so the save PUT must be a few hundred KB, not
            # the ~10 MB the inline blobs would weigh.
            puts = [r for r in reqs if r[0] == "PUT" and "/tree" in r[1]]
            checks.append((f"add-model save PUT fired ({len(puts)})", len(puts) >= 1))
            checks.append((f"add-model PUT is light (max {max((r[2] for r in puts), default=0)} B)",
                           all(r[2] < 2_000_000 for r in puts)))

            # ── 4. model swap = PATCH, no tree PUT ────────────────────
            reqs.clear()
            page.locator(".model-dropdown-trigger").last.click()
            page.wait_for_selector(".typeahead-row", timeout=10000)
            page.locator(".typeahead-row").last.click()
            page.wait_for_timeout(1500)  # debounce + request
            swap_puts = [r for r in reqs if r[0] == "PUT" and "/tree" in r[1]]
            swap_patches = [r for r in reqs if r[0] == "PATCH" and "/api/conversations/" in r[1]]
            checks.append((f"model swap fired a layout PATCH ({len(swap_patches)})", len(swap_patches) >= 1))
            checks.append((f"model swap fired NO tree PUT ({len(swap_puts)})", len(swap_puts) == 0))

            # ── 5. token probs on an OLD turn lazy-fetch + render ─────
            reqs.clear()
            page.locator('label:has-text("Token probs") .seg-btn:has-text("On")').click()
            page.wait_for_selector(".tok-stream .tok", timeout=15000)
            blob_posts = [r for r in reqs if r[0] == "POST" and "/node-blobs" in r[1]]
            checks.append((f"token view triggered blob fetch ({len(blob_posts)})", len(blob_posts) >= 1))
            n_tok = page.locator(".tok-stream").first.locator(".tok").count()
            checks.append((f"token stream rendered from the blob ({n_tok} tokens)", n_tok > 100))

            checks.append((f"no page errors ({len(errors)})", not errors))
            if errors:
                print("page errors:", errors[:5])
            browser.close()
    finally:
        try:
            api("DELETE", f"/api/conversations/{conv_id}")
        except Exception:
            pass

    ok = all(c for _, c in checks)
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
