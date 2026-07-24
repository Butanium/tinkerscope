<!--
  The `?` modal: what the UI does, for a HUMAN.

  Why it exists: every other doc in this repo is written for an agent driving the
  tool from a terminal (the `tinkerscope` skill) or for whoever maintains it
  (docs/*.md). Nothing explained the browser UI to the person using it, so the
  discoverable-by-accident features (keyboard row nav, the shift/ctrl modifier
  axes, branch-from-start) were effectively invisible. Its prose twin is the
  `tinkerscope-guide` skill — keep the two in sync when behavior changes.

  Two tabs on purpose: "Guide" answers *what is this screen*, "Keys" is the
  lookup table you reopen later. Both stay short enough to scan.
-->
<script lang="ts">
  import Modal from '$lib/Modal.svelte';

  let { onclose }: { onclose: () => void } = $props();

  let tab = $state<'guide' | 'keys'>('guide');

  /** [keys, what it does, extra context] — rendered as the Keys tab's rows. */
  const KEYS: { group: string; rows: [string, string][] }[] = [
    {
      group: 'Composer',
      rows: [
        ['Enter', 'Send to every panel (or just the picked send targets)'],
        ['Shift + Enter', 'Newline'],
        ['Esc', 'Enter prompt history — then ↑/↓ to browse, Esc again to leave'],
        ['Enter (per-panel box)', 'Continue that one panel only — the others stay put']
      ]
    },
    {
      group: 'Chat rows (click a row first)',
      rows: [
        ['↑ / ↓', 'Move the focus ring up/down that panel'],
        ['← / →', "Cycle the focused row's ‹k/N› sibling branches"],
        ['Esc', 'Drop the focus ring']
      ]
    },
    {
      group: 'Row toolbar modifiers (hold, then click)',
      rows: [
        ['Ctrl / ⌘', 'Do it in ALL panels at once — edit · regenerate · delete · continue (only with >1 panel)'],
        ['Shift + edit (user row)', 'Fork a full editable copy of the conversation — generates nothing'],
        ['Shift + regenerate', 'Replace this branch in place instead of adding a sibling'],
        ['Shift + delete', 'Delete every branch at this turn, not just the shown one'],
        ['Shift + continue', 'Resume INSIDE the think block (assistant turns with reasoning)'],
        ['Shift + copy', 'Include the thinking text in the copy'],
        ['Shift + bookmark', 'Pin instantly, skipping the note dialog']
      ]
    },
    {
      group: 'Sidebar modifiers',
      rows: [
        ['Shift + New workspace', 'Create it BLANK — otherwise it inherits the current models'],
        ['Shift + Add panel', 'Add an empty panel — otherwise it clones the last one']
      ]
    },
    {
      group: 'Anywhere',
      rows: [['Esc (modal open)', 'Close the modal']]
    }
  ];
</script>

<Modal title="How to use tinkerscope" {onclose} modalStyle="width: 760px;">
  {#snippet headerExtra()}
    <div class="help-tabs">
      <button class="help-tab" class:active={tab === 'guide'} onclick={() => (tab = 'guide')}>Guide</button>
      <button class="help-tab" class:active={tab === 'keys'} onclick={() => (tab = 'keys')}>Keys</button>
    </div>
  {/snippet}

  {#if tab === 'guide'}
    <p class="help-lede">
      A playground for checkpoints from your Tinker training runs. Point it at a directory of runs, pick
      models, and talk to them side by side — every reply is kept, so you can branch, resample and compare
      instead of losing what the model said last time.
    </p>

    <section class="help-sec">
      <h3>Workspaces and panels</h3>
      <p>
        A <b>workspace</b> is one saved conversation setup: its message tree, its system prompt, and the set of
        <b>panels</b> it shows. One panel = one model (a run checkpoint, a raw base model, or an OpenRouter
        reference model). Sending a message fires it into <em>every</em> panel, so the columns are the same
        prompt answered by different models.
      </p>
      <p class="help-note">
        Switching workspaces restores its models too. A new workspace inherits the current models
        (Shift → blank). Panels reorder by dragging a column header; the sidebar pickers and send chips follow.
      </p>
    </section>

    <section class="help-sec">
      <h3>Nothing is destroyed — branches</h3>
      <p>
        Regenerating, editing a message, or drawing N samples does not overwrite anything: each becomes a
        <b>sibling branch</b>, and the row shows a <code>‹ k/N ›</code> cycler you click (or ←/→) to walk them.
        Deleting prunes only that subtree. So "what did it say the other three times?" is always answerable.
      </p>
      <p class="help-note">
        Editing a <em>user</em> message forks and regenerates from there; Shift+edit forks a full editable
        copy and generates nothing. Editing an <em>assistant</em> message just writes a manual branch —
        useful for putting words in its mouth and continuing.
      </p>
    </section>

    <section class="help-sec">
      <h3>Threads</h3>
      <p>
        The <b>⑂ branch from start</b> toggle next to the composer makes your next message a brand-new first
        message rather than a reply — a second <b>thread</b> in the same workspace. The switcher beside it
        lists every thread across all panels so you can jump between them; a dot tells you whether a thread
        exists in every panel or only some.
      </p>
      <p class="help-note">
        With ⑂ on you also get a <b>thread system prompt</b>: recorded on that thread's first message and
        appended to the global one. Continuing an existing thread always reuses that thread's own prompt.
      </p>
    </section>

    <section class="help-sec">
      <h3>Highlight rules</h3>
      <p>
        Sidebar → <b>Highlights</b>. A rule is a named pattern (plain text or regex, or/and across several
        patterns, scoped to a role) with a color. Matching text gets painted everywhere it renders. Rules are
        the backbone of the other views: the distribution chart buckets samples by which rules they match, and
        token probabilities can be colored by how much probability mass a rule attracts.
      </p>
    </section>

    <section class="help-sec">
      <h3>Distribution chart</h3>
      <p>The bar-chart button (top of the sidebar) opens it. Three modes:</p>
      <ul>
        <li><b>By rules</b> — each sample bucketed by the SET of highlight rules it matches (grey = none, striped = a combo). This is the "what does it usually say?" view for an N-sample draw.</li>
        <li><b>By answer</b> — the plain exact-match histogram over sample text.</li>
        <li><b>First token</b> — the model's OWN probability distribution over the first generated token, from stored logprobs. Click chips to exclude, drag one onto another to merge, search to surface a token that's hidden in the tail.</li>
      </ul>
      <p class="help-note">Pick which turn to chart with the turn selector; it defaults to the latest and updates live while samples stream in.</p>
    </section>

    <section class="help-sec">
      <h3>Token probabilities</h3>
      <p>
        Sidebar → <b>Token probs</b> renders assistant replies as their raw token stream, each token tinted by
        surprisal, with a hover popover showing its probability and the top-5 alternatives. It's display-only
        and retroactive — turns sampled earlier already carry the data. Pick up to two highlight rules under
        <b>Color by match</b> to tint by "how much mass went to text matching this rule" instead of surprisal.
      </p>
    </section>

    <section class="help-sec">
      <h3>Prefill — putting words in its mouth</h3>
      <p>
        The composer's <b>prefill</b> field is text the assistant is made to have already started saying; the
        model extends it. Type a raw <code>&lt;think&gt;</code> to force a reasoning opening, or a whole
        think block to jump straight to the answer. It persists across sends, so you can draw N samples off
        one prefill.
      </p>
    </section>

    <section class="help-sec">
      <h3>Keeping things</h3>
      <p>
        <b>Pins</b> (the bookmark button on a row) save a sample with a note; the play button in the sidebar
        browses them as a slideshow. <b>Share packs</b> bundle checkpoints, default params and whole
        workspaces into one portable file — <code>tinkerscope pack export</code> to author, <code>tinkerscope
        --pack &lt;file|url&gt;</code> to open someone else's setup with no local run dirs.
      </p>
    </section>

    <section class="help-sec">
      <h3>Driven from the terminal</h3>
      <p>
        Everything on this screen is live-shared with the <code>tinkpg</code> CLI, so an agent (or you) can
        select a run, fire a chat, or draw a distribution from a terminal and watch it land here. The <b>#</b>
        button on a row copies that node's id, which is how you point the CLI at a specific message.
      </p>
    </section>
  {:else}
    <table class="help-keys">
      {#each KEYS as g (g.group)}
        <tbody>
          <tr><th colspan="2" class="help-keys-group">{g.group}</th></tr>
          {#each g.rows as [k, what] (k)}
            <tr><td class="help-key"><kbd>{k}</kbd></td><td>{what}</td></tr>
          {/each}
        </tbody>
      {/each}
    </table>
    <p class="help-note help-keys-foot">
      Row keys are ignored while you're typing in a box or a modal is open, so nothing steals your Escape.
    </p>
  {/if}
</Modal>

<style>
  .help-tabs { display: flex; gap: var(--space-1); margin-left: auto; margin-right: var(--space-3); }
  .help-tab { background: none; border: 1px solid transparent; color: var(--color-text-muted); font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: var(--radius-pill); }
  .help-tab:hover { color: var(--color-text); }
  .help-tab.active { color: var(--color-accent); border-color: var(--color-border); background: var(--color-accent-bg); }

  .help-lede { font-size: 0.86rem; color: var(--color-text-secondary); line-height: 1.6; margin-bottom: var(--space-4); }
  .help-sec { margin-bottom: var(--space-4); }
  .help-sec h3 { font-size: 0.8rem; font-weight: 600; color: var(--color-accent); margin-bottom: var(--space-2); }
  .help-sec p { font-size: 0.82rem; color: var(--color-text-secondary); line-height: 1.6; margin-bottom: var(--space-2); }
  .help-sec ul { margin: 0 0 var(--space-2) var(--space-4); padding: 0; }
  .help-sec li { font-size: 0.82rem; color: var(--color-text-secondary); line-height: 1.6; margin-bottom: var(--space-1); }
  .help-sec b { color: var(--color-text); font-weight: 600; }
  .help-note { font-size: 0.78rem !important; color: var(--color-text-muted) !important; border-left: 2px solid var(--color-border); padding-left: var(--space-3); }
  code { font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.94em; background: var(--color-surface-alt); padding: 0.05em 0.3em; border-radius: var(--radius-sm); }

  .help-keys { width: 100%; border-collapse: collapse; }
  .help-keys-group { text-align: left; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-muted); padding: var(--space-3) 0 var(--space-1); }
  .help-keys td { font-size: 0.8rem; color: var(--color-text-secondary); padding: 0.22rem 0; vertical-align: top; line-height: 1.5; }
  .help-key { width: 200px; padding-right: var(--space-3) !important; white-space: nowrap; }
  kbd { font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.72rem; color: var(--color-text); background: var(--color-surface-alt); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: 0.1em 0.4em; }
  .help-keys-foot { margin-top: var(--space-4); }
</style>
