// Pure unit tests for reasoning.ts + the tree's handling of a closed model's
// reasoning payload — run with Node's TS type-stripping:
//   node web/src/lib/reasoning.test.ts

import { thinkingKind, isSealed, thinkingHeader, encryptedNote, itemsThinkingKind } from './reasoning.ts';
import { foldAssistant, editAssistant, editedItems, ancestryMessages, editInputFork, activePath, type ConvTree } from './tree.ts';
import type { ReasoningDetail } from './types.ts';

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
function ok(cond: boolean, msg = 'expected true'): void {
  if (!cond) throw new Error(msg);
}
function eq(a: unknown, b: unknown, msg = ''): void {
  const sa = JSON.stringify(a);
  const sb = JSON.stringify(b);
  if (sa !== sb) throw new Error(`${msg} expected ${sb} got ${sa}`);
}

// The shape openai/gpt-6-luna returned (2026-10-15).
const LUNA: ReasoningDetail[] = [
  { type: 'reasoning.summary', summary: '**Factoring**', format: 'openai-responses-v1', index: 0 },
  { type: 'reasoning.encrypted', data: 'gAAAA…', format: 'openai-responses-v1', id: 'rs_1', index: 1 }
];

test('kinds', () => {
  eq(thinkingKind(LUNA), 'summary');
  eq(thinkingKind([{ type: 'reasoning.text', text: 't', signature: 'sig' }]), 'signed');
  eq(thinkingKind([{ type: 'reasoning.encrypted', data: 'x' }]), 'encrypted');
  eq(thinkingKind([{ type: 'reasoning.text', text: 'plain', format: 'unknown' }]), null);
  eq(thinkingKind(undefined), null);
  ok(isSealed(LUNA) && !isSealed([]));
});

test('label', () => {
  eq(thinkingHeader(1234), 'Thinking · 1,234 tokens');
  eq(thinkingHeader(undefined), 'Thinking');
  eq(encryptedNote(103), 'encrypted (103 tokens)');
  eq(encryptedNote(undefined, 'id only'), 'encrypted (id only)');
});

function lunaTree(): { t: ConvTree; aid: string } {
  const t0: ConvTree = {
    nodes: { u: { id: 'u', role: 'user', content: 'q', parent: null, children: [] } },
    rootChildren: ['u'],
    selected: { __root__: 'u' }
  };
  const { tree, ids } = foldAssistant(t0, 'u', [
    { content: 'ans', reasoning: '**Factoring**', reasoning_details: LUNA, reasoning_tokens: 18 }
  ]);
  return { t: tree, aid: ids[0] };
}

test('the payload reaches the next fire', () => {
  const { t, aid } = lunaTree();
  const msgs = ancestryMessages(t, aid);
  eq(msgs[1].reasoning_details, LUNA);
});

test('edit that leaves the thinking alone keeps the payload', () => {
  const { t, aid } = lunaTree();
  const r = editAssistant(t, aid, 'edited answer', '**Factoring**')!;
  eq(r.tree.nodes[r.newId].reasoning_details, LUNA);
  eq(r.tree.nodes[r.newId].reasoning_tokens, 18);
});

test('drop thinking drops the payload', () => {
  const { t, aid } = lunaTree();
  const r = editAssistant(t, aid, 'ans', '')!;
  const n = r.tree.nodes[r.newId];
  ok(n.reasoning === undefined && n.reasoning_details === undefined && n.reasoning_tokens === undefined);
});

test('rewritten thinking drops the payload (it would not be what the model saw)', () => {
  const { t, aid } = lunaTree();
  const r = editAssistant(t, aid, 'ans', 'my own words')!;
  ok(r.tree.nodes[r.newId].reasoning_details === undefined);
});

test('a fork-copy keeps the payload with its text', () => {
  const { t } = lunaTree();
  const r = editInputFork(t, 'u', 'q, reworded', { role: 'user', copyDownstream: true })!;
  const path = activePath(r.tree);
  eq(path[1].reasoning_details, LUNA);
});

// A Responses-wire turn: reasoning (summary over an encrypted blob), text, a call.
const RS = { id: 'rs_1', type: 'reasoning', summary: [{ type: 'summary_text', text: '**Plan** reply' }], encrypted_content: 'gAAA' };
const MSG = { id: 'msg_1', type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: 'hi', annotations: [] }] };
const FC = { id: 'fc_1', type: 'function_call', call_id: 'c1', name: 'send', arguments: '{}' };

test('a Responses row reads as summary / encrypted thinking', () => {
  eq(itemsThinkingKind([RS, MSG]), 'summary');
  eq(itemsThinkingKind([{ ...RS, summary: [] }, MSG]), 'encrypted');
  eq(itemsThinkingKind([{ id: 'rs_2', type: 'reasoning', summary: [] }]), 'encrypted'); // id only
  eq(itemsThinkingKind([MSG]), null);
});

test('editing a Responses turn swaps its text, keeps or drops the reasoning item', () => {
  eq(editedItems([RS, MSG, FC], 'hello', true), [RS, { role: 'assistant', content: 'hello' }, FC]);
  eq(editedItems([RS, MSG, FC], 'hello', false), [{ role: 'assistant', content: 'hello' }, FC]);
  // no message before: the new text goes before the call; empty text adds none
  eq(editedItems([RS, FC], 'said', true), [RS, { role: 'assistant', content: 'said' }, FC]);
  eq(editedItems([RS, MSG], '', true), [RS]);
});

test('editAssistant on a Responses turn carries the edited items', () => {
  const u = foldAssistant({ nodes: { u: { id: 'u', role: 'user', content: 'q', parent: null, children: [] } }, rootChildren: ['u'], selected: {} } as ConvTree,
    'u', [{ content: 'hi', reasoning: '**Plan** reply', reasoning_tokens: 9 }]);
  const a = u.ids[0];
  const kept = editAssistant(u.tree, a, 'hello', '**Plan** reply', undefined, false, [RS, MSG])!;
  eq(kept.tree.nodes[kept.newId].items, [RS, { role: 'assistant', content: 'hello' }]);
  eq(kept.tree.nodes[kept.newId].reasoning_tokens, 9);
  const dropped = editAssistant(u.tree, a, 'hello', '', undefined, false, [RS, MSG])!;
  eq(dropped.tree.nodes[dropped.newId].items, [{ role: 'assistant', content: 'hello' }]);
  eq(dropped.tree.nodes[dropped.newId].reasoning, undefined);
});

// A top-level throw exits node non-zero (no @types/node / process needed).
if (failed) throw new Error(`\n${failed} failed / ${passed + failed}\n${fails.join('\n')}`);
console.log(`reasoning.test.ts: ${passed} passed`);
