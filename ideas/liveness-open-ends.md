## Sample liveness: the two open ends

v1.4.0 made a waiting sample observable (tinker_sampler.py "Liveness"). Two
things it rests on were never observed, and one gap is known:

- **A cold warmup keeps answering polls.** Untested — every candidate checkpoint
  on 2026-10-05 was deleted or warm. The first time a genuinely cold model is
  sampled (an old LoRA nobody touched for weeks, an adapterless base that hasn't
  been served lately), watch its readout: it should say "tinker: working" (or
  "queued") the whole way. "no answer from tinker for …" during a warmup means
  SILENT_S is wrong and a healthy warmup will get reconnected on.
- **What actually wedged the 4-day-old process.** Two dead pooled connections,
  but new requests kept hanging after they closed. A stuck session long-poll is
  the other candidate. If it recurs, before restarting: `ss -tinp` the process,
  and note whether the readout shows "no answer" (dead connection — the fix
  handles it) or "tinker: working" forever (the server's own promise is stuck —
  nothing client-side can tell, only Stop). Tinker side: thinking-machines-lab/
  tinker-feedback#151.
- **Liveness is per sampling client, not per request.** One dead request on a
  client whose other requests are answered is not detected. Per-request would
  mean submitting through private SDK internals (`_send_asample_request` +
  `_APIFuture` with a per-call observer) — only worth it if this shape is ever
  seen.

— Opus 5.5, 2026-10-05
