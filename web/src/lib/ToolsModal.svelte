<script lang="ts">
  // Edit the open workspace's tools: their definitions (JSON — see lib/tools-def.ts
  // for what the parser accepts), how each one's calls are answered (by hand, a
  // fixed text, or python run by the server — api/tool_handlers.py), the cap on
  // auto-answered rounds, and named sets shared across workspaces.
  import { onMount, untrack } from 'svelte';
  import Modal from '$lib/Modal.svelte';
  import { parseToolDefs, formatToolDefs, toolName, cleanHandlers, defFields, withDefFields } from '$lib/tools-def';
  import { api, type ToolSet, type ToolTestResult } from '$lib/api';
  import { tip } from '$lib/tooltip.svelte';
  import Icon from '$lib/Icon.svelte';
  import type { ToolDef, ToolHandler, ToolHandlers } from '$lib/types';

  let {
    tools,
    handlers,
    maxSteps,
    codeAllowed,
    workspaceId,
    onsave,
    onclose
  }: {
    tools: ToolDef[] | null;
    handlers: ToolHandlers | null;
    maxSteps: number | null;
    codeAllowed: boolean;
    workspaceId: string | null;
    onsave: (tools: ToolDef[] | null, handlers: ToolHandlers | null, maxSteps: number | null) => void;
    onclose: () => void;
  } = $props();

  const MODES: [ToolHandler['mode'], string, string][] = [
    ['manual', 'Manual', 'You answer each call (the result form)'],
    ['static', 'Static', 'Every call gets the same text'],
    ['python', 'Python', 'The server runs your code on each call']
  ];

  // seeded once: the editor owns the text while open
  let text = $state(untrack(() => formatToolDefs(tools)));
  // the JSON is folded once there are tools: the handlers are what gets edited
  let defsOpen = $state(untrack(() => !tools?.length));
  let parsed = $derived(parseToolDefs(text));
  // the handler rows follow the LAST VALID definitions (a half-typed JSON edit
  // must not make them blink out)
  let names = $state<string[]>(untrack(() => (tools ?? []).map(toolName)));
  $effect(() => {
    if (parsed.error === undefined) names = (parsed.tools ?? []).map(toolName);
  });
  let hs = $state<ToolHandlers>(untrack(() => structuredClone($state.snapshot(handlers) ?? {})));
  let steps = $state(untrack(() => (maxSteps == null ? '' : String(maxSteps))));
  let stepsNum = $derived(steps.trim() === '' ? null : Number(steps));
  let stepsBad = $derived(stepsNum !== null && !(Number.isInteger(stepsNum) && stepsNum >= 0 && stepsNum <= 100));

  function h(name: string): ToolHandler {
    return hs[name] ?? { mode: 'manual' };
  }
  function setMode(name: string, mode: ToolHandler['mode']) {
    hs[name] = { ...h(name), mode };
  }
  function setOn(name: string, on: boolean) {
    const { disabled: _, ...rest } = h(name);
    hs[name] = on ? rest : { ...rest, disabled: true };
  }
  /** Remove a tool: its definition from the JSON and its handler (Cancel undoes). */
  // ── one tool's definition, unfolded under its row (edits rewrite the JSON) ──
  let defOpen = $state<Record<string, boolean>>({});
  // a parameters / raw-definition box that doesn't parse yet keeps its own text
  let defDraft = $state<Record<string, { text: string; error: string } | undefined>>({});
  function defOf(name: string): ToolDef | undefined {
    return parsed.error === undefined ? parsed.tools?.find((t) => toolName(t) === name) : undefined;
  }
  function putDef(name: string, next: ToolDef) {
    if (parsed.error !== undefined) return;
    const newName = toolName(next);
    text = formatToolDefs((parsed.tools ?? []).map((t) => (toolName(t) === name ? next : t)));
    if (newName !== name) {
      // a rename carries the handler, the fold and the test box along
      if (hs[name]) { hs[newName] = hs[name]; delete hs[name]; }
      defOpen[newName] = defOpen[name];
      testArgs[newName] = testArgs[name];
      delete defOpen[name];
    }
  }
  function editField(name: string, field: 'name' | 'description', v: string) {
    const t = defOf(name);
    if (t) putDef(name, withDefFields(t, { [field]: v }));
  }
  function editJson(name: string, v: string, native: boolean) {
    const t = defOf(name);
    if (!t) return;
    try {
      const val = v.trim() ? JSON.parse(v) : undefined;
      if (native && (!val || typeof val !== 'object' || typeof val.type !== 'string'))
        throw new Error('a tool definition needs a "type"');
      defDraft[name] = undefined;
      putDef(name, native ? val : withDefFields(t, { parameters: val }));
    } catch (e) {
      defDraft[name] = { text: v, error: (e as Error).message };
    }
  }
  function pretty(v: unknown): string {
    return v === undefined ? '' : JSON.stringify(v, null, 2);
  }

  function remove(name: string) {
    if (parsed.error !== undefined) return;
    text = formatToolDefs((parsed.tools ?? []).filter((t) => toolName(t) !== name));
    delete hs[name];
    delete testOut[name];
  }
  let missingCode = $derived(names.find((n) => h(n).mode === 'python' && !(h(n).code ?? '').trim()));
  let blocked = $derived(parsed.error !== undefined || stepsBad || !!missingCode);

  function save() {
    if (blocked) return;
    onsave(parsed.tools ?? null, cleanHandlers(hs, names), stepsNum);
    onclose();
  }

  // ── test run (the UNSAVED handler, on sample arguments) ──
  let testArgs = $state<Record<string, string>>({});
  let testOut = $state<Record<string, ToolTestResult | 'running'>>({});
  async function test(name: string) {
    let args: unknown;
    try {
      args = JSON.parse(testArgs[name]?.trim() || '{}');
    } catch (e) {
      testOut[name] = { ok: false, error: `Arguments aren't JSON: ${(e as Error).message}` };
      return;
    }
    const spec = cleanHandlers({ [name]: h(name) }, [name])?.[name];
    if (!spec) return;
    testOut[name] = 'running';
    try {
      testOut[name] = await api.testToolHandler({
        handler: spec, name, args,
        context: { workspace_id: workspaceId, panel: null, step: 1, thread: [] }
      });
    } catch (e) {
      testOut[name] = { ok: false, error: (e as Error).message };
    }
  }

  // ── named sets ──
  let sets = $state<ToolSet[]>([]);
  let pick = $state('');
  let setName = $state('');
  let setMsg = $state<string | null>(null);
  onMount(() => {
    api.listToolSets().then((s) => (sets = s)).catch(() => {});
  });
  function loadSet(e: MouseEvent) {
    const s = sets.find((x) => x.name === pick);
    if (!s) return;
    // Shift: the set's handlers only — keep these definitions (a recorded call's)
    if (!e.shiftKey) text = formatToolDefs(s.tools);
    hs = structuredClone(s.tool_handlers ?? {});
    steps = s.tool_max_steps == null ? '' : String(s.tool_max_steps);
    testOut = {};
  }
  async function saveSet() {
    const name = setName.trim();
    if (!name || blocked) return;
    try {
      await api.saveToolSet(name, {
        tools: parsed.tools ?? null, tool_handlers: cleanHandlers(hs, names), tool_max_steps: stepsNum
      });
      sets = await api.listToolSets();
      pick = name;
      setMsg = `Saved “${name}”`;
    } catch (e) {
      setMsg = (e as Error).message;
    }
  }

  // Tab in a code box indents instead of leaving the field
  function codeKeys(e: KeyboardEvent) {
    if (e.key !== 'Tab' || e.shiftKey) return;
    e.preventDefault();
    const el = e.currentTarget as HTMLTextAreaElement;
    el.setRangeText('    ', el.selectionStart, el.selectionEnd, 'end');
    el.dispatchEvent(new Event('input'));
  }
</script>

<Modal title="Tools" {onclose} modalStyle="width: 760px; max-width: 94vw;">
  <button class="defs-toggle" onclick={() => (defsOpen = !defsOpen)} aria-expanded={defsOpen} data-testid="tools-defs-toggle">
    <svg class="section-chevron" class:open={defsOpen} width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
    <span class="tools-status" class:err={parsed.error !== undefined} data-testid="tools-status">
      {#if parsed.error !== undefined}{parsed.error}{:else if parsed.tools}{parsed.tools.length} tool{parsed.tools.length === 1 ? '' : 's'}: {parsed.tools.map(toolName).join(', ')}{:else}No tools{/if}
    </span>
  </button>
  {#if defsOpen || parsed.error !== undefined}
    <textarea
      class="tools-json"
      data-testid="tools-json"
      bind:value={text}
      spellcheck="false"
      rows="10"
      placeholder={'[{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}]'}
      onkeydown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
      }}
    ></textarea>
  {/if}

  {#if names.length}
    <div class="th-list" data-testid="tool-handlers">
      {#each names as name (name)}
        {@const hd = h(name)}
        {@const out = testOut[name]}
        <div class="th-row" class:off={hd.disabled} data-tool={name}>
          <div class="th-head">
            <input type="checkbox" class="th-on" checked={!hd.disabled} onchange={(e) => setOn(name, e.currentTarget.checked)}
              data-tooltip={hd.disabled ? 'Off: not offered to the model' : 'Offered to the model — untick to switch it off'} use:tip
              aria-label="Offer {name} to the model" data-testid="tool-on" />
            <button class="th-name" onclick={() => (defOpen[name] = !defOpen[name])} aria-expanded={!!defOpen[name]} data-testid="tool-def-toggle">
              <svg class="section-chevron" class:open={defOpen[name]} width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
              <code>{name}</code>
            </button>
            <div class="seg-toggle" role="group" aria-label="How {name} is answered">
              {#each MODES as [m, label, hint] (m)}
                <button class="seg-btn" class:active={hd.mode === m} data-mode={m} onclick={() => setMode(name, m)} data-tooltip={hint} use:tip>{label}</button>
              {/each}
            </div>
            {#if hd.mode !== 'manual'}
              <label class="th-cont" data-tooltip="Sample the model's next turn after answering" use:tip>
                <input type="checkbox" checked={hd.auto_continue !== false} onchange={(e) => (hs[name] = { ...hd, auto_continue: e.currentTarget.checked })} />
                continue
              </label>
            {/if}
            <button class="th-del" onclick={() => remove(name)} disabled={parsed.error !== undefined}
              data-tooltip="Remove this tool (Cancel undoes)" use:tip aria-label="Remove {name}" data-testid="tool-remove"><Icon name="trash" size={13} /></button>
          </div>
          {#if defOpen[name]}
            {@const def = defOf(name)}
            <div class="th-def" data-testid="tool-def">
              {#if !def}
                <div class="tools-status err">Fix the definitions JSON to edit this tool here.</div>
              {:else}
                {@const f = defFields(def)}
                {@const draft = defDraft[name]}
                {#if f.native}
                  <span class="th-label">Definition</span>
                  <textarea class="th-code" rows="4" spellcheck="false" value={draft?.text ?? pretty(def)} oninput={(e) => editJson(name, e.currentTarget.value, true)} data-testid="tool-def-json"></textarea>
                {:else}
                  <label class="th-field"><span class="th-label">Name</span>
                    <input class="th-args" value={f.name} onchange={(e) => editField(name, 'name', e.currentTarget.value.trim())} data-testid="tool-def-name" />
                  </label>
                  <label class="th-field"><span class="th-label">Description</span>
                    <textarea class="th-text" rows="3" value={f.description} oninput={(e) => editField(name, 'description', e.currentTarget.value)} data-testid="tool-def-desc"></textarea>
                  </label>
                  <label class="th-field"><span class="th-label">Parameters (JSON schema)</span>
                    <textarea class="th-code" rows="8" spellcheck="false" value={draft?.text ?? pretty(f.parameters)} oninput={(e) => editJson(name, e.currentTarget.value, false)} onkeydown={codeKeys} data-testid="tool-def-params"></textarea>
                  </label>
                {/if}
                {#if draft}<div class="tools-status err">{draft.error}</div>{/if}
              {/if}
            </div>
          {/if}
          {#if hd.mode === 'static'}
            <textarea class="th-text" rows="2" placeholder="What every call returns" data-testid="handler-static"
              value={hd.text ?? ''} oninput={(e) => (hs[name] = { ...hd, text: e.currentTarget.value })}></textarea>
          {:else if hd.mode === 'python'}
            <div class="th-sig">def handler(name, args, call_id, context):</div>
            <textarea class="th-code" rows="6" spellcheck="false" data-testid="handler-code"
              placeholder={'return f"sent: {args[\'message\']}"'}
              value={hd.code ?? ''} oninput={(e) => (hs[name] = { ...hd, code: e.currentTarget.value })}
              onkeydown={codeKeys}></textarea>
            {#if !codeAllowed}
              <div class="th-off" data-testid="python-off">Python handlers are off on this server (start it with <code>--allow-tool-code</code>): calls stay manual.</div>
            {/if}
          {/if}
          {#if hd.mode !== 'manual'}
            <div class="th-test">
              <input class="th-args" placeholder={'{} — the call\'s arguments'} bind:value={testArgs[name]} data-testid="handler-args"
                onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); test(name); } }} />
              <button class="btn-skip" onclick={() => test(name)} disabled={out === 'running' || (hd.mode === 'python' && !(hd.code ?? '').trim())} data-testid="handler-test">Test</button>
            </div>
            {#if out && out !== 'running'}
              <div class="th-out" class:err={!out.ok} data-testid="handler-out">
                {#if out.ok}{#if out.image}<img src={out.image} alt="handler screenshot" />{/if}<pre>{out.text ?? ''}</pre>{:else}<pre>{out.error}</pre>{/if}
                {#if out.logs}<pre class="th-logs">{out.logs}</pre>{/if}
              </div>
            {/if}
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  <div class="th-foot">
    <label class="th-steps" data-tooltip="Auto-answered rounds per send before it stops (default 5)" use:tip>
      Max steps <input type="number" min="0" max="100" placeholder="5" bind:value={steps} class:err={stepsBad} data-testid="tool-max-steps" />
    </label>
    <span class="th-sets">
      <select bind:value={pick} data-testid="tool-set-pick" aria-label="Tool set">
        <option value="">Tool set…</option>
        {#each sets as s (s.name)}<option value={s.name}>{s.name}</option>{/each}
      </select>
      <button class="btn-skip" disabled={!pick} onclick={loadSet} data-tooltip="Load this set (Shift: its handlers only)" use:tip data-testid="tool-set-load">Load</button>
      <input class="th-setname" placeholder="Set name" bind:value={setName} data-testid="tool-set-name" />
      <button class="btn-skip" disabled={!setName.trim() || blocked} onclick={saveSet} data-testid="tool-set-save">Save as set</button>
    </span>
  </div>
  {#if setMsg}<div class="tools-status">{setMsg}</div>{/if}
  {#if missingCode}<div class="tools-status err">{missingCode}: a python handler needs code</div>{/if}

  <div class="tag-form-actions">
    <button class="btn-new" onclick={save} disabled={blocked} data-testid="tools-save">Save</button>
    <button class="btn-skip" onclick={onclose}>Cancel</button>
  </div>
</Modal>

<style>
  .tools-json, .th-code, .th-text { width: 100%; box-sizing: border-box; font-family: 'Courier New', Courier, monospace; font-size: 0.8rem; line-height: 1.45; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: var(--space-2); resize: vertical; }
  .th-text { font-family: inherit; }
  .tools-json:focus, .th-code:focus, .th-text:focus, .th-args:focus, .th-setname:focus { outline: none; border-color: var(--color-accent); }
  .tools-status { font-size: 0.76rem; color: var(--color-text-muted); padding: var(--space-2) 0; overflow-wrap: anywhere; }
  .tools-status.err { color: var(--color-no); }
  .btn-skip { background: none; border: 1px solid var(--color-border); border-radius: var(--radius-sm); color: var(--color-text-muted); padding: var(--space-2) var(--space-3); font-size: 0.78rem; cursor: pointer; }
  .btn-skip:disabled { opacity: 0.5; cursor: default; }
  .defs-toggle { display: flex; align-items: center; gap: 6px; width: 100%; background: none; border: none; padding: 0 0 var(--space-1); cursor: pointer; text-align: left; color: var(--color-text-muted); }
  .defs-toggle .tools-status { padding: var(--space-1) 0; }
  .th-list { display: flex; flex-direction: column; gap: var(--space-2); margin: var(--space-2) 0; }
  .th-row { border: 1px solid var(--color-border); border-radius: var(--radius); padding: var(--space-2); display: flex; flex-direction: column; gap: 6px; }
  .th-head { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
  .th-row.off > :not(.th-head), .th-row.off .th-name, .th-row.off .seg-toggle, .th-row.off .th-cont { opacity: 0.5; }
  .th-on { margin: 0; cursor: pointer; }
  .th-del { background: none; border: none; color: var(--color-text-muted); cursor: pointer; padding: 2px; display: inline-flex; border-radius: var(--radius-sm); }
  .th-del:hover:not(:disabled) { color: var(--color-no); background: var(--color-surface-alt); }
  .th-del:disabled { opacity: 0.4; cursor: default; }
  .th-name { display: inline-flex; align-items: center; gap: 4px; background: none; border: none; padding: 0; cursor: pointer; color: var(--color-text); text-align: left; min-width: 0; flex: 1 1 140px; }
  .th-name code { font-family: 'Courier New', Courier, monospace; font-weight: 700; font-size: 0.85rem; overflow-wrap: anywhere; }
  .th-def { display: flex; flex-direction: column; gap: 6px; padding-left: 20px; }
  .th-field { display: flex; flex-direction: column; gap: 3px; }
  .th-label { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-muted); }
  .th-cont { display: inline-flex; align-items: center; gap: 4px; font-size: 0.76rem; color: var(--color-text-secondary); cursor: pointer; }
  .th-sig { font-family: 'Courier New', Courier, monospace; font-size: 0.78rem; color: var(--color-text-muted); }
  .th-off { font-size: 0.76rem; color: var(--color-no); }
  .th-test { display: flex; gap: var(--space-2); align-items: center; }
  .th-args, .th-setname { flex: 1; min-width: 0; font-family: 'Courier New', Courier, monospace; font-size: 0.78rem; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: 4px 8px; }
  .th-setname { font-family: inherit; flex: 0 1 140px; }
  .th-out { border-left: 3px solid var(--color-accent); padding: 2px var(--space-2); font-size: 0.78rem; }
  .th-out.err { border-left-color: var(--color-no); color: var(--color-no); }
  .th-out pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-family: 'Courier New', Courier, monospace; max-height: 220px; overflow-y: auto; }
  .th-out .th-logs { color: var(--color-text-muted); margin-top: 4px; }
  .th-out img { max-height: 90px; max-width: 200px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); display: block; margin-bottom: 4px; }
  .th-foot { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); flex-wrap: wrap; font-size: 0.78rem; color: var(--color-text-secondary); }
  .th-steps { display: inline-flex; align-items: center; gap: 6px; }
  .th-steps input { width: 56px; font-size: 0.78rem; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: 3px 6px; }
  .th-steps input.err { border-color: var(--color-no); }
  .th-sets { display: inline-flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
  .th-sets select { font-size: 0.78rem; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: 4px 6px; max-width: 180px; }
</style>
