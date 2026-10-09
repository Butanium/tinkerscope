## Move the system prompt to the top of the conversation

Clément's idea: the system prompt currently sits next to the send box, as if it were part of the next
message. Put it at the top of the conversation instead, as the first (editable) turn, which is where it
lives in the actual request. That makes it obvious which turns it applies to, and it gives a natural place
for the per-model system prompt / prefill of [per-model-prefill-and-system-prompt](per-model-prefill-and-system-prompt.md)
(e.g. one header row per panel when panels differ).

— Claude (Opus 5.5), 2026-10-08, from Clément's request
