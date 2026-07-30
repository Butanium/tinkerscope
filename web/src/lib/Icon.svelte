<!--
  The app's SVG glyph set, in one place.

  Why a component and not inline SVG per call site: the Help modal shows the same
  pictogram the row toolbar draws, so a person reading "Shift + edit" can see
  WHICH button that is. A second hand-drawn copy of each path would drift the
  moment an icon changed; one dispatch table can't.

  Shift-held variants are separate names (`replace` = regenerate-in-place,
  `edit-copy` = fork-a-full-copy, `trash-all`, `tag-quick`) — the alternate glyph
  IS the signal that the modifier changed the action.
-->
<script module lang="ts">
  export type IconName =
    // row toolbar
    | 'regen' | 'replace' | 'edit' | 'edit-copy' | 'trash' | 'trash-all'
    | 'tag' | 'tag-quick' | 'continue' | 'continue-think'
    | 'copy' | 'copy-all' | 'check' | 'hash' | 'send-to'
    | 'use-sample' | 'discard-others'
    // sidebar / chrome
    | 'chart' | 'pins' | 'dataset' | 'help' | 'stop' | 'plus' | 'new-blank'
    | 'theme-light' | 'theme-dark' | 'theme-auto' | 'eye' | 'upload';
</script>

<script lang="ts">
  let { name, size = 13 }: { name: IconName; size?: number } = $props();
</script>

{#if name === 'upload'}
  <!-- open a file FROM this computer: an arrow rising out of a tray -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M8 10.5V2.5M5 5.5L8 2.5l3 3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /><path d="M2.5 10v3.5h11V10" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'regen'}
  <svg width={size} height={size} viewBox="0 0 14 14" fill="none"><path d="M1.5 7a5.5 5.5 0 0 1 9.9-3.3M12.5 7a5.5 5.5 0 0 1-9.9 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M11.5 1v3h-3M2.5 13v-3h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'replace'}
  <svg width={size} height={size} viewBox="0 0 14 14" fill="none"><path d="M1.5 7a5.5 5.5 0 0 1 9.9-3.3M12.5 7a5.5 5.5 0 0 1-9.9 3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M11.5 1v3h-3M2.5 13v-3h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /><rect x="5.1" y="5.1" width="3.8" height="3.8" rx="0.6" fill="currentColor" /></svg>
{:else if name === 'edit'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M10.5 2.5l3 3L6 13l-3.5.5L3 10l7.5-7.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
{:else if name === 'edit-copy'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2.5" y="2.5" width="7.5" height="9.5" rx="1" stroke="currentColor" stroke-width="1.2" /><path d="M6 4.5h6a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" /></svg>
{:else if name === 'trash'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'trash-all'}
  <!-- trash + a back layer = "delete every branch at this level" -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M5.5 5.5l.5 7.5h5.5l.5-7.5M5 5.5h8M7.5 5.5V4h3v1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 3.2h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /><path d="M2.6 3.2l.5 6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'tag'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" stroke="currentColor" stroke-width="1.5" /><circle cx="5.5" cy="5.5" r="1" fill="currentColor" /></svg>
{:else if name === 'tag-quick'}
  <!-- filled bookmark = save instantly, no note prompt -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2 2h6l6 6-6 6-6-6V2Z" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" /><circle cx="5.3" cy="5.3" r="1" fill="var(--color-bg)" /></svg>
{:else if name === 'continue'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg>
{:else if name === 'continue-think'}
  <!-- thought bubble + plus = continue the reasoning (resume inside the think block) -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="8.4" rx="4.2" stroke="currentColor" stroke-width="1.3" /><path d="M8 4.4v3.6M6.2 6.2h3.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /><circle cx="5" cy="12.5" r="1.15" fill="currentColor" /><circle cx="2.8" cy="14.4" r="0.75" fill="currentColor" /></svg>
{:else if name === 'copy'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="5" y="5" width="8.5" height="8.5" rx="1.2" stroke="currentColor" stroke-width="1.2" /><path d="M3 10.5A1.5 1.5 0 0 1 2.5 9.5V3.5A1.5 1.5 0 0 1 4 2h6a1.5 1.5 0 0 1 1.1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
{:else if name === 'copy-all'}
  <!-- copy + text lines = "copy the FULL conversation as markdown" -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="5" y="4.5" width="8.5" height="9" rx="1.2" stroke="currentColor" stroke-width="1.2" /><path d="M7 7.3h4.5M7 9.3h4.5M7 11.3h2.6" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" /><path d="M3 10.5A1.5 1.5 0 0 1 2.5 9.5V3.5A1.5 1.5 0 0 1 4 2h6a1.5 1.5 0 0 1 1.1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
{:else if name === 'check'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5L13 5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'hash'}
  <!-- node-id glyph (a # = "this row's id") -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M6.2 2.5L4.8 13.5M11.2 2.5L9.8 13.5M2.8 6h10.5M2.5 10h10.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
{:else if name === 'send-to'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M2 6h10M9.5 3.5 12 6 9.5 8.5M14 10H4M6.5 7.5 4 10l2.5 2.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'use-sample'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4" /><path d="M5.2 8.3l1.9 1.9 3.7-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'discard-others'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2.5" y="5" width="7" height="8.5" rx="1" stroke="currentColor" stroke-width="1.3" /><path d="M7 2.5h6.5V11" stroke="currentColor" stroke-width="1.2" opacity="0.5" /><path d="M10.8 4.8l2.4 2.4M13.2 4.8l-2.4 2.4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
{:else if name === 'chart'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2" y="9" width="3" height="5" rx="0.5" stroke="currentColor" stroke-width="1.3" /><rect x="6.5" y="5" width="3" height="9" rx="0.5" stroke="currentColor" stroke-width="1.3" /><rect x="11" y="2" width="3" height="12" rx="0.5" stroke="currentColor" stroke-width="1.3" /></svg>
{:else if name === 'pins'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M3 2.5h10a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5H3A1.5 1.5 0 0 1 1.5 12V4A1.5 1.5 0 0 1 3 2.5Z" stroke="currentColor" stroke-width="1.3" /><path d="M6.5 6l4 2-4 2V6Z" fill="currentColor" /></svg>
{:else if name === 'dataset'}
  <svg width={size} height={size} viewBox="0 0 14 14" fill="none"><path d="M3 1.5h5l3.5 3.5v7.5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.3" /><path d="M8 1.5v3.5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /><path d="M5 8h4M5 10h3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
{:else if name === 'help'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.25" stroke="currentColor" stroke-width="1.3" /><path d="M6.2 6.1a1.85 1.85 0 1 1 2.3 1.85c-.35.1-.5.35-.5.75v.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /><circle cx="8" cy="11.4" r="0.75" fill="currentColor" /></svg>
{:else if name === 'stop'}
  <svg width={size} height={size} viewBox="0 0 14 14" fill="none"><rect x="2" y="2" width="10" height="10" rx="1.5" fill="currentColor" /></svg>
{:else if name === 'plus'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M8 4v8M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
{:else if name === 'new-blank'}
  <!-- blank page + plus: a fresh workspace with no model -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M4 1.5h5L12.5 5v6.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /><path d="M8.5 1.5V5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /><path d="M7.5 7v3M6 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
{:else if name === 'theme-light'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="3.5" stroke="currentColor" stroke-width="1.5" /><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
{:else if name === 'theme-dark'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M14 9.2A6 6 0 0 1 6.8 2 6 6 0 1 0 14 9.2Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
{:else if name === 'theme-auto'}
  <!-- monitor glyph (follows the system scheme) -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><rect x="2" y="3" width="12" height="8" rx="1" stroke="currentColor" stroke-width="1.5" /><path d="M6 14h4M8 11v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
{:else if name === 'eye'}
  <!-- read-only marker (static site badge) -->
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none"><path d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /><circle cx="8" cy="8" r="1.8" stroke="currentColor" stroke-width="1.4" /></svg>
{/if}
