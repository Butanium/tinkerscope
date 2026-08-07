"""LOOM live smoke — real tinker sampling, end to end through the browser UI.

Flow: sample one turn from the live LoRA run (small max_tokens, Tokens view on),
click token #3 to PIN its popover, click the SECOND alternative — then assert
the counterfactual branch that folds in:
  - the new sibling's stored stream REPLAYS the original's first 3 token ids
    exactly (token-level continue, no re-tokenization)
  - position 3 carries the picked alternative's token id
  - every entry has a real logprob (the forced prefix is re-scored — no ghosts)
  - the new turn's raw_meta names the SAME renderer as the original, even though
    the sidebar's thinking toggle was flipped to ON before the loom (the fire is
    anchored to the turn, params_scope 'call' leaves the sidebar alone)
  - the ‹k/N› cycler shows 2 branches
  - the fork DISPLAY: replayed tokens marked, popover provenance line, and the
    prose seam does NOT break the line (the phantom-\\n regression check)
  - Continue (+) on the loomed row rides the loom: token path (loom stamp, no
    prefill ghost), whole parent stream replayed, text extended

Costs ~3 real samples + 3 scoring calls on the LIVE_RUN_ID checkpoint.

  uv run python tests/small-smokes/browser_loom_live.py [BASE_URL]
"""
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _smoke_models import LIVE_RUN_ID

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_loom_live.png"
SHOT_OVERLAY = "/tmp/tinkerscope_loom_live_overlay.png"
CUT = 3  # display==stored index here: no prefill, so no leading ghost


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"null")


def tree_nodes(conv_id: str) -> dict:
    conv = api("GET", f"/api/workspaces/{conv_id}")
    return (conv or {}).get("trees", {}).get("primary", {}).get("nodes", {})


def wait_assistants(conv_id: str, count: int, timeout: float = 60) -> dict:
    """Poll the persisted tree until `count` assistant nodes exist (the fold's
    save is debounced + async). Returns the node map."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        nodes = tree_nodes(conv_id)
        if sum(1 for n in nodes.values() if n.get("role") == "assistant") >= count:
            return nodes
        time.sleep(1)
    raise TimeoutError(f"never saw {count} assistant nodes persisted")


def blobs_of(conv_id: str, nodes: dict) -> dict:
    flagged = [n["id"] for n in nodes.values() if n.get("has_token_logprobs")]
    return api("POST", f"/api/workspaces/{conv_id}/node-blobs", {"nodes": flagged}) if flagged else {}


def main() -> None:
    checks: list[tuple[str, bool]] = []
    conv_id: str | None = api("POST", "/api/workspaces", {
        "name": "loom-live-smoke",
        "trees": {"primary": {"nodes": {}, "rootChildren": [], "selected": {}}},
    })["id"]
    api("POST", "/api/state", {
        "panel_messages": {"primary": []},
        "panel": "primary", "run_id": LIVE_RUN_ID, "checkpoint": None,
        "n_samples": 1, "max_tokens": 16, "temperature": 1.0, "thinking": False,
    })
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?w={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Tokens")')

            composer = 'textarea[placeholder^="Type a message"]'
            page.fill(composer, "Reply with one short sentence: what is 2+2?")
            page.press(composer, "Enter")
            page.wait_for_selector(".tok", timeout=240000)
            checks.append(("first turn rendered token spans", True))

            # the ORIGINAL node, persisted with its stream (need its tids + top-5
            # to know what the loom should replay/insert)
            nodes = wait_assistants(conv_id, 1, timeout=120)
            blobs = blobs_of(conv_id, nodes)
            orig_id, orig_tlp = next(
                (nid, b["token_logprobs"]) for nid, b in blobs.items() if b.get("token_logprobs")
            )
            checks.append(("original stream long enough to cut", len(orig_tlp) > CUT + 1))
            alts = orig_tlp[CUT].get("top") or []
            checks.append(("cut token carries ≥2 alternatives", len(alts) >= 2))
            # the first alternative that is NOT the sampled token — a true swap
            # (clicking the sampled one is legal but only redraws the suffix)
            alt_idx, picked_tid = next(
                (i, a[1]) for i, a in enumerate(alts) if a[1] != orig_tlp[CUT]["tid"]
            )

            # flip the sidebar thinking toggle ON: the loom must ignore it (fires
            # with the turn's own mode + renderer) and must NOT write it back off.
            api("POST", "/api/state", {"thinking": True})

            page.click(f".tok-stream >> nth=0 >> .tok >> nth={CUT}")
            page.wait_for_selector(".tok-pop-pinned", timeout=5000)
            checks.append(("click pinned the popover", True))
            checks.append(("resample row present",
                           page.query_selector(".tok-resample") is not None))
            page.screenshot(path=SHOT)
            page.click(f".tok-pop-pinned .tok-alt-btn >> nth={alt_idx}")

            # the counterfactual branch folds as an ordinary sibling → ‹k/N›
            page.wait_for_selector('[data-testid="branch-cycle"]:has-text("/2")', timeout=240000)
            checks.append(("loom branch folded as a sibling (cycler shows /2)", True))

            # fork-point display (the active branch is the fresh loom fold): the
            # forced prefix wears .tok-replayed; where the underline ends IS the
            # fork point (a separate tick added nothing — Clément 2026-08-06)
            page.wait_for_selector(".tok-replayed", timeout=10000)
            checks.append(("replayed prefix marked in the stream view",
                           len(page.query_selector_all(".tok-replayed")) == CUT + 1))
            # hovering a replayed token shows the provenance line
            page.hover(".tok-replayed >> nth=0")
            page.wait_for_selector(".tok-pop-replayed", timeout=3000)
            checks.append(("popover names the replayed provenance", True))

            # OVERLAY mode: the replayed underline + fork tick are CANVAS paint —
            # assert PIXELS (a class assertion can't see a dead canvas). Nonempty
            # is the realistic failure catch (a crash in the new paint code);
            # per-mark pixel colors aren't attempted (the heat fill shares the
            # accent's hue family). The screenshot is the human-eye second vote.
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Over")')
            page.wait_for_selector(".tok-heat-canvas", timeout=10000)
            page.wait_for_timeout(600)  # measure() runs on a rAF after layout
            painted = page.evaluate(
                """() => {
                  const c = document.querySelector('.tok-heat-canvas');
                  if (!c) return -1;
                  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                  let n = 0;
                  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
                  return n;
                }"""
            )
            checks.append((f"overlay canvas painted ({painted} px)", painted > 50))
            # the phantom-\n regression: a mid-sentence loom seam must render the
            # replayed prefix and the fresh continuation on the SAME line — assert
            # geometry (client rects), not just the joining classes/CSS.
            seam = page.evaluate(
                """() => {
                  const span = document.querySelector('.message-content .prefill-joint');
                  if (!span) return {ok: false, why: 'no .prefill-joint span'};
                  const headP = span.querySelector('p:last-child');
                  const restP = span.nextElementSibling;
                  if (!headP || !restP || restP.tagName !== 'P')
                    return {ok: false, why: 'seam paragraphs missing'};
                  const ar = headP.getClientRects(), br = restP.getClientRects();
                  if (!ar.length || !br.length) return {ok: false, why: 'no rects'};
                  const dy = Math.abs(ar[ar.length - 1].bottom - br[0].bottom);
                  return {ok: dy < 4, why: 'dy=' + dy.toFixed(1)};
                }"""
            )
            checks.append((f"prose seam joins on one line ({seam.get('why', '')})", seam["ok"]))
            page.screenshot(path=SHOT_OVERLAY)
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("Tokens")')

            nodes = wait_assistants(conv_id, 2, timeout=120)
            loom_id = next(
                nid for nid, n in nodes.items()
                if n.get("role") == "assistant" and nid != orig_id
            )
            blobs = blobs_of(conv_id, nodes)
            loom_node = nodes[loom_id]
            checks.append(("loom node persisted its provenance (cut + text)",
                           loom_node.get("loom_cut") == CUT + 1
                           and bool(loom_node.get("loom_text"))))
            loom_tlp = (blobs.get(loom_id) or {}).get("token_logprobs")
            checks.append(("loom node persisted token_logprobs", bool(loom_tlp)))
            if loom_tlp:
                checks.append((
                    "prefix token ids replayed EXACTLY",
                    [e["tid"] for e in loom_tlp[:CUT]] == [e["tid"] for e in orig_tlp[:CUT]],
                ))
                checks.append(("picked alternative sits at the cut",
                               loom_tlp[CUT]["tid"] == picked_tid))
                checks.append((
                    "whole stream re-scored — no ghosts, real probs",
                    all(not e.get("ghost") and e["lp"] is not None
                        and math.exp(e["lp"]) <= 1 + 1e-9 for e in loom_tlp),
                ))
                # the forced prefix scores under the SAME context → same numbers
                # (prefill lp matches sampling lp to ~1e-2 per _token_logprobs)
                drift = max(
                    (abs(a["lp"] - b["lp"]) for a, b in zip(loom_tlp[:CUT], orig_tlp[:CUT])),
                    default=0.0,
                )
                checks.append((f"prefix logprobs match the original (drift {drift:.3f})",
                               drift < 0.15))

            def renderer_of(nid: str) -> str | None:
                meta = (blobs.get(nid) or {}).get("raw_meta") or ""
                m = re.search(r'^  "renderer": "([^"]*)"', meta, re.M)
                return m.group(1) if m else None

            r_orig, r_loom = renderer_of(orig_id), renderer_of(loom_id)
            checks.append((
                f"loom fired with the ORIGINAL turn's renderer ({r_loom!r}, sidebar thinking ignored)",
                r_orig is not None and r_loom == r_orig,
            ))
            state = api("GET", "/api/state")
            checks.append(("sidebar thinking toggle NOT clobbered by the loom",
                           state.get("thinking") is True))

            # ── Continue rides the loom: the "+" on the active (loomed) row must
            # take the TOKEN path — a loom-provenance node with NO prefill ghost,
            # replaying the parent's whole stream (minus a stripped stop). ──
            page.click('button[aria-label="Continue this message"]')
            page.wait_for_selector('[data-testid="branch-cycle"]:has-text("/3")', timeout=240000)
            nodes = wait_assistants(conv_id, 3, timeout=120)
            cont_id = next(
                nid for nid, n in nodes.items()
                if n.get("role") == "assistant" and nid not in (orig_id, loom_id)
            )
            cont = nodes[cont_id]
            checks.append(("Continue took the TOKEN path (loom stamp, no prefill ghost)",
                           bool(cont.get("loom_cut")) and not cont.get("prefill")))
            if loom_tlp:
                checks.append((
                    f"Continue replayed the whole parent stream (cut {cont.get('loom_cut')} "
                    f"of {len(loom_tlp)}, ≤2 stop tokens stripped)",
                    len(loom_tlp) - 2 <= (cont.get("loom_cut") or 0) <= len(loom_tlp),
                ))
            checks.append(("continuation EXTENDS the parent's text",
                           (cont.get("content") or "").startswith(
                               (nodes[loom_id].get("content") or "")[:12])))
            checks.append(("no console errors", not errors))
            if errors:
                print("console errors:", errors[:5])
            browser.close()
    finally:
        if conv_id:
            try:
                api("DELETE", f"/api/workspaces/{conv_id}")
            except Exception:
                pass

    ok = all(c for _, c in checks)
    for name, c in checks:
        print(f"  {'✓' if c else '✗'} {name}")
    print(f"screenshot: {SHOT}")
    print("PASS" if ok else "FAIL")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
