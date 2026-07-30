// Pure unit tests for pack-source.ts — the `?w=` id-vs-pack-source discriminator and
// the collision naming. Run WITHOUT a test framework via Node 22's built-in TS
// type-stripping:   node web/src/lib/pack-source.test.ts
// (house pattern — no dep added, no @types/node needed.)

import {
  isPackSource,
  isLocalPath,
  slug,
  packWorkspaceId,
  bumpUntilFree,
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
// These pin the rule against src/tinkerscope/pack.py `_dedupe_conflicting`, which
// performs the SAME renaming server-side. A review found the two had diverged on
// both scenarios below; they're regression tests now, not just unit tests.
test('bumpUntilFree: free name is returned untouched', () => {
  eq(bumpUntilFree('demo', () => true), 'demo');
});

test('bumpUntilFree: suffixes and increments', () => {
  const taken = new Set(['demo']);
  eq(bumpUntilFree('demo', (c) => !taken.has(c)), 'demo (2)');
  taken.add('demo (2)');
  eq(bumpUntilFree('demo', (c) => !taken.has(c)), 'demo (3)');
});

test('bumpUntilFree: an existing suffix CONTINUES its own counter', () => {
  // The divergence a review caught: the server restarted at (2), so "x (5)" became
  // "x (2)" there and "x (6)" here — same link, two different ids.
  const taken = new Set(['x (5)']);
  eq(bumpUntilFree('x (5)', (c) => !taken.has(c)), 'x (6)');
});

test('bumpUntilFree: never stacks suffixes', () => {
  const taken = new Set(['demo (2)']);
  ok(!bumpUntilFree('demo (2)', (c) => !taken.has(c)).includes(') ('));
});

test('bumpUntilFree: the predicate decides — a taken NAME with a free id does not rename', () => {
  // The sharper divergence: renaming on a name collision forks a workspace off its
  // canonical pack id while that id stays free, so a later open reads as
  // never-installed and &open=<canonical-id> misses. Only the ID may force a rename.
  const takenIds = new Set(['pack-p-other']);
  const isFree = (c: string) => !takenIds.has(packWorkspaceId('p', c));
  eq(bumpUntilFree('demo', isFree), 'demo', 'a free id means no rename, whatever the names are');
  eq(bumpUntilFree('other', isFree), 'other (2)', 'a taken id renames');
});

test('bumpUntilFree: the derived id stays a legal store id', () => {
  const taken = new Set(['pack-p-demo']);
  const name = bumpUntilFree('demo', (c) => !taken.has(packWorkspaceId('p', c)));
  ok(/^[A-Za-z0-9_-]+$/.test(packWorkspaceId('p', name)), packWorkspaceId('p', name));
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
