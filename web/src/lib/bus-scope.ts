// Which PlaygroundState fields belong to the OPEN WORKSPACE, and which are
// process-global. PURE — no Svelte imports (bus-scope.test.ts runs it under node).
//
// The state bus (src/tinkerscope/api/state.py) is ONE PlaygroundState per server
// PROCESS. That is what makes `tinkpg` drive what you are looking at, and it was
// right when a workspace was just "the panels on screen". Since then a workspace
// gained its own persisted identity: its panel layout, per-panel
// models and system prompt are saved with it and restored on open. A per-workspace
// value in a process-global slot is a clobber waiting to happen, and it happened:
//
//   tab A opens workspace X → pushes X's layout onto the bus → tab B (on workspace
//   Y) mirrors the bus → B now shows X's models → B's syncPanels sees X's panel ids
//   as new, calls save() → 400ms later Y is persisted on disk with X's models.
//
// No user action needed on the losing tab; the CLI could do it too. Four of the
// author's workspaces were corrupted this way before it was diagnosed (2026-07-24).
//
// The rule: every bus message is stamped with the workspace it describes
// (`workspace_id`), and a client adopts the WORKSPACE-SCOPED fields only when
// that stamp is its own. Sampling params stay deliberately global — one knob for
// every panel and every client, which is the whole point of the shared bus.
//
// See docs/API_CONTRACT.md §"Workspace scoping on the state bus".

import type { PlaygroundState } from './types.ts';

/** Fields of PlaygroundState that describe the OPEN WORKSPACE (persisted with the
 *  workspace), as opposed to the process-global sampling params / chat lifecycle.
 *  `panels` carries the per-panel run_id/checkpoint AND the transcript echoes, so
 *  the whole array is workspace-scoped. */
export const WORKSPACE_FIELDS = [
  'panels',
  'workspace_id',
  'system_prompt',
  'system_enabled'
] as const;

/**
 * Fold an incoming bus snapshot into this client's mirror.
 *
 * Adopt everything when the incoming state is OURS (or when there is nobody to
 * protect); otherwise keep our own workspace-scoped fields and take only the
 * global ones, so another tab's workspace can never be rendered — or, worse,
 * persisted — as if it were ours.
 *
 * @param mine      current mirror (null before the first snapshot)
 * @param incoming  the state the server just pushed
 * @param myId      the workspace this client has open (null = none yet)
 */
export function mergeBusState(
  mine: PlaygroundState | null,
  incoming: PlaygroundState,
  myId: string | null
): PlaygroundState {
  // Bootstrap: nothing of our own to keep.
  if (!mine) return incoming;
  // No workspace open yet (initial load, before the workspace store settles) —
  // the bus is all we know, and restoreSession/#loadTrees will assert ours shortly.
  if (myId == null) return incoming;
  // UNSTAMPED incoming: a fresh process, an older client, or a CLI patch that only
  // touched params. Nobody is claiming a different workspace, so it is ours to take;
  // this is what keeps `tinkpg open <run>` driving the browser.
  if (incoming.workspace_id == null) return incoming;
  if (incoming.workspace_id === myId) return incoming;
  // Someone else's workspace is on the bus. Take the global fields, keep ours.
  const merged = { ...incoming } as PlaygroundState;
  for (const f of WORKSPACE_FIELDS) (merged as Record<string, unknown>)[f] = mine[f];
  return merged;
}

/** True when `patch` writes any workspace-scoped field, i.e. it must carry a
 *  `workspace_id` stamp so other clients can tell whose workspace it describes.
 *  The per-panel sub-patch keys (`panel`+run_id/checkpoint/messages/…) and the
 *  bulk echo maps count — they all mutate `panels`. */
export function touchesWorkspace(patch: Record<string, unknown>): boolean {
  return (
    'panels' in patch ||
    'panel_messages' in patch ||
    'panel_thread_system' in patch ||
    'system_prompt' in patch ||
    'system_enabled' in patch ||
    'panel' in patch
  );
}
