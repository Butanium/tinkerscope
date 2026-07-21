"""_parse_probe_file — the `battery` probe front-matter contract."""
import pytest

from tinkerscope.cli import _parse_probe_file


def test_no_frontmatter_passthrough():
    opts, msg = _parse_probe_file("should I:\nA) x\nB) y")
    assert opts == {} and msg == "should I:\nA) x\nB) y"


def test_full_header():
    text = (
        "---\n"
        "system: Answer with a letter.\n"
        'prefill: "Recommendation: **"\n'
        "n: 2\n"
        "temperature: 0.7\n"
        "max-tokens: 150\n"
        "thinking: both\n"
        "panel: p-9, primary\n"
        "---\n"
        "the message\nline two"
    )
    opts, msg = _parse_probe_file(text)
    assert opts == {
        "system": "Answer with a letter.",
        "prefill": "Recommendation: **",
        "n": 2,
        "temperature": 0.7,
        "max_tokens": 150,
        "thinking": "both",
        "panel": ["p-9", "primary"],
    }
    assert msg == "the message\nline two"


def test_no_system_bool_and_thinking_modes():
    opts, _ = _parse_probe_file("---\nno-system: true\nthinking: off\n---\nm")
    assert opts == {"no_system": True, "thinking": False}
    opts, _ = _parse_probe_file("---\nthinking: on\n---\nm")
    assert opts == {"thinking": True}


def test_message_body_is_verbatim_after_one_stripped_newline():
    # exactly ONE leading blank line after `---` is stripped; the rest is exact
    _, msg = _parse_probe_file("---\nn: 1\n---\n\nkeep\n\ninternal blanks\n")
    assert msg == "keep\n\ninternal blanks\n"


def test_unknown_key_raises():
    with pytest.raises(ValueError, match="unknown front-matter key 'sytem'"):
        _parse_probe_file("---\nsytem: oops\n---\nm")


def test_unterminated_header_raises():
    with pytest.raises(ValueError, match="unterminated"):
        _parse_probe_file("---\nn: 1\nsystem: never closed")


def test_colonless_line_raises():
    with pytest.raises(ValueError, match="without ':'"):
        _parse_probe_file("---\njust words\n---\nm")
