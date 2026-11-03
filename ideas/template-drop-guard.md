## Refuse a fire whose chat template dropped one of its messages

Since 2026-10-30 every system turn is sent where it stands, and a user turn can be
switched to system mid-thread. On the native and vLLM paths that message goes through
a chat template, and a template may silently leave a message out. The team lead
rendered Qwen3-8B's HF template with a `developer` turn mid-thread: the turn was absent
and nothing raised. That case was resolved by dropping `developer` from the role toggle
(2026-10-31). The same failure can come back with any template that ignores a role or a
position, e.g. one that keeps only a leading system message.

The guard: both paths hold the rendered prompt before sampling (vLLM `/tokenize`
returns it; the tinker renderers build it). Check that every message's text occurs in
it, in order, and stop the fire with an error naming the role and index ("the model's
chat template dropped the system message at turn 4") when one doesn't. This is a
substring check, so formatting a template adds around the text doesn't matter. A
thinking block the template strips from history on purpose (`strip_thinking_from_history`)
must not count as a drop: check `content` only, not `reasoning`.

Not done because no template in use is known to drop a system message. Do it when one
turns up, or when a new backend whose templates we don't control is added.

— claude-opus-5-5 (roles teammate), 2026-11-02
