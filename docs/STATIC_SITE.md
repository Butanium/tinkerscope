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

Writes go to an **IndexedDB** overlay (`lib/overlay-store.ts`), namespaced per site by
**slug + URL path** (`tscope-static:<site>@<path>:…`). The path matters: one `github.io`
origin hosts many exports, and `<site>` is only a slug of the title — two sites both
titled "demo" would otherwise share an overlay, so a visitor's installs and edits on one
would surface in the other. Two sites can't occupy the same path, so the path
disambiguates for free. **Baked workspaces are immutable**: a write targeting one is
accepted and dropped, so an incidental layout normalization can't permanently shadow the
published content with something worse. Workspaces the visitor *installs* live in the
overlay and do persist.

#### Why not localStorage (it was, until 2026-07-30)

localStorage caps at about **5 MB per origin** — measured 4.98 MB in headless Chromium
on this box. One real workspace body is 12.3 MB with its logprobs *already stripped*, so
installing a pack of any consequence was impossible. And it failed quietly: the write
threw, `writeLocal` caught and console-warned it, and then every read came back through
the same store, so the workspace opened **empty**. IndexedDB reports a 6442 MB quota on
the same origin and round-trips a 37.6 MB workspace-shaped payload in 484 ms write /
277 ms read.

The awkward part is that IndexedDB is async while the ~30 read sites in `api-static.ts`
are sync, so the overlay is an in-memory map hydrated **once** at startup and
authoritative thereafter; writes update it immediately and flush in the background.
Every entry point into `staticApi` awaits that hydrate through a single wrapper
(`gated`) rather than 30 individual `await`s — the list only has to be wrong once, and
the failure mode (workspaces missing on a cold load) is a race that wouldn't reproduce
locally. Any pre-existing localStorage overlay is adopted on first hydrate and left in
place, since a visitor may still load an older cached bundle.

Pinned by `tests/small-smokes/browser_pack_big.py`, which installs a pack far past the
old ceiling and asserts it survives a reload — the reload being the part that used to
fail.

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
  imported so it stays out of the main bundle) and installs into the overlay.
  Cross-origin fetch works for GitHub-hosted packs: `raw.githubusercontent.com` sends
  `access-control-allow-origin: *`.
  A non-http `?w=` value is **resolved against `document.baseURI` and fetched** rather
  than refused — a static site has no filesystem, so the only thing such a value can be
  is a relative URL (`?w=./demo.yaml.gz` for a pack published beside its viewer). A real
  filesystem path simply 404s, and the error points at the file picker. The live
  instance keeps the opposite rule, where non-http genuinely means a path on disk.
- **A file the visitor picks or drops** (static only) → read straight off their disk,
  no hosting and no CORS. This is what lets a published site act as a general reader for
  someone else's export; a live instance doesn't need it, since `?w=<path>` already
  reads the filesystem, and passing a `File` there is refused rather than half-supported
  (it would write to an overlay a live instance never reads).

Both fetch paths **un-gzip transparently**, sniffing the gzip magic bytes rather than the
extension, so a `pack export --logprobs` pack — which has to be compressed to clear
GitHub's 100 MB file limit — opens like any other. That uses the browser's native
`DecompressionStream`, so it adds no dependency.

### The load reports progress

A pack link installs on plain NAVIGATION, and an 18 MB gzipped export takes tens of
seconds. Before `PackLoadingModal.svelte`, that read as a broken site: the visitor's
first frame was whatever workspace happened to be newest, under a "workspace not found
— opened the most recent one instead" banner, and then it silently swapped when the
pack landed. Now a `?w=` the app is about to install suppresses that banner (the URL is
not a missing workspace, it's a pending one) and raises a modal reporting
`fetch → decode → parse → install`, with real byte counts while downloading.

Two things about it that look like details and aren't:

- **Each phase yields a macrotask before doing its work** (`report()` in
  `pack-install.ts`). `parse` and `install` block the main thread for seconds on a large
  pack, so without the yield the label would only paint once the work it names was over.
- **`content-length` is a hint, not a total.** If the host applies `content-encoding`,
  the stream reader hands back DECODED bytes measured against a COMPRESSED length, so
  the modal treats `done > total` as "no total" and goes indeterminate rather than
  showing 240%.

### `--pack-link`: making a published `?w=<id>` shareable

After an install the URL is rewritten to the tidy `?w=pack-<pack>-<ws>`, which is the
thing a reader will naturally copy and send. On its own that link resolves **only in the
browser that already installed the pack** — anyone else's overlay has no such workspace,
and they land on the not-found fallback.

So a site can publish where each workspace comes from:

```bash
tinkerscope site export ./site --pack-link ./demo.yaml.gz=https://…/demo.yaml.gz
tinkerscope site export ./site --pack-link https://…/demo.yaml.gz   # already uploaded
```

The exporter LOADS each pack to enumerate the ids it will mint (`pack_workspace_ids`,
the same function the installer uses — a hand-written map would drift silently, since a
wrong id is indistinguishable from a workspace nobody has) and writes
`manifest.pack_links = {id: url}`. An unknown `?w=<id>` then resolves through that map
and installs, opening the workspace that was actually asked for. The ids are printed at
export time, because they are derived and nothing else tells you what they came out as.

`PATH=URL` is for a pack you haven't uploaded yet: the path is read for the ids, the URL
is what visitors fetch. A bare `https://…` spec is never split, so a URL carrying `?k=v`
survives. One `--pack-link` and no `--pack-url` implies the latter.

⚠️ The resolution is gated on the workspace list having LOADED (`wsLoaded` in
`+page.svelte`). The `?w=` effect first runs at mount, when an empty `ws.list` makes
"missing" and "not fetched yet" identical — ungated, every visit to an installed pack
link re-downloads and re-installs it. `browser_pack_link_map.py`'s second-visit check is
what caught that.

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

**When the prompt appears depends on the mode**, and the split is deliberate
(`canInstallUnprompted` in `+page.svelte`):

| | nothing overwritten | a collision |
|---|---|---|
| **static site** | installs, no prompt | asks: replace / keep both |
| **live instance** | asks: Install / Cancel | asks: replace / keep both |

A collision always asks, everywhere — overwriting destroys a workspace that is already
here, which is a data question rather than a trust one.

The mode split is about what an install can *reach*. On a live instance `?w=<path>` makes
the SERVER read the filesystem and the result lands in the real on-disk state dir among
actual research workspaces — something a visitor's browser could not otherwise do, and
any page can navigate a browser to a `localhost` URL (the CORS allowlist guards fetches,
not navigation). On a static site the install lands in that site's own IndexedDB
namespace, is deletable, and cannot touch the baked workspaces.

After installing, the URL is rewritten to the plain `?w=<id>` (and `open=` dropped),
so a reload is a normal open and never a re-install.

`&open=<workspace-id>` picks which of a multi-workspace pack lands open; an unknown
id opens the first and says so.

### Getting out of read-only

A published site raises exactly one question it can't act on — *how do I sample these
myself* — so the read-only badge is a button that answers it (`OpenLocallyModal.svelte`).
The answer is a `uvx … tinkerscope --pack <url>` command, which needs a pack URL:

- **`site export --pack-url <url>`** bakes one into the manifest, for the workspaces the
  site ships;
- a workspace the visitor installed from a `?w=<url>` link carries its own, recorded at
  install time under the overlay key `ws.source.<id>` (deliberately NOT a field on the
  Workspace body — that shape is the wire/disk contract shared with the live server, and
  provenance is static-only). Per-workspace wins, since one site can host several packs'
  worth of content.

With **no** URL known the panel says the command starts an *empty* tinkerscope and won't
reproduce the page, rather than printing something that looks like it should work. That
asymmetry is what `browser_open_locally.py` pins, by exporting the same site twice.

Each panel also carries a **copy-its-identity** button beside the model name: a `ckpt:`
panel gives its `tinker://…/sampler_weights/…` path, a `base:` panel its model id — the
string you'd paste into your own script. Covering base models matters because a published
workspace is often *entirely* base models, where a checkpoint-only button would look
absent rather than inapplicable (caught on the live deploy, not by the smoke, whose
fixture had only a checkpoint). Not offered for a discovered run — its id is
scan-dir-relative and means nothing elsewhere — nor for OpenRouter, whose id is already
shown in full below. It replaced a `· loose sampler` suffix: jargon for "no run dir behind
this", true of every checkpoint in a pack or a published site, and it duplicated the model
name on a second line.

### Trust

A pack is data, never code — models, params, workspace trees — and that claim is load-
bearing enough to have been checked rather than assumed: message content is `<`/`>`-
escaped before `marked` runs (`highlight-render.ts` `renderMarkdown`), so nothing in a
pack reaches the DOM as markup.

What remains is **attribution, not compromise**. The content is *conversations*, which
once in a sidebar look exactly like turns your own checkpoints produced, and nothing in
the app records who authored a workspace. So a third party can point your published
viewer at their pack and borrow your domain.

That was judged not worth a modal on a static site (Clément, 2026-07-30) — an attacker
can publish their own static tinkerscope with the same fabricated content just as easily,
and the pack URL sits in the address bar throughout, so the modal was buying very little.
It is worth one on a live instance, where the install reaches the real state dir.

A **query parameter** to skip the prompt was considered and rejected: whoever writes the
URL would control it, attacker included, so it would delete the check rather than
configure it. If a live instance ever needs an opt-out it has to be author-controlled
(an export-time / launch-time setting), never link-controlled.

The local-path branch is server-side by necessity, and deliberately not sandboxed to
the scan roots: it's the same read `--pack /any/path.yaml` already does, from a
different trigger, on a single-user tool bound to loopback.

---

## Where the code lives

| Concern | File |
|---|---|
| Static-mode detection, data root, overlay namespace + hydrate | `web/src/lib/static-mode.ts` |
| The IndexedDB overlay itself (map + flush + quota) | `web/src/lib/overlay-store.ts` |
| The baked-JSON backend (mirrors `ApiClient`) | `web/src/lib/api-static.ts` |
| Heavy-field split (browser mirror of `workspace_store.split_node`) | `web/src/lib/node-split.ts` |
| Id-vs-source discriminator + `(2)` naming (pure) | `web/src/lib/pack-source.ts` |
| Pack logprob encoding, mirror of `pack.py::restore_logprobs` (pure) | `web/src/lib/pack-logprobs.ts` |
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
