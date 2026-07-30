<!--
  Response-distribution chart modal. The parent hands per-panel, per-assistant-
  turn sample lists (ChartPanelData[], reactive — it live-updates while a batch
  streams); everything else is owned here:

  - turn picker — which assistant turn to chart; defaults to the LATEST turn
    (tracks the newest turn while `Latest` is selected).
  - two bucketing modes (math in $lib/chart):
      · "highlight rules" (default when any assistant-scoped rule is enabled):
        each sample is bucketed by the SET of rules it matches — grey = none,
        solid = one rule, striped = a multi-rule combo.
      · "exact answers": the legacy trimmed string-equality histogram (an
        empty answer buckets as [NO ANSWER], not dropped).
  - per-rule chart toggles (rules mode) — a chip per applicable rule; clicking
    one excludes it from the BUCKETING (not from chat painting): a rule the
    prompt makes ubiquitous ("smoking" when the question is about smoking)
    stripes every bucket and drowns the signal. Exclusions are chart-only, and
    remembered PER WORKSPACE (see the view persistence below).
  - match scope (rules mode) — which text the rules run against: response /
    thinking / either, or "split" = a response|thinking bar pair per model,
    adjacent under ONE model name (bucketed separately over the same samples).
  - "first N chars" (rules mode) — cap matching to the OPENING of that text
    (per part, so it means the same under every scope). For rules that describe
    how a reply STARTS — a `<answer>` tag, a "Verdict:" header — where the model
    later discussing the same token would otherwise count as a match. Blank =
    whole text. Remembered per workspace, and the inspector dims what fell
    outside the cap so a "no match" sample explains itself.
  - thinking filter — which population to chart when the turn mixes samples
    generated WITH and WITHOUT a chain of thought (regen batches with the
    thinking toggle flipped; panels on different models), since their answer
    distributions often differ: all samples pooled / one of them / "split" =
    a think and a no-think bar per model, adjacent under ONE model name over
    DISJOINT samples (each its own 100%, each labeled with its own n).
    Applies to all three bucketing modes, upstream of them, and composes with
    the match-scope split (up to 4 bars per model); only shown when the picked
    turn actually has a mix (inert otherwise, so a hidden selection can't
    silently filter).
  - folded (reduced) panels are excluded by default; an "include folded
    panels" toggle appears when any are present.
  - the charted prompt(s) above the chart — grouped per distinct prompt, since
    panels can diverge (each shows which models it belongs to).
  - click a segment → inspect its samples below the chart, rendered through the
    normal highlight pipeline so the matched patterns are painted (the thinking
    fold auto-opens when the scope involves thinking, and each sample's fold is
    then the reader's to set). The inspector is deliberately ISOLATED from the
    plot's churn: it addresses its bar by a stable ref and holds the folds in
    state, because `data.bars` is rebuilt on every streamed sample — see the
    inspect section below.

  The whole view is PERSISTED (`$lib/chart-view` — localStorage): the three
  how-you-look-at-it picks (mode / match scope / thinking filter) globally, and
  everything question-specific (turn, folded panels, excluded rules, first-token
  exclusions / merges / added tokens) per WORKSPACE. So reopening after another
  sampling round — or a reload — puts you back where you were, and a workspace
  you've never charted starts from your last picks with a clean slate of tweaks.
-->
<script lang="ts" module>
  import { loadChartView, saveChartView, type ChartMode, type ChartView } from './chart-view';

  // ── the whole chart view, persisted (see chart-view.ts) ───────────
  // All of it lives at MODULE scope so it survives the modal's destroy-on-close,
  // and in localStorage so it survives a reload — the three how-you-look-at-it
  // picks globally, everything question-specific per WORKSPACE (Clément,
  // 2026-07-29: reopening after another sampling round and re-picking every
  // time). `modePref` is what was CHOSEN; the live `mode` may fall back when a
  // turn can't serve it (no logprobs), and that fallback must not overwrite it.
  //
  //   chartOff    rule ids dropped from the BUCKETING (not from chat painting)
  //   matchLimit  rules mode: match only the first N chars (0 = whole text)
  //   ftExcluded  first-token units dropped from the named segments; their mass
  //               + samples fold into the grey rest, unless ftRenorm
  //   ftGroups    merges — each is a list of display tokens fused into one color
  //   ftAdded     recorded-but-hidden tokens surfaced from the rest (by identity;
  //               each panel resolves its OWN recorded p for the tid)
  //   ftRenorm    when on, the grey rest drops from the bar entirely (top-K tail
  //               + any excluded units) and the NAMED units rescale to 100% —
  //               meaningful with or without an exclusion
  // (ftExcluded / ftGroups are keyed by UNIT: a display token, or a merged
  // group's ftGroupKey.)
  let modePref = $state<ChartMode | null>(null);
  let matchScope = $state<MatchScope | 'split'>('response');
  let thinkFilter = $state<ThinkFilter>('all');
  let turnSel = $state('last'); // 'last' | stringified turn index
  let includeFolded = $state(false);
  let chartOff = $state<string[]>([]);
  let matchLimit = $state(0);
  let ftExcluded = $state<string[]>([]);
  let ftGroups = $state<string[][]>([]);
  let ftAdded = $state<{ token: string; tid: number }[]>([]);
  let ftRenorm = $state(false);

  /** The id whose view is currently loaded — guards the reload effect. */
  let viewOf = $state<string | null | undefined>(undefined);

  // The READER's position: which segment is being inspected (by stable bar ref,
  // never an index — see barRef) and which samples' thinking folds the user
  // opened. Module-scoped like the rest, so closing and reopening the modal
  // doesn't drop what you were reading; NOT persisted to disk — it's a position
  // within a session, not a preference. A ref that no longer resolves simply
  // yields no inspector.
  let inspect = $state<{ ref: string; key: string } | null>(null);
  let thinkOpen = $state<Record<string, boolean>>({});

  function applyView(v: ChartView): void {
    modePref = v.mode;
    matchScope = v.scope;
    thinkFilter = v.think;
    turnSel = v.turn;
    includeFolded = v.includeFolded;
    chartOff = v.rulesOff;
    matchLimit = v.matchLimit;
    ftExcluded = v.ftExcluded;
    ftGroups = v.ftGroups;
    ftAdded = v.ftAdded;
    ftRenorm = v.ftRenorm;
  }

  function currentView(): ChartView {
    return {
      mode: modePref,
      scope: matchScope,
      think: thinkFilter,
      turn: turnSel,
      includeFolded,
      rulesOff: chartOff,
      matchLimit,
      ftExcluded,
      ftGroups,
      ftAdded,
      ftRenorm
    };
  }
</script>

<script lang="ts">
  import Modal from './Modal.svelte';
  import {
    buildChartSources,
    chartByAnswers,
    chartByFirstToken,
    chartByRules,
    chartRules,
    contrastText,
    ftGroupKey,
    FT_REST,
    NONE_COLOR,
    wrapLabel,
    type AddedToken,
    type ChartPanelData,
    type ChartSample,
    type ChartSource,
    type MatchScope,
    type ThinkFilter
  } from './chart';
  import { displayToken, prob } from './token-logprob';
  import { searchStoredTokens, type TokenCandidate } from './token-search';
  import { highlightStore } from './highlights.svelte';
  import { nodeBlobs } from './node-blobs.svelte';
  import { workspaces as ws } from './workspaces.svelte';
  import { renderContent } from './render';
  import { tip } from '$lib/tooltip.svelte';

  let { sources, onclose }: { sources: ChartPanelData[]; onclose: () => void } = $props();

  // ── persisted view: load / save ───────────────────────────────────
  // The saved view is per WORKSPACE (question-specific bits) over global picks;
  // reload it when the workspace changes under an open modal — the CLI can
  // switch it out from under us.
  $effect(() => {
    const id = ws.activeId;
    if (viewOf === id) return;
    viewOf = id;
    applyView(loadChartView(id));
    inspect = null;
    thinkOpen = {};
  });
  function saveView(): void {
    saveChartView(ws.activeId, currentView());
  }

  // ── controls ──────────────────────────────────────────────────────
  // The live mode is the persisted CHOICE, except that a stored 'firsttoken'
  // reopening where nothing carries logprobs would land on a dead empty state
  // under a disabled button — fall back for the display only, so a turn that
  // CAN serve it still does. ('rules' with no applicable rule keeps its own
  // explanatory empty state; that one is worth showing.)
  const rulesDefault = (): ChartMode =>
    chartRules(highlightStore.rules).length > 0 ? 'rules' : 'answers';
  // ($derived.by, not $derived: hasFirstToken is declared further down.)
  const mode = $derived.by<ChartMode>(() =>
    modePref === 'firsttoken' && !hasFirstToken ? rulesDefault() : (modePref ?? rulesDefault())
  );
  function setMode(m: ChartMode): void {
    modePref = m;
    saveView();
    inspect = null;
  }

  // ── per-rule chart toggles (rules mode) ───────────────────────────
  // The rules that could bucket assistant samples — each gets an on/off chip.
  const applicableRules = $derived(chartRules(highlightStore.rules));
  const activeRules = $derived(highlightStore.rules.filter((r) => !chartOff.includes(r.id)));
  const allRulesOff = $derived(
    applicableRules.length > 0 && applicableRules.every((r) => chartOff.includes(r.id))
  );
  /** The first-N-chars match cap. Blank / 0 / junk = no cap; buckets change
   *  under the reader, so a live inspect selection is dropped like toggleRule's. */
  function setMatchLimit(raw: string) {
    const n = Math.floor(Number(raw));
    matchLimit = Number.isFinite(n) && n > 0 ? n : 0;
    inspect = null;
    saveView();
  }
  function toggleRule(id: string) {
    chartOff = chartOff.includes(id) ? chartOff.filter((x) => x !== id) : [...chartOff, id];
    // Bucket keys are positional in the active-rule list — they all shift
    // when the set changes, so a kept inspect selection would silently point
    // at a different bucket.
    inspect = null;
    saveView();
  }

  const hasFolded = $derived(sources.some((s) => s.folded));
  const activeSources = $derived(sources.filter((s) => includeFolded || !s.folded));

  const turnCount = $derived(Math.max(0, ...activeSources.map((s) => s.turns.length)));
  const turnIdx = $derived(turnSel === 'last' ? -1 : Number(turnSel));
  // A restored turn index can outlive its turn (deleted, or a branch switch
  // shortened the thread) — that would strand the chart on an empty state.
  $effect(() => {
    if (turnSel !== 'last' && !(Number(turnSel) < turnCount)) turnSel = 'last';
  });

  /** The picked turn of one panel ('last' → its own newest turn). */
  function pickTurn(s: ChartPanelData) {
    return turnIdx < 0 ? s.turns[s.turns.length - 1] : s.turns[turnIdx];
  }

  /** Question preview for the turn picker (first panel that has that turn). */
  function turnLabel(k: number): string {
    for (const s of activeSources) {
      const t = s.turns[k];
      if (!t) continue;
      const q = t.question.replace(/\s+/g, ' ').trim();
      const head = q.length > 48 ? q.slice(0, 47) + '…' : q;
      return `Turn ${k + 1}: ${head}${t.streaming ? ' (streaming…)' : ''}`;
    }
    return `Turn ${k + 1}`;
  }

  // ── thinking filter ───────────────────────────────────────────────
  // Which population(s) of the picked turn to chart: pooled, one of them, or
  // 'split' (a bar each — disjoint samples, so each gets its own 100%). Gated
  // on the turn actually containing both; when it doesn't, the control hides
  // AND the filter is treated as 'all' — a leftover selection must not
  // silently filter a mix-free turn.
  const pickedRaw = $derived(
    activeSources.map((s) => ({
      panel: s.panel,
      model: s.model,
      samples: pickTurn(s)?.samples ?? []
    }))
  );
  const hasThinkMix = $derived.by(() => {
    const all = pickedRaw.flatMap((s) => s.samples);
    return all.some((x) => x.reasoning) && all.some((x) => !x.reasoning);
  });

  // Bars index-align with chartSources (so inspect can find the sample text).
  // Both splits (thinking filter / match scope) expand a panel into several
  // bars sharing its model name and differing by `sub`, which the layout below
  // renders as one group of adjacent bars under one label. See buildChartSources.
  const chartSources = $derived(
    buildChartSources(pickedRaw, {
      think: hasThinkMix ? thinkFilter : 'all',
      scopeSplit: mode === 'rules' && matchScope === 'split'
    })
  );
  /** Charted prompt(s), grouped — panels can diverge on what was asked. */
  const questionGroups = $derived.by(() => {
    const groups: { q: string; models: string[] }[] = [];
    for (const s of activeSources) {
      const t = pickTurn(s);
      if (!t) continue;
      const g = groups.find((g) => g.q === t.question);
      if (g) g.models.push(s.model);
      else groups.push({ q: t.question, models: [s.model] });
    }
    return groups;
  });
  const streaming = $derived(activeSources.some((s) => pickTurn(s)?.streaming));
  function truncQ(q: string): string {
    return q.length > 200 ? '…' + q.slice(-200) : q;
  }

  // First-token mode availability: ANY turn of any active source carries a
  // logprob record (not just the picked turn — the picker defaults to the
  // latest turn, which may be an OpenRouter regen without data). `hasFirst`
  // counts too: a light node's record lives server-side (storage v2) and is
  // fetched on demand below — the mode must not look unavailable meanwhile.
  const hasFirstToken = $derived(
    activeSources.some((s) => s.turns.some((t) => t.samples.some((x) => x.first || x.hasFirst)))
  );
  // Lazy blob fetch for the PICKED turn (only — never the whole workspace):
  // light samples flagged hasFirst without an inline/cached record get their
  // node blobs batch-fetched; `first` then fills in reactively through the
  // parent's cache-resolved sources. ensure() dedupes cached/in-flight ids, so
  // re-running on every pick/mode change is cheap.
  $effect(() => {
    if (mode !== 'firsttoken') return;
    const ids = pickedRaw
      .flatMap((s) => s.samples)
      .filter((x) => x.hasFirst && !x.first && x.nodeId)
      .map((x) => x.nodeId!);
    if (ids.length) void nodeBlobs.ensure(ids);
  });
  /** True while the picked turn still has flagged-but-unfetched records. */
  const ftLoading = $derived(
    mode === 'firsttoken' && pickedRaw.some((s) => s.samples.some((x) => x.hasFirst && !x.first))
  );
  // ── first-token: recorded tokens (search + add) ───────────────────
  // Everything with a recorded position-0 logprob for the charted turn, per
  // source: each sample's sampled first token + its top-K alternatives, deduped
  // by tid keeping the max lp (same prompt+position ⇒ the lp is the same up to
  // jitter — never summed). Both the search index and the source of an added
  // token's per-panel probability.
  function recordedMap(src: ChartSource): Map<number, { raw: string; lp: number }> {
    const m = new Map<number, { raw: string; lp: number }>();
    const consider = (raw: string, tid: number, lp: number | null) => {
      if (lp == null) return;
      const cur = m.get(tid);
      if (!cur || lp > cur.lp) m.set(tid, { raw, lp });
    };
    for (const s of src.samples) {
      const f = s.first;
      if (!f) continue;
      consider(f.t, f.tid, f.lp);
      for (const [t, tid, lp] of f.top ?? []) consider(t, tid, lp);
    }
    return m;
  }
  const ftRecorded = $derived(mode === 'firsttoken' ? chartSources.map(recordedMap) : []);
  // Union across panels (dedup handled in searchStoredTokens), for the add-search.
  const ftCandidates = $derived.by((): TokenCandidate[] => {
    const out: TokenCandidate[] = [];
    for (const m of ftRecorded) for (const [tid, { raw, lp }] of m) out.push({ t: raw, tid, lp });
    return out;
  });
  // Resolve `ftAdded` (identities) to per-source AddedToken[] using each panel's
  // own recorded p; a panel that never recorded the tid contributes nothing there.
  const ftAddedPerSource = $derived.by((): AddedToken[][] =>
    ftRecorded.map((m) =>
      ftAdded.flatMap((a) => {
        const r = m.get(a.tid);
        const p = r ? prob(r.lp) : null;
        return p != null ? [{ token: a.token, tid: a.tid, p }] : [];
      })
    )
  );

  const ft = $derived(
    mode === 'firsttoken'
      ? chartByFirstToken(chartSources, {
          excluded: new Set(ftExcluded),
          groups: ftGroups,
          added: ftAddedPerSource,
          renormalize: ftRenorm
        })
      : null
  );

  const data = $derived(
    mode === 'rules'
      ? chartByRules(chartSources, activeRules, matchScope === 'split' ? 'response' : matchScope, matchLimit)
      : mode === 'firsttoken'
        ? (ft?.data ?? null)
        : chartByAnswers(chartSources)
  );

  // ── first-token: the interactive chip row (exclude / merge / add) ──
  // Named units (colored, click→exclude, drag→merge) + excluded units (greyed,
  // click→re-include). Added tokens carry a ✕ to drop them back into the rest.
  const addedTids = $derived(new Set(ftAdded.map((a) => a.tid)));
  const namedTokens = $derived(
    new Set((ft?.data.legend ?? []).filter((l) => l.key !== FT_REST).flatMap((l) => l.members ?? [l.key]))
  );
  /** Members behind a unit key (a group's tokens, or the token itself). */
  function unitMembers(key: string): string[] {
    return ftGroups.find((g) => ftGroupKey(g) === key) ?? [key];
  }
  type FtChip = { key: string; label: string; members: string[]; color?: string; excluded: boolean; addedTid?: number };
  const ftChips = $derived.by((): FtChip[] => {
    if (mode !== 'firsttoken' || !ft) return [];
    const chips: FtChip[] = ft.data.legend
      .filter((l) => l.key !== FT_REST)
      .map((l) => {
        const members = l.members ?? [l.key];
        // tag a singleton chip that is an added-token so it gets a remove ✕
        const added = members.length === 1 ? ftAdded.find((a) => a.token === members[0]) : undefined;
        return { key: l.key, label: l.label, members, color: l.colors[0], excluded: false, addedTid: added?.tid };
      });
    for (const key of ftExcluded) {
      const members = unitMembers(key);
      chips.push({ key, label: members.join(' + '), members, excluded: true });
    }
    return chips;
  });

  // ── first-token: add-token search ─────────────────────────────────
  let ftQuery = $state('');
  // Matches that are actually HIDDEN — not already a shown named unit, not already
  // added. (Surfacing an already-shown token would be a no-op.)
  const ftMatches = $derived.by(() => {
    if (mode !== 'firsttoken' || !ftQuery.trim()) return [];
    return searchStoredTokens(ftQuery, ftCandidates)
      .filter((m) => !addedTids.has(m.tid) && !namedTokens.has(displayToken(m.t)))
      .slice(0, 20);
  });

  function toggleFtExclude(key: string) {
    ftExcluded = ftExcluded.includes(key) ? ftExcluded.filter((k) => k !== key) : [...ftExcluded, key];
    inspect = null; // segment keys shift under exclusion
    saveView();
  }
  function addToken(m: { t: string; tid: number }) {
    const token = displayToken(m.t);
    if (!ftAdded.some((a) => a.tid === m.tid)) ftAdded = [...ftAdded, { token, tid: m.tid }];
    ftQuery = '';
    saveView();
  }
  function removeAdded(tid: number) {
    const gone = ftAdded.find((a) => a.tid === tid);
    ftAdded = ftAdded.filter((a) => a.tid !== tid);
    // also drop it from any merge + exclusion so it fully returns to the rest
    if (gone) {
      ftGroups = ftGroups
        .map((g) => g.filter((t) => t !== gone.token))
        .filter((g) => g.length >= 2);
      ftExcluded = ftExcluded.filter((k) => k !== gone.token);
    }
    inspect = null;
    saveView();
  }
  /** Merge two units (drag one chip onto another): fuse their tokens into one
   *  group, dropping any prior group those tokens belonged to. */
  function mergeUnits(srcKey: string, dstKey: string) {
    if (srcKey === dstKey) return;
    const merged = [...new Set([...unitMembers(dstKey), ...unitMembers(srcKey)])];
    ftGroups = [...ftGroups.filter((g) => !g.some((t) => merged.includes(t))), merged];
    // a fresh group is un-excluded; clear any stale member exclusions
    ftExcluded = ftExcluded.filter((k) => k !== srcKey && k !== dstKey && !merged.includes(k));
    inspect = null;
    saveView();
  }
  function unmerge(key: string) {
    ftGroups = ftGroups.filter((g) => ftGroupKey(g) !== key);
    ftExcluded = ftExcluded.filter((k) => k !== key);
    inspect = null;
    saveView();
  }
  // bespoke drag-onto-target (the shared lib/drag-reorder is gap-shaped, not this)
  let ftDrag = $state<string | null>(null);
  let ftDragOver = $state<string | null>(null);

  /** Per-segment hover text — first-token mode reads differently: pct is the
   *  MODEL's probability, count is how often it was actually sampled. */
  function segTooltip(seg: { label: string; count: number; pct: number; sampleIdx: number[] }, total: number): string {
    if (mode === 'firsttoken')
      return `‘${seg.label}’ — model p=${seg.pct.toFixed(1)}% · sampled ${seg.count}/${total}${seg.count > 0 ? ' · click to inspect' : ''}`;
    return `${seg.label} — ${seg.count}/${total} (${seg.pct.toFixed(0)}%) · click to inspect`;
  }

  // ── SVG layout ────────────────────────────────────────────────────
  // Consecutive sub-labeled bars of ONE PANEL (a split's bar set) form one
  // GROUP: adjacent bars, one model name centered under them. Grouping keys off
  // the source's `panel`, not the model label — two panels can run the same
  // model and must not fuse. A group's n sums its DISTINCT populations (a
  // match-scope pair re-buckets the same samples; the think split's two bars are
  // disjoint), and when its bars disagree on n each carries its own.
  const CHART_H = 300, TOP_PAD = 10, LEFT_PAD = 45, GROUP_GAP = 60, PAIR_GAP = 8;
  const SUB_Y = 13, SUB_LH = 11; // first sub-label baseline below the bars, then line height
  type PlacedBar = { bar: import('./chart').ChartBar; bi: number; x: number };
  type BarGroup = { model: string; total: number; center: number; perBarN: boolean; bars: PlacedBar[] };
  const layout = $derived.by(() => {
    if (!data) return null;
    const groups: BarGroup[] = [];
    const panelOf = (bi: number) => chartSources[bi]?.panel ?? -1;
    for (let bi = 0; bi < data.bars.length; bi++) {
      const bar = data.bars[bi];
      const g = groups[groups.length - 1];
      if (g && bar.sub && g.bars[0].bar.sub && panelOf(g.bars[0].bi) === panelOf(bi))
        g.bars.push({ bar, bi, x: 0 });
      else groups.push({ model: bar.model, total: 0, center: 0, perBarN: false, bars: [{ bar, bi, x: 0 }] });
    }
    for (const g of groups) {
      const seen = new Set<string>();
      for (const b of g.bars) {
        const key = chartSources[b.bi]?.pop ?? String(b.bi);
        if (seen.has(key)) continue;
        seen.add(key);
        g.total += b.bar.total;
      }
      g.perBarN = g.bars.some((b) => b.bar.total !== g.bars[0].bar.total);
    }
    const maxBars = Math.max(1, ...groups.map((g) => g.bars.length));
    const bw = maxBars > 2 ? 44 : maxBars > 1 ? 56 : 80; // split groups get slimmer bars
    let x = LEFT_PAD;
    for (const g of groups) {
      x += GROUP_GAP / 2;
      g.bars.forEach((b, i) => {
        b.x = x;
        x += bw + (i < g.bars.length - 1 ? PAIR_GAP : 0);
      });
      g.center = (g.bars[0].x + g.bars[g.bars.length - 1].x + bw) / 2;
      x += GROUP_GAP / 2;
    }
    // Sub labels sit between the bars and the model name → deeper bottom pad.
    const subLines = Math.max(0, ...groups.flatMap((g) => g.bars.map((b) => barSubLines(g, b.bar).length)));
    const modelDy = subLines ? SUB_Y + subLines * SUB_LH + 3 : 14;
    return { groups, bw, subLines, modelDy, width: x, height: TOP_PAD + CHART_H + 96 + modelDy };
  });
  /** A bar's sub-label lines: the sub's ' · '-separated levels, plus its own n
   *  when the group's bars cover different sample counts. */
  function barSubLines(g: { perBarN: boolean }, bar: import('./chart').ChartBar): string[] {
    if (!bar.sub) return [];
    const lines = bar.sub.split(' · ');
    if (g.perBarN) lines.push(`n=${bar.total}`);
    return lines;
  }

  // ── inspect (click a segment) ─────────────────────────────────────
  // The inspected bar is addressed by a STABLE identity, never by its index in
  // `data.bars`: the bar list is re-derived on every streamed sample, and bars
  // come and go mid-batch (a panel gains its first sample; the think split's
  // no-think bar appears; a fold retires the streaming pseudo-turn). An index
  // then silently re-points the inspector at a different bucket, or misses and
  // takes the whole panel — with it, the reader's scroll and folds.
  const barRef = (s: ChartSource | undefined): string =>
    s ? `${s.panel ?? s.model}|${s.pop ?? ''}|${s.sub ?? ''}` : '';
  // (s.panel is the WORKSPACE panel id, threaded through ChartPanelData — a
  // positional index here would shift with the bar list and defeat the point.)
  function toggleInspect(bi: number, key: string) {
    const ref = barRef(chartSources[bi]);
    inspect = inspect && inspect.ref === ref && inspect.key === key ? null : { ref, key };
  }
  const isInspected = (bi: number, key: string) =>
    inspect?.key === key && inspect.ref === barRef(chartSources[bi]);
  const inspected = $derived.by(() => {
    if (!inspect || !data) return null;
    const bi = chartSources.findIndex((s) => barRef(s) === inspect!.ref);
    const bar = data.bars[bi];
    const seg = bar?.segments.find((s) => s.key === inspect!.key);
    const src = chartSources[bi];
    if (!bar || !seg || !src || seg.count === 0) return null;
    // The scope this bar was matched under — the inspector auto-opens the
    // thinking fold when the match could live there.
    const scope: MatchScope =
      mode !== 'rules' ? 'response'
      : (src.matchOn ?? (matchScope === 'split' ? 'response' : matchScope));
    return { bar, seg, scope, samples: seg.sampleIdx.map((i) => src.samples[i]).filter(Boolean) };
  });

  // The inspector's per-sample thinking folds are EXPLICIT state, not DOM state.
  // A live batch re-renders this list constantly, and any moment the bucket is
  // briefly unresolvable destroys the rendered <details> — DOM-held open state
  // would silently snap back to the default under the reader. Keyed by node id
  // where there is one (a streaming sample only gets one at fold time, so its
  // override is reset then — the alternative is keying on content that is still
  // being written). `scope` supplies the default: opened when the match could
  // live in the CoT.
  const sampleKey = (s: ChartSample, i: number) => s.nodeId ?? `#${i}`;
  const foldOpen = (s: ChartSample, i: number, scope: MatchScope) =>
    thinkOpen[sampleKey(s, i)] ?? scope !== 'response';

  /** Legend/inspect swatch background — stripes for multi-rule combos. */
  function swatchStyle(colors: string[]): string {
    if (colors.length === 0) return `background: ${NONE_COLOR}`;
    if (colors.length === 1) return `background: ${colors[0]}`;
    const stops = colors.map((c, i) => `${c} ${i * 5}px ${(i + 1) * 5}px`).join(', ');
    return `background: repeating-linear-gradient(45deg, ${stops})`;
  }

  function segFill(colors: string[], legendIdx: number): string {
    if (colors.length === 0) return NONE_COLOR;
    if (colors.length === 1) return colors[0];
    return `url(#hlseg-${legendIdx})`;
  }
</script>

<Modal title="Response Distribution" {onclose} modalStyle="width: 1120px; max-width: 96vw; max-height: 92vh;">
  {#if sources.length === 0}
    <div class="backend-error">No response data to chart. Send a message first (use Samples &gt; 1 for best results).</div>
  {:else}
    <div class="chart-controls">
      <div class="chart-mode" role="group" aria-label="Bucketing mode">
        <button class="chart-mode-btn" class:active={mode === 'rules'} onclick={() => setMode('rules')}
          data-tooltip="Bucket samples by which highlight rules match them" use:tip>highlight rules</button>
        <button class="chart-mode-btn" class:active={mode === 'answers'} onclick={() => setMode('answers')}
          data-tooltip="Bucket samples by exact answer text (short constrained answers)" use:tip>exact answers</button>
        <button class="chart-mode-btn" class:active={mode === 'firsttoken'} disabled={!hasFirstToken}
          onclick={() => setMode('firsttoken')}
          data-tooltip={hasFirstToken
            ? "The model's probability distribution over the first generated token"
            : 'Needs token logprobs — captured on native tinker sampling only'} use:tip>first token</button>
      </div>
      {#if turnCount > 1}
        <select class="chart-turn" bind:value={turnSel} onchange={saveView} aria-label="Charted turn">
          <option value="last">Latest turn</option>
          {#each Array(turnCount) as _, k (k)}
            <option value={String(k)}>{turnLabel(k)}</option>
          {/each}
        </select>
      {/if}
      {#if mode === 'rules'}
        <div class="chart-mode" role="group" aria-label="Match scope">
          {#each [['response', 'Match rules against the response text only'],
                  ['thinking', 'Match rules against the thinking/CoT only'],
                  ['either', 'Match rules against thinking + response combined'],
                  ['split', 'Two adjacent bars per model — response and thinking, matched separately']] as [scope, tt] (scope)}
            <button class="chart-mode-btn" class:active={matchScope === scope}
              onclick={() => { matchScope = scope as typeof matchScope; saveView(); inspect = null; }}
              data-tooltip={tt} use:tip>{scope}</button>
          {/each}
        </div>
        <label class="chart-check" data-tooltip="Match rules only in the opening N characters (blank = all)" use:tip>
          first
          <input class="chart-limit" type="number" min="1" step="50" placeholder="all"
            value={matchLimit || ''} oninput={(e) => setMatchLimit(e.currentTarget.value)}
            aria-label="Match only the first N characters" />
          chars
        </label>
      {/if}
      {#if hasThinkMix}
        <select
          class="chart-think"
          bind:value={thinkFilter}
          onchange={() => { inspect = null; saveView(); }}
          aria-label="Thinking filter"
          data-tooltip="This turn mixes think / no-think samples — chart one, both, or split"
          use:tip
        >
          <option value="all">all samples</option>
          <option value="thinking">with thinking</option>
          <option value="no-thinking">without thinking</option>
          <option value="split">split think / no-think</option>
        </select>
      {/if}
      {#if hasFolded}
        <label class="chart-check" data-tooltip="Folded panels are excluded from the chart by default" use:tip>
          <input type="checkbox" bind:checked={includeFolded} onchange={saveView} /> include folded panels
        </label>
      {/if}
    </div>
    {#if mode === 'rules' && applicableRules.length > 0}
      <div class="chart-rules" role="group" aria-label="Rules included in the chart">
        {#each applicableRules as r (r.id)}
          {@const off = chartOff.includes(r.id)}
          <button
            class="chart-rule-chip"
            class:off
            aria-pressed={!off}
            data-tooltip={off
              ? 'Excluded from the chart — click to re-include'
              : 'Exclude this rule from the bucketing'}
            use:tip
            onclick={() => toggleRule(r.id)}
          >
            <span class="chart-rule-swatch" style="background: {r.color}"></span>{r.name}
          </button>
        {/each}
      </div>
    {/if}
    {#if questionGroups.length === 1}
      <p class="chart-question">{truncQ(questionGroups[0].q)}{streaming ? ' · streaming…' : ''}</p>
    {:else if questionGroups.length > 1}
      <div class="chart-question">
        {#each questionGroups as g (g.q)}
          <p><span class="chart-q-models">{g.models.join(', ')}:</span> {truncQ(g.q)}</p>
        {/each}
        {#if streaming}<p>streaming…</p>{/if}
      </div>
    {/if}
    {#if mode === 'firsttoken' && ft?.mixed}
      <div class="chart-note">⚠ this turn mixes batches with different first-token distributions (regenerated on another checkpoint or renderer mode) — bars use the NEWEST batch's top-K; older batches' sampled tokens are kept with their own probabilities.</div>
    {/if}
    {#if !data || !layout}
      {#if activeSources.length === 0}
        <div class="backend-error">All panels with data are folded — enable “include folded panels” above to chart them.</div>
      {:else if mode === 'firsttoken' && ftLoading}
        <div class="chart-note">Fetching this turn's token logprobs…</div>
      {:else if mode === 'firsttoken'}
        <div class="backend-error">No token logprobs on this turn — first-token distributions need native tinker samples (OpenRouter and token-streamed single samples don't carry them).</div>
      {:else if mode === 'rules' && allRulesOff}
        <div class="backend-error">Every highlight rule is toggled off for this chart — click a rule chip above to re-include it.</div>
      {:else if mode === 'rules'}
        <div class="backend-error">No enabled highlight rules apply to assistant turns — add rules in the sidebar's Highlights panel, or switch to “exact answers”.</div>
      {:else}
        <div class="backend-error">No response data to chart for this turn.</div>
      {/if}
    {:else}
      <svg class="chart-svg" viewBox="0 0 {layout.width} {layout.height}" width="100%" preserveAspectRatio="xMidYMid meet">
        <defs>
          {#each data.legend as entry, li (entry.key)}
            {#if entry.colors.length > 1}
              <pattern id="hlseg-{li}" patternUnits="userSpaceOnUse" width={entry.colors.length * 6} height="6" patternTransform="rotate(45)">
                {#each entry.colors as c, j (j)}
                  <rect x={j * 6} y="0" width="6" height="6" fill={c} />
                {/each}
              </pattern>
            {/if}
          {/each}
        </defs>
        {#each [0, 25, 50, 75, 100] as tick (tick)}
          {@const y = TOP_PAD + CHART_H - (tick / 100) * CHART_H}
          <line x1={LEFT_PAD} y1={y} x2={layout.width} y2={y} stroke="var(--color-border)" stroke-width="0.5" />
          <text x={LEFT_PAD - 6} y={y + 4} text-anchor="end" fill="var(--color-text-muted)" font-size="11">{tick}%</text>
        {/each}
        {#each layout.groups as g (g.bars[0].bi)}
          {#each g.bars as pb (pb.bi)}
            {#each pb.bar.segments as seg, si (seg.key)}
              {@const prevPct = pb.bar.segments.slice(0, si).reduce((sum, v) => sum + v.pct, 0)}
              {@const y = TOP_PAD + CHART_H - ((prevPct + seg.pct) / 100) * CHART_H}
              {@const h = (seg.pct / 100) * CHART_H}
              {#if h > 0}
                {@const li = data.legend.findIndex((e) => e.key === seg.key)}
                <rect
                  x={pb.x} {y} width={layout.bw} height={h} rx="1"
                  fill={segFill(seg.colors, li)}
                  class="chart-seg"
                  class:selected={isInspected(pb.bi, seg.key)}
                  role="button" tabindex="0" aria-label="{seg.label}: {seg.count} of {pb.bar.total}"
                  data-tooltip={segTooltip(seg, pb.bar.total)}
                  use:tip
                  onclick={() => toggleInspect(pb.bi, seg.key)}
                  onkeydown={(e) => e.key === 'Enter' && toggleInspect(pb.bi, seg.key)}
                />
                {#if h > 14}
                  <text x={pb.x + layout.bw / 2} y={y + h / 2 + 4} text-anchor="middle" pointer-events="none"
                    fill={contrastText(seg.colors[0] ?? NONE_COLOR)} font-size="10" font-weight="600">{seg.pct.toFixed(0)}%</text>
                {/if}
              {/if}
            {/each}
            {#if pb.bar.sub}
              <text x={pb.x + layout.bw / 2} y={TOP_PAD + CHART_H + SUB_Y} text-anchor="middle"
                fill="var(--color-text-muted)" font-size="9" font-style="italic">
                {#each barSubLines(g, pb.bar) as line, li (li)}
                  <tspan x={pb.x + layout.bw / 2} dy={li === 0 ? 0 : SUB_LH}>{line}</tspan>
                {/each}
              </text>
            {/if}
          {/each}
          <text x={g.center} y={TOP_PAD + CHART_H + layout.modelDy} text-anchor="middle" fill="var(--color-text)" font-size="11" font-weight="500">
            {#each wrapLabel(g.model) as line, li (li)}
              <tspan x={g.center} dy={li === 0 ? 0 : 13}>{line}</tspan>
            {/each}
            <tspan x={g.center} dy="14" fill="var(--color-text-muted)" font-size="10" font-weight="400">n={g.total}</tspan>
          </text>
        {/each}
      </svg>
      {#if mode === 'firsttoken'}
        <!-- Interactive legend: click a chip to exclude/re-include, drag one onto
             another to merge into one color, ✕ to un-merge / drop an added token. -->
        <div class="ft-chips" role="group" aria-label="First-token units">
          {#each ftChips as chip (chip.key)}
            {@const merged = chip.members.length > 1}
            <div
              class="ft-chip"
              class:off={chip.excluded}
              class:merged
              class:drop-target={ftDragOver === chip.key && ftDrag !== chip.key}
              draggable={!chip.excluded}
              role="button"
              tabindex="0"
              aria-pressed={!chip.excluded}
              data-tooltip={chip.excluded
                ? 'Excluded — click to re-include'
                : merged
                  ? 'Merged — click to exclude, drag onto another to grow, ⊗ to split'
                  : 'Click to exclude · drag onto another to merge'}
              use:tip
              onclick={() => toggleFtExclude(chip.key)}
              onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), toggleFtExclude(chip.key))}
              ondragstart={(e) => { ftDrag = chip.key; e.dataTransfer?.setData('text/plain', chip.key); }}
              ondragend={() => { ftDrag = null; ftDragOver = null; }}
              ondragover={(e) => { if (ftDrag && ftDrag !== chip.key) { e.preventDefault(); ftDragOver = chip.key; } }}
              ondragleave={() => { if (ftDragOver === chip.key) ftDragOver = null; }}
              ondrop={(e) => { e.preventDefault(); if (ftDrag) mergeUnits(ftDrag, chip.key); ftDrag = null; ftDragOver = null; }}
            >
              <span class="chart-legend-swatch" style={chip.color ? `background: ${chip.color}` : `background: ${NONE_COLOR}`}></span>
              <span class="ft-chip-label">{chip.label}</span>
              {#if merged}
                <button class="ft-chip-x" title="Split this group" aria-label="Split group"
                  onclick={(e) => { e.stopPropagation(); unmerge(chip.key); }}>⊗</button>
              {:else if chip.addedTid != null}
                <button class="ft-chip-x" title="Remove — back into the rest" aria-label="Remove added token"
                  onclick={(e) => { e.stopPropagation(); removeAdded(chip.addedTid!); }}>✕</button>
              {/if}
            </div>
          {/each}
          {#if !ftRenorm}
            <div class="chart-legend-item ft-rest-legend">
              <span class="chart-legend-swatch" style="background: {NONE_COLOR}"></span>
              <span class="chart-legend-label">{FT_REST}</span>
            </div>
          {/if}
          <label class="ft-renorm"
            data-tooltip="Drop the grey rest and rescale the shown tokens to 100%"
            use:tip>
            <input type="checkbox" bind:checked={ftRenorm} onchange={saveView} />
            <span>renormalize</span>
          </label>
        </div>
        <!-- Add a recorded-but-hidden token (from stored logprobs; no model call). -->
        <div class="ft-add">
          <input class="ft-add-input" type="text" placeholder="add a hidden token… (e.g. “ D”)"
            bind:value={ftQuery}
            data-tooltip="Search tokens recorded for this turn and pull one out of the rest"
            use:tip />
          {#if ftQuery.trim()}
            <div class="ft-matches">
              {#if ftMatches.length === 0}
                <span class="ft-no-match">no hidden token matches “{ftQuery.trim()}” in this turn's recorded logprobs</span>
              {:else}
                {#each ftMatches as m (m.tid)}
                  <button class="ft-match" onclick={() => addToken(m)}
                    data-tooltip="{m.kind} match · p={((prob(m.lp) ?? 0) * 100).toFixed(1)}% — click to add" use:tip>
                    <span class="ft-match-tok">{displayToken(m.t)}</span>
                    <span class="ft-match-p">{((prob(m.lp) ?? 0) * 100).toFixed(1)}%</span>
                  </button>
                {/each}
              {/if}
            </div>
          {/if}
        </div>
      {:else}
        <div class="chart-legend">
          {#each data.legend as entry (entry.key)}
            <div class="chart-legend-item">
              <span class="chart-legend-swatch" style={swatchStyle(entry.colors)}></span>
              <span class="chart-legend-label">{entry.label}</span>
            </div>
          {/each}
        </div>
      {/if}
      <!-- One inspected text, cut at the match cap when this part was bucketed
           under one: everything past it is dimmed and unpainted-looking, so a
           sample sitting in "no match" while a later hit is plainly visible
           reads as the cap doing its job, not the chart lying. The two halves
           render independently — a markdown construct straddling the exact
           boundary may render slightly off, same trade as renderPrefilled. -->
      {#snippet capped(text: string, applies: boolean)}
        {#if applies && matchLimit > 0 && text.length > matchLimit}
          <!-- eslint-disable-next-line svelte/no-at-html-tags -->
          {@html renderContent(text.slice(0, matchLimit), 'assistant')}
          <div class="chart-cap-line"><span>first {matchLimit} chars — matched to here</span></div>
          <div class="chart-cap-rest">
            <!-- eslint-disable-next-line svelte/no-at-html-tags -->
            {@html renderContent(text.slice(matchLimit), 'assistant')}
          </div>
        {:else}
          <!-- eslint-disable-next-line svelte/no-at-html-tags -->
          {@html renderContent(text, 'assistant')}
        {/if}
      {/snippet}
      {#if inspected}
        {@const capReasoning = mode === 'rules' && inspected.scope !== 'response'}
        {@const capContent = mode === 'rules' && inspected.scope !== 'thinking'}
        <div class="chart-inspect">
          <div class="chart-inspect-head">
            <span class="chart-legend-swatch" style={swatchStyle(inspected.seg.colors)}></span>
            <span class="chart-inspect-title">{inspected.seg.label} — {inspected.seg.count}/{inspected.bar.total} from {inspected.bar.model}{inspected.bar.sub ? ` · ${inspected.bar.sub}` : ''}</span>
            <button class="chart-inspect-close" onclick={() => (inspect = null)} aria-label="Close inspector">×</button>
          </div>
          <div class="chart-inspect-list">
            {#each inspected.samples as s, i (sampleKey(s, i))}
              <div class="chart-inspect-sample">
                {#if s.reasoning}
                  <details
                    class="chart-inspect-think"
                    open={foldOpen(s, i, inspected.scope)}
                    ontoggle={(e) => (thinkOpen[sampleKey(s, i)] = (e.currentTarget as HTMLDetailsElement).open)}
                  >
                    <summary>thinking</summary>
                    {@render capped(s.reasoning, capReasoning)}
                  </details>
                {/if}
                {#if s.content.trim()}
                  {@render capped(s.content, capContent)}
                {:else}
                  <span class="chart-no-answer">[no answer — all budget spent thinking]</span>
                {/if}
              </div>
            {/each}
          </div>
        </div>
      {/if}
    {/if}
  {/if}
</Modal>

<style>
  .chart-controls { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; margin-bottom: var(--space-3); }
  .chart-mode { display: inline-flex; border: 1px solid var(--color-border); border-radius: var(--radius); overflow: hidden; }
  .chart-mode-btn { border: none; background: var(--color-bg); color: var(--color-text-muted); font-size: 0.76rem; padding: 4px 10px; cursor: pointer; }
  .chart-mode-btn + .chart-mode-btn { border-left: 1px solid var(--color-border); }
  .chart-mode-btn.active { background: var(--color-accent); color: #fff; }
  .chart-mode-btn:disabled { opacity: 0.45; cursor: default; }
  .chart-note { font-size: 0.76rem; color: #b45309; background: #f59e0b14; border: 1px solid #f59e0b66; border-radius: var(--radius); padding: var(--space-2) var(--space-3); margin-bottom: var(--space-3); }
  .chart-turn, .chart-think { font-size: 0.78rem; padding: 3px 6px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); max-width: 340px; }
  .chart-check { display: inline-flex; align-items: center; gap: 5px; font-size: 0.78rem; color: var(--color-text-muted); cursor: pointer; user-select: none; }
  .chart-limit { width: 62px; font-size: 0.78rem; padding: 3px 4px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); }
  .chart-rules { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-3); }
  .chart-rule-chip { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-bg); color: var(--color-text); font-size: 0.76rem; padding: 2px 10px 2px 6px; cursor: pointer; }
  .chart-rule-chip:hover { border-color: var(--color-text-muted); }
  .chart-rule-chip.off { opacity: 0.45; text-decoration: line-through; }
  .chart-rule-swatch { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .chart-question { font-size: 0.82rem; color: var(--color-text-muted); font-style: italic; margin-bottom: var(--space-4); padding: var(--space-2) var(--space-3); background: var(--color-bg); border-radius: var(--radius); border: 1px solid var(--color-border-light); }
  .chart-question p { margin: 0; }
  .chart-question p + p { margin-top: 4px; }
  .chart-q-models { font-weight: 600; font-style: normal; color: var(--color-text); }
  .chart-no-answer { color: var(--color-text-muted); font-style: italic; font-size: 0.76rem; }
  .chart-svg { display: block; max-height: 62vh; }
  .chart-svg text { font-family: var(--font-sans); }
  .chart-seg { cursor: pointer; }
  .chart-seg:hover { filter: brightness(0.92); }
  .chart-seg.selected { stroke: var(--color-text); stroke-width: 1.5; }
  .chart-legend { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4); margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
  .chart-legend-item { display: flex; align-items: center; gap: var(--space-1); }
  .chart-legend-swatch { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }
  .chart-legend-label { font-size: 0.78rem; color: var(--color-text); }
  /* first-token interactive legend */
  .ft-chips { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
  .ft-chip { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-bg); color: var(--color-text); font-size: 0.76rem; padding: 2px 8px 2px 6px; cursor: grab; user-select: none; }
  .ft-chip:hover { border-color: var(--color-text-muted); }
  .ft-chip.off { opacity: 0.45; text-decoration: line-through; cursor: pointer; }
  .ft-chip.merged { border-style: dashed; border-color: var(--color-text-muted); }
  .ft-chip.drop-target { outline: 2px solid var(--color-accent); outline-offset: 1px; }
  .ft-chip-label { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ft-chip-x { border: none; background: none; color: var(--color-text-muted); font-size: 0.85rem; line-height: 1; cursor: pointer; padding: 0 1px; }
  .ft-chip-x:hover { color: var(--color-text); }
  .ft-rest-legend { margin-left: var(--space-2); }
  .ft-renorm { display: inline-flex; align-items: center; gap: 5px; margin-left: var(--space-3); font-size: 0.76rem; color: var(--color-text-muted); cursor: pointer; user-select: none; }
  .ft-renorm input { cursor: pointer; margin: 0; }
  .ft-add { position: relative; margin-top: var(--space-3); }
  .ft-add-input { width: 260px; max-width: 100%; font-size: 0.78rem; padding: 4px 8px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); }
  .ft-matches { display: flex; flex-wrap: wrap; gap: var(--space-1); margin-top: var(--space-2); max-height: 120px; overflow-y: auto; }
  .ft-match { display: inline-flex; align-items: center; gap: 5px; border: 1px dashed var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); font-size: 0.74rem; padding: 2px 7px; cursor: pointer; }
  .ft-match:hover { border-color: var(--color-accent); }
  .ft-match-tok { font-family: var(--font-mono, monospace); }
  .ft-match-p { color: var(--color-text-muted); }
  .ft-no-match { font-size: 0.74rem; color: var(--color-text-muted); font-style: italic; }
  .chart-inspect { margin-top: var(--space-3); border: 1px solid var(--color-border-light); border-radius: var(--radius); background: var(--color-bg); }
  .chart-inspect-head { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--color-border-light); }
  .chart-inspect-title { font-size: 0.8rem; font-weight: 600; color: var(--color-text); flex: 1; }
  .chart-inspect-close { border: none; background: none; color: var(--color-text-muted); font-size: 1rem; cursor: pointer; padding: 0 4px; }
  .chart-inspect-close:hover { color: var(--color-text); }
  .chart-inspect-list { max-height: 260px; overflow-y: auto; padding: var(--space-2) var(--space-3); display: flex; flex-direction: column; gap: var(--space-2); }
  .chart-inspect-sample { font-size: 0.8rem; color: var(--color-text); padding: var(--space-2); border: 1px solid var(--color-border-light); border-radius: var(--radius); background: var(--color-surface); overflow-wrap: anywhere; }
  .chart-inspect-sample :global(p) { margin: 0 0 0.4em; }
  .chart-inspect-sample :global(p:last-child) { margin-bottom: 0; }
  /* beyond the match cap: dimmed, and its highlight marks de-tinted so only the
     text the bucketing actually saw looks painted */
  .chart-cap-line { display: flex; align-items: center; gap: var(--space-2); margin: var(--space-1) 0; font-size: 0.68rem; color: var(--color-text-muted); white-space: nowrap; }
  .chart-cap-line::after { content: ''; flex: 1; border-top: 1px dashed var(--color-border); }
  .chart-cap-rest { opacity: 0.55; }
  .chart-cap-rest :global(mark.hl-mark) { background: none; }
  .chart-inspect-think { margin-bottom: var(--space-1); }
  .chart-inspect-think summary { font-size: 0.72rem; color: var(--color-text-muted); cursor: pointer; }
</style>
