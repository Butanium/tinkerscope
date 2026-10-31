<script lang="ts">
  // The role a turn is written as — the edit dialog's head and the composer.
  import { INPUT_ROLES, type InputRole } from '$lib/tree';

  let { value = $bindable(), testid = 'role-toggle' }: { value: InputRole; testid?: string } = $props();
</script>

<div class="seg-toggle seg-mini role-toggle" role="radiogroup" aria-label="Role" data-testid={testid}>
  {#each INPUT_ROLES as r (r)}
    <button type="button" class="seg-btn" class:active={value === r} role="radio" aria-checked={value === r} onclick={() => (value = r)}>{r}</button>
  {/each}
</div>

<style>
  .role-toggle { flex-shrink: 0; }
</style>
