// Panel-id minting: monotonic per workspace, never reused.
//
// Ids used to be minted by gap-filling — reserved 'compare' for slot 1, then the
// lowest free p-N. So closing a column and adding one handed the SAME id to a
// different model, which made `panel:node` handles silently re-point and made a
// workspace's own history unquotable. `highestPanelSeq` is the back-compat half:
// a workspace saved before `panel_seq` existed has to seed the counter above every
// p-N it already contains, INCLUDING panels it has since closed.
import { FIRST_PANEL_ID, highestPanelSeq, legacyLayout, mintPanelId } from './panel-id.ts';

let pass = 0;
const fails: string[] = [];
function eq<T>(what: string, got: T, want: T) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) pass++;
  else fails.push(`${what}: got ${g}, want ${w}`);
}

// ── seeding the counter for a pre-panel_seq workspace ──
eq('empty workspace seeds 0', highestPanelSeq({}), 0);

eq(
  'reserved names carry no number',
  highestPanelSeq({ trees: { primary: {}, compare: {} } }),
  0
);

eq(
  'highest p-N in trees wins',
  highestPanelSeq({ trees: { primary: {}, 'p-2': {}, 'p-7': {}, 'p-3': {} } }),
  7
);

eq(
  'the layout counts too',
  highestPanelSeq({ trees: { primary: {} }, panels: [{ id: 'primary' }, { id: 'p-4' }] }),
  4
);

// THE case the whole scheme exists for: a closed panel is absent from trees AND
// the layout, so only seen_panels remembers its number. Miss it and the next mint
// re-issues p-9 onto a different model.
eq(
  'a CLOSED panel still reserves its number',
  highestPanelSeq({ trees: { primary: {} }, panels: [{ id: 'primary' }], seen_panels: ['primary', 'p-9'] }),
  9
);

eq(
  'double digits are compared numerically, not lexically',
  highestPanelSeq({ trees: { 'p-9': {}, 'p-10': {} } }),
  10
);

eq('malformed ids are ignored', highestPanelSeq({ trees: { 'p-': {}, 'p-x': {}, 'pp-3': {}, 'p-2x': {} } }), 0);

eq('null-ish fields are tolerated', highestPanelSeq({ trees: null, panels: null, seen_panels: null }), 0);

// ── the minting contract (the real function the store delegates to) ──
const mint = mintPanelId;

eq('the invented first id matches the python default', FIRST_PANEL_ID, 'p-1');

{
  const taken = new Set(['primary', 'compare']);
  let seq = highestPanelSeq({ trees: { primary: {}, compare: {} } });
  const got: string[] = [];
  for (let i = 0; i < 3; i++) {
    const r = mint(seq, taken);
    seq = r.seq;
    taken.add(r.id);
    got.push(r.id);
  }
  eq('a legacy primary/compare workspace mints from p-1', got, ['p-1', 'p-2', 'p-3']);
}

{
  // Close p-2, then mint: the number must NOT come back.
  const taken = new Set(['p-1', 'p-3']); // p-2 closed → gone from trees/layout
  let seq = highestPanelSeq({ trees: { 'p-1': {}, 'p-3': {} }, seen_panels: ['p-1', 'p-2', 'p-3'] });
  const r = mint(seq, taken);
  eq('a closed id is never re-minted', r.id, 'p-4');
}

{
  // An imported / hand-edited workspace can carry a p-N above the counter.
  const taken = new Set(['p-1', 'p-2']);
  const r = mint(1, taken); // counter lags reality
  eq('minting skips ids already present', r.id, 'p-3');
}

// ── layout for a body saved before `panels` was persisted ──
// The regression this exists for: with the default first panel renamed 'primary'
// → 'p-1', nothing in the shown layout matched a legacy body's tree keys, so it
// opened BLANK — turns on disk, no column rendering them. Caught by three browser
// smokes; pinned here so it can't come back silently.
const row = (id: string, run: string | null = null) => ({ id, run_id: run, checkpoint: null });

eq(
  'a legacy tree adopts its own id and inherits the shown model',
  legacyLayout(['primary'], [row('p-1', 'run-a')]),
  [{ id: 'primary', run_id: 'run-a', checkpoint: null }]
);
eq(
  'every legacy tree gets a column, models inherited positionally',
  legacyLayout(['primary', 'compare'], [row('p-1', 'run-a')]),
  [{ id: 'primary', run_id: 'run-a', checkpoint: null },
   { id: 'compare', run_id: null, checkpoint: null }]
);
eq(
  'a layout that already covers every tree is left alone',
  legacyLayout(['primary'], [row('primary', 'run-a'), row('compare', 'run-b')]),
  null
);
eq('no trees ⇒ nothing to fix', legacyLayout([], [row('p-1')]), null);

console.log(`panel-id.test: ${pass} passed, ${fails.length} failed`);
if (fails.length) {
  // A top-level throw exits node non-zero (no @types/node / process needed).
  throw new Error('\n' + fails.join('\n'));
}
