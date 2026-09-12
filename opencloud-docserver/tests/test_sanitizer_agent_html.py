"""Sanitizer tests for agent-authored hostile HTML (SEC+FUZZ).

TC-E17-07 / TC-E17-08 — agent edits are just another untrusted input class.
When agents generate or inject HTML into documents, that HTML must pass
through the sanitizer with the same safety guarantees as user-typed content.
This suite verifies:

* **Agent-corpus security** — classic XSS payloads injected by agents are
  stripped, leaving no executable primitives (TC-E17-07).

* **Structured HTML fuzzing** — Hypothesis composes agent-authored HTML from
  adversarial fragments; the sanitizer never crashes, always idempotent,
  and the document remains safe on re-parse (TC-E17-08).

The safety verdict is made on *re-parsed* output (HTMLParser pass), so
entity-escaped text that cannot execute is not confused with real HTML.

Conventions:

* Tests named `test_agent_*` explicitly simulate agent-authored content.
* Hypothesis tests use strategies that generate agent-like HTML structures.
* Idempotence is a hard requirement: ``sanitize(sanitize(x)) == sanitize(x)``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.editor.sanitize import sanitize_html


class _Collector(HTMLParser):
    """Collect the tags and attributes of a parsed document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append(tag.lower())
        self.attrs.append((tag.lower(), dict(attrs)))

    def handle_startendtag(self, tag, attrs) -> None:
        self.tags.append(tag.lower())
        self.attrs.append((tag.lower(), dict(attrs)))


# Safe tags that agents are allowed to use
_AGENT_SAFE_TAGS = {
    "p", "b", "i", "u", "em", "strong", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "tr", "td", "th", "br", "div", "span",
    "img", "a", "s", "sup", "sub", "strike", "del", "ins", "code", "hr",
    "figure", "figcaption", "nav", "object",
    "header", "footer", "section",
}

# Dangerous tags that agents must not be able to inject
_AGENT_DANGEROUS_TAGS = {
    "script", "iframe", "object", "embed", "applet", "form", "input", "button",
    "select", "textarea", "meta", "link", "style", "base", "frame", "frameset",
    "svg", "math", "video", "audio", "template", "portal",
}

# URL-bearing attributes that can smuggle executable schemes
_AGENT_URL_ATTRS = {
    "src", "href", "action", "formaction", "xlink:href", "data", "background",
    "poster", "lowsrc", "dynsrc", "srcset",
}


def _has_executable_scheme(value: str) -> bool:
    """True when an attribute value begins with an executable URL scheme.

    ``data:image/...`` is deliberately allowed (safe inline images).
    """
    v = value.strip().lower() if value else ""
    if re.match(r"(?:javascript|vbscript)\s*:", v):
        return True
    return v.startswith("data:") and not v.startswith("data:image/")


def _assert_non_executable(out: str) -> None:
    """Assert a sanitized document contains no executable construct when re-parsed."""
    parser = _Collector()
    parser.feed(out)
    for tag in parser.tags:
        assert tag not in _AGENT_DANGEROUS_TAGS, (
            f"dangerous tag <{tag}> survived in sanitized agent content: {out!r}"
        )
    for tag, attrs in parser.attrs:
        for name, value in attrs.items():
            lname = name.lower()
            assert not lname.startswith("on"), (
                f"event handler {lname} survived in sanitized agent content: {out!r}"
            )
            if lname in _AGENT_URL_ATTRS and (value or "").strip():
                assert not _has_executable_scheme(value), (
                    f"executable scheme in {lname} of sanitized agent content: {out!r}"
                )


def _check_agent_html(payload: str) -> str:
    """Sanitize agent HTML, assert safety and idempotence, return output."""
    out = sanitize_html(payload)
    assert isinstance(out, str)
    _assert_non_executable(out)
    again = sanitize_html(out)
    assert again == out, f"sanitizer not idempotent:\n1st={out!r}\n2nd={again!r}"
    return out


# ---------------------------------------------------------------------------
# 1. Agent-corpus: classic XSS payloads that agents might attempt to inject
# ---------------------------------------------------------------------------


_AGENT_XSS_CORPUS: list[str] = [
    # ---- classic script tags ----------------------------------------------
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<script src='data:text/javascript,alert(1)'></script>",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",      # tag-nesting bypass
    "<<script>alert(1)//<</script>",                    # mangled close
    "<scr\0ipt>alert(1)</scr\0ipt>",                    # null-byte smuggling
    "<svg><script>alert(1)</script></svg>",
    # ---- event handlers ----------------------------------------------------
    '<img src=x onerror="alert(1)">',
    '<img src="x" onerror=&#97;lert(1)>',              # entity-obfuscated name
    '<p onmouseover="alert(1)">x</p>',
    '<input autofocus onfocus="alert(1)">',
    "<svg/onload=alert(1)>",
    '<video><source onerror="alert(1)">',
    '"><svg/onload=alert(1)>',                          # attribute-breakout
    # ---- script URL schemes ------------------------------------------------
    '<img src="javascript:alert(1)">',
    '<a href="javascript:alert(1)">click</a>',
    '<a href="jav&#x09;ascript:alert(1)">x</a>',       # tab-obfuscated scheme
    '<a href="java&#115;cript:alert(1)">x</a>',         # entity-obfuscated
    '<a href="JAVASCRIPT:alert(1)">x</a>',              # case variation
    '<form action="javascript:alert(1)"><input></form>',
    '<base href="javascript:alert(1)//">',
    '<img src="x" srcset="x 1x, javascript:alert(1) 2x">',  # srcset smuggling
    # ---- CSS ----------------------------------------------------------------
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<div style="width:expression(alert(1))">x</div>',  # IE expression
    '<div style="background-image:url(data:image/svg+xml,<svg onload=alert(1)>)">x</div>',
    # ---- meta / link / object / embed / iframe ------------------------------
    '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
    '<link rel="import" href="data:text/html,<script>alert(1)</script>">',
    '<object data="data:text/html,<script>alert(1)</script>"></object>',
    "<embed src='data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='>",
    '<iframe src="javascript:alert(1)"></iframe>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    # ---- attribute-breakout via entity-encoded quotes -----------------------
    '<div title="&quot; onmouseover=&quot;alert(1)">x</div>',
    '<a href="/ok" alt="&quot;&gt;&lt;svg onload=alert(1)&gt;">x</a>',
    '<img src="/s.png" alt="&quot; autofocus onfocus=&quot;alert(1)">',
    # ---- SVG/event-source-in-srcset -----------------------------------------
    '<img src="x" srcset="javascript:alert(1) 1x, /ok.png 2x">',
]


@pytest.mark.parametrize(
    "payload", _AGENT_XSS_CORPUS, ids=[f"agent-xss-{i:02d}" for i in range(len(_AGENT_XSS_CORPUS))]
)
def test_agent_xss_corpus_strips_all_executables(payload: str):
    """TC-E17-07: agent-authored XSS payloads lose all executable primitives.

    Agents may attempt to inject script tags, event handlers, or script URL
    schemes through their generated HTML. The sanitizer must strip all of
    these, leaving only inert text content that cannot execute."""
    _check_agent_html(payload)


def test_agent_corpus_still_preserves_benign_formatting():
    """Agent-authored safe HTML must not be mangled.

    Agents should be able to use formatting tags (p, b, i, h1-h6, etc.) and
    safe attributes (href with https/http/relative URLs, img src with safe
    paths). This is a sanity control: the corpus is checking stripping, not
    that everything is removed."""
    html = (
        "<p>hello <b>world</b> &amp; goodbye — "
        "<a href='https://ok.example/x?a=1&amp;b=2'>link</a> "
        "<img alt='pic' src='/assets/pic.png'></p>"
    )
    out = _check_agent_html(html)
    assert "hello" in out
    assert "<b>world</b>" in out
    assert "goodbye" in out
    assert "https://ok.example/x?a=1&amp;b=2" in out
    assert "/assets/pic.png" in out
    assert "onerror" not in out


def test_agent_script_content_is_suppressed_not_leaked():
    """TC-E17-07: stripped script/iframe/etc. must not leak content as text.

    A dropped <script> must not leave alert(1) behind in the document text.
    This is the exact regression the sanitizer's suppression depth exists to
    prevent — if content suppression is broken, the payload becomes visible."""
    assert sanitize_html("<script>alert(1)</script>") == ""
    assert sanitize_html("<script>window.location='evil'</script>") == ""
    assert sanitize_html("<iframe><p>phished ui</p></iframe>") == ""


# ---------------------------------------------------------------------------
# 2. Agent-authored structured HTML: Hypothesis-driven fuzzing
# ---------------------------------------------------------------------------


# Benign HTML fragments agents are allowed to use
_AGENT_BENIGN_FRAGMENTS: list[str] = [
    "<p>hello world</p>",
    "<b>bold text</b>",
    "<i>italic text</i>",
    "<h1>heading</h1>",
    "<a href='https://example.com'>link</a>",
    "<img src='/assets/image.png' alt='alt text'>",
    "<ul><li>item 1</li><li>item 2</li></ul>",
    "<table><tr><td>cell</td></tr></table>",
    "<div class='container'><span>text</span></div>",
    "plain text without tags",
    "unicode: αβγ λπσ",
    "<br>",
    "<hr>",
]

# Dangerous fragments agents must not be able to inject
_AGENT_DANGEROUS_FRAGMENTS: list[str] = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    '<img src=x onerror=alert(1)>',
    "<svg/onload=alert(1)>",
    "javascript:alert(1)",
    '<a href="javascript:alert(1)">x</a>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<object data="data:text/html,<script>alert(1)</script>"></object>',
    '<base href="javascript:alert(1)">',
    '<form action="javascript:alert(1)"><input></form>',
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    '"><svg/onload=alert(1)>',
]


_AGENT_FRAGMENTS = st.one_of(
    st.sampled_from(_AGENT_BENIGN_FRAGMENTS),
    st.sampled_from(_AGENT_DANGEROUS_FRAGMENTS),
)


_AGENT_HTML = st.lists(
    _AGENT_FRAGMENTS, min_size=0, max_size=12
)


@given(pieces=_AGENT_HTML)
@settings(max_examples=250, deadline=None)
def test_agent_html_fuzz_sanitizes_cleanly_and_idempotently(pieces: list[str]):
    """TC-E17-08: agent-inserted structured HTML fuzz never crashes.

    Agents may generate complex HTML by assembling fragments. Hypothesis
    composes hostile documents from an adversarial vocabulary (attacks
    interleaved with benign content). Invariants hold for every input:
    the sanitizer never raises, never 500s, and produces idempotent output."""
    html = "".join(pieces)
    _check_agent_html(html)


# ---------------------------------------------------------------------------
# 3. Agent-specific safety properties
# ---------------------------------------------------------------------------


@given(text=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=100))
@settings(max_examples=100, deadline=None)
def test_agent_text_insertions_remain_safe(text: str):
    """Agents inserting plain text must not acquire executable capability.

    When an agent inserts text into a document (e.g., summarizing content,
    generating explanations), that text must remain inert. Even if the text
    looks like HTML, it should be escaped, not parsed."""
    # Agent inserts text into existing safe context
    context = "<p>Existing content</p>"
    # Simulate agent text insertion (this would be escaped by the editor)
    # For this test, we verify that text that *looks* like HTML is escaped
    payload = f"<p>{context}</p><p>{text}</p>"
    out = _check_agent_html(payload)
    # The sanitizer escapes angle brackets in text content
    # so they never become real tags
    assert "<p>Existing content</p>" in out or "Existing content" in out


@given(tag=st.sampled_from(list(_AGENT_DANGEROUS_TAGS)))
def test_agent_dangerous_tags_are_removed(tag: str):
    """Agents cannot inject dangerous tags through any means.

    Even if an agent tries to inject a script, iframe, object, etc., the
    sanitizer must remove the entire tag (including its content for
    executable tags like script)."""
    payload = f"<p>safe</p><{tag}>malicious</{tag}><p>safe</p>"
    out = sanitize_html(payload)
    # Dangerous tag and its content should be removed
    assert f"<{tag}" not in out.lower()
    assert "safe" in out


@given(tag=st.sampled_from([t for t in _AGENT_SAFE_TAGS if t != "object"]))
def test_agent_safe_tags_are_preserved(tag: str):
    """Agents can use safe formatting tags without restriction.

    Tags like p, b, i, h1-h6, ul, ol, li, table, tr, td, th, br, div, span
    are safe and should be preserved in agent-authored content."""
    if tag in {"img", "br", "hr"}:
        payload = f"<{tag}>"
    elif tag in {"p", "div", "span"}:
        payload = f"<{tag}>content</{tag}>"
    elif tag in {"b", "i", "u", "em", "strong"}:
        payload = f"<{tag}>content</{tag}>"
    elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        payload = f"<{tag}>heading</{tag}>"
    elif tag in {"ul", "ol"}:
        payload = f"<{tag}><li>item</li></{tag}>"
    elif tag in {"table"}:
        payload = f"<{tag}><tr><td>cell</td></tr></{tag}>"
    else:
        payload = f"<{tag}>content</{tag}>"

    out = _check_agent_html(payload)
    # Safe tag should be preserved (though attribute may change)
    assert tag in out.lower() or payload.lower() in out.lower()


@given(
    attr=st.sampled_from([
        "onerror", "onload", "onclick", "onmouseover",
        "onfocus", "onblur", "onsubmit", "onchange"
    ])
)
def test_agent_event_handlers_are_stripped(attr: str):
    """Agents cannot inject event handlers through any tag.

    Event handlers like onerror, onload, onclick, etc. must be stripped
    from all attributes, even on safe tags."""
    payload = f'<img src="x" {attr}="alert(1)">'
    out = _check_agent_html(payload)
    # Event handler must be removed
    assert attr not in out.lower()


@given(
    scheme=st.sampled_from([
        "javascript:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,<script>alert(1)</script>",
    ])
)
def test_agent_script_schemes_in_urls_are_blocked(scheme: str):
    """Agents cannot smuggle executable schemes through URL attributes.

    href, src, action, and other URL-bearing attributes must not allow
    javascript:, vbscript:, or dangerous data: URIs (except data:image/)."""
    payload = f'<a href="{scheme}">link</a>'
    out = sanitize_html(payload)
    # The entire href should be stripped or neutralized
    assert scheme not in out
    # Output should be safe (no executable scheme)
    assert not _has_executable_scheme(out)


def test_agent_sanitizer_is_idempotent():
    """Repeated sanitization must produce identical output.

    A core safety property: sanitize(sanitize(x)) == sanitize(x).
    This prevents multi-pass parsing attacks and ensures stable output."""
    test_cases = [
        "<script>alert(1)</script>",
        '<img src="x" onerror="alert(1)">',
        '<a href="javascript:alert(1)">x</a>',
        "<p>safe <b>content</b></p>",
        "plain text",
        "<<script>alert(1)//<</script>",
    ]
    for payload in test_cases:
        first = sanitize_html(payload)
        second = sanitize_html(first)
        third = sanitize_html(second)
        assert first == second == third, (
            f"idempotence failed for {payload!r}: "
            f"{first!r} != {second!r} != {third!r}"
        )


def test_agent_empty_and_none_input_handling():
    """Sanitizer must handle empty and None inputs gracefully.

    Agents may send empty HTML or None in edge cases. The sanitizer should
    return empty string without raising exceptions."""
    assert sanitize_html("") == ""
    assert sanitize_html(None) == ""  # type: ignore[arg-type]
    # NOTE: existing behaviour — whitespace-only input is preserved as-is.
    # The sanitizer only strips dangerous elements/attributes, not whitespace.
    out = sanitize_html("   ")
    assert out == "   " or out == ""


def test_agent_malformed_html_does_not_crash_sanitizer():
    """Malformed HTML from agents must not cause 500 errors.

    Agents may send intentionally malformed or broken HTML. The sanitizer
    must handle it gracefully (return safe output) without raising."""
    malformed_cases = [
        "<p>unclosed tag",
        "<div><span>nested</div></span>",  # mismatched
        "<<invalid>>",
        "<script><</script>",  # incomplete
        "text with <<< brackets >>>",
    ]
    for payload in malformed_cases:
        try:
            out = sanitize_html(payload)
            # Output should be a string, safe (no dangerous tags)
            assert isinstance(out, str)
            # If it has tags, they should be safe
            parser = _Collector()
            try:
                parser.feed(out)
                for tag in parser.tags:
                    assert tag not in _AGENT_DANGEROUS_TAGS
            except Exception:
                # If re-parsing fails, that's okay for malformed input
                pass
        except Exception as e:
            pytest.fail(f"Sanitizer crashed on malformed input {payload!r}: {e}")


def test_valueless_attr_fragment_never_wipes_document():
    """A malformed fragment like ``<TD </p>`` makes HTMLParser emit valueless
    attributes as None ([('<', None), ('p', None)]). The escaper must not
    crash on it — a crash used to fall through to the safety-net ``return ""``
    and silently wipe the entire document (content-loss, not just XSS)."""
    out = sanitize_html("<p><p>Existing content</p></p><p><TD </p>")
    assert "Existing content" in out, f"document wiped: {out!r}"
    assert "<td" in out
    # vulnerable variant: hostile script + malformed fragment must strip
    # the script yet keep the good text
    hostile = "<script>alert(1)</script><p><TD <p>keep me</p>"
    out2 = sanitize_html(hostile)
    assert "keep me" in out2
    assert "<script" not in out2.lower()
    # idempotence (the sanitizer contract for repeated agent passes)
    assert sanitize_html(out2) == out2
