// Tool use: the tree half (pending calls, result chains, result edits, the wire
// messages) and the tools editor's parser. Run: node web/src/lib/tool-use.test.ts

import {
  __resetIds,
  emptyTree,
  appendTurn,
  foldAssistant,
  pendingToolCalls,
  appendToolResults,
  editToolResult,
  ancestryMessages,
  regenTarget,
  activePath,
  assertValid,
  applyTreeOp,
  heavyNodeIds,
  lightenTree,
  type ConvTree
} from './tree.ts';
import { parseToolDefs, cleanHandlers, offeredCount, defFields, withDefFields } from './tools-def.ts';
import type { ToolCall } from './types.ts';

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

const call = (id: string, name: string, args = '{}'): ToolCall => ({
  id,
  type: 'function',
  function: { name, arguments: args }
});

/** user → assistant(calls) — the shape every test starts from. */
function withCalls(calls: ToolCall[]): { t: ConvTree; asst: string } {
  __resetIds();
  const u = appendTurn(emptyTree(), 'hi');
  const f = foldAssistant(u.tree, u.nodeId, [{ content: '', tool_calls: calls, finish_reason: 'tool_calls' }]);
  return { t: f.tree, asst: f.ids[0] };
}

test('an assistant turn with calls is pending until every call has a result', () => {
  const { t, asst } = withCalls([call('c1', 'a'), call('c2', 'b')]);
  eq(pendingToolCalls(t)?.pending.map((c) => c.id), ['c1', 'c2']);
  eq(pendingToolCalls(t)?.assistantId, asst);
  const r1 = appendToolResults(t, [{ tool_call_id: 'c1', name: 'a', content: 'one' }]);
  eq(pendingToolCalls(r1.tree)?.pending.map((c) => c.id), ['c2'], 'partial answer');
  const r2 = appendToolResults(r1.tree, [{ tool_call_id: 'c2', name: 'b', content: 'two' }]);
  eq(pendingToolCalls(r2.tree), null);
  assertValid(r2.tree);
});

test('results land as a selected chain under the calling turn', () => {
  const { t, asst } = withCalls([call('c1', 'a'), call('c2', 'b')]);
  const r = appendToolResults(t, [
    { tool_call_id: 'c1', name: 'a', content: 'one' },
    { tool_call_id: 'c2', content: 'two' }
  ]);
  const path = activePath(r.tree);
  eq(path.map((n) => n.role), ['user', 'assistant', 'tool', 'tool']);
  eq(r.tree.nodes[r.ids[0]].parent, asst);
  eq(r.tree.nodes[r.ids[1]].parent, r.ids[0]);
  eq(r.tree.nodes[r.ids[1]].name, undefined, 'name omitted when not given');
});

test('wire messages carry tool_calls, tool_call_id and name', () => {
  const { t } = withCalls([call('c1', 'send', '{"content":"x"}')]);
  const r = appendToolResults(t, [{ tool_call_id: 'c1', name: 'send', content: 'ok' }]);
  const msgs = ancestryMessages(r.tree, r.ids[0]);
  eq(msgs[1], { role: 'assistant', content: '', tool_calls: [call('c1', 'send', '{"content":"x"}')] });
  eq(msgs[2], { role: 'tool', content: 'ok', tool_call_id: 'c1', name: 'send' });
});

test('regenerating the reply to a tool result fires from that result', () => {
  const { t } = withCalls([call('c1', 'a')]);
  const r = appendToolResults(t, [{ tool_call_id: 'c1', content: 'ok' }]);
  const f = foldAssistant(r.tree, r.ids[0], [{ content: 'done' }]);
  const rt = regenTarget(f.tree, f.ids[0]);
  eq(rt?.userParentId, r.ids[0]);
  eq(rt?.fireMessages.map((m) => m.role), ['user', 'assistant', 'tool']);
});

test('editing a result forks a sibling answering the same call; later results are not copied', () => {
  const { t } = withCalls([call('c1', 'a'), call('c2', 'b')]);
  const r = appendToolResults(t, [
    { tool_call_id: 'c1', name: 'a', content: 'one' },
    { tool_call_id: 'c2', name: 'b', content: 'two' }
  ]);
  const e = editToolResult(r.tree, r.ids[0], 'ONE')!;
  const n = e.tree.nodes[e.newId];
  eq([n.role, n.content, n.tool_call_id, n.name, n.parent], ['tool', 'ONE', 'c1', 'a', r.tree.nodes[r.ids[0]].parent]);
  eq(pendingToolCalls(e.tree)?.pending.map((c) => c.id), ['c2'], 'the fork waits on c2 again');
  assertValid(e.tree);
});

test('a tool node survives the op layer (role allowed, fields kept)', () => {
  const { t, asst } = withCalls([call('c1', 'a')]);
  const next = applyTreeOp(t, {
    op: 'add_nodes',
    nodes: [{ id: 'tool1', role: 'tool', content: 'ok', tool_call_id: 'c1', name: 'a', parent: asst }],
    select: true
  });
  eq(next.nodes.tool1.tool_call_id, 'c1');
  eq(pendingToolCalls(next), null);
});

test('a node with verbatim items goes out inline, or by node id once light', () => {
  __resetIds();
  const u = appendTurn(emptyTree(), 'hi');
  const items = [{ type: 'reasoning', id: 'rs_1', summary: [], encrypted_content: 'B' }, { type: 'function_call', call_id: 'c1', name: 'f', arguments: '{}' }];
  const f = foldAssistant(u.tree, u.nodeId, [{ content: '', items, tool_calls: [call('c1', 'f')] }]);
  const id = f.ids[0];
  eq(ancestryMessages(f.tree, id)[1].items, items, 'fresh fold: inline');
  const light = lightenTree(f.tree, heavyNodeIds(f.tree))!;
  eq([light.nodes[id].items, light.nodes[id].has_items], [undefined, true]);
  const m = ancestryMessages(light, id)[1];
  eq([m.items, m.items_node], [undefined, id], 'light: by node');
});

test('a computer call is pending until a screenshot result answers it', () => {
  const comp: ToolCall = { id: 'cc1', type: 'computer_call', function: { name: 'computer', arguments: '{"type":"screenshot"}' } };
  const { t } = withCalls([comp]);
  eq(pendingToolCalls(t)?.pending[0].type, 'computer_call');
  const out = [{ role: 'user', content: [{ type: 'input_image', detail: 'high', image_url: 'data:image/png;base64,AA' }] }];
  const r = appendToolResults(t, [{ tool_call_id: 'cc1', name: 'computer', content: '[screenshot]', items: out }]);
  eq(pendingToolCalls(r.tree), null);
  eq(r.tree.nodes[r.ids[0]].items, out);
});

test('no pending calls on a plain thread', () => {
  __resetIds();
  const u = appendTurn(emptyTree(), 'hi');
  const f = foldAssistant(u.tree, u.nodeId, [{ content: 'hello' }]);
  eq(pendingToolCalls(f.tree), null);
  eq(pendingToolCalls(emptyTree()), null);
});

test('parseToolDefs: full shape, bare functions, blank, errors', () => {
  eq(parseToolDefs('').tools, null);
  eq(parseToolDefs('[]').tools, null);
  const full = parseToolDefs('[{"type":"function","function":{"name":"a","parameters":{"type":"object"}}}]');
  eq(full.tools, [{ type: 'function', function: { name: 'a', parameters: { type: 'object' } } }]);
  const bare = parseToolDefs('{"name":"b","description":"d","strict":true}');
  eq(bare.tools, [{ type: 'function', function: { name: 'b', description: 'd', strict: true } }]);
  // native + flat Responses tools stay verbatim
  const resp = parseToolDefs('[{"type":"computer"},{"type":"function","name":"c","strict":true,"parameters":{}}]');
  eq(resp.tools, [{ type: 'computer' }, { type: 'function', name: 'c', strict: true, parameters: {} }]);
  eq(parseToolDefs('[{"type":"function","name":"c"},{"type":"function","function":{"name":"c"}}]').error, 'Two tools are named "c"');
  eq(typeof parseToolDefs('[{').error, 'string');
  eq(parseToolDefs('[{"description":"x"}]').error, 'Tool 1 has no "name"');
  eq(parseToolDefs('[{"name":"a"},{"name":"a"}]').error, 'Two tools are named "a"');
});

test('cleanHandlers keeps what each mode uses, drops manual entries and orphans', () => {
  const out = cleanHandlers(
    {
      send: { mode: 'static', text: 'ok', code: 'draft', auto_continue: true },
      run: { mode: 'python', code: 'return 1', text: 'old', timeout: 5, auto_continue: false },
      look: { mode: 'manual', text: 'x' },
      gone: { mode: 'static', text: 'orphan' }
    },
    ['send', 'run', 'look']
  );
  eq(out, {
    send: { mode: 'static', text: 'ok' },
    run: { mode: 'python', code: 'return 1', timeout: 5, auto_continue: false }
  });
  eq(cleanHandlers({ look: { mode: 'manual' } }, ['look']), null);
});

test('a switched-off tool keeps its entry (even manual) and is not counted as offered', () => {
  const hs = cleanHandlers({ a: { mode: 'manual', disabled: true }, b: { mode: 'static', text: 'x', disabled: true } }, ['a', 'b', 'c']);
  eq(hs, { a: { mode: 'manual', disabled: true }, b: { mode: 'static', disabled: true, text: 'x' } });
  const tools = [call('1', 'a'), call('2', 'b'), call('3', 'c')].map((c) => ({ type: 'function', function: { name: c.function.name } }));
  eq(offeredCount(tools, hs), 1);
});

test('definition fields edit in place, in either function shape', () => {
  const nested = { type: 'function', function: { name: 'send', description: 'post it', parameters: { type: 'object' } } };
  eq(defFields(nested), { native: false, name: 'send', description: 'post it', parameters: { type: 'object' } });
  eq(withDefFields(nested, { description: 'Post to chat.' }).function, { name: 'send', description: 'Post to chat.', parameters: { type: 'object' } });
  const flat = { type: 'function', name: 'send', strict: true, parameters: {} };
  eq(withDefFields(flat, { name: 'say', description: '' }), { type: 'function', name: 'say', strict: true, parameters: {} });
  eq(defFields({ type: 'computer' }), { native: true });
});

console.log(`tool-use: ${passed} passed, ${failed} failed`);
if (failed) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n\n'));
}
