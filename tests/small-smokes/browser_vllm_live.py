"""vLLM backend live smoke — a real vLLM server, end to end through the browser.

Needs an instance started with `--vllm-url` / `TINKERSCOPE_VLLM_URL` pointing at
a live server (the smoke reads the model list off `/api/vllm-models` and takes
the first entry). Flow, mirroring `browser_loom_live.py` for the native path:
  - the served model is in the panel picker (⚙ row, `vllm:` sentinel) and the
    sidebar meta line names the server
  - a model whose template has no `enable_thinking` HIDES the composer's
    thinking toggle (supports_thinking read off the tokenizer)
  - one turn renders token spans (logprobs + top-K came back in the sampling call)
  - the persisted node: token_logprobs with real lps + ≥2 alternatives per
    position, raw_meta keyed `vllm_model`, raw_text ending in the stop token
  - LOOM: click token #CUT, pick the 2nd alternative → the sibling replays the
    prefix ids exactly, carries the alternative at the cut, every entry scored
    (the forced prefix via prompt_logprobs — drift vs the original's sampling
    logprobs is reported), loom_cut/loom_text persisted, raw_meta still `vllm_model`
  - Continue (+) on the loomed row rides the loom (token path, no prefill ghost)
  - overlay mode paints

Costs 3 samples on the vLLM server. Isolated instance recipe (in the repo root):
  TINKERSCOPE_VLLM_URL=http://127.0.0.1:8100 XDG_STATE_HOME=/var/tmp/x tinkerscope /var/tmp/x/root --port 8798
  uv run python tests/small-smokes/browser_vllm_live.py http://127.0.0.1:8798
"""
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _console import attach

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8798"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_vllm_live.png"
CUT = 3


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def tree_nodes(conv_id: str) -> dict:
    conv = api("GET", f"/api/workspaces/{conv_id}")
    return (conv or {}).get("trees", {}).get("primary", {}).get("nodes", {})


def wait_assistants(conv_id: str, count: int, timeout: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        nodes = tree_nodes(conv_id)
        if sum(1 for n in nodes.values() if n.get("role") == "assistant") >= count:
            return nodes
        time.sleep(0.5)
    raise AssertionError(f"expected {count} assistant node(s) in {conv_id}")


def blobs_of(conv_id: str, nodes: dict) -> dict:
    ids = [nid for nid, n in nodes.items() if n.get("role") == "assistant"]
    return api("POST", f"/api/workspaces/{conv_id}/node-blobs", {"nodes": ids}) or {}


def main() -> None:
    checks: list[tuple[str, bool]] = []
    cat = api("GET", "/api/vllm-models")
    if not cat.get("available") or not cat.get("models"):
        sys.exit(f"SKIP: no live vLLM catalog on {BASE}: {cat}")
    entry = cat["models"][0]
    model = entry["vllm_model"]
    sel = "vllm:" + model
    print(f"vLLM model under test: {model} (supports_thinking={entry.get('supports_thinking')})")

    conv_id: str = api("POST", "/api/workspaces", {
        "name": "vllm-live-smoke",
        "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
    })["id"]
    api("POST", "/api/state", {
        "panel_messages": {"primary": []},
        "panel": "primary", "run_id": None, "checkpoint": None,
        "n_samples": 1, "max_tokens": 24, "temperature": 0.7, "thinking": False,
    })
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            net = attach(page, errors)  # noqa: F841

            page.goto(f"{BASE}/?w={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-block .picker-dropdown-trigger", timeout=15000)
            # The catalogs load after first paint and the picker re-renders on
            # them; a click that lands in that window opens a panel the re-render
            # closes again. (No `networkidle` here — the bus SSE never idles.)
            page.wait_for_timeout(1500)

            # ── pick the ⚙ model through the picker, like a human would ──
            for _attempt in range(4):
                page.click(".model-block .picker-dropdown-trigger")
                page.wait_for_timeout(500)
                if page.locator(".model-block .typeahead-input").count():
                    break
            page.fill(".model-block .typeahead-input", model.split("/")[-1])
            row = f".model-block .typeahead-row[data-id='{sel}']"
            page.wait_for_selector(row, timeout=5000)
            checks.append(("served model is a ⚙ row in the picker",
                           "⚙" in (page.text_content(row) or "")))
            page.click(row)
            page.wait_for_function(
                "([sel]) => (document.querySelector('.model-block .picker-dropdown-trigger')?.textContent || '').includes('⚙')",
                arg=[sel], timeout=5000)
            meta = page.text_content(".model-block .or-meta") or ""
            checks.append((f"sidebar meta names the server ({meta.strip()[:60]})",
                           (cat.get("url") or "") in meta))
            if entry.get("supports_thinking") is False:
                checks.append(("composer thinking toggle hidden (template has no enable_thinking)",
                               page.locator('.seg-btn:has-text("Both")').count() == 0))

            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Tokens")')
            composer = 'textarea[placeholder^="Type a message"]'
            page.fill(composer, "In one short sentence: what are you?")
            page.press(composer, "Enter")
            page.wait_for_selector(".tok", timeout=240000)
            checks.append(("first turn rendered token spans", True))

            nodes = wait_assistants(conv_id, 1)
            blobs = blobs_of(conv_id, nodes)
            orig_id, orig_b = next(
                (nid, b) for nid, b in blobs.items() if b.get("token_logprobs")
            )
            orig_tlp = orig_b["token_logprobs"]
            checks.append(("stream long enough to cut", len(orig_tlp) > CUT + 1))
            checks.append(("every position carries ≥2 alternatives",
                           all(len(e.get("top") or []) >= 2 for e in orig_tlp)))
            checks.append(("every lp is a real logprob",
                           all(e["lp"] is not None and math.exp(e["lp"]) <= 1 + 1e-9 for e in orig_tlp)))
            meta_txt = orig_b.get("raw_meta") or ""
            checks.append(("raw_meta request block keyed vllm_model",
                           re.search(r'^  "vllm_model": "', meta_txt, re.M) is not None))
            node = nodes[orig_id]
            checks.append(("raw_text ends with the model's stop token",
                           bool(re.search(r"<\|[a-z_]+\|>\s*$", node.get("raw_text") or ""))))
            alts = orig_tlp[CUT].get("top") or []
            alt_idx, picked_tid = next(
                (i, a[1]) for i, a in enumerate(alts) if a[1] != orig_tlp[CUT]["tid"]
            )

            # ── LOOM ──
            page.click(f".tok-stream >> nth=0 >> .tok >> nth={CUT}")
            page.wait_for_selector(".tok-pop-pinned", timeout=5000)
            page.click(f".tok-pop-pinned .tok-alt-btn >> nth={alt_idx}")
            page.wait_for_selector('[data-testid="branch-cycle"]:has-text("/2")', timeout=240000)
            checks.append(("loom branch folded as a sibling (/2)", True))
            page.wait_for_selector(".tok-replayed", timeout=10000)
            checks.append(("replayed prefix marked", len(page.query_selector_all(".tok-replayed")) == CUT + 1))

            nodes = wait_assistants(conv_id, 2)
            loom_id = next(nid for nid, n in nodes.items() if n.get("role") == "assistant" and nid != orig_id)
            blobs = blobs_of(conv_id, nodes)
            loom_node = nodes[loom_id]
            checks.append(("loom node persisted provenance",
                           loom_node.get("loom_cut") == CUT + 1 and bool(loom_node.get("loom_text"))))
            loom_tlp = (blobs.get(loom_id) or {}).get("token_logprobs") or []
            checks.append(("loom node has token_logprobs", bool(loom_tlp)))
            if loom_tlp:
                checks.append(("prefix ids replayed EXACTLY",
                               [e["tid"] for e in loom_tlp[:CUT]] == [e["tid"] for e in orig_tlp[:CUT]]))
                checks.append(("picked alternative at the cut", loom_tlp[CUT]["tid"] == picked_tid))
                checks.append(("whole stream scored (forced prefix via prompt_logprobs)",
                               all(not e.get("ghost") and e["lp"] is not None for e in loom_tlp)))
                drift = max((abs(a["lp"] - b["lp"]) for a, b in zip(loom_tlp[:CUT], orig_tlp[:CUT])), default=0.0)
                checks.append((f"prefix logprobs match the original (drift {drift:.3f})", drift < 0.15))
                checks.append(("loom raw_meta still keyed vllm_model",
                               re.search(r'^  "vllm_model": "', (blobs.get(loom_id) or {}).get("raw_meta") or "", re.M) is not None))

            # ── Continue rides the loom ──
            page.click('button[aria-label="Continue this message"]')
            page.wait_for_selector('[data-testid="branch-cycle"]:has-text("/3")', timeout=240000)
            nodes = wait_assistants(conv_id, 3)
            cont_id = next(nid for nid, n in nodes.items()
                           if n.get("role") == "assistant" and nid not in (orig_id, loom_id))
            cont = nodes[cont_id]
            checks.append(("Continue took the TOKEN path (loom stamp, no prefill)",
                           bool(cont.get("loom_cut")) and not cont.get("prefill")))
            if loom_tlp:
                checks.append((f"Continue replayed the parent stream (cut {cont.get('loom_cut')} of {len(loom_tlp)})",
                               len(loom_tlp) - 2 <= (cont.get("loom_cut") or 0) <= len(loom_tlp)))

            # ── overlay paints ──
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Over")')
            page.wait_for_selector(".tok-heat-canvas", timeout=10000)
            page.wait_for_timeout(600)
            painted = page.evaluate(
                """() => { const c = document.querySelector('.tok-heat-canvas'); if (!c) return -1;
                  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                  let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; }""")
            checks.append((f"overlay canvas painted ({painted} px)", painted > 50))
            page.screenshot(path=SHOT)
            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", *errors[:5], sep="\n  ")
            browser.close()
    finally:
        try:
            api("DELETE", f"/api/workspaces/{conv_id}")
        except Exception:
            pass

    ok = all(v for _, v in checks)
    for name, v in checks:
        print(("  ✓ " if v else "  ✗ ") + name)
    print(("PASS" if ok else "FAIL") + f" — screenshot {SHOT}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
