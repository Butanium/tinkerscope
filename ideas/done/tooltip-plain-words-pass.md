## A plain-words pass over the remaining tooltips

Clément cut the ⑂ tooltip from "While on, each composer send starts a NEW thread
instead of replying" to "If enabled, each send starts a fresh conversation" and
asked for the rest to be reviewed for the same fix: too long, or app jargon
(thread, fork, sibling, "hollow dot", "match mass"). I proposed 15 rewrites; he
never answered — he was mid-way through other asks — so none are applied. Before
applying, re-grep: `eaea840` and later commits changed some of these lines.

| where | now | proposed |
|---|---|---|
| ⑂ thread-system chip | Edit the next new thread's system prompt — replaces the workspace one | System prompt for each fresh conversation |
| workspace system chip | Workspace system prompt — threads with their own override it | Default system prompt (a conversation's own one wins) |
| the power dots | Apply / mute the … (hollow dot = muted) | Apply or mute — the text is kept |
| thread switcher | Jump to another thread — switches every panel that has it | Switch conversation (in every panel that has it) |
| regenerate / Shift | Regenerate → new sibling branch / Replace this branch in place (other siblings kept) | Regenerate (keeps the old reply) / Regenerate in place (replaces this reply) |
| edit (user row) | Edit → fork + regenerate (shift: fork full copy) | Edit and regenerate (Shift: copy, no reply) |
| continue / Shift | Continue this message (extends it; n-samples → pick one) / Continue the REASONING — resume inside the think block | Continue writing this message / Continue the thinking |
| system row edit | Edit — forks a new thread and regenerates the first reply | Edit and regenerate the first reply |
| no-logprobs pill | No token logprobs on this turn — native tinker sampling only | No token probabilities recorded for this turn |
| View → Layout | All = every sample stacked; Cycle = one at a time with ‹/› | Show samples stacked, or one at a time |
| View → Thinking blocks | Whether think/reasoning blocks start folded or already open | Show thinking folded or open by default |
| Contrast slider | 0 = opacity tracks match mass, 1 = any match at full tint | Higher = any match shows at full color |
| add panel | Add panel · clones the first panel's thread (Shift: blank) | Add panel with the same conversation (Shift: empty) |
| chart: answers mode | Bucket samples by exact answer text (short constrained answers) | Group samples by exact answer text |
| chart: token search | Search tokens recorded for this turn and pull one out of the rest | Find a recorded token and give it its own chip |

Left alone on purpose: the CLI-facing ones (session id, copy node handle), whose
audience knows the `tinkerscope` vocabulary.

*(opus-5.5, 2026-10-13, tinkerscope session)*

**Done 2026-10-13**: all 15 applied as proposed (Clément: "u can implement yes"), plus each control's Shift variant so one button never mixes the two vocabularies (edit: "Edit a copy of the conversation (no reply)" / "Edit (keeps the original; Shift: copy)"; add panel: "Add an empty panel"; system-row Shift: "Copy the conversation under a new prompt"). The panel prefill's power dot got the same "Apply or mute — the text is kept". Smokes that matched the old text: `browser_continue_scope` (now by aria-label), `browser_shift_edit_assistant`.
