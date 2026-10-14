<!--
  The app's SVG glyph set, in one place.

  Why a component and not inline SVG per call site: the Help modal shows the same
  pictogram the row toolbar draws, so a person reading "Shift + edit" can see
  WHICH button that is. A second hand-drawn copy of each path would drift the
  moment an icon changed; one dispatch table can't.

  Shift-held variants are separate names (`replace` = regenerate-in-place,
  `edit-copy` = fork-a-full-copy, `trash-all`, `tag-quick`, `continue-think`) — the
  alternate glyph IS the signal that the modifier changed the action.

  Drawing rules (16 grid, stroke 1.4, round caps/joins, no bg-coloured fills) and
  the render-and-look loop: docs/GLYPHS.md.
-->
<script module lang="ts">
  export type IconName =
    // row toolbar
    | 'regen' | 'replace' | 'edit' | 'edit-copy' | 'trash' | 'trash-all'
    | 'tag' | 'tag-quick' | 'continue' | 'continue-think'
    | 'copy' | 'copy-all' | 'check' | 'hash' | 'send-to'
    | 'use-sample' | 'discard-others' | 'discard-path'
    // sidebar / chrome
    | 'chart' | 'pins' | 'dataset' | 'help' | 'stop' | 'undo' | 'plus' | 'minus'
    | 'focus' | 'unfocus'
    // prefill scope: the thinking half / the answer (non-thinking) half
    | 'think' | 'answer' | 'no-think' | 'search'
    | 'new-blank' | 'arrow-right' | 'sidebar-fold' | 'sidebar-unfold'
    | 'theme-light' | 'theme-dark' | 'theme-auto' | 'eye' | 'upload' | 'external';
</script>

<script lang="ts">
  let { name, size = 13 }: { name: IconName; size?: number } = $props();
</script>

{#if name === 'upload'}
  <!-- open a file FROM this computer: an arrow rising out of a tray -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M8 10.5V2.5M5 5.5L8 2.5l3 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M2.5 10v3.5h11V10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'regen'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M1.714 8a6.286 6.286 0 0 1 11.314 -3.771M14.286 8a6.286 6.286 0 0 1 -11.314 3.771" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M13.143 1.143v3.429h-3.429M2.857 14.857v-3.429h3.429" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'replace'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M1.714 8a6.286 6.286 0 0 1 11.314 -3.771M14.286 8a6.286 6.286 0 0 1 -11.314 3.771" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M13.143 1.143v3.429h-3.429M2.857 14.857v-3.429h3.429" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><rect x="5.829" y="5.829" width="4.343" height="4.343" rx="0.686" fill="currentColor" /></svg>
{:else if name === 'edit'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M10.5 2.5l3 3L6 13l-3.5.5L3 10l7.5-7.5Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'edit-copy'}
  <!-- pencil over a sheet's corner: edit a COPY (fork), not the turn itself -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2.5 11V3.5a1 1 0 0 1 1-1H10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M11.5 4.5l2.5 2.5-6.2 6.2-3.1.6.6-3.1 6.2-6.2Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'trash'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'trash-all'}
  <!-- two whole cans = every branch at this turn -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M1.8 4.5h5.4M3.8 4.5V3.4h1.4v1.1M2.6 4.5l.4 7.5h3.2l.4-7.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M8.8 4.5h5.4M10.8 4.5V3.4h1.4v1.1M9.6 4.5l.4 7.5h3.2l.4-7.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'tag'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><circle cx="5.5" cy="5.5" r="1" fill="currentColor" /></svg>
{:else if name === 'tag-quick'}
  <!-- filled tag = save instantly, no note prompt. The hole is geometry (evenodd), not a
       background-coloured dot, so it stays a hole on a tinted hover background -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z M3.7 5.6a1.9 1.9 0 1 0 3.8 0a1.9 1.9 0 1 0-3.8 0Z" fill="currentColor" fill-rule="evenodd" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'continue'}
  <!-- dots then an arrow: keep writing from where the turn stopped -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="2.6" cy="8" r="1" fill="currentColor" /><circle cx="5.6" cy="8" r="1" fill="currentColor" /><path d="M8.5 8h5M11 5.5 13.5 8 11 10.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'continue-think'}
  <!-- thought bubble with an ellipsis: resume INSIDE the think block -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="8.4" rx="4.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><circle cx="5.5" cy="6.2" r=".95" fill="currentColor" /><circle cx="8" cy="6.2" r=".95" fill="currentColor" /><circle cx="10.5" cy="6.2" r=".95" fill="currentColor" /><circle cx="5" cy="12.5" r="1.15" fill="currentColor" /><circle cx="2.8" cy="14.4" r="0.75" fill="currentColor" /></svg>
{:else if name === 'copy'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="5" y="5" width="8.5" height="8.5" rx="1.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 10.5A1.5 1.5 0 0 1 2.5 9.5V3.5A1.5 1.5 0 0 1 4 2h6a1.5 1.5 0 0 1 1.1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'copy-all'}
  <!-- copy + text lines = "copy the FULL conversation as markdown" -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="5" y="4.5" width="8.5" height="9" rx="1.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M7 7.3h4.5M7 9.3h4.5M7 11.3h2.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 10.5A1.5 1.5 0 0 1 2.5 9.5V3.5A1.5 1.5 0 0 1 4 2h6a1.5 1.5 0 0 1 1.1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'check'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5L13 5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'hash'}
  <!-- node-id glyph (a # = "this row's id") -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M6.2 2.5L4.8 13.5M11.2 2.5L9.8 13.5M2.8 6h10.5M2.5 10h10.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'send-to'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2 6h10M9.5 3.5 12 6 9.5 8.5M14 10H4M6.5 7.5 4 10l2.5 2.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'use-sample'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M5.2 8.3l1.9 1.9 3.7-4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'discard-others'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2.5" y="5" width="7" height="8.5" rx="1" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M7 2.5h6.5V11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" opacity="0.5" /><path d="M10.8 4.8l2.4 2.4M13.2 4.8l-2.4 2.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'discard-path'}
  <!-- Shift+discard: the path on screen stays (a chain of turns), every branch off it goes -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M5 3v10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><circle cx="5" cy="3" r="1.3" fill="currentColor" /><circle cx="5" cy="8" r="1.3" fill="currentColor" /><circle cx="5" cy="13" r="1.3" fill="currentColor" /><path d="M6.8 8h1.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" opacity="0.5" /><path d="M10 6.3l3.4 3.4M13.4 6.3 10 9.7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'chart'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2" y="9" width="3" height="5" rx="0.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><rect x="6.5" y="5" width="3" height="9" rx="0.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><rect x="11" y="2" width="3" height="12" rx="0.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'pins'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 2.5h10a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5H3A1.5 1.5 0 0 1 1.5 12V4A1.5 1.5 0 0 1 3 2.5Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M6.5 6l4 2-4 2V6Z" fill="currentColor" /></svg>
{:else if name === 'external'}
  <!-- hand this off to another app: arrow leaving an open-cornered box -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M8.571 2.857H2.857a1.143 1.143 0 0 0 -1.143 1.143v9.143a1.143 1.143 0 0 0 1.143 1.143h9.143a1.143 1.143 0 0 0 1.143 -1.143v-5.714" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M9.714 1.714h4.571v4.571M14.286 1.714L7.429 8.571" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'dataset'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3.429 1.714h5.714l4 4v8.571a1.143 1.143 0 0 1 -1.143 1.143H3.429a1.143 1.143 0 0 1 -1.143 -1.143v-11.429a1.143 1.143 0 0 1 1.143 -1.143Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M9.143 1.714v4h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M5.714 9.143h4.571M5.714 11.429h3.429" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'help'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.25" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M6.2 6.1a1.85 1.85 0 1 1 2.3 1.85c-.35.1-.5.35-.5.75v.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><circle cx="8" cy="11.4" r="0.75" fill="currentColor" /></svg>
{:else if name === 'stop'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2.286" y="2.286" width="11.429" height="11.429" rx="1.714" fill="currentColor" /></svg>
{:else if name === 'undo'}
  <!-- counter-clockwise arrow: put back what was just removed -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 7.5a5 5 0 1 1 1.6 3.7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M2.2 4v3.6h3.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'plus'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M8 4v8M4 8h8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'minus'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M4 8h8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'focus'}
  <!-- corners pointing out: show this panel alone -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'unfocus'}
  <!-- corners pointing in: back to every panel -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M6 2.5V6H2.5M13.5 6H10V2.5M10 13.5V10h3.5M2.5 10H6v3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'think'}
  <!-- brain: the thinking half of a send -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M8 3.56a1.94 1.94 0 0 0 -3.61 0.53A2.29 2.29 0 0 0 2.98 7.87a2.2 2.2 0 0 0 0.7 2.9A2.11 2.11 0 0 0 8 11.83Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M8 3.56a1.94 1.94 0 0 1 3.61 0.53A2.29 2.29 0 0 1 13.02 7.87a2.2 2.2 0 0 1 -0.7 2.9A2.11 2.11 0 0 1 8 11.83" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M4.39 4.08c0.18 0.88 0.7 1.41 1.5 1.58M11.61 4.08c-0.18 0.88 -0.7 1.41 -1.5 1.58M2.98 7.87c0.62 -0.35 1.32 -0.44 2.02 -0.18M13.02 7.87c-0.62 -0.35 -1.32 -0.44 -2.02 -0.18" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'answer'}
  <!-- speech bubble with text: the answer (non-thinking) half. Pointed tail on
       purpose — the thought bubble (`continue-think`) trails circles -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M4.5 3h7A2.5 2.5 0 0 1 14 5.5v3a2.5 2.5 0 0 1-2.5 2.5H8.2L5 13.4V11h-.5A2.5 2.5 0 0 1 2 8.5v-3A2.5 2.5 0 0 1 4.5 3Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M5.3 6h5.4M5.3 8h3.4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'no-think'}
  <!-- the think brain, dimmed and struck through: drawn WITHOUT thinking. Not the
       speech bubble — that is the answer half of a prefill scope, a different question.
       Opacity, not a mask: a mask id repeats per instance and dies with a hidden first copy -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><g opacity="0.55"><path d="M8 3.56a1.94 1.94 0 0 0 -3.61 0.53A2.29 2.29 0 0 0 2.98 7.87a2.2 2.2 0 0 0 0.7 2.9A2.11 2.11 0 0 0 8 11.83Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M8 3.56a1.94 1.94 0 0 1 3.61 0.53A2.29 2.29 0 0 1 13.02 7.87a2.2 2.2 0 0 1 -0.7 2.9A2.11 2.11 0 0 1 8 11.83" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M4.39 4.08c0.18 0.88 0.7 1.41 1.5 1.58M11.61 4.08c-0.18 0.88 -0.7 1.41 -1.5 1.58M2.98 7.87c0.62 -0.35 1.32 -0.44 2.02 -0.18M13.02 7.87c-0.62 -0.35 -1.32 -0.44 -2.02 -0.18" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></g><path d="M2.2 13.8 13.8 2.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'search'}
  <!-- magnifier: the Ctrl+K search palette -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M10.4 10.4 13.6 13.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'arrow-right'}
  <!-- "push it that way": unfold a reduced panel, send a draft into a panel -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'sidebar-fold' || name === 'sidebar-unfold'}
  <!-- a stylized « (fold the sidebar away) / » (bring it back): the leading
       chevron solid, the trailing one fainter, so it reads as motion -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" style={name === 'sidebar-unfold' ? 'transform: scaleX(-1)' : undefined}><path d="M8 3.5 3.5 8 8 12.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M13 3.5 8.5 8l4.5 4.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" opacity="0.45" /></svg>
{:else if name === 'new-blank'}
  <!-- blank page + plus: a fresh workspace with no model -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M4 1.5h5L12.5 5v6.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M8.5 1.5V5h3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M7.5 7v3M6 8.5h3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'theme-light'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'theme-dark'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M14 9.2A6 6 0 0 1 6.8 2 6 6 0 1 0 14 9.2Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'theme-auto'}
  <!-- half light, half dark: follows the system scheme (was a monitor, which read
       as "screen" rather than "theme") -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><path d="M8 2.4a5.6 5.6 0 0 0 0 11.2Z" fill="currentColor" /></svg>
{:else if name === 'eye'}
  <!-- "view": the read-only badge (static site) AND the row-toolbar
       view-all-samples toggle -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><circle cx="8" cy="8" r="1.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{/if}
