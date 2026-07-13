"""FastAPI app entry point.

Serves both the JSON API and (when a build exists) the compiled web UI from
one process: routers are registered first, then the SPA is mounted at `/`,
so `/api/*` always wins precedence.

All dependencies (tinker, tinker-cookbook) are required and always installed,
so there is no import-degrade path. Two *runtime* conditions are still
surfaced via /api/health so the UI can react: TINKER_API_KEY unset, and a
run's base model not being in tinker's currently-served list.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import conversation_store
from .discovery import get_capabilities
from .routes import (
    chat,
    conversations,
    datasets,
    highlights,
    models,
    openrouter_models,
    pins,
    prefs,
    state,
)
from .settings import SETTINGS


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Storage v2: migrate the legacy conversations.json (if present) into the
    # per-conversation files + node blobs, then build the in-memory summary cache.
    # RAISES on a migration verify mismatch — refuse to start rather than serve
    # partial data (the legacy file is left untouched). Off the event loop because
    # verifying a large store is CPU-bound; the raise still propagates through await.
    await asyncio.to_thread(conversation_store.boot)
    # Warm the tinker capabilities cache so the first /api/models call can
    # already mark runs sampleable/not. Non-fatal if it fails (offline / no key).
    # Offloaded to a thread: the tinker SDK's get_server_capabilities is sync and
    # warns (deadlock risk) if called on the event loop.
    await asyncio.to_thread(get_capabilities)
    yield


app = FastAPI(title="tinkerscope", lifespan=lifespan)

# Only the vite dev server is cross-origin (the packaged UI is same-origin);
# allow any localhost port so dev proxies don't need CORS surgery.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

app.include_router(models.router)
app.include_router(openrouter_models.router)
app.include_router(chat.router)
app.include_router(state.router)
app.include_router(datasets.router)
app.include_router(highlights.router)
app.include_router(pins.router)
app.include_router(prefs.router)
app.include_router(conversations.router)


@app.get("/api/health")
def health() -> dict:
    caps = get_capabilities()
    return {
        "ok": True,
        "root": str(SETTINGS.root),
        "scan_roots": [str(r) for r in SETTINGS.scan_roots],
        "tinker_key": bool(SETTINGS.tinker_api_key),
        "openrouter_key": bool(SETTINGS.openrouter_api_key),
        # caps: {"available": bool, "supported_models": [str, ...], "error": str|None}
        **caps,
    }


def _web_dist() -> Path | None:
    """Locate the built frontend.

    Source checkouts (editable installs) prefer `web/dist` — the live vite
    build output — over the packaged `web_dist`, which is a staging copy left
    behind by previous wheel builds. Wheel installs have no `web/` sibling, so
    they fall through to the packaged copy.
    """
    checkout = Path(__file__).resolve().parents[3] / "web" / "dist"
    if (checkout / "index.html").exists():
        return checkout
    packaged = Path(__file__).resolve().parents[1] / "web_dist"
    if (packaged / "index.html").exists():
        return packaged
    return None


_dist = _web_dist()
if _dist is not None:
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
