"""resolve_params — the two param routes of a ChatRequest.

"global" (browser): explicit values win, absent ones fall back to fixed server
defaults, and (in chat.py) the resolved params are written into the shared state.
"call" (CLI): explicit values apply to the one chat, absent ones inherit the
CURRENT global state, nothing is written back. system_prompt="" is an explicit
"no system prompt" in either scope (the chat builder treats empty as absent).

thread_system_prompt resolves independently of params_scope: explicit wins
("" = explicitly none), None inherits the target panel's mirror (→ "" when the
panel has none / doesn't exist). compose_system joins the two parts.
"""
from tinkerscope.api.routes.chat import ChatRequest, compose_system, resolve_params
from tinkerscope.api.state import PlaygroundState


def _st(**kw) -> PlaygroundState:
    st = PlaygroundState()
    st.system_prompt = "global sys"
    st.temperature = 0.7
    st.max_tokens = 555
    st.n_samples = 4
    st.thinking = True
    st.top_p = 0.9
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _req(**kw) -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": "hi"}], **kw)


def test_global_scope_absent_params_use_server_defaults():
    p = resolve_params(_req(), _st())
    assert p == {"system_prompt": None, "thread_system_prompt": "", "temperature": 1.0,
                 "max_tokens": 1024, "n_samples": 1, "thinking": False, "top_p": None}


def test_global_scope_explicit_params_win():
    p = resolve_params(_req(temperature=0.2, max_tokens=99, n_samples=3,
                            thinking="both", system_prompt="s", top_p=0.5), _st())
    assert p == {"system_prompt": "s", "thread_system_prompt": "", "temperature": 0.2,
                 "max_tokens": 99, "n_samples": 3, "thinking": "both", "top_p": 0.5}


def test_call_scope_absent_params_inherit_state():
    p = resolve_params(_req(params_scope="call"), _st())
    assert p == {"system_prompt": "global sys", "thread_system_prompt": "",
                 "temperature": 0.7, "max_tokens": 555,
                 "n_samples": 4, "thinking": True, "top_p": 0.9}


def test_call_scope_explicit_params_override_without_full_inherit():
    p = resolve_params(_req(params_scope="call", temperature=0.0, max_tokens=16), _st())
    assert p["temperature"] == 0.0 and p["max_tokens"] == 16
    assert p["system_prompt"] == "global sys" and p["thinking"] is True  # still inherited


def test_call_scope_empty_system_is_explicit_none():
    # "" never inherits — the CLI's --no-system escape hatch.
    p = resolve_params(_req(params_scope="call", system_prompt=""), _st())
    assert p["system_prompt"] == ""


def test_muted_global_system_is_not_inherited():
    # Power off (the split-chip mute): the kept text is skipped on inherit...
    p = resolve_params(_req(params_scope="call"), _st(system_enabled=False))
    assert p["system_prompt"] is None
    # ...but an explicit per-call prompt still applies (explicit wins)...
    p = resolve_params(_req(params_scope="call", system_prompt="s"), _st(system_enabled=False))
    assert p["system_prompt"] == "s"
    # ...and the legacy tri-state None (flag never set) behaves enabled.
    p = resolve_params(_req(params_scope="call"), _st(system_enabled=None))
    assert p["system_prompt"] == "global sys"


def test_n_samples_clamped():
    assert resolve_params(_req(n_samples=0), _st())["n_samples"] == 1
    assert resolve_params(_req(n_samples=999), _st())["n_samples"] == 200


# --------------------------------------------------------------------------- #
# thread_system_prompt tri-state (None = inherit panel mirror / "" / value)
# --------------------------------------------------------------------------- #
def _st_thread(mirror: "str | None") -> PlaygroundState:
    st = _st()
    st.panels[0].thread_system_prompt = mirror  # default panel id == "primary"
    return st


def test_thread_system_absent_inherits_panel_mirror_in_both_scopes():
    for scope in ("global", "call"):
        p = resolve_params(_req(params_scope=scope), _st_thread("thread sys"))
        assert p["thread_system_prompt"] == "thread sys", scope


def test_thread_system_explicit_value_wins_over_mirror():
    p = resolve_params(_req(thread_system_prompt="mine"), _st_thread("mirror"))
    assert p["thread_system_prompt"] == "mine"


def test_thread_system_explicit_empty_never_inherits():
    p = resolve_params(_req(thread_system_prompt=""), _st_thread("mirror"))
    assert p["thread_system_prompt"] == ""


def test_thread_system_unknown_panel_resolves_empty():
    p = resolve_params(_req(panel="p-9"), _st_thread("mirror"))
    assert p["thread_system_prompt"] == ""


# --------------------------------------------------------------------------- #
# compose_system: newline join, empty/None parts skipped
# --------------------------------------------------------------------------- #
def test_compose_system_joins_both_parts():
    assert compose_system("global", "thread") == "global\nthread"


def test_compose_system_skips_empty_parts():
    assert compose_system("global", "") == "global"
    assert compose_system(None, "thread") == "thread"
    assert compose_system("", "thread") == "thread"
    assert compose_system(None, "") == ""
    assert compose_system("", "") == ""
