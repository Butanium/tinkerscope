# Releasing tinkerscope

Versioning exists here for one reason: this tool owns **persisted user data**
(workspaces, their branch trees, blobs, prefs, packs) on a machine where the
server, the CLI and the browser bundle all move together. A version + tag is how
a future reader — or a collaborator consuming a share pack — knows which on-disk
shape they are looking at, and which upgrade needed a migration.

## Scheme

Semver, on the DATA + WIRE contract, not on the UI:

| bump | when |
|---|---|
| **major** | the wire or on-disk shape changes incompatibly — old clients / old state need a migration. A major release MUST ship a migration path (automatic where possible) and a `docs/MIGRATIONS.md` entry. |
| **minor** | new capability, back-compatible (a new endpoint, a new panel feature, a new CLI command). |
| **patch** | fixes and internals with no contract change. |

Back-compat is a cost, and on a single-user tool with an editable install it is
usually not worth paying: **a major bump is the licence to drop it**, provided
the migration is written down and the data is converted (or convertible). That is
exactly the deal for the `conversations → workspaces` rename.

## Cutting a release

1. Land the work; `uv run pytest -q`, `uv run ruff check`, `npm test`,
   `npm run check` (0 errors), `scripts/smoke.sh` (the short state/wire set), and
   click through what changed on a `scripts/dev-isolated.sh` instance, reading the
   screenshots. No full browser-smoke sweep: it never caught anything at release
   time (ENGINEERING_LOGS 2026-10-01).
2. Bump `version` in `pyproject.toml`.
3. Add the `docs/MIGRATIONS.md` entry if the shape moved (what changed, what the
   automatic migration does, how to roll back — name the backup path it writes).
4. Commit, then tag:

   ```bash
   git tag -a v1.0.0 -m "workspaces rename: wire + disk"
   ```

5. `uv tool install -e .` if the local instance needs it (it is editable, so a
   backend change only needs the :8767 process restarted — see CLAUDE.md
   "Deploys").
6. Publish to PyPI (below).

## Publishing to PyPI

```bash
rm -rf dist && uv build && uv publish
```

`uv build` runs `hatch_build.py`, which needs **node/npm** — it compiles `web/`
and stages it at `src/tinkerscope/web_dist/` so the wheel is a self-contained
single-process app. Check `dist/*.whl` actually carries `web_dist/index.html`
before uploading; a wheel without it installs fine and then serves nothing.

Two things are deliberately NOT in the published metadata:

- **`[tool.uv.sources]` does not travel.** It is uv-only dev metadata (and PyPI
  forbids direct URLs anyway), so the fork pin on `tinker-cookbook` is a LOCAL
  override — PyPI users resolve upstream from PyPI and therefore have no
  `tml_v0_disable_thinking` renderer until PR #839 lands. That degrades rather
  than breaks: `supports_thinking` falls through to the `tml*` effort-directive
  branch. Once the PR merges, pin `tinker-cookbook>=<that version>` here and
  drop the source override.
- **The README's links are repo-relative**, so the screenshots and doc links do
  not render on the PyPI project page. Known and accepted — the page is a
  pointer to GitHub, not the docs.

## History

Per-release detail lives in `CHANGELOG.md`; this table is the one-line "what
shape does this version mark" index.

| version | what it marks |
|---|---|
| `v1.4.1` | hotfix: the page reconnects its live stream itself after a server restart (a browser behind `ssh -L` never retried). Browser-only; no wire or disk change. |
| `v1.4.0` | sample liveness: per-request polling, the tinker status beside "waiting on the model", reconnect + resend on 3 min of silence or a lost request; a foldable sidebar. Additive: one SSE event (`status`), one bus event (`chat_status`); disk untouched. |
| `v1.3.0` | CLI conveniences + sampling fidelity: `--timeout` on the sampling verbs (the tinker sample call itself is unbounded again), `tinkpg open` for any model selector, `battery --ws/--new-ws`, `pack export --highlights`, `running_chats` on the bus, the waiting-on-the-model readout. Additive: one SSE event (`start`), one state field; disk untouched. |
| `v1.2.0` | vLLM as a native model kind (`vllm:<name>`) + `serve --multi-user` (one state bus per session). Additive: disk untouched, and a single-user instance's wire is byte-identical to 1.1.0. |
| `v1.1.0` | **server-authoritative trees** — all mutation as ops on `POST /api/workspaces/{id}/ops` with a per-workspace `rev`, chats folded server-side, the bus transcript echo removed. WIRE moved (old browser bundles must reload); one CLI app (`tinkerscope` / `tinkpg`). |
| `v1.0.0` | **first PyPI release** — `uv tool install tinkerscope`. Packaging metadata + the install strings the tool hands out (README, `?` modal, the static site's open-locally panel). No wire or disk change. |
| `v0.9.0` | the `conversations → workspaces` rename, wire + disk (see `docs/MIGRATIONS.md`). Clean cut: old routes 404, old field names rejected; only `?c=` is still read. Tagged `v1.0.0` until 2026-08-05, when that version was reassigned to the PyPI release — this one never shipped anywhere but this box. |
| `v0.1.0` | the pre-rename tool: auto-discovery, branching, N-panel compare, storage v2, share packs, token probs — everything up to and including the workspace-scoping fix for the cross-tab layout clobber. The wire and disk still say `conversation`. |
