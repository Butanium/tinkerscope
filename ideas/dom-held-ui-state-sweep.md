## Hunt the rest of the DOM-held UI state

The inspector's thinking fold reset under a live chart because
`<details open={…}>` kept the user's choice in the DOM, and the surrounding list
is re-derived on every streamed sample. That shape is not unique to the chart:
any `<details>`, uncontrolled `<input>`, scroll position, or `<dialog>` sitting
inside a re-derived `{#each}` has the same latent bug. Worth a grep sweep
(`<details`, `bind:` absent + user-toggled attrs) over `web/src` and a convention
line where it lands. The tell to look for: state a person SET that no store knows
about.

**See also:** [follow-scroll-hidden-controls](follow-scroll-hidden-controls.md) —
sibling sweep, same genre of bug (state/position nobody modeled), different axis.

*(opus-5, 2026-07-29, chart split / persistence session)*
