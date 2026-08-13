"""The one-app-two-names seams (P3 review fixes, 2026-08-12).

Three regressions pinned here:
- `tinkerscope --base-url X <verb>` — the serve-injection used to rewrite any
  leading option to `serve --base-url …` → exit 2 "No such option", while
  `tinkerscope --help` advertised the flag.
- `tinkerscope.serve:main` — installed console-script shims froze that import
  target; deleting it bricked every existing install's `tinkerscope` command.
- the generated reference taught the back-compat alias `--conv` instead of the
  canonical `--ws` (max-by-length beat declaration order on 7 commands).

The injection only happens under prog_name "tinkerscope" (CliRunner's default
prog name is neither), so every injection test passes it explicitly.
"""
from __future__ import annotations

from typer.testing import CliRunner

from tinkerscope import cli

runner = CliRunner()

BASE = "http://127.0.0.1:9999"


def _reset_url_cache(monkeypatch):
    monkeypatch.setattr(cli, "_BASE_URL", None)
    monkeypatch.setattr(cli, "_BASE_URL_OVERRIDE", None)


def test_tinkerscope_base_url_reaches_the_driver_verb(monkeypatch):
    _reset_url_cache(monkeypatch)
    monkeypatch.setattr(cli, "_instance_info", lambda: {"discovered": False})
    monkeypatch.setattr(cli, "_get", lambda *a, **k: {})
    r = runner.invoke(cli.app, ["--base-url", BASE, "url"], prog_name="tinkerscope")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == BASE


def test_tinkerscope_base_url_equals_form_not_injected(monkeypatch):
    _reset_url_cache(monkeypatch)
    monkeypatch.setattr(cli, "_instance_info", lambda: {"discovered": False})
    monkeypatch.setattr(cli, "_get", lambda *a, **k: {})
    r = runner.invoke(cli.app, [f"--base-url={BASE}", "url"], prog_name="tinkerscope")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == BASE


def test_tinkerscope_help_after_base_url_is_the_top_level_help(monkeypatch):
    _reset_url_cache(monkeypatch)
    r = runner.invoke(cli.app, ["--base-url", BASE, "--help"], prog_name="tinkerscope")
    assert r.exit_code == 0, r.output
    assert "serve" in r.output and "send" in r.output


def test_tinkerscope_serve_options_still_inject(monkeypatch):
    """`tinkerscope --pack f` / `--port N` (the documented one-liners) keep
    routing to serve — only TOP-app options opt out of the injection."""
    import tinkerscope.serve as serve_mod

    calls: dict = {}

    def spy(dirs, **kw):
        calls["dirs"] = dirs
        calls.update(kw)

    monkeypatch.setattr(serve_mod, "run_server", spy)
    r = runner.invoke(cli.app, ["--port", "9999"], prog_name="tinkerscope")
    assert r.exit_code == 0, r.output
    assert calls["port"] == 9999


def test_serve_main_shim_delegates_to_the_cli_app(monkeypatch):
    import tinkerscope.serve as serve_mod

    called: list[bool] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(True))
    serve_mod.main()
    assert called == [True]


def test_reference_teaches_ws_not_the_conv_alias():
    from tinkerscope import _gen_cli_ref

    full = _gen_cli_ref.generate_full()
    assert "--ws" in full
    assert "--conv" not in full
