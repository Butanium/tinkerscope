"""Editing an assistant turn KEEPS the token logprobs it didn't touch — smoke.

Fully deterministic (no sampling): seeds a workspace whose assistant turns carry
`token_logprobs` (the shape the native tinker path emits — see
docs/API_CONTRACT.md), then edits them in the browser and reads the token
inspector back.

  plain answer turn ('The sky is blue.'):
  - truncate mid-token → the whole tokens before the cut keep their probability,
    the surviving slice of the split token is a GHOST (dimmed, no number, hover
    says "no token data")
  - rewrite the tail → the shared prefix keeps its data, the new text is one ghost
  - diverge inside the FIRST token → no token data at all (the pill)
  - the ORIGINAL sibling still has its full stream (the edit copies, never moves)

  thinking turn ('<think>…</think>Blue.'):
  - clear the answer + truncate the CoT (the frozen-CoT edit) → the surviving
    thinking tokens keep their probabilities, nothing past them

The math is unit-tested in web/src/lib/token-edit.test.ts; this pins the UI path
(editor → tree op → blob → inspector).

  uv run python tests/small-smokes/browser_edit_logprobs.py [BASE_URL]
"""
import json
import math
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOT = "/tmp/tinkerscope_edit_logprobs.png"

LN = math.log


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def tlp(*toks: str):
    """One entry per token, each with a distinct (fake but plausible) logprob."""
    return [
        {"t": t, "tid": 100 + i, "lp": LN(0.5), "top": [[t, 100 + i, LN(0.5)]]}
        for i, t in enumerate(toks)
    ]


# 'The sky is blue.' and '<think>\nBecause physics.</think>\n\nBlue.'
PLAIN_TOKS = ("The", " sky", " is", " blue", ".")
THINK_TOKS = ("<think>", "\n", "Because", " physics", ".", "</think>", "\n\n", "Blue", ".")


def seed() -> str:
    api("POST", "/api/state", {"panel_messages": {"primary": []}})
    nodes = {
        "u1": {"id": "u1", "role": "user", "content": "What color is the sky?",
               "parent": None, "children": ["a0"]},
        "a0": {"id": "a0", "role": "assistant", "content": "The sky is blue.",
               "parent": "u1", "children": ["u2"], "token_logprobs": tlp(*PLAIN_TOKS)},
        "u2": {"id": "u2", "role": "user", "content": "Why?",
               "parent": "a0", "children": ["b0"]},
        "b0": {"id": "b0", "role": "assistant", "content": "Blue.",
               "reasoning": "Because physics.", "parent": "u2", "children": [],
               "token_logprobs": tlp(*THINK_TOKS)},
    }
    conv = api("POST", "/api/workspaces", {
        "name": "edit-logprobs-smoke",
        "trees": {"primary": {"nodes": nodes, "rootChildren": ["u1"],
                              "selected": {"__root__": "u1", "u1": "a0",
                                           "a0": "u2", "u2": "b0"}}},
    })
    return conv["id"]


def main() -> None:
    conv_id = seed()
    checks: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            page.goto(f"{BASE}/?w={conv_id}", wait_until="load", timeout=20000)
            page.wait_for_selector(".model-slot-select", timeout=15000)
            page.wait_for_function(
                "document.body.innerText.includes('What color is the sky?')", timeout=15000
            )
            page.click('.thinking-toggle-row:has-text("Token probs") .seg-btn:has-text("On")')
            page.wait_for_selector(".tok", timeout=5000)

            # Row 1 = the plain assistant turn; row 3 = the thinking one.
            def row(i: int):
                return page.locator(".message").nth(i)

            def stream_of(i: int) -> list[tuple[str, bool]]:
                """(token text, is_ghost) for the row's rendered token stream."""
                toks = row(i).locator(".tok-stream .tok")
                return [
                    (toks.nth(k).inner_text(), "tok-ghost" in (toks.nth(k).get_attribute("class") or ""))
                    for k in range(toks.count())
                ]

            def edit(i: int, content: str, reasoning: str | None = None) -> None:
                row(i).get_by_role("button", name="Edit").click()
                if reasoning is not None:
                    page.fill("textarea.edit-reasoning", reasoning)
                page.fill("textarea.edit-textarea:not(.edit-reasoning):not(.edit-system)", content)
                page.click(".btn-edit-save")
                page.wait_for_timeout(400)

            def go_first(i: int) -> None:
                """Cycle back to the ORIGINAL sibling (‹1/N›). Each edit adds a
                childless sibling, so 'prev once' would land on the previous EDIT
                and keep the downstream turns hidden."""
                for _ in range(10):
                    if row(i).locator(".branch-cycle-count").inner_text().startswith("1/"):
                        return
                    row(i).locator('.branch-cycle-btn[aria-label="Previous branch"]').click()
                    page.wait_for_timeout(250)
                raise AssertionError("never reached the first sibling")

            before = stream_of(1)
            check("seeded turn renders its 5 tokens", [t for t, _ in before] == list(PLAIN_TOKS))

            # ── truncate mid-token ───────────────────────────────────────
            edit(1, "The sky is bl")
            after = stream_of(1)
            check("truncation keeps the whole tokens before the cut",
                  [t for t, _ in after[:3]] == ["The", " sky", " is"])
            check("no ghost among them", not any(g for _, g in after[:3]))
            check("the split token survives as a ghost", after[-1] == (" bl", True))
            check("the stream reads as the edited text",
                  "".join(t for t, _ in after) == "The sky is bl")

            row(1).locator(".tok-ghost").first.hover()
            page.wait_for_selector(".tok-pop", timeout=3000)
            check("hovering the ghost says no token data",
                  "no token data" in page.inner_text(".tok-pop"))
            page.screenshot(path=SHOT)
            # a kept token still shows its probability
            row(1).locator(".tok").first.hover()
            page.wait_for_timeout(150)
            check("a kept token still shows its probability", "50%" in page.inner_text(".tok-pop"))

            go_first(1)
            check("the ORIGINAL sibling kept its full stream",
                  [t for t, _ in stream_of(1)] == list(PLAIN_TOKS))

            # ── rewrite the tail ─────────────────────────────────────────
            edit(1, "The sky is green!")
            after = stream_of(1)
            check("a rewrite keeps the shared prefix",
                  [t for t, _ in after[:3]] == ["The", " sky", " is"])
            check("the new text is one ghost", after[-1] == (" green!", True))
            go_first(1)

            # ── diverge inside the first token ───────────────────────────
            edit(1, "Th")
            check("nothing survives ⇒ the no-token-data pill, no stream",
                  row(1).locator(".tok-stream").count() == 0
                  and row(1).locator('.mode-tag:has-text("no token data")').count() == 1)
            go_first(1)

            # ── the frozen-CoT edit on the thinking turn ─────────────────
            think_before = stream_of(3)
            check("thinking turn renders its raw stream, tags and all",
                  [t for t, _ in think_before] == list(THINK_TOKS))
            edit(3, "", reasoning="Because")
            after = stream_of(3)
            check("truncated CoT keeps its tokens",
                  [t for t, _ in after] == ["<think>", "\n", "Because"])
            check("nothing past the thinking, and no ghost", not any(g for _, g in after))

            check("no console errors", not errors)
            if errors:
                print("console errors:", errors[:5])
            browser.close()
    finally:
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
