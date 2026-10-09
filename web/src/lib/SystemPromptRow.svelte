<!-- The workspace system prompt as the FIRST row of a panel's conversation —
     where it sits in the actual request. Folded it's one line (the prompt, or
     "none"); unfolded it's the editor. The text is workspace-wide, so every
     panel's row edits the same prompt; only the fold is per panel.
     Read-only: shown only when a prompt applied, click to expand. -->
<script lang="ts">
  import SplitChip from './SplitChip.svelte';
  import { tip } from './tooltip.svelte';

  let {
    text,
    enabled,
    open,
    shared,
    readOnly,
    disabled,
    onpower,
    onfold,
    oninput
  }: {
    text: string;
    /** the power flag (s.system_enabled, legacy-derived) */
    enabled: boolean;
    open: boolean;
    /** >1 panel: the row says the prompt is common to all of them */
    shared: boolean;
    readOnly: boolean;
    /** editor gated until the workspace is open (see +page's load-window note) */
    disabled: boolean;
    onpower: () => void;
    onfold: () => void;
    oninput: (v: string) => void;
  } = $props();

  let active = $derived(enabled && text.trim().length > 0);
  let roExpanded = $state(false);
</script>

{#if readOnly}
  {#if active}
    <button
      class="sys-row ro"
      class:expanded={roExpanded}
      data-testid="system-row"
      onclick={() => (roExpanded = !roExpanded)}
    >
      <span class="sys-row-tag">system</span>
      <span class="sys-row-text">{roExpanded ? text : text.replace(/\s+/g, ' ')}</span>
    </button>
  {/if}
{:else}
  <div class="sys-row" class:active class:open data-testid="system-row">
    <div class="sys-row-head">
      <SplitChip
        label="system prompt"
        {active}
        {enabled}
        {open}
        testid="system"
        powerTip="Apply / mute the workspace system prompt (hollow dot = muted)"
        editTip="Edit the system prompt (shared by every panel)"
        {onpower}
        {onfold}
      />
      {#if !open}
        <button class="sys-row-peek" class:muted={!enabled} onclick={onfold} data-tooltip="Click to edit" use:tip>
          {text.trim() ? text.replace(/\s+/g, ' ') : 'none'}
        </button>
      {:else if shared}
        <span class="sys-row-note">all panels</span>
      {/if}
    </div>
    {#if open}
      <textarea
        class="prefill-textarea sys-row-textarea"
        class:muted={!enabled}
        {disabled}
        value={text}
        oninput={(e) => oninput((e.target as HTMLTextAreaElement).value)}
        rows="3"
        placeholder="Optional system prompt..."
      ></textarea>
      {#if !enabled && text.trim()}<div class="prefill-offhint">muted — kept, not applied to sends</div>{/if}
    {/if}
  </div>
{/if}

<style>
  .sys-row { flex-shrink: 0; padding: var(--space-2) var(--space-3); background: var(--color-system-bg); border: 1px solid var(--color-border); border-radius: var(--radius-lg); min-width: 0; }
  .sys-row.active { border-color: color-mix(in srgb, var(--color-accent) 40%, transparent); }
  .sys-row-head { display: flex; align-items: center; gap: var(--space-2); min-width: 0; }
  .sys-row-peek { flex: 1; min-width: 0; text-align: left; background: none; border: none; padding: 0; cursor: pointer; font-family: var(--font-mono); font-size: 0.74rem; color: var(--color-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sys-row-peek.muted { color: var(--color-text-muted); font-style: italic; }
  .sys-row-note { font-size: 0.68rem; color: var(--color-text-muted); }
  .sys-row-textarea { background: var(--color-bg); }
  .sys-row.ro { display: flex; align-items: baseline; gap: var(--space-2); width: 100%; text-align: left; cursor: pointer; }
  .sys-row-tag { flex-shrink: 0; font-size: 0.6rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-accent); }
  .sys-row-text { min-width: 0; font-family: var(--font-mono); font-size: 0.74rem; color: var(--color-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sys-row.ro.expanded .sys-row-text { white-space: pre-wrap; overflow: visible; }
</style>
