## Per-model prefill and system prompt, carried by packs

Today the prefill and the system prompt are global to the conversation, and a share pack can't set either
(PACK.md: a global system prompt isn't part of session persistence). Model organisms often need a
model-specific setup to behave like they did in the eval: the CoT-override post (weird-personas exp04)
prefilled the CoT with "Hmm," for DeepSeek-V3.1 and "The user is" for Nemotron-3-Ultra, "Here's a thinking
process:" for Nemotron-3.5-Lightning. Without it, the pair models rarely close their think block (1/8 and 0/8
draws in the 2026-10-08 pack test), so a recipient's first look at the pack doesn't show the CoT/answer
split the post is about.

Proposal:
- A per-model prefill and per-model system prompt (model-level defaults, overridable per panel), so a
  side-by-side of two families each gets its own setup.
- Pack fields for both, e.g. `models: [{label, ckpt, prefill: "Hmm,", system: "..."}]`, applied on `--pack`.

Related bug (same investigation): an unclosed think block is classified differently depending on the path.
In `src/tinkerscope/api/tinker_sampler.py`, `sample_stream` → `one()` (~line 1029, `to_parse = seq.tokens`),
without a prefill the parser never sees the opening think tag, so the text is filed as the answer; with a
prefill, DeepSeek's unclosed text becomes reasoning with an empty answer while Nemotron's stays the answer.
Pick one rule (probably: anything after the opening think tag with no close is reasoning, flagged
"unclosed") and apply it to every family.

— Claude (Opus 5.5), 2026-10-08, from Clément's request after the CoT-override pack test
