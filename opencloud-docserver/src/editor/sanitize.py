"""HTML sanitizer to prevent XSS in editor content.

Strips dangerous elements and attributes that could lead to XSS attacks
while preserving the formatting (bold, italic, underline, headings, lists)
that editors typically use.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser


class _XSSSanitizer(HTMLParser):
    """Parse HTML and rebuild it without dangerous elements/attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._output: list[str] = []
        self._safe_tags = {"p", "b", "i", "u", "em", "strong", "h1", "h2", "h3", "h4", "h5", "h6",
                          "ul", "ol", "li", "table", "tr", "td", "th", "br", "div", "span",
                          "img", "a", "s", "sup", "sub", "strike", "del", "ins", "code", "hr",
                          "figure", "figcaption", "nav", "object",
                          "header", "footer", "section"}
        self._unsafe_tags = {"script", "iframe", "object", "embed", "applet", "form", "input",
                            "button", "select", "textarea", "meta", "link", "style", "base",
                            "frame", "frameset", "head", "title", "body", "html"}
        # Structural wrappers may be dropped as tags but their children are
        # ordinary document content. Everything else in _unsafe_tags is an
        # active/executable element whose inner content must be suppressed
        # too (a dropped <script>/<style> must not leak `alert(1)` or raw
        # CSS out as visible document text).
        self._content_preserving_containers = {"html", "body"}
        # Void elements carry no content, so they must neither open nor close
        # a suppression scope (meta/link/base emit no </meta></link> end tag;
        # counting them would swallow the rest of the document).
        self._void_tags = {"img", "br", "meta", "link", "base", "input", "frame", "embed", "hr"}
        # Depth of currently-open content-suppressing unsafe elements.
        self._suppress_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._unsafe_tags:
            if (tag not in self._content_preserving_containers
                    and tag not in self._void_tags):
                self._suppress_depth += 1
            return  # drop the entire unsafe tag
        if tag in self._safe_tags:
            attr_str = _attrs_to_html(attrs)
            self._output.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._unsafe_tags:
            if (tag not in self._content_preserving_containers
                    and tag not in self._void_tags
                    and self._suppress_depth > 0):
                self._suppress_depth -= 1
            return
        if tag in self._safe_tags:
            self._output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._suppress_depth > 0:
            return  # content of a dropped script/style/iframe must not leak
        # Escape angle brackets in raw data so entity-decoded tags
        # (&#60;script&#62;) can never survive as real HTML elements.
        safe = data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._output.append(safe)

    def handle_entityref(self, name: str) -> None:
        if self._suppress_depth > 0:
            return
        self._output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._suppress_depth > 0:
            return
        self._output.append(f"&#{name};")

    def get_output(self) -> str:
        return "".join(self._output)


# URL-bearing attributes that can smuggle a script scheme (javascript:, vbscript:,
# data:text/html) when the browser loads them. ``src``/``href`` are the obvious ones;
# the others are legacy/lesser-known but still exploitable (srcset, dynsrc, lowsrc,
# background, poster). ``srcset`` needs its own parser so it is validated separately.
_IMAGE_URL_ATTRS = {"src", "dynsrc", "lowsrc", "poster", "background", "data"}
_LINK_URL_ATTRS = {"href", "xlink:href", "formaction", "action"}


def _is_safe_image_url(value: str) -> bool:
    """True if value is a safe image URL: data:image/ or http(s)/relative."""
    if value.startswith("data:image/"):
        return True
    if value.startswith(("https://", "http://", "/", "./", "../")):
        return True
    return False


def _is_safe_link_url(value: str) -> bool:
    """True if value is a safe link URL: http(s), relative, mailto:, tel:, anchor."""
    if value.startswith(("https://", "http://", "mailto:", "tel:", "#", "/", "./", "../")):
        return True
    return False


def _is_safe_srcset(value: str) -> bool:
    """Every candidate URL in a srcset must be a safe image URL."""
    for candidate in value.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        url = candidate.split(None, 1)[0] if candidate.split(None, 1) else candidate
        if not _is_safe_image_url(url):
            return False
    return True


def _escape_attr_value(value: str | None) -> str:
    """Escape an attribute value for safe re-emission.

    The parser decodes character references inside attribute values, so a
    value like ``&quot; onmouseover=&quot;alert(1)`` reaches us as
    ``" onmouseover="alert(1)``. Re-encoding the quotes (& angle brackets)
    prevents the browser from re-parsing forged attributes or tags out of
    the sanitized output (attribute breakout / tag breakout).

    ``None`` (valueless attributes the parser emits for malformed fragments
    like ``<TD </p>``) is treated as empty — a None would otherwise crash
    ``.replace`` and the sanitize_html safety-net would wipe the document.
    """
    value = value or ""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _attrs_to_html(attrs) -> str:
    """Return HTML attribute string, stripping dangerous attributes."""
    if not attrs:
        return ""
    safe_attrs: list[tuple[str, str]] = []
    for name, value in attrs:
        lname = (name or "").lower()
        # Strip dangerous event handler attributes (onclick, onerror, ...)
        if lname.startswith("on"):
            continue
        # Strip style attributes with URL schemes that could load remote content
        if lname == "style" and value:
            # Allow basic color/spacing styles but strip url(), data: URIs, etc.
            safe_style = _sanitize_style(value)
            if safe_style:
                safe_attrs.append(("style", safe_style))
            continue
        # Validate URL-bearing attributes (img src/srcset, a href, background, ...)
        if value:
            if lname == "srcset":
                if not _is_safe_srcset(value):
                    continue  # drop srcset containing a script-scheme candidate
            elif lname in _IMAGE_URL_ATTRS:
                if not _is_safe_image_url(value):
                    continue  # drop dangerous URL (javascript:, vbscript:, data:text/html)
            elif lname in _LINK_URL_ATTRS:
                if not _is_safe_link_url(value):
                    continue
        safe_attrs.append((name, value))
    # Every emitted value is escaped so decoded quotes/angle brackets in the
    # original input can never forge attributes or tags on re-parse.
    return "".join(
        f' {name}="{_escape_attr_value(value)}"'
        for name, value in safe_attrs
        if value is not None  # valueless attrs from malformed fragments (None) are dropped
    )


def _sanitize_style(value: str) -> str | None:
    """Strip unsafe constructs from inline style. Returns None if style is unsafe."""
    # Allow common text styles only (property whitelist)
    safe_props = {
        "color", "background-color", "font-family", "font-size", "font-weight",
        "font-style", "font-variant", "text-transform", "direction",
        "text-align", "text-decoration", "margin", "margin-top",
        "margin-bottom", "margin-left", "margin-right", "padding", "padding-top",
        "padding-bottom", "padding-left", "padding-right", "border", "border-top",
        "border-bottom", "border-left", "border-right", "line-height",
    }

    # Reject any style that contains an unsafe construct anywhere
    unsafe_patterns = [
        r'url\s*\([^)]*\)',      # url(...)
        r'expression\s*\(',       # IE expressions
        r'javascript:',             # javascript: URLs
        r'vbscript:',               # VBScript URLs
        r'behavior:',               # CSS behavior
        r'-moz-binding',            # Mozilla binding
        r'@import',                 # @import
        r'\bposition\s*:',         # position: fixed/absolute (ui hijack)
    ]
    for pattern in unsafe_patterns:
        if re.search(pattern, value, re.IGNORECASE):
            return None

    # Split into individual declarations and keep only whitelisted props.
    result_parts: list[str] = []
    for decl in value.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if prop not in safe_props:
            continue
        # Reject dangerous values within an otherwise-safe property
        if re.search(r'(url\s*\(|data:|base64|\bexpression\b|javascript:|vbscript:)', val, re.IGNORECASE):
            continue
        # CSS escapes (\65 xpression) and HTML entities (&#101;xpression) can
        # hide the tokens above from the string checks — reject them outright.
        if "\\" in val or "&" in val:
            continue
        result_parts.append(f"{prop}: {val};")

    result = " ".join(result_parts)
    if not result.strip():
        return None
    if len(result) > 512:
        return None
    return result.strip()


def sanitize_html(html: str) -> str:
    """Sanitize HTML, stripping script tags, iframe, and event handlers.

    Preserves safe formatting tags: p, b, i, u, h1-h6, ul, ol, li, table, tr, td, th, br.
    Removes on* event attributes (onclick, onerror, etc.) and dangerous style values.
    """
    if not html:
        return ""
    sanitizer = _XSSSanitizer()
    try:
        # keep in sync with converter._escape_bogus_markup: `<?`/`<!` would
        # otherwise swallow text up to the next `>` inside the sanitizer too
        sanitizer.feed(re.sub(r"<(\?|!(?!--)(?!doctype))", r"&lt;\1", html, flags=re.IGNORECASE))
        sanitizer.close()  # flush trailing charref/& data before get_output()
    except Exception:
        # If parsing fails, return empty string for safety
        return ""
    return _normalize_block_structure(sanitizer.get_output())


def _normalize_block_structure(html: str) -> str:
    """Normalise stray block-inside-<p> and list-within-list markup
    (contenteditable quirks Chromium can produce).

    1. A list/table/div nested inside ``<p>`` (``<p><ul><li>..</li></ul></p>``)
       is lifted out — the block converters expect block elements at body
       level. Surrounding text becomes a sibling paragraph.
    2. A ``<ul>/<ol>`` sitting directly inside another list (a sibling of an
       ``<li>``, which invalid HTML) is moved inside the preceding ``<li>``
       so outline levels stay well-formed.

    Loops because nested cases unroll gradually.
    """
    prev = None
    while prev != html:
        prev = html
        html = re.sub(
            r"<p[^>]*>(\s*)(<(?:ul|ol|table|div)[\s>].*?)(\s*)</p>",
            lambda m: m.group(1) + m.group(2) + m.group(3),
            html,
            flags=re.S,
        )
        html = re.sub(
            r"<li([^>]*)>((?:(?!</li>).)*)</li>(\s*)(<ul[\s>].*?</ul>|<ol[\s>].*?</ol>)",
            lambda m: f"<li{m.group(1)}>{m.group(2)}{m.group(3)}{m.group(4)}</li>",
            html,
            flags=re.S,
        )
    return html
