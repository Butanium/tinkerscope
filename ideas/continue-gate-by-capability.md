## Gate the whole-conversation continue path by CAPABILITY, not renderer name

`_continue_prompt` (tinker_sampler.py) routes tml_v0 through `_tml_continue` via
`renderer_name.startswith("tml")`. That's brittle if another whole-conversation
renderer (one whose `render_message` raises / no per-message assistant header)
appears — it'd crash the old way. The same file already prefers capability probes
(`_build_generation_prompt` checks
`"effort" in inspect.signature(build_generation_prompt)`). A parallel probe —
e.g. does `render_message` raise NotImplementedError, or a
`renders_whole_conversation(renderer)` helper — would be more robust and
consistent. Low effort; do it when a 2nd such renderer shows up.

*(opus-4.8, 2026-07-23 — YAGNI until a 2nd such renderer shows up)*
