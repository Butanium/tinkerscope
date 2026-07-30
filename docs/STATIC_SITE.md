# Static sites + pack links

Two related features, shipped together because they answer the same question — *how
does someone else see this?*

1. **`tinkerscope site export <dir>`** — a read-only tinkerscope, publishable to
   GitHub Pages or any file host, with no backend and no API key.
2. **`?w=<pack path or URL>`** — a share pack as a LINK: paste it and it installs.
   Works on a live instance and on a static site.

---

## 1. Static site export

```bash
# from the directory the instance scans (state is keyed by the scan roots)
tinkerscope site export ./site --title "weird personas" --workspace "hi + cigarettes"
python3 -m http.server -d ./site 8080      # preview
```

### What a visitor gets

The real playground, minus anything that would sample or write: workspaces, branch
trees, threads and the thread switcher, N-panel comparison, the distribution chart in
all three modes, token probabilities, highlight rules (editable — they're the
analysis tool, and they live in the visitor's own localStorage), pins, the slideshow,
the help modal, keyboard navigation.

### What is hidden, and why

| Hidden | Reason |
|---|---|
| Composer (global + per-panel), send, prefill | Nothing to sample with |
| Row toolbar: regenerate, continue, edit, delete, discard-others, send-branch→panel | Would rewrite the tree |
| Model pickers (shown as plain labels), add/remove panel, `+ Tinker/OpenRouter model` | Changing a panel's model does nothing without sampling |
| Sampling params (temperature / max tokens / samples / thinking / advanced) | These are the LIVE params, not the ones that produced the baked turns — each turn's **Raw** view carries those. Showing them invites reading them as provenance |
| Workspace new / rename, dataset peek, refresh models, stop-all | Server-side operations |
| The "sampling unavailable" banner, the `live` status dot | Restating the obvious; there is no bus to be connected to |

**Kept** on every row: `Raw`, copy message, copy workspace, copy node id, bookmark,
and sample-select (navigation, not mutation). A workspace the visitor installs from a
pack link also keeps its delete button — that one is theirs.

### Size — read this before publishing

Per-token logprobs are ~97% of a real store's bytes. Measured on a 25-workspace
instance: **24 MB of light bodies, 901 MB of blobs**, one workspace accounting for
665 MB alone. The exporter prints a per-workspace breakdown and warns past 100 MB.

Three ways to stay small, in order of preference:

- `--workspace NAME` (repeatable) — publish the two or three that make the point.
- `--no-logprobs` — drops the token inspector and the chart's *first token* mode;
  everything else survives. Typically a 30–40× reduction.
- `--no-pins`.

### What a filtered export deliberately does NOT publish

`--workspace X` means *publish only X*, and two instance-wide stores would otherwise
leak past it:

- **Pins are dropped** (pass `--pins` to force them back). A pin is a saved sample —
  question, response, reasoning, and the `dataset_path` it came from — and carries no
  workspace id, so it cannot be scoped per-workspace. An unfiltered export still
  includes them and says so, because that path is "publish everything".
- **`chart_view` is narrowed** to the exported workspace ids. The mirrored blob holds
  up to 40 workspaces and its records name ids and carry `ftAdded` token strings. The
  author's *global* picks stay — those are a viewing preference, not content.

Both were found by review, probing the exact curated-publish flow this page
recommends; `tests/test_site_export.py` pins them.

### On-disk layout

Each `data/*.json` is byte-shaped like the endpoint it stands in for, so the
frontend's static transport has no special cases and the wire contract stays
single-sourced (see `docs/API_CONTRACT.md`).

```
site/
├── index.html          # rewritten: relative asset refs + the static manifest
├── _app/ favicon.svg   # the SPA, copied verbatim
├── .nojekyll           # GitHub Pages serves nothing under _app/ without it
└── data/
    ├── manifest.json                    # informational twin of the injected global
    ├── health.json  models.json  state.json  prefs.json
    ├── tinker-models.json  openrouter-models.json
    ├── highlights.json  pins.json
    ├── workspaces.json                  # ≡ GET /api/workspaces (the summary index)
    └── workspaces/
        ├── <id>.json                    # ≡ GET /api/workspaces/<id> (light body)
        └── <id>.blobs/<node>.json       # ≡ POST …/node-blobs, one file per node
```

The index lives at `data/workspaces.json`, *outside* `data/workspaces/`, so a
workspace whose id is literally `index` can't collide with it.

### The two index.html rewrites

Both are needed for a GitHub Pages **project** site (`user.github.io/repo/`), and
both are verified by `tests/small-smokes/browser_static_site.py`, which serves from a
`/repo/` subpath precisely so a root-only deploy can't mask a regression.

1. **Absolute asset refs → relative.** SvelteKit's SPA fallback emits `/_app/…`,
   which 404s under a subpath.
2. **The router's base path, computed at runtime.** Relative assets alone are not
   enough: with `base: ""` the client router tries to match `/repo/` against the
   app's only route (`/`) and throws `Not found: /repo/` before rendering anything.
   The exporter replaces the literal with
   `new URL(".", location.href).pathname.replace(/\/$/, "")`.

Computing it at runtime (rather than a `--base` rebuild) is what lets ONE exported
artifact work at the origin root and at any subpath. If SvelteKit's bootstrap shape
ever changes, the export FAILS loudly rather than shipping a site that 404s itself.

### How the frontend knows

`index.html` gets `<script>window.__TSCOPE_STATIC__ = {…}</script>`. Detection is
synchronous at module init (`lib/static-mode.ts`) because `lib/api.ts` must pick its
transport before any consumer touches it — an async probe would race that. A live
instance never defines the global, so the cost there is zero.

Writes go to localStorage, namespaced per site (`tscope-static:<site>:…`) because one
`github.io` origin hosts many. **Baked workspaces are immutable**: a write targeting
one is accepted and dropped, so an incidental layout normalization can't permanently
shadow the published content with something worse. Workspaces the visitor *installs*
live in the overlay and do persist.

### Chart view state travels

The distribution chart's per-workspace view (mode, match scope, thinking filter,
picked turn, excluded rule chips, first-token exclusions / merges / added tokens) is
localStorage-backed in `lib/chart-view.ts` — which the Python exporter cannot read.
So it is **mirrored into server prefs** under `chart_view` (debounced), and
`prefs.json` is baked whole. A published site therefore opens the chart bucketed the
way its author left it.

Merge rule on load: a browser that has never charted takes the published view
wholesale; anything local always wins. So a visitor's own tweaks survive, and a fresh
visitor sees the author's setup.

It is still absent from the workspace record, and therefore from share packs — a pack
is content, this is a viewing preference.

### Publishing to GitHub Pages

```bash
tinkerscope site export ./site --title "my demo" --workspace "the good one"
cd site && git init && git add -A && git commit -m "tinkerscope demo"
gh repo create my-demo --public --source=. --push
gh api -X POST repos/:owner/my-demo/pages -f source[branch]=main -f source[path]=/
```

Notes: `.nojekyll` is written for you. Fonts and KaTeX CSS load from public CDNs, so
a fully offline copy loses those (nothing else). Nothing in a site export carries an
API key — there is no key to carry.

---

## 2. `?w=` takes a pack link

A share pack used to be a launch-time flag (`tinkerscope --pack <file|url>`), so
sharing a setup meant "restart your server with this file". Now the pack IS the link:

```
http://localhost:8765/?w=/home/me/packs/demo.yaml
http://localhost:8765/?w=https://raw.githubusercontent.com/u/r/main/demo.yaml
https://u.github.io/demo/?w=https://raw.githubusercontent.com/u/r/main/demo.yaml
                                                        …&open=pack-demo-the-good-one
```

### Telling an id from a source

A workspace id matches `^[A-Za-z0-9_-]+$` (`workspace_store._SAFE_ID`), so **a value
containing `/`, `:` or `.` cannot be an id**. That makes the extension fully
back-compatible — every previously valid `?w=` still resolves as an id — and needs no
second query param. The rule is `lib/pack-source.ts` `isPackSource`, pinned by
`pack-source.test.ts`.

### Who reads the file

- **Live instance** → `POST /api/pack/apply` (`api/routes/packs.py`), reusing
  `pack.load_pack` + `pack.apply_pack`. This is the only way a local **filesystem
  path** can be read at all.
- **Static site** → the browser fetches and parses it (`js-yaml`, dynamically
  imported so it stays out of the main bundle) and installs into the localStorage
  overlay. A filesystem path is refused with an explanation rather than silently
  doing nothing. Cross-origin fetch works for GitHub-hosted packs:
  `raw.githubusercontent.com` sends `access-control-allow-origin: *`.

### Collisions

Pack workspace ids are deterministic (`pack-<pack-slug>-<workspace-slug>`), so
re-opening a link lands on the same ids. The install is two-phase: a preview reports
which ids exist, then a prompt asks.

- **Replace** — `on_conflict=overwrite`, the historical idempotent behavior.
- **Keep both** — `on_conflict=new`: the incoming copy is renamed `<name> (2)`, which
  gives it a fresh id, and the existing workspace is untouched. A third open gives
  `(3)`; it never stacks into `(2) (2)`, and `x (5)` continues to `x (6)` rather than
  restarting at `(2)`.

**Only an ID collision renames.** Renaming because a display *name* is taken would
fork a workspace off its canonical id while that id stayed free — a later open would
read as never-installed and `&open=<canonical-id>` would miss. Names aren't unique
anywhere else in the app either. Both halves of this rule are one function,
`bumpUntilFree` in `lib/pack-source.ts`, mirrored by `_dedupe_conflicting` in
`pack.py`; a review caught them diverging on both counts, and tests on both sides now
pin it.

**The prompt is unconditional** — a non-colliding pack asks too, with a plain
Install/Cancel. A pack link installs on plain *navigation*, and any web page can
navigate a browser to a `localhost` URL (the API's CORS allowlist guards fetches, not
navigation). A silent install would let a third party plant workspaces whose
transcripts read as though your own checkpoints produced them. One click, with the
source named by host, closes that.

After installing, the URL is rewritten to the plain `?w=<id>` (and `open=` dropped),
so a reload is a normal open and never a re-install.

`&open=<workspace-id>` picks which of a multi-workspace pack lands open; an unknown
id opens the first and says so.

### Trust

A pack is data, never code — models, params, workspace trees. But "you can just
delete it" undersells the risk: the content it installs is *conversations*, which
once in your sidebar look exactly like turns your own checkpoints produced. That's
why the prompt is unconditional and names the source host — installs happen on
navigation, which no CORS policy governs.

The local-path branch is server-side by necessity, and deliberately not sandboxed to
the scan roots: it's the same read `--pack /any/path.yaml` already does, from a
different trigger, on a single-user tool bound to loopback.

---

## Where the code lives

| Concern | File |
|---|---|
| Static-mode detection, data root, localStorage overlay | `web/src/lib/static-mode.ts` |
| The baked-JSON backend (mirrors `ApiClient`) | `web/src/lib/api-static.ts` |
| Heavy-field split (browser mirror of `workspace_store.split_node`) | `web/src/lib/node-split.ts` |
| Id-vs-source discriminator + `(2)` naming (pure) | `web/src/lib/pack-source.ts` |
| Pack loading / install orchestration | `web/src/lib/pack-install.ts` |
| The collision prompt | `web/src/lib/PackInstallModal.svelte` |
| Chart-view mirror + merge | `web/src/lib/chart-view.ts` |
| Site exporter | `src/tinkerscope/site_export.py` |
| `POST /api/pack/apply` | `src/tinkerscope/api/routes/packs.py` |
| `preview_pack` / `apply_pack(on_conflict=…)` | `src/tinkerscope/pack.py` |
| CLI (`tinkerscope site export`) | `src/tinkerscope/serve.py` |

Tests: `web/src/lib/{pack-source,chart-view}.test.ts`,
`tests/test_pack_apply_route.py`, `tests/small-smokes/browser_static_site.py`
(self-contained: builds a state dir, exports, serves at a subpath, drives it),
`tests/small-smokes/browser_pack_link.py` (live instance, local path).
