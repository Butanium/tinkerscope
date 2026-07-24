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

1. Land the work; `uv run pytest -q`, `npm test`, `npm run check` (0 errors),
   and the token-free browser smokes against a `scripts/dev-isolated.sh` instance.
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

## History

| version | what it marks |
|---|---|
| `v0.1.0` | the pre-rename tool: auto-discovery, branching, N-panel compare, storage v2, share packs, token probs — everything up to and including the workspace-scoping fix for the cross-tab layout clobber. The wire and disk still say `conversation`. |
