// Pure unit tests for pack-source.ts — the `?w=` id-vs-pack-source discriminator and
// the collision naming. Run WITHOUT a test framework via Node 22's built-in TS
// type-stripping:   node web/src/lib/pack-source.test.ts
// (house pattern — no dep added, no @types/node needed.)

import {
  isPackSource,
  isLocalPath,
  slug,
  packWorkspaceId,
  nextAvailableName,
  nextAvailableId,
  sourceLabel
} from './pack-source.ts';

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

// ── isPackSource ─────────────────────────────────────────────────────
// The whole back-compat claim rests on this: every id the store can mint
// (_SAFE_ID = [A-Za-z0-9_-]+) must still read as an id, never as a source.
test('real workspace ids are never treated as sources', () => {
  for (const id of [
    'a410b399-ebb9-4819-a34a-d8e017573e14',
    'pack-weird-personas-smoke',
    'smoke-static',
    'Untitled_2',
    'a'
  ]) {
    ok(!isPackSource(id), `${id} should be an id`);
  }
});

test('paths and URLs are sources', () => {
  for (const src of [
    'https://raw.githubusercontent.com/u/r/main/pack.yaml',
    'http://localhost:8000/p.yaml',
    '/home/c/packs/demo.yaml',
    './demo.yaml',
    '../up/demo.yml',
    'demo.yaml', // a bare filename still carries a dot ⇒ not an id
    'packs/demo'
  ]) {
    ok(isPackSource(src), `${src} should be a source`);
  }
});

test('empty is neither', () => {
  ok(!isPackSource(''));
});

// ── isLocalPath ──────────────────────────────────────────────────────
test('isLocalPath separates fetchable URLs from filesystem paths', () => {
  ok(!isLocalPath('https://x.io/p.yaml'));
  ok(!isLocalPath('HTTP://x.io/p.yaml'), 'scheme match is case-insensitive');
  ok(isLocalPath('/abs/p.yaml'));
  ok(isLocalPath('./rel.yaml'));
  ok(isLocalPath('file:///p.yaml'), 'file:// is not fetchable either ⇒ refused as local');
});

// ── slug / id derivation (must mirror pack.py) ───────────────────────
test('slug matches pack.py _slug', () => {
  eq(slug('weird personas'), 'weird-personas');
  eq(slug('hi + cigarettes'), 'hi-cigarettes');
  eq(slug('identity v3?'), 'identity-v3');
  eq(slug('!!!'), 'x');
  eq(slug('keep_me-1'), 'keep_me-1');
});

test('packWorkspaceId mirrors apply_pack ids', () => {
  eq(packWorkspaceId('weird personas', 'hi + cigarettes'), 'pack-weird-personas-hi-cigarettes');
});

// ── collision naming ─────────────────────────────────────────────────
test('nextAvailableName suffixes and increments', () => {
  eq(nextAvailableName('demo', []), 'demo');
  eq(nextAvailableName('demo', ['demo']), 'demo (2)');
  eq(nextAvailableName('demo', ['demo', 'demo (2)']), 'demo (3)');
  eq(nextAvailableName('demo', ['demo', 'demo (3)']), 'demo (2)', 'fills the gap');
});

test('nextAvailableName does not stack suffixes', () => {
  eq(nextAvailableName('demo (2)', ['demo (2)']), 'demo (3)');
  eq(nextAvailableName('demo (2)', ['demo (2)', 'demo (3)']), 'demo (4)');
});

test('nextAvailableId increments with a dash and stays a legal id', () => {
  eq(nextAvailableId('pack-a-b', []), 'pack-a-b');
  eq(nextAvailableId('pack-a-b', ['pack-a-b']), 'pack-a-b-2');
  eq(nextAvailableId('pack-a-b', ['pack-a-b', 'pack-a-b-2']), 'pack-a-b-3');
  ok(/^[A-Za-z0-9_-]+$/.test(nextAvailableId('pack-a-b', ['pack-a-b'])));
});

// ── sourceLabel ──────────────────────────────────────────────────────
test('sourceLabel is short and names the host for a URL', () => {
  eq(
    sourceLabel('https://raw.githubusercontent.com/u/r/main/demo.yaml'),
    'demo.yaml (raw.githubusercontent.com)'
  );
  eq(sourceLabel('/home/c/packs/demo.yaml'), 'demo.yaml');
  eq(sourceLabel('./demo.yaml'), 'demo.yaml');
});

// ── summary ──────────────────────────────────────────────────────────
console.log(`pack-source.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  throw new Error('\n' + fails.join('\n\n'));
}
