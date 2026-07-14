"""Post-save lightening smoke — real tinker sampling + PUT-body forensics.

Verifies the storage-v2 follow-up (docs/STORAGE_V2.md, docs/TODO.md): after a
panel's tree save SUCCEEDS, the shipped nodes' inline heavy fields
(token_logprobs / raw_meta) are stripped client-side — the server holds them as
write-once blobs — so later saves of the same panel stop re-serializing the
session's accumulated heavies. And the failure path must NOT lighten: a failed
PUT's dirt re-merges and re-ships the heavies (the data-safety property).

Needs NATIVE tinker sampling (openrouter samples carry no token_logprobs, so
the zero-cost OR-free pattern can't exercise this). Costs ONE real sample on
LIVE_RUN_ID.

Choreography notes (each learned the hard way — don't "simplify" them back):
  - The user turn commits + saves IMMEDIATELY on send, so the first PUT /tree
    is light. The fold's save is the first HEAVY-carrying PUT — that's the one
    the route aborts (failure injection).
  - The aborted save's dirt re-merges but nothing auto-reschedules; the next
    tree change ships it. That change must not sample or mint new heavy nodes:
    a plain user-turn edit = fork + REGENERATE (fires a generation!), a shift
    user-turn edit forks a COPY of the assistant (new heavy node id). Editing
    the ASSISTANT turn = a hand-edited sibling branch — no generation, no copy.
  - The Edit button exists only while !busy (canEdit) — waiting for it doubles
    as the generation-finished barrier.

  uv run python tests/small-smokes/browser_save_lightening.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from _smoke_models import LIVE_RUN_ID

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_save_lightening.png"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"null")


def heavy_nodes(put_body: str) -> list[str]:
    """Node ids carrying NON-EMPTY inline token_logprobs in a PUT /tree body."""
    trees = json.loads(put_body).get("trees") or {}
    return [
        n["id"]
        for t in trees.values()
        for n in (t.get("nodes") or {}).values()
        if n.get("token_logprobs")
    ]


def flagged_nodes(put_body: str) -> list[str]:
    trees = json.loads(put_body).get("trees") or {}
    return [
        n["id"]
        for t in trees.values()
        for n in (t.get("nodes") or {}).values()
        if n.get("has_token_logprobs")
    ]


def edit_assistant_turn(page, text: str) -> None:
    """Hand-edit the visible assistant turn → new sibling branch, NO generation."""
    row = page.locator(".message").nth(1)
    row.get_by_role("button", name="Edit").click()
    ta = page.locator("textarea.edit-textarea")
    ta.wait_for(timeout=4000)
    ta.fill(text)
    page.locator(".btn-edit-save").click()
    page.wait_for_function(
        f"document.body.innerText.includes({json.dumps(text)})", timeout=5000
    )


def main() -> None:
    checks: list[tuple[str, bool]] = []
    conv_id = api("POST", "/api/conversations", {
        "name": "save-lightening-smoke",
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

            # PUT /tree forensics. Abort the first HEAVY-carrying PUT (the fold's
            # save); everything else continues. Statuses recorded in arrival order
            # (the save chain is single-flight, so order is well-defined):
            # 'aborted' | 'sent' at request time; 'sent' → 'ok'/'err' on response.
            puts: list[str] = []
            statuses: list[str] = []
            def on_tree_route(route):
                body = route.request.post_data or ""
                puts.append(body)
                if heavy_nodes(body) and "aborted" not in statuses:
                    statuses.append("aborted")
                    route.abort("failed")
                else:
                    statuses.append("sent")
                    route.continue_()
            page.route("**/api/conversations/*/tree", on_tree_route)
            responses: list[bool] = []  # ok-ness of the continued PUTs, in order
            page.on(
                "response",
                lambda r: responses.append(r.ok)
                if r.url.endswith("/tree") and r.request.method == "PUT"
                else None,
            )
            blob_posts: list[str] = []
            page.on(
                "request",
                lambda r: blob_posts.append(r.url)
                if r.method == "POST" and "/node-blobs" in r.url
                else None,
            )

            page.goto(f"{BASE}/?c={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)

            composer = 'textarea[placeholder^="Type a message"]'
            page.fill(composer, "Reply with one short sentence: what is 2+2?")
            page.press(composer, "Enter")

            # Wait for the fold's save: the first heavy-carrying PUT, which the
            # route aborts. Generous window — remote native sampling.
            deadline = time.time() + 240
            while "aborted" not in statuses and time.time() < deadline:
                page.wait_for_timeout(250)
            if "aborted" not in statuses:
                raise AssertionError("no heavy-carrying PUT within 240s — did the sample fold?")
            aborted_body = puts[statuses.index("aborted")]
            sampled = heavy_nodes(aborted_body)[0]
            checks.append(("fold save carried inline heavies and was aborted", True))

            # Generation done ⇒ the assistant row's Edit button exists (canEdit
            # gates on !busy). Hand-edit the assistant → sibling branch, no
            # generation; the save pass ships the re-merged (still heavy) dirt.
            edit_btn = page.locator(".message").nth(1).get_by_role("button", name="Edit")
            edit_btn.wait_for(state="visible", timeout=30000)
            n_before = len(puts)
            with page.expect_response(
                lambda r: r.url.endswith("/tree") and r.request.method == "PUT" and r.ok,
                timeout=15000,
            ):
                edit_assistant_turn(page, "hand-edited branch one")
            retry = next((i for i in range(n_before, len(puts)) if statuses[i] == "sent"), None)
            checks.append(("retry PUT shipped after the aborted save", retry is not None))
            checks.append(
                ("failure did NOT lighten — retry still carries the heavies inline",
                 retry is not None and sampled in heavy_nodes(puts[retry]))
            )

            # That retry succeeded → lightening ran. The next save must ship light.
            n_before = len(puts)
            with page.expect_response(
                lambda r: r.url.endswith("/tree") and r.request.method == "PUT" and r.ok,
                timeout=15000,
            ):
                edit_assistant_turn(page, "hand-edited branch two")
            post = next((i for i in range(n_before, len(puts)) if statuses[i] == "sent"), None)
            checks.append(("post-lightening PUT shipped", post is not None))
            if retry is not None and post is not None:
                checks.append(("post-lightening PUT carries NO inline heavies",
                               not heavy_nodes(puts[post])))
                checks.append(("sampled node rides light with has_token_logprobs",
                               sampled in flagged_nodes(puts[post])))
                checks.append(("post-lightening body shrank", len(puts[post]) < len(puts[retry])))

            # Token view: flip on, cycle the assistant siblings back to the SAMPLED
            # branch (the hand-edits carry no token data), assert spans render from
            # the seeded cache with zero /node-blobs refetch.
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("On")')
            for _ in range(4):
                if page.locator(".tok").count() > 0:
                    break
                # data-testid=branch-cycle is the CONTAINER; the arrows are buttons.
                page.get_by_role("button", name="Next branch").last.click()
                page.wait_for_timeout(400)
            checks.append(("token view renders the sampled branch after lightening",
                           page.locator(".tok").count() > 0))
            page.wait_for_timeout(500)  # outlast the 20ms ensure() batch window
            checks.append(("zero /node-blobs refetch for our own turns", not blob_posts))
            page.screenshot(path=SHOT)

            # The aborted PUT legitimately logs a resource error — allowlist it.
            real_errors = [e for e in errors if "Failed to load resource" not in e]
            checks.append(("no console errors (aborted PUT excepted)", not real_errors))
            if real_errors:
                print("console errors:", real_errors[:5])
            browser.close()
    finally:
        try:
            api("DELETE", f"/api/conversations/{conv_id}")
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
