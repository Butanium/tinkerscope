<script lang="ts">
  // The role a turn is written as — the edit dialog's head and the composer.
  import { ROLE_CHOICES, type InputRole } from '$lib/tree';

  let {
    value = $bindable(),
    roles = ROLE_CHOICES,
    testid = 'role-toggle',
    onchange
  }: { value: InputRole; roles?: readonly InputRole[]; testid?: string; onchange?: (r: InputRole) => void } = $props();

  function pick(r: InputRole) {
    if (onchange) onchange(r);
    else value = r;
  }
</script>

<div class="seg-toggle seg-mini role-toggle" role="radiogroup" aria-label="Role" data-testid={testid}>
  {#each roles as r (r)}
    <button type="button" class="seg-btn" class:active={value === r} role="radio" aria-checked={value === r} onclick={() => pick(r)}>{r}</button>
  {/each}
</div>

<style>
  .role-toggle { flex-shrink: 0; }
</style>
