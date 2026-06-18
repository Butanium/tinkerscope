# deprecated/

- `server.py` — Harry Mayne's original single-file playground backend (FastAPI + SSE),
  the fork origin for tinkerscope. Read `models.yaml` (hand-maintained), used latteries'
  `TinkerCaller`, and had negation_neglect-specific endpoints (`/api/questions` over a
  `facts/` dir, lightshow highlight export). Deprecated 2026-06-18: fully superseded by the
  package backend under `src/tinkerscope/api/` — auto-discovery (`discovery.py`) replaces
  `models.yaml`, the tinker SDK is called directly (no latteries) in `tinker_sampler.py`,
  and the project-specific endpoints were dropped. Kept for provenance/credit; safe to delete.
