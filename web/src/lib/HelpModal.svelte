<!--
  The `?` modal: what the UI does, for a HUMAN.

  Why it exists: every other doc in this repo is written for an agent driving the
  tool from a terminal (the `tinkerscope:cli` skill) or for whoever maintains it
  (docs/*.md). Nothing explained the browser UI to the person using it, so the
  discoverable-by-accident features (keyboard row nav, the shift/ctrl modifier
  axes, branch-from-start) were effectively invisible. Its prose twin is the
  `tinkerscope:guide` skill — keep the two in sync when behavior changes.

  Every button this file names is drawn with its REAL glyph, from the shared
  `lib/Icon.svelte` the toolbar uses: "Shift + edit" is useless if you can't tell
  which of eight unlabeled icons is edit.

  Two tabs on purpose: "Guide" answers *what is this screen*, "Keys" is the
  lookup table you reopen later. Both stay short enough to scan.
-->
<script lang="ts">
  import Modal from '$lib/Modal.svelte';
  import Icon, { type IconName } from '$lib/Icon.svelte';

  let { onclose }: { onclose: () => void } = $props();

  let tab = $state<'guide' | 'keys'>('guide');

  type ButtonDoc = { icon: IconName; name: string; what: string };

  /** The row toolbar, in the order it renders on a chat row. */
  const ROW_BUTTONS: ButtonDoc[] = [
    { icon: 'regen', name: 'regenerate', what: 'sample this turn again — arrives as a new sibling branch' },
    { icon: 'continue', name: 'continue', what: 'let the model EXTEND this message instead of replying to it' },
    { icon: 'edit', name: 'edit', what: 'change the text; forks a branch, never overwrites' },
    { icon: 'trash', name: 'delete', what: 'prune this branch and everything under it' },
    { icon: 'tag', name: 'bookmark', what: 'pin this sample with a note' },
    { icon: 'copy', name: 'copy message', what: 'just this message' },
    { icon: 'copy-all', name: 'copy conversation', what: 'the whole thread as markdown' },
    { icon: 'send-to', name: 'send to panel', what: "copy this branch's context into another panel" },
    { icon: 'hash', name: 'copy node id', what: 'the id the `tinkpg --node` flag addresses' }
  ];

  /** Extra buttons that only exist on the cards of an n&gt;1 sample draw. */
  const SAMPLE_BUTTONS: ButtonDoc[] = [
    { icon: 'use-sample', name: 'make active', what: 'collapse the draw to this sample (the rest stay ‹k/N› siblings)' },
    { icon: 'continue', name: 'continue this sample', what: 'make it active, then extend it' },
    { icon: 'discard-others', name: 'discard others', what: 'keep this sample, delete its siblings' }
  ];

  /** The icon column at the top of the sidebar. */
  const SIDEBAR_BUTTONS: ButtonDoc[] = [
    { icon: 'chart', name: 'distribution chart', what: 'the "what does it usually say?" view over an N-sample draw' },
    { icon: 'pins', name: 'pins', what: 'browse everything you bookmarked, as a slideshow' },
    { icon: 'dataset', name: 'training data', what: "peek at the selected run's training set" },
    { icon: 'regen', name: 'refresh', what: 'rescan the run directory + re-check which checkpoints still serve' },
    { icon: 'help', name: 'help', what: 'this modal' },
    { icon: 'stop', name: 'stop', what: 'abort generation in every panel' }
  ];

  type KeyRow = {
    /** Rendered as <kbd> chips joined by "+". */
    keys: string[];
    /** The toolbar button the modifier applies to — drawn, then named. */
    icon?: IconName;
    btn?: string;
    what: string;
    /** Buttons the row applies to as a set (shown after the description). */
    also?: IconName[];
  };

  const KEYS: { group: string; rows: KeyRow[] }[] = [
    {
      group: 'Composer',
      rows: [
        { keys: ['Enter'], what: 'Send to every panel (or just the picked send targets)' },
        { keys: ['Shift', 'Enter'], what: 'Newline' },
        { keys: ['Esc'], what: 'Enter prompt history — then ↑/↓ to browse, Esc again to leave' },
        { keys: ['Enter (per-panel box)'], what: 'Continue that one panel only — the others stay put' }
      ]
    },
    {
      group: 'Chat rows (click a row first)',
      rows: [
        { keys: ['↑ / ↓'], what: 'Move the focus ring up/down that panel' },
        { keys: ['← / →'], what: "Cycle the focused row's ‹k/N› sibling branches" },
        { keys: ['Esc'], what: 'Drop the focus ring' }
      ]
    },
    {
      group: 'Row toolbar modifiers (hold, then click)',
      rows: [
        {
          keys: ['Ctrl / ⌘'],
          what: 'Do it in ALL panels at once (only with >1 panel) —',
          also: ['edit', 'regen', 'trash', 'continue']
        },
        { keys: ['Shift'], icon: 'edit', btn: 'edit', what: 'On a USER row: fork a full editable copy of the conversation, generating nothing' },
        { keys: ['Shift'], icon: 'regen', btn: 'regenerate', what: 'Replace this branch in place instead of adding a sibling' },
        { keys: ['Shift'], icon: 'trash', btn: 'delete', what: 'Delete every branch at this turn, not just the shown one' },
        { keys: ['Shift'], icon: 'continue', btn: 'continue', what: 'Resume INSIDE the think block (assistant turns with reasoning)' },
        { keys: ['Shift'], icon: 'copy', btn: 'copy', what: 'Include the thinking text in the copy' },
        { keys: ['Shift'], icon: 'tag', btn: 'bookmark', what: 'Pin instantly, skipping the note dialog' }
      ]
    },
    {
      group: 'Sidebar modifiers',
      rows: [
        { keys: ['Shift'], icon: 'plus', btn: 'New workspace', what: 'Create it BLANK — otherwise it inherits the current models' },
        { keys: ['Shift'], icon: 'plus', btn: 'Add panel', what: 'Add an empty panel — otherwise it clones the last one' }
      ]
    },
    {
      group: 'Anywhere',
      rows: [{ keys: ['Esc (modal open)'], what: 'Close the modal' }]
    }
  ];
</script>

<!-- One button, drawn the way the app draws it; `label` names it inline in prose. -->
{#snippet chip(icon: IconName, label?: string)}
  <span class="help-chip"><Icon name={icon} size={12} />{#if label}<span class="help-chip-label">{label}</span>{/if}</span>
{/snippet}

{#snippet legend(items: ButtonDoc[])}
  <ul class="help-btns">
    {#each items as b (b.name + b.icon)}
      <li>{@render chip(b.icon)}<b>{b.name}</b> — {b.what}</li>
    {/each}
  </ul>
{/snippet}

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
      <h3>Lost? Ask your agent</h3>
      <p>
        tinkerscope ships two Claude skills, so the fastest help is often not this modal:
      </p>
      <ul>
        <li>
          <code>tinkerscope:guide</code> — the long-form version of this page. Ask Claude "walk me through
          tinkerscope" or "what does this button do" and it reads the screen back to you.
        </li>
        <li>
          <code>tinkerscope:cli</code> — lets Claude <em>drive</em> this playground from its terminal: pick a
          run, send a prompt, draw a 50-sample distribution. It lands in this browser live, while you watch.
        </li>
      </ul>
      <!-- Folded by default: most people arrive with the skills already installed,
           and a wall of install commands is noise until you need it. -->
      <details class="help-fold">
        <summary>Your agent doesn't know them? Install the plugin</summary>
        <p class="help-fold-note">
          The repo doubles as a plugin marketplace, so both skills come as one install.
        </p>
        <p class="help-fold-h">Claude Code</p>
        <pre>/plugin marketplace add Butanium/tinkerscope
/plugin install tinkerscope@tinkerscope</pre>
        <p class="help-fold-note">
          Third-party marketplaces are manual-update by default — toggle auto-update from
          <code>/plugin</code> › Marketplaces, or run <code>/plugin marketplace update tinkerscope</code>
          then <code>/reload-plugins</code>.
        </p>
        <p class="help-fold-h">Codex</p>
        <pre>codex plugin marketplace add Butanium/tinkerscope
codex plugin add tinkerscope@tinkerscope</pre>
        <p class="help-fold-note">Updates are manual: <code>codex plugin marketplace upgrade tinkerscope</code>.</p>
        <p class="help-fold-h">Cursor / Copilot / Windsurf / Cline</p>
        <pre>npx skills add Butanium/tinkerscope --agent &lt;agent&gt; -g -y</pre>
        <p class="help-fold-note">
          And the tool itself, on a machine that doesn't have it:
          <code>uv tool install git+https://github.com/Butanium/tinkerscope</code>.
        </p>
      </details>
    </section>

    <section class="help-sec">
      <h3>Start here</h3>
      <ol class="help-steps">
        <li>Sidebar → <b>Models</b>: click the picker, type a few letters of the run, pick a checkpoint.</li>
        <li>Type in the composer and hit <kbd>Enter</kbd>.</li>
        <li>Set <b>Samples</b> to 20 and send again — you now have 20 replies to the same prompt, not one.</li>
        <li>Open {@render chip('chart')} to see how they split; write a <b>Highlight rule</b> to name the split.</li>
        <li>Hit <b>Compare</b> in the sidebar to add a second model and answer both at once.</li>
      </ol>
    </section>

    <section class="help-sec">
      <h3>Workspaces and panels</h3>
      <p>
        A <b>workspace</b> is one saved conversation setup: its message tree, its system prompt, and the set of
        <b>panels</b> it shows. One panel = one model (a run checkpoint, a raw base model, or an OpenRouter
        reference model). Sending a message fires it into <em>every</em> panel, so the columns are the same
        prompt answered by different models.
      </p>
      <p class="help-note">
        Switching workspaces restores its models too. {@render chip('plus')} makes a new workspace that
        inherits the current models (Shift → blank). Panels reorder by dragging a column header; the sidebar
        pickers and send chips follow.
      </p>
      <p class="help-note">
        Both sidebar pickers — workspace and model — are the same control: click it and <em>type to filter</em>,
        ↑/↓ to walk, Enter to pick, Esc to close. Workspace rows are newest-first and show when you last
        touched them, which is how you tell two same-named workspaces apart.
      </p>
    </section>

    <section class="help-sec">
      <h3>The sidebar icon row</h3>
      {@render legend(SIDEBAR_BUTTONS)}
      <p class="help-note">The leftmost icon cycles the theme: light → dark → auto (follows your system).</p>
    </section>

    <section class="help-sec">
      <h3>Nothing is destroyed — branches</h3>
      <p>
        {@render chip('regen', 'regenerate')}, {@render chip('edit', 'edit')} or drawing N samples does not
        overwrite anything: each becomes a <b>sibling branch</b>, and the row shows a <code>‹ k/N ›</code>
        cycler you click (or ←/→) to walk them. {@render chip('trash', 'delete')} prunes only that subtree.
        So "what did it say the other three times?" is always answerable.
      </p>
      <p class="help-note">
        Editing a <em>user</em> message forks and regenerates from there; Shift+edit forks a full editable
        copy and generates nothing. Editing an <em>assistant</em> message just writes a manual branch —
        useful for putting words in its mouth and continuing.
      </p>
    </section>

    <section class="help-sec">
      <h3>The row toolbar</h3>
      <p>Hover a message and its buttons appear, in this order:</p>
      {@render legend(ROW_BUTTONS)}
      <p class="help-note">
        <b>Raw</b> leads the row as a text button — it shows the model's untouched output, tags and all.
        In a narrow column the tail of the row folds behind a chevron; click it to unfold the rest onto a
        second line. Holding Shift or Ctrl swaps the glyph to show what the modifier will do — see the
        <b>Keys</b> tab.
      </p>
      <p class="help-note">Cards of an N-sample draw add three more:</p>
      {@render legend(SAMPLE_BUTTONS)}
    </section>

    <section class="help-sec">
      <h3>N samples at once</h3>
      <p>
        <b>Samples</b> in the sidebar is how many replies one send draws. They arrive as cards under the turn —
        all of them stacked, or one at a time with ‹/› if you flip <b>Sample view</b> to Cycle. Each card is a
        real branch: keep one and the others stay reachable through its ‹k/N› cycler.
      </p>
      <p class="help-note">
        A distribution is only as interesting as the temperature that drew it — at 0 you get the same reply
        twenty times. The chart, the highlight rules and the first-token view all read this one draw.
      </p>
    </section>

    <section class="help-sec">
      <h3>Comparing models</h3>
      <p>
        With more than one panel, a send fans out to all of them. The <b>Send to</b> chips above the composer
        narrow that to a subset; the small box at the bottom of a column continues <em>that</em> column alone.
        A column header has a grip to drag it into a new position and a <b>−</b> to fold it out of the way
        (folded panels are skipped by the chart unless you ask for them).
      </p>
      <p class="help-note">
        Holding Ctrl/⌘ while clicking a row button applies it to every panel at that depth — the way to
        regenerate the same turn everywhere and keep the columns aligned.
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
      <p>{@render chip('chart')} at the top of the sidebar opens it. Three modes:</p>
      <ul>
        <li><b>By rules</b> — each sample bucketed by the SET of highlight rules it matches (grey = none, striped = a combo). This is the "what does it usually say?" view for an N-sample draw.</li>
        <li><b>By answer</b> — the plain exact-match histogram over sample text.</li>
        <li><b>First token</b> — the model's OWN probability distribution over the first generated token, from stored logprobs. Click chips to exclude, drag one onto another to merge, search to surface a token that's hidden in the tail.</li>
      </ul>
      <p class="help-note">Pick which turn to chart with the turn selector; it defaults to the latest and updates live while samples stream in. The mode, match scope and thinking filter follow you everywhere; the question-specific bits (turn, excluded rule chips, char cap, first-token tweaks) are remembered per workspace.</p>
      <p class="help-note">When a turn mixes samples drawn with and without thinking, a filter appears: chart them pooled, one population only, or <b>split think / no-think</b> — a bar each, side by side under the model name, each over its own samples with its own n. In rules mode the <b>split</b> match scope does the same for the text the rules run against (response vs thinking, same samples); the two combine.</p>
      <p class="help-note">Rules mode also takes a <b>first N chars</b> cap: rules then only match the opening of the text. Use it for rules about how a reply <i>starts</i> — a <code>&lt;answer&gt;</code> tag, a "Verdict:" header — which would otherwise also match the model talking about that tag later on. Blank = the whole text; the inspector dims whatever fell past the cap.</p>
    </section>

    <section class="help-sec">
      <h3>Token probabilities</h3>
      <p>
        Sidebar → <b>Token probs</b> renders assistant replies as their raw token stream, each token tinted by
        surprisal, with a hover popover showing its probability and the top-5 alternatives. It's display-only
        and retroactive — turns sampled earlier already carry the data.
      </p>
      <p class="help-note">
        Under it, <b>Color by match</b> flips the tint to "how much probability mass went to text matching
        this rule" instead of surprisal. Turn it On and pick up to two highlight rules — two rules split each
        token into a top and a bottom band. Off keeps your picks for next time.
      </p>
      <p class="help-note">
        The <b>Contrast</b> slider under the rule chips reshapes probability → opacity. At <b>0</b> opacity
        tracks the mass (the relative read: how much went to matching text); at <b>1</b> it's a step — any
        nonzero match at full tint, for "is anything related in the top-5 at all?". <b>0.50</b> (the default)
        is the √ ramp in between, where a 1% match still reads at 10% of full.
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
      <p class="help-note">
        With thinking set to <b>Both</b> (n samples each way in one send), the scope toggle picks which half
        the prefill applies to; the other half is left un-prefilled.
      </p>
    </section>

    <section class="help-sec">
      <h3>Keeping things</h3>
      <p>
        {@render chip('tag', 'bookmark')} on a row saves a sample with a note; {@render chip('pins')} in the
        sidebar browses them as a slideshow. <b>Share packs</b> bundle checkpoints, default params and whole
        workspaces into one portable file — <code>tinkerscope pack export</code> to author, <code>tinkerscope
        --pack &lt;file|url&gt;</code> to open someone else's setup with no local run dirs.
      </p>
    </section>

    <section class="help-sec">
      <h3>Driven from the terminal</h3>
      <p>
        Everything on this screen is live-shared with the <code>tinkpg</code> CLI, so an agent (or you) can
        select a run, fire a chat, or draw a distribution from a terminal and watch it land here — that's what
        the <code>tinkerscope:cli</code> skill above teaches Claude to do.
        {@render chip('hash')} on a row copies that node's id, which is how you point the CLI at a specific
        message.
      </p>
    </section>
  {:else}
    <table class="help-keys">
      {#each KEYS as g (g.group)}
        <tbody>
          <tr><th colspan="2" class="help-keys-group">{g.group}</th></tr>
          {#each g.rows as r (r.keys.join() + (r.btn ?? ''))}
            <tr>
              <td class="help-key">
                {#each r.keys as k, i (k)}{#if i > 0}<span class="help-sep">+</span>{/if}<kbd>{k}</kbd>{/each}
                {#if r.icon}<span class="help-sep">+</span>{@render chip(r.icon, r.btn)}{/if}
              </td>
              <td>
                {r.what}
                {#if r.also}{#each r.also as ic (ic)}{@render chip(ic)}{/each}{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      {/each}
    </table>
    <p class="help-note help-keys-foot">
      Row keys are ignored while you're typing in a box or a modal is open, so nothing steals your Escape.
      Every button above lives on a chat row — the <b>Guide</b> tab's "row toolbar" section names them all.
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
  .help-sec p { font-size: 0.82rem; color: var(--color-text-secondary); line-height: 1.8; margin-bottom: var(--space-2); }
  .help-sec ul, .help-sec ol { margin: 0 0 var(--space-2) var(--space-4); padding: 0; }
  .help-sec li { font-size: 0.82rem; color: var(--color-text-secondary); line-height: 1.6; margin-bottom: var(--space-1); }
  .help-steps li { line-height: 1.9; }
  .help-sec b { color: var(--color-text); font-weight: 600; }
  .help-note { font-size: 0.78rem !important; color: var(--color-text-muted) !important; border-left: 2px solid var(--color-border); padding-left: var(--space-3); }
  code { font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.94em; background: var(--color-surface-alt); padding: 0.05em 0.3em; border-radius: var(--radius-sm); }

  /* A button as the app draws it. Inline in prose (baseline-aligned) and as the
     bullet of a legend row, so "which one is edit?" never needs an answer. */
  .help-chip { display: inline-flex; align-items: center; gap: 4px; vertical-align: -0.2em; margin: 0 1px; padding: 2px 5px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface-alt); color: var(--color-text); }
  .help-chip-label { font-size: 0.75rem; font-weight: 600; }

  /* Folded install commands — present but out of the way (see the markup note). */
  .help-fold { border: 1px solid var(--color-border); border-radius: var(--radius); padding: var(--space-2) var(--space-3); }
  .help-fold summary { font-size: 0.78rem; color: var(--color-text-muted); cursor: pointer; list-style-position: outside; }
  .help-fold summary:hover { color: var(--color-text); }
  .help-fold[open] summary { margin-bottom: var(--space-2); color: var(--color-text); }
  .help-fold-h { font-size: 0.75rem !important; font-weight: 600; color: var(--color-text) !important; margin: var(--space-2) 0 2px !important; }
  .help-fold-note { font-size: 0.75rem !important; color: var(--color-text-muted) !important; margin-bottom: 0 !important; line-height: 1.6; }
  .help-fold pre { font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.72rem; color: var(--color-text); background: var(--color-surface-alt); border-radius: var(--radius-sm); padding: 6px 8px; margin: 0 0 4px; overflow-x: auto; white-space: pre; }

  .help-btns { list-style: none; margin: 0 0 var(--space-2) 0 !important; padding: 0; }
  .help-btns li { display: flex; align-items: baseline; gap: 6px; font-size: 0.8rem; color: var(--color-text-secondary); line-height: 1.7; margin-bottom: 2px; }
  .help-btns b { color: var(--color-text); font-weight: 600; }

  .help-keys { width: 100%; border-collapse: collapse; }
  .help-keys-group { text-align: left; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-muted); padding: var(--space-3) 0 var(--space-1); }
  .help-keys td { font-size: 0.8rem; color: var(--color-text-secondary); padding: 0.28rem 0; vertical-align: top; line-height: 1.6; }
  .help-key { width: 235px; padding-right: var(--space-3) !important; }
  .help-sep { color: var(--color-text-muted); margin: 0 3px; }
  kbd { font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.72rem; color: var(--color-text); background: var(--color-surface-alt); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: 0.1em 0.4em; white-space: nowrap; }
  .help-keys-foot { margin-top: var(--space-4); }
</style>
