// Pure unit tests for node-handle.ts — run WITHOUT a test framework via Node's
// built-in TS type-stripping:   node web/src/lib/node-handle.test.ts

import { parseNodeHandle, resolveNodeHandle } from './node-handle.ts';

let failed = 0;
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try {
    await fn();
  } catch (e) {
    failed++;
    console.error(`✗ ${name}\n    ${(e as Error).message}`);
  }
}
function eq(a: unknown, b: unknown, msg = ''): void {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg} expected ${JSON.stringify(b)} got ${JSON.stringify(a)}`);
}

const WS = {
  id: '73c24b20-7b8f-4521-b4c7-7b9deb2a37fb',
  name: 'demo',
  trees: {
    'p-4': {
      rootChildren: ['u1'],
      selected: { u1: 'a2' },
      nodes: {
        u1: { id: 'u1', role: 'user', content: 'q', parent: null, children: ['a1', 'a2'] },
        a1: { id: 'a1', role: 'assistant', content: 'off-path reply', parent: 'u1', children: [] },
        a2: { id: 'a2', role: 'assistant', content: 'selected reply', parent: 'u1', children: [] }
      }
    }
  }
};
const list = async () => [{ id: WS.id, name: 'demo' }, { id: 'ffff0000-x', name: 'other' }] as any;
const get = async () => WS as any;

await test('parses the three-part handle, prefix or full ws id', () => {
  eq(parseNodeHandle(' 73c24b20:p-4:nj6ai2m '), { ws: '73c24b20', panel: 'p-4', node: 'nj6ai2m' });
  eq(parseNodeHandle(`${WS.id}:primary:n96zo5`), { ws: WS.id, panel: 'primary', node: 'n96zo5' });
});

await test('ordinary searches are not handles', () => {
  for (const q of ['smoke', 'should i smoke during the party?', 'p-4:a1', 'a:b:c d', 'ratio 3:2:1'])
    eq(parseNodeHandle(q), null, q);
});

await test('resolves to a hit carrying the full ws id, panel, node, branch facts', async () => {
  const hit = await resolveNodeHandle({ ws: '73c24b20', panel: 'p-4', node: 'a1' }, list, get);
  if (typeof hit === 'string') throw new Error(hit);
  eq([hit.workspace_id, hit.panel, hit.node_id, hit.role], [WS.id, 'p-4', 'a1', 'assistant']);
  eq([hit.sib_index, hit.sib_count, hit.on_active_path], [0, 2, false]);
  const sel = await resolveNodeHandle({ ws: '73c24b20', panel: 'p-4', node: 'a2' }, list, get);
  eq(typeof sel !== 'string' && sel.on_active_path, true, 'a2 is the selected child');
});

await test('unresolvable handles say why', async () => {
  const r1 = await resolveNodeHandle({ ws: 'dead', panel: 'p-4', node: 'a1' }, list, get);
  const r2 = await resolveNodeHandle({ ws: '73c2', panel: 'p-9', node: 'a1' }, list, get);
  const r3 = await resolveNodeHandle({ ws: '73c2', panel: 'p-4', node: 'zz' }, list, get);
  eq([typeof r1, typeof r2, typeof r3], ['string', 'string', 'string']);
  eq((r2 as string).includes('p-9') && (r3 as string).includes('zz'), true);
});

if (failed) throw new Error(`${failed} failed`);
console.log('node-handle.test.ts: all passed');
