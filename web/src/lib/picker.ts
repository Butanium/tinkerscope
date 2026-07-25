// The item shape both combobox components speak (`Typeahead.svelte` and the
// select-like `PickerDropdown.svelte` wrapping it). Structural, so any caller
// list with these fields fits — e.g. model-catalog's `ModelItem`.

export type PickerItem = {
  id: string;
  label: string;
  /** Secondary line under the label. Defaults to `id` when the label differs
   *  from it (a model's full path); pass explicitly to show something else —
   *  workspace rows show when they were last touched, not their uuid. */
  sub?: string;
  /** Shown but not pickable — greyed, click/Enter is a no-op. */
  disabled?: boolean;
  /** Pickable but flagged: greyed AND demoted below available rows (a model
   *  that isn't samplable right now — a warning, not a block). */
  unavailable?: boolean;
  /** Extra text matched by the filter but never displayed. */
  search?: string;
};
