<script lang="ts">
  // One panel's sampling overrides (compare mode), drawn in its sidebar model
  // block. Folded: a link carrying the active overrides ("temp 0.3 · effort
  // high"). Open: one control per param, each starting on "sidebar" (inherit).
  // State lives in +page (session pref keyed `<workspace>:<panel>`); this
  // component only reports edits. See lib/panel-params.ts.
  import { tip } from '$lib/tooltip.svelte';
  import { EFFORT_LEVELS, summarizePanelParams, type PanelParams } from '$lib/panel-params';
  import type { Effort, OpenRouterCaps } from '$lib/types';

  let {
    params,
    open,
    global,
    caps,
    isOpenrouter,
    onToggle,
    onSet,
    onClear
  }: {
    params: PanelParams | undefined;
    open: boolean;
    /** The sidebar's values — what an unset override inherits (shown as the default option). */
    global: { temperature: number; max_tokens: number; thinking: boolean | 'both'; reasoning_effort: Effort | null };
    caps: OpenRouterCaps | undefined;
    isOpenrouter: boolean;
    onToggle: () => void;
    onSet: <K extends keyof PanelParams>(key: K, value: PanelParams[K] | undefined) => void;
    onClear: () => void;
  } = $props();

  let summary = $derived(summarizePanelParams(params));
  const thinkWord = (t: boolean | 'both') => (t === 'both' ? 'both' : t ? 'on' : 'off');
  // The model's own levels when the catalog knows them (others are coerced by
  // OpenRouter), in the canonical low→high order.
  let efforts = $derived(caps?.efforts?.length ? EFFORT_LEVELS.filter((e) => caps!.efforts.includes(e)) : EFFORT_LEVELS);
  let ignoresTemp = $derived(!!caps?.ignores.includes('temperature'));

  function numOrUndef(v: string, lo: number, hi: number, round = false): number | undefined {
    if (v.trim() === '') return undefined;
    const n = Number(v);
    if (!Number.isFinite(n)) return undefined;
    const c = Math.max(lo, Math.min(hi, n));
    return round ? Math.round(c) : c;
  }
</script>

<div class="pp-root" data-testid="panel-params">
  <button
    class="pp-toggle"
    class:set={!!summary}
    data-testid="panel-params-toggle"
    data-tooltip={summary ? 'This panel samples with these instead of the sidebar' : "This panel's own sampling params"}
    use:tip
    onclick={onToggle}
  >
    <svg class="pp-chev" class:open width="10" height="10" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    {summary || 'panel params'}
  </button>
  {#if open}
    <div class="pp-grid">
      <label for="pp-think">Thinking</label>
      <select id="pp-think" class="pp-select" data-testid="pp-thinking"
        value={params?.thinking == null ? '' : String(params.thinking)}
        onchange={(e) => {
          const v = (e.target as HTMLSelectElement).value;
          onSet('thinking', v === '' ? undefined : v === 'both' ? 'both' : v === 'true');
        }}>
        <option value="">sidebar ({thinkWord(global.thinking)})</option>
        <option value="false">off</option>
        <option value="true">on</option>
        <option value="both">both</option>
      </select>

      {#if isOpenrouter}
        <label for="pp-effort">Effort</label>
        <select id="pp-effort" class="pp-select" data-testid="pp-effort"
          value={params?.reasoning_effort ?? ''}
          onchange={(e) => {
            const v = (e.target as HTMLSelectElement).value;
            onSet('reasoning_effort', v === '' ? undefined : (v as PanelParams['reasoning_effort']));
          }}>
          <option value="">sidebar ({global.reasoning_effort ?? 'model default'})</option>
          <option value="default">model default{caps?.default_effort ? ` (${caps.default_effort})` : ''}</option>
          {#each efforts as e (e)}<option value={e}>{e}</option>{/each}
        </select>
      {/if}

      <label for="pp-temp">Temp</label>
      <input id="pp-temp" class="pp-input" type="number" min="0" max="2" step="0.05" data-testid="pp-temperature"
        placeholder={`sidebar (${global.temperature.toFixed(2)})`}
        disabled={ignoresTemp}
        value={params?.temperature ?? ''}
        onchange={(e) => onSet('temperature', numOrUndef((e.target as HTMLInputElement).value, 0, 2))} />
      {#if ignoresTemp}<span class="pp-note" data-tooltip="OpenRouter drops it for this model without an error" use:tip>ignored by this model</span>{/if}

      <label for="pp-max">Max tokens</label>
      <input id="pp-max" class="pp-input" type="number" min="1" max="128000" data-testid="pp-max-tokens"
        placeholder={`sidebar (${global.max_tokens})`}
        value={params?.max_tokens ?? ''}
        onchange={(e) => onSet('max_tokens', numOrUndef((e.target as HTMLInputElement).value, 1, 128000, true))} />
    </div>
    {#if summary}
      <button class="pp-clear" data-testid="pp-clear" onclick={onClear}>use the sidebar's</button>
    {/if}
  {/if}
</div>

<style>
  .pp-root { display: flex; flex-direction: column; gap: var(--space-1); }
  .pp-toggle { display: inline-flex; align-items: center; gap: 4px; align-self: flex-start; max-width: 100%; background: none; border: none; padding: 0; cursor: pointer; font-size: 0.7rem; color: var(--color-text-muted); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pp-toggle:hover { color: var(--color-accent); }
  .pp-toggle.set { color: var(--color-accent); }
  .pp-chev { flex-shrink: 0; transition: transform 0.12s; }
  .pp-chev.open { transform: rotate(90deg); }
  .pp-grid { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 4px var(--space-2); padding-left: 14px; }
  .pp-grid label { font-size: 0.7rem; color: var(--color-text-muted); }
  .pp-select, .pp-input { min-width: 0; padding: 2px var(--space-2); background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text); font-size: 0.72rem; }
  .pp-input { font-family: var(--font-mono); }
  .pp-input:disabled { opacity: 0.5; }
  .pp-note { grid-column: 2; font-size: 0.65rem; color: var(--color-text-muted); font-style: italic; margin-top: -2px; }
  .pp-clear { align-self: flex-start; margin-left: 14px; background: none; border: none; padding: 0; cursor: pointer; font-size: 0.68rem; color: var(--color-text-muted); text-decoration: underline; }
  .pp-clear:hover { color: var(--color-accent); }
</style>
