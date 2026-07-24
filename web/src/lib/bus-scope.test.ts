// Pure unit tests for bus-scope.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/bus-scope.test.ts
// Exit code != 0 on failure.

import { mergeBusState, touchesWorkspace, WORKSPACE_FIELDS } from './bus-scope.ts';
import type { PanelState, PlaygroundState } from './types.ts';

let passed = 0;
let failed = 0;
const fails: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    fails.push(`✗ ${name}\n    ${(e as Error).message}`);
  }
}
function eq(a: unknown, b: unknown, msg = ''): void {
  const sa = JSON.stringify(a);
  const sb = JSON.stringify(b);
  if (sa !== sb) throw new Error(`${msg} expected ${sb} got ${sa}`);
}
function ok(cond: boolean, msg = 'expected true'): void {
  if (!cond) throw new Error(msg);
}

function panel(id: string, run: string | null): PanelState {
  return { id, run_id: run, checkpoint: 'final', messages: [] };
}
function state(convId: string | null, panels: PanelState[], over: Partial<PlaygroundState> = {}): PlaygroundState {
  return {
    panels,
    conversation_id: convId,
    system_prompt: null,
    system_enabled: null,
    temperature: 1.0,
    max_tokens: 1024,
    n_samples: 1,
    thinking: false,
    top_p: null,
    chat_id: 0,
    running: false,
    last_event: null,
    last_event_ts: 0,
    ...over
  };
}

const MINE = state('ws-mine', [panel('primary', 'inkling'), panel('p-2', 'inkling-2')], {
  system_prompt: 'mine'
});
const THEIRS = state('ws-theirs', [panel('primary', 'deepseek'), panel('p-5', 'nemotron')], {
  system_prompt: 'theirs',
  system_enabled: true,
  temperature: 0.3,
  n_samples: 12,
  chat_id: 7,
  running: true
});

// ── the corruption case ────────────────────────────────────────────────
test('foreign workspace: panel layout is NOT adopted', () => {
  const merged = mergeBusState(MINE, THEIRS, 'ws-mine');
  eq(merged.panels.map((p) => p.id), ['primary', 'p-2'], 'panel ids');
  eq(merged.panels.map((p) => p.run_id), ['inkling', 'inkling-2'], 'models');
  eq(merged.conversation_id, 'ws-mine', 'stamp');
});

test('foreign workspace: system prompt is NOT adopted', () => {
  const merged = mergeBusState(MINE, THEIRS, 'ws-mine');
  eq(merged.system_prompt, 'mine');
  eq(merged.system_enabled, null);
});

test('foreign workspace: GLOBAL params ARE adopted (shared bus is the point)', () => {
  const merged = mergeBusState(MINE, THEIRS, 'ws-mine');
  eq(merged.temperature, 0.3, 'temperature');
  eq(merged.n_samples, 12, 'n_samples');
  eq(merged.chat_id, 7, 'chat_id');
  eq(merged.running, true, 'running');
});

test('foreign workspace: our own state object is not mutated', () => {
  const mine = state('ws-mine', [panel('primary', 'inkling')]);
  mergeBusState(mine, THEIRS, 'ws-mine');
  eq(mine.panels.map((p) => p.run_id), ['inkling']);
  eq(mine.conversation_id, 'ws-mine');
});

// ── the cases that must still adopt ────────────────────────────────────
test('own workspace: adopt wholesale', () => {
  const incoming = state('ws-mine', [panel('primary', 'inkling'), panel('p-3', 'new-one')]);
  const merged = mergeBusState(MINE, incoming, 'ws-mine');
  eq(merged.panels.map((p) => p.id), ['primary', 'p-3'], 'a same-workspace layout change lands');
});

test('unstamped incoming (fresh process / CLI patch): adopt — keeps tinkpg driving', () => {
  const cli = state(null, [panel('primary', 'cli-picked-run')]);
  const merged = mergeBusState(MINE, cli, 'ws-mine');
  eq(merged.panels.map((p) => p.run_id), ['cli-picked-run']);
});

test('no mirror yet (first snapshot): adopt', () => {
  eq(mergeBusState(null, THEIRS, 'ws-mine').conversation_id, 'ws-theirs');
});

test('no workspace open yet: adopt', () => {
  eq(mergeBusState(MINE, THEIRS, null).conversation_id, 'ws-theirs');
});

test('WORKSPACE_FIELDS is exactly what merge protects', () => {
  const merged = mergeBusState(MINE, THEIRS, 'ws-mine') as Record<string, unknown>;
  for (const f of WORKSPACE_FIELDS)
    eq(merged[f], (MINE as Record<string, unknown>)[f], `kept ${f}`);
  for (const k of Object.keys(THEIRS))
    if (!(WORKSPACE_FIELDS as readonly string[]).includes(k))
      eq(merged[k], (THEIRS as Record<string, unknown>)[k], `took ${k}`);
});

// ── stamping predicate ─────────────────────────────────────────────────
test('touchesWorkspace: workspace writes are detected', () => {
  ok(touchesWorkspace({ panels: [] }), 'panels');
  ok(touchesWorkspace({ panel_messages: {} }), 'panel_messages');
  ok(touchesWorkspace({ panel_thread_system: {} }), 'panel_thread_system');
  ok(touchesWorkspace({ system_prompt: 'x' }), 'system_prompt');
  ok(touchesWorkspace({ system_enabled: false }), 'system_enabled');
  ok(touchesWorkspace({ panel: 'p-2', run_id: 'r' }), 'single-panel sub-patch');
});

test('touchesWorkspace: global-only writes are not', () => {
  ok(!touchesWorkspace({ temperature: 0.7 }), 'temperature');
  ok(!touchesWorkspace({ n_samples: 4, thinking: true }), 'params');
  ok(!touchesWorkspace({ conversation_id: 'x' }), 'a bare stamp needs no stamping');
});

console.log(`\nbus-scope: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n\n'));
}
