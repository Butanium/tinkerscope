"""Hatch build hook: compile the web frontend into the wheel.

Runs `npm ci` + `npm run build` in `web/` and stages the output (`web/dist`)
at `src/tinkerscope/web_dist/`, which the wheel picks up via the `artifacts`
config. So `uv tool install tinkerscope` ships a self-contained single-process
app — users never touch npm.

The SvelteKit app is configured with adapter-static to emit a static SPA into
`web/dist` (see web/svelte.config.js).

Requires node/npm at *build* time only. `npm run build` invokes vite/svelte
directly (not an npm lifecycle script), so a global `ignore-scripts=true` npm
config does not interfere.

Set TINKERSCOPE_SKIP_WEB_BUILD=1 to reuse an existing web_dist/ (useful for
fast iteration on Python-only changes).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebDistBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return
        # Editable installs run from the source tree, where the API serves
        # `web/dist` directly — no need to compile/stage anything.
        if version == "editable":
            return
        root = Path(self.root)
        web = root / "web"
        staged = root / "src" / "tinkerscope" / "web_dist"

        if os.environ.get("TINKERSCOPE_SKIP_WEB_BUILD") == "1":
            if not (staged / "index.html").exists():
                raise RuntimeError(
                    "TINKERSCOPE_SKIP_WEB_BUILD=1 but no existing "
                    f"{staged}/index.html to reuse"
                )
            return

        if shutil.which("npm") is None:
            raise RuntimeError(
                "npm is required to build the tinkerscope wheel (it compiles "
                "the web UI). Install node, or set TINKERSCOPE_SKIP_WEB_BUILD=1 "
                "to reuse a previous build."
            )

        if not (web / "node_modules").is_dir():
            subprocess.run(["npm", "ci"], cwd=web, check=True)
        subprocess.run(["npm", "run", "build"], cwd=web, check=True)

        if staged.exists():
            shutil.rmtree(staged)
        shutil.copytree(web / "dist", staged)
