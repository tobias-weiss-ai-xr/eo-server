"""ODT <-> HTML conversion using odfpy.

Stoic goals (same as the DOCX converter): preserve text,
bold/italic/underline, headings, lists, tables, and images. We do NOT
attempt pagination or print-fidelity — the editor is a web page, not a
print preview.

HTML -> ODT is lossy by nature (web HTML is richer than we map); we map
only the subset our editor produces, plus whatever reasonable tags appear.

Images survive as self-contained data URIs on the HTML side and as
``draw:frame``/``draw:image`` (package-embedded binary) on the ODT side.
The ``alt`` text of an ``<img>`` is preserved as the ODF-standard
``svg:title`` child of the ``draw:frame`` (accessible name) and comes
back as an ``alt`` attribute on the re-exported ``<img>``.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import re
import zipfile
from html import escape

from odf.draw import Frame, Image
from odf.element import Element, Node
from odf.namespaces import (
    CHARTNS,
    DCNS,
    DRAWNS,
    MATHNS,
    OFFICENS,
    STYLENS,
    SVGNS,
    TABLENS,
    TEXTNS,
    XLINKNS,
    XMLNS,
)
from odf.office import Annotation
from odf.opendocument import OpenDocumentText, load
from odf.style import (
    ParagraphProperties,
    Style,
    TableCellProperties,
    TableColumnProperties,
    TableProperties,
    TextProperties,
)
from odf.table import CoveredTableCell, Table, TableCell, TableColumn, TableRow
from odf.text import (
    A,
    BookmarkEnd,
    BookmarkRef,
    BookmarkStart,
    ChangeEnd,
    ChangeStart,
    Deletion,
    H,
    Insertion,
    List,
    ListItem,
    ListLevelStyleNumber,
    ListStyle,
    Note,
    NoteBody,
    NoteCitation,
    P,
    Span,
    TrackedChanges,
)

from src.editor.converter import (
    _MONO_FONTS,
    _PAGE_SETUP_RE,
    _inline_to_text,
    _inline_tokens,
    _normalize_color,
    _page_setup_marker,
    _parse_border,
    _tokenize_body,
    _twips_attr,
)

_BOLD_WEIGHTS = {"bold", "bolder", "600", "700", "800", "900"}
_ITALIC_STYLES = {"italic", "oblique"}
_UNDERLINE_STYLES = {"solid", "dotted", "dash", "long-dash", "dot-dash",
                     "dot-dot-dash", "wave", "double"}
# Anything that is not "none" (or unset) counts as underline, but keeping
# an explicit set documents the intent and guards against typos.


# --------------------------------------------------------------------------
# Style resolution
# --------------------------------------------------------------------------

def _style_text_props(el):
    """Collect the character-effect properties of one style element."""
    for child in el.childNodes:
        if child.qname == (STYLENS, "text-properties"):
            return {
                "fontweight": child.getAttribute("fontweight"),
                "fontstyle": child.getAttribute("fontstyle"),
                "textunderlinestyle": child.getAttribute("textunderlinestyle"),
                "textlinethroughstyle": child.getAttribute("textlinethroughstyle"),
                "color": child.getAttribute("color"),
                "backgroundcolor": child.getAttribute("backgroundcolor"),
                "fontfamily": child.getAttribute("fontfamily"),
                "fontsize": child.getAttribute("fontsize"),
                "fontvariant": child.getAttribute("fontvariant"),
                "texttransform": child.getAttribute("texttransform"),
                "textposition": child.getAttribute("textposition"),
            }
    return {}


def _none_flags() -> dict:
    return {
        "bold": None, "italic": None, "underline": None, "strike": None,
        "color": None, "bg": None, "font_family": None, "font_size": None,
        "vert": None, "small_caps": None, "all_caps": None,
    }


def _build_style_resolver(doc):
    """Return a resolver mapping a style name to effective character flags.

    The resolver walks the parent-style chain and returns a dict with
    keys ``bold`` / ``italic`` / ``underline`` whose values are
    ``True``, ``False`` or ``None`` (unspecified — inherit from the
    enclosing context). Property-level inheritance is preserved: a child
    style only overrides the properties it actually declares.
    """
    raw: dict[str, tuple[dict, str]] = {}
    for root in (doc.styles, doc.automaticstyles):
        for el in root.childNodes:
            if el.qname == (STYLENS, "style") and el.getAttribute("family") == "text":
                name = el.getAttribute("name")
                if name:
                    raw[name] = (_style_text_props(el), el.getAttribute("parentstylename"))

    cache: dict[str, dict] = {}

    def resolve(name: str | None) -> dict:
        if not name:
            return _none_flags()
        if name in cache:
            return cache[name]
        if name not in raw:
            return _none_flags()
        props, parent = raw[name]
        out = dict(resolve(parent))
        weight = props.get("fontweight")
        if weight in _BOLD_WEIGHTS:
            out["bold"] = True
        elif weight in ("normal", "lighter", "100", "200", "300", "400", "500"):
            out["bold"] = False
        fstyle = props.get("fontstyle")
        if fstyle in _ITALIC_STYLES:
            out["italic"] = True
        elif fstyle == "normal":
            out["italic"] = False
        under = props.get("textunderlinestyle")
        if under in _UNDERLINE_STYLES:
            out["underline"] = True
        elif under == "none":
            out["underline"] = False
        linethrough = props.get("textlinethroughstyle")
        if linethrough and linethrough != "none":
            out["strike"] = True
        elif linethrough == "none":
            out["strike"] = False
        color = props.get("color")
        if color:
            out["color"] = _normalize_color(color)
        bg = props.get("backgroundcolor")
        if bg:
            out["bg"] = _normalize_color(bg)
        family = props.get("fontfamily")
        if family is not None:
            out["font_family"] = family
        size = props.get("fontsize")
        if size is not None:
            out["font_size"] = size.lower()
        variant = props.get("fontvariant")
        if variant in ("small-caps", "smallcaps"):
            out["small_caps"] = True
        elif variant in ("normal", "none"):
            out["small_caps"] = False
        ttransform = props.get("texttransform")
        if ttransform == "uppercase":
            out["all_caps"] = True
        elif ttransform in ("none", "lowercase"):
            out["all_caps"] = False
        valign = props.get("textposition")
        if valign in ("super", "superscript"):
            out["vert"] = "sup"
        elif valign in ("sub", "subscript"):
            out["vert"] = "sub"
        elif valign == "auto":
            out["vert"] = None
        cache[name] = out
        return out

    return resolve


def _list_kinds(doc) -> dict[str, str]:
    """Map list-style names to 'ul' or 'ol' based on their first level."""
    kinds: dict[str, str] = {}
    for root in (doc.styles, doc.automaticstyles):
        for el in root.childNodes:
            if el.qname == (TEXTNS, "list-style"):
                name = el.getAttribute("name")
                if not name:
                    continue
                for child in el.childNodes:
                    if child.qname == (TEXTNS, "list-level-style-bullet"):
                        kinds[name] = "ul"
                        break
                    if child.qname == (TEXTNS, "list-level-style-number"):
                        kinds[name] = "ol"
                        break
    return kinds


# --------------------------------------------------------------------------
# Image handling (draw:frame / draw:image <-> <img>)
# --------------------------------------------------------------------------


def _get_attr(el, ns: str, name: str) -> str | None:
    """Read a namespaced attribute from a loaded ODF element."""
    return dict(el.attributes).get((ns, name))


def _picture_mime(name: str) -> str:
    """Best-effort media type for a Pictures/ member from its extension."""
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _extract_pictures(data: bytes) -> dict[str, tuple[str, bytes]]:
    """Map every ``Pictures/`` member of the ODT package to (mime, bytes).

    ODF stores referenced images under ``Pictures/``; ``draw:image``
    xlink:hrefs point at those paths. Best-effort: on any zip problem we
    simply get no pictures and the text still converts.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return {}
    pictures: dict[str, tuple[str, bytes]] = {}
    for name in z.namelist():
        if name.startswith("Pictures/"):
            pictures[name] = (_picture_mime(name), z.read(name))
    return pictures


def _data_uri(mime: str, content: bytes) -> str:
    """Encode image bytes as a self-contained ``data:`` URI for the editor."""
    b64 = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{b64}"


_DATA_URI_RE = re.compile(r"^data:([^;,\s]+)(;[^,]*)?,(.*)$", re.S)


def _decode_data_uri(src: str) -> tuple[str | None, bytes | None]:
    """Decode a ``data:`` URI into (mime, bytes).

    Returns (None, None) when the value is not an embeddable data URI (e.g.
    an http(s) src that a server-side converter cannot fetch).
    """
    if not src:
        return None, None
    m = _DATA_URI_RE.match(src)
    if not m:
        return None, None
    mime = (m.group(1) or "image/png").lower()
    meta = m.group(2) or ""
    payload = m.group(3)
    try:
        if "base64" in meta:
            content = base64.b64decode(payload, validate=True)
        else:
            from urllib.parse import unquote

            content = unquote(payload).encode("utf-8")
    except Exception:
        return None, None
    if not content:
        return None, None
    return mime, content


def _sniff_mime(data: bytes) -> str:
    """Detect an image media type from its magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:5] in (b"<?xml", b"<svg "):
        return "image/svg+xml"
    return "image/png"


_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read pixel dimensions out of a JPEG's SOF marker."""
    i = 2
    n = len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in _SOF_MARKERS:
            if i + 9 <= n:
                return (int.from_bytes(data[i + 7:i + 9], "big"),
                        int.from_bytes(data[i + 5:i + 7], "big"))
            return None
        if marker == 0xFF or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + seglen
    return None


def _image_dimensions(mime: str, data: bytes) -> tuple[int, int] | None:
    """Best-effort intrinsic pixel size for common raster formats."""
    mime = (mime or "").lower()
    if mime == "image/png" and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return (int.from_bytes(data[16:20], "big"),
                int.from_bytes(data[20:24], "big"))
    if mime == "image/gif" and data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return (int.from_bytes(data[6:8], "little"),
                int.from_bytes(data[8:10], "little"))
    if mime == "image/jpeg" and data[:2] == b"\xff\xd8":
        return _jpeg_dimensions(data)
    if mime == "image/bmp" and data[:2] == b"BM" and len(data) >= 26:
        w = int.from_bytes(data[18:22], "little")
        h = int.from_bytes(data[22:26], "little")
        return (w, abs(h))
    return None


def _frame_alt(frame_el) -> str:
    """Accessible name (svg:title child) of a draw:frame, if any."""
    for child in frame_el.childNodes:
        if child.nodeType == Node.ELEMENT_NODE and child.qname == (SVGNS, "title"):
            return "".join(n.data for n in child.childNodes if n.nodeType == Node.TEXT_NODE)
    return ""


def _odf_find(el, qname):
    """Recursively find the first descendant element with ``qname`` (odfpy
    Elements have no ElementTree-style ``.iter``)."""
    for ch in el.childNodes:
        if ch.nodeType != Node.ELEMENT_NODE:
            continue
        if ch.qname == qname:
            return ch
        found = _odf_find(ch, qname)
        if found is not None:
            return found
    return None


def _odf_text(el) -> str:
    """Concatenate all text-node data under ``el``."""
    out = []
    for ch in el.childNodes:
        if ch.nodeType == Node.TEXT_NODE:
            out.append(ch.data or "")
        elif ch.nodeType == Node.ELEMENT_NODE:
            out.append(_odf_text(ch))
    return "".join(out)


def _odt_frame_object(frame, pictures) -> str | None:
    """Return an object placeholder for a draw:frame holding an embedded
    object, or None for plain image frames."""
    name = frame.getAttribute("name") or ""
    txt = _odf_text(frame)
    if _odf_find(frame, (CHARTNS, "chart")) is not None:
        return '<div class="object" data-type="chart" data-label="Chart"></div>'
    math_el = _odf_find(frame, (MATHNS, "math"))
    if math_el is not None:
        return f'<div class="object" data-type="equation">{escape(_odf_text(math_el))}</div>'
    if name.startswith("object-"):
        typ = name[len("object-"):]
        return f'<div class="object" data-type="{escape(typ)}">{escape(txt)}</div>'
    if _odf_find(frame, (DRAWNS, "custom-shape")) is not None:
        return '<div class="object" data-type="shape"></div>'
    if _odf_find(frame, (DRAWNS, "text-box")) is not None:
        return f'<div class="object" data-type="textbox">{escape(txt)}</div>'
    return None


def _frame_to_html(frame_el, pictures: dict) -> str:
    """Render a draw:frame (holding a draw:image) as an <img> data URI.

    Handles both package-referenced images (xlink:href into ``Pictures/``)
    and ``office:binary-data`` embedded directly in content.xml. The
    frame's ``svg:title`` (if any) is emitted back as the ``alt``
    attribute.
    """
    alt = _frame_alt(frame_el)
    for child in frame_el.childNodes:
        if child.nodeType != Node.ELEMENT_NODE or child.qname != (DRAWNS, "image"):
            continue
        mime, content = None, None
        href = _get_attr(child, XLINKNS, "href")
        if href:
            key = href[2:] if href.startswith("./") else href
            key = key.lstrip("/")
            entry = pictures.get(href) or pictures.get(key)
            if entry:
                mime, content = entry
        if content is None:
            for sub in child.childNodes:
                if sub.nodeType == Node.ELEMENT_NODE and sub.qname == (OFFICENS, "binary-data"):
                    raw = "".join(n.data for n in sub.childNodes
                                   if n.nodeType == Node.TEXT_NODE)
                    try:
                        content = base64.b64decode(raw)
                        mime = _sniff_mime(content)
                    except Exception:
                        content = None
                    break
        if content:
            attrs = []
            for key in ("width", "height"):
                val = _get_attr(frame_el, SVGNS, key)
                m = re.fullmatch(r"\s*(\d+)\s*px\s*", val or "")
                if m:
                    attrs.append(f' {key}="{m.group(1)}"')
            if alt:
                attrs.append(f' alt="{escape(alt)}"')
            return f'<img src="{_data_uri(mime or "image/png", content)}"' + "".join(attrs) + "/>"
    return ""


def _parse_px(value) -> int | None:
    """Parse an integer pixel length from an HTML attribute (e.g. '120')."""
    if value is None:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(?:px)?\s*", value)
    return int(m.group(1)) if m else None


def _raw_attr(el, *names):
    """Read an element attribute tolerating odfpy's name validation.

    ``getAttribute`` rejects names it does not know about, but FO attrs like
    ``fo:border-bottom`` are accessed by several spellings; walk the raw
    attribute map instead.
    """
    wanted = {n.replace("-", "") for n in names}
    for (ns, local), val in (getattr(el, "attributes", None) or {}).items():
        if local.replace("-", "") in wanted:
            return val
    return None


def _style_para_props(el):
    """Collect the paragraph properties of one style element."""
    for child in el.childNodes:
        if child.qname == (STYLENS, "paragraph-properties"):
            return {
                "textalign": _raw_attr(child, "textalign"),
                "lineheight": _raw_attr(child, "lineheight"),
                "marginleft": _raw_attr(child, "marginleft"),
                "marginright": _raw_attr(child, "marginright"),
                "margintop": _raw_attr(child, "margintop"),
                "marginbottom": _raw_attr(child, "marginbottom"),
                "textindent": _raw_attr(child, "textindent"),
                "breakbefore": _raw_attr(child, "breakbefore"),
                "writingmode": _raw_attr(child, "writingmode"),
                "borderbottom": _raw_attr(child, "border-bottom", "borderbottom"),
                "bordertop": _raw_attr(child, "border-top", "bordertop"),
                "borderleft": _raw_attr(child, "border-left", "borderleft"),
                "borderright": _raw_attr(child, "border-right", "borderright"),
            }
    return {}


def _build_para_resolver(doc):
    """Resolve a paragraph style name to its effective paragraph props.

    Returns a dict with keys textalign / lineheight / marginleft /
    marginright / margintop / marginbottom / textindent / breakbefore /
    writingmode, honouring parent-style inheritance.
    """
    raw: dict[str, tuple[dict, str]] = {}
    for root in (doc.styles, doc.automaticstyles):
        for el in root.childNodes:
            if el.qname == (STYLENS, "style") and el.getAttribute("family") == "paragraph":
                name = el.getAttribute("name")
                if name:
                    raw[name] = (_style_para_props(el), el.getAttribute("parentstylename"))

    cache: dict[str, dict] = {}

    def _none() -> dict:
        return {k: None for k in (
            "textalign", "lineheight", "marginleft", "marginright",
            "margintop", "marginbottom", "textindent", "breakbefore",
            "writingmode", "borderbottom", "bordertop", "borderleft",
            "borderright",
        )}

    def resolve(name: str | None) -> dict:
        if not name:
            return _none()
        if name in cache:
            return cache[name]
        if name not in raw:
            return _none()
        props, parent = raw[name]
        out = dict(resolve(parent))
        for key, val in props.items():
            if val is not None:
                out[key] = val
        cache[name] = out
        return out

    return resolve


def _odt_len_to_pt(val: str) -> float | None:
    """Normalise an ODF length ("24pt", "2.12cm", "0.5in") to points."""
    m = re.match(r"^\s*([\d.]+)\s*(pt|cm|in|mm)?\s*$", val, re.I)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "pt").lower()
    scale = {"pt": 1.0, "cm": 28.3465, "in": 72.0, "mm": 2.83465}
    try:
        return num * scale.get(unit, 1.0)
    except (TypeError, ValueError):
        return None


def _para_css(props: dict) -> list[str]:
    """CSS declarations for a resolved ODF paragraph style."""
    css: list[str] = []
    align = props.get("textalign")
    if align in ("center",):
        css.append("text-align:center")
    elif align in ("end", "right"):
        css.append("text-align:right")
    lh = props.get("lineheight") or ""
    m = re.match(r"^(\d+(?:\.\d+)?)%$", lh.strip())
    if m:
        mult = float(m.group(1)) / 100.0
        if abs(mult - 1.0) > 1e-6:
            css.append(f"line-height:{mult:g}")
    for key, csskey in (
        ("marginleft", "margin-left"), ("marginright", "margin-right"),
        ("margintop", "margin-top"), ("marginbottom", "margin-bottom"),
        ("textindent", "text-indent"),
    ):
        v = _odt_len_to_pt(props.get(key) or "")
        if v:
            css.append(f"{csskey}:{v:g}pt")
    mode = props.get("writingmode")
    if mode and mode.lower() in ("rtl", "rl", "rl-tb"):
        css.append("direction:rtl")
    if (props.get("breakbefore") or "").lower() == "page":
        css.append("page-break-before:always")
    return css


def odt_to_html(data: bytes) -> str:
    """Convert ODT bytes to an HTML fragment (content only, no <html>).

    Never raises on hostile or corrupt input (fault-injection suite): an
    unreadable document degrades to empty content instead of crashing the
    read path with a 500.
    """
    try:
        return _odt_to_html(data)
    except Exception:
        return ""


def _odt_to_html(data: bytes) -> str:
    """Convert ODT bytes to an HTML fragment (content only, no <html>)."""
    doc = load(io.BytesIO(data))
    resolve = _build_style_resolver(doc)
    presolve = _build_para_resolver(doc)
    cellresolve = _build_cell_resolver(doc)
    kinds = _list_kinds(doc)
    pictures = _extract_pictures(data)
    changes = _collect_changes(doc)
    # A caption is a text:p carrying text:sequence-name (e.g. "Table") that
    # immediately precedes a table: it is rendered as the table's <figcaption>
    # (and must not also render as a standalone paragraph).
    children = list(doc.text.childNodes)
    caption_for: dict = {}
    skip_paras = set()
    for i, ch in enumerate(children):
        if ch.nodeType != Node.ELEMENT_NODE:
            continue
        if ch.qname == (TEXTNS, "p") and ch.attributes.get((TEXTNS, "sequence-name")):
            nxt = children[i + 1] if i + 1 < len(children) else None
            if nxt is not None and nxt.nodeType == Node.ELEMENT_NODE and nxt.qname == (TABLENS, "table"):
                from odf import teletype
                caption_for[nxt] = teletype.extractText(ch).strip()
                skip_paras.add(id(ch))

    # Extract header and footer from master page if present
    header_html = ""
    footer_html = ""

    # Find the master page in styles.xml
    for root in (doc.styles, doc.masterstyles, doc.automaticstyles):
        for child in root.childNodes:
            if child.qname == (STYLENS, "master-page"):
                # Extract header content
                for subchild in child.childNodes:
                    if subchild.qname == (STYLENS, "header"):
                        header_html = _master_page_section_to_html(subchild, resolve, pictures)
                    elif subchild.qname == (STYLENS, "footer"):
                        footer_html = _master_page_section_to_html(subchild, resolve, pictures)

    parts: list[str] = []
    pending_list: str | None = None  # 'ul' | 'ol' while collecting <li>s

    def flush_list() -> None:
        nonlocal pending_list
        if pending_list:
            parts.append(f"</{pending_list}>")
            pending_list = None

    def render(children) -> None:
        """Render body-level children into ``parts`` (recurses into
        text:section elements so section columns survive the round-trip)."""
        nonlocal pending_list
        for child in children:
            if child.nodeType != Node.ELEMENT_NODE:
                continue
            qname = child.qname
            if qname == (TEXTNS, "section"):
                cols = child.attributes.get((TEXTNS, "columns"))
                if cols:
                    saved = parts[:]
                    parts.clear()
                    render(child.childNodes)
                    sec_html = "".join(parts)
                    parts[:] = saved
                    parts.append(f'<section data-columns="{cols}">{sec_html}</section>')
                    continue
                parts.append('<hr class="section-break">')
                render(child.childNodes)  # section without columns: inline
                continue
            if qname == (TEXTNS, "table-of-content"):
                name = child.attributes.get((TEXTNS, "name")) or ""
                parts.append(f'<nav class="toc" data-title="{escape(name)}"></nav>')
                continue
            if qname in ((TEXTNS, "p"), (TEXTNS, "h")):
                if id(child) in skip_paras:
                    continue
                flush_list()
                parts.append(_paragraph_to_html(child, resolve, pictures, presolve, changes))
            elif qname == (TEXTNS, "list"):
                kind = kinds.get(child.getAttribute("stylename"), "ul")
                if pending_list != kind:
                    flush_list()
                    pending_list = kind
                    parts.append(f"<{kind}>")
                for item in _list_items_to_html(child, resolve, kinds, pictures):
                    parts.append(item)
            elif qname == (TABLENS, "table"):
                flush_list()
                parts.append(_table_to_html(child, resolve, pictures, presolve, cellresolve, caption_for.get(child)))
            elif qname == (DRAWNS, "frame"):
                # A draw:frame may hold an image OR an embedded object
                # (shape/textbox/chart/equation). Detect objects first.
                flush_list()
                obj = _odt_frame_object(child, pictures)
                if obj is not None:
                    parts.append(obj)
                else:
                    frame_html = _frame_to_html(child, pictures)
                    if frame_html:
                        parts.append(f"<p>{frame_html}</p>")

    render(doc.text.childNodes)
    flush_list()

    # Build the final HTML with header/footer
    html_parts = [_page_setup_marker_from_odt(doc)]
    if header_html:
        html_parts.append(f'<header class="page-header">{header_html}</header>')
    html_parts.extend(parts)
    if footer_html:
        html_parts.append(f'<footer class="page-footer">{footer_html}</footer>')

    return "\n".join(p for p in html_parts if p)


def _odt_len_to_twips(value) -> int | None:
    """ODF length string (e.g. '21cm', '0.98in', '297mm', '567pt') -> twips.
    Bare numbers are interpreted as 1/100 mm per the ODF length default."""
    import re as _re
    if value is None:
        return None
    m = _re.match(r'\s*(-?[\d.]+)\s*(cm|mm|in|pt|pc)?\s*$', str(value))
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2) or ""
    tw = {"cm": v * 566.929, "mm": v * 56.6929, "in": v * 1440.0,
          "pt": v * 20.0, "pc": v * 240.0, "": v / 10.0}.get(unit)
    return int(round(tw)) if tw is not None else None


def _odt_twips_to_len(tw: int) -> str:
    return f"{tw / 566.929:.3f}cm"


def _page_setup_marker_from_odt(doc) -> str:
    """Marker for the master page's page layout ('' when unknown)."""
    from odf.style import PageLayout, PageLayoutProperties
    try:
        layout_name = None
        for root in (doc.styles, doc.masterstyles, doc.automaticstyles):
            for child in root.childNodes:
                if getattr(child, "qname", ("", ""))[1] == "master-page":
                    layout_name = child.getAttribute("pagelayoutname") or layout_name
        if not layout_name:
            return ""
        props = None
        for root in (doc.automaticstyles, doc.styles):
            for child in root.childNodes:
                if (getattr(child, "qname", ("", ""))[1] == "page-layout"
                        and child.getAttribute("name") == layout_name):
                    found = child.getElementsByType(PageLayoutProperties)
                    if found:
                        props = found[0]
                        break
            if props is not None:
                break
        if props is None:
            return ""
        g = props.getAttribute
        orient = "landscape" if (g("printorientation") or "").lower() == "landscape" else "portrait"
        tw = lambda v: _odt_len_to_twips(v)
        w, h = tw(g("pagewidth")), tw(g("pageheight"))
        if orient == "portrait" and w and h and w > h:
            w, h = h, w
        marker = _page_setup_marker(
            w, h, orient,
            tw(g("margintop")), tw(g("marginbottom")),
            tw(g("marginleft")), tw(g("marginright")))
        return marker
    except Exception:
        return ""


def _apply_page_setup_marker_odt(doc, attrs: str) -> None:
    """Apply the marker to the document's page layout.

    Creates/extends the WO_PageLayout automatic style and points the
    Standard master page at it (creating the master when odfpy made none).
    ponytail: the layout lives in content.xml automatic-styles (odfpy
    limitation); LibreOffice honors it only when Standard references it
    there — move to styles.xml automatic-styles if LO interop matters.
    """
    from odf.style import MasterPage, PageLayout, PageLayoutProperties
    tw = lambda name: _twips_attr(attrs, name)
    has_any = any(tw(n) for n in ("page-w", "page-h", "margin-top",
                                  "margin-bottom", "margin-left", "margin-right"))
    om = re.search(r'data-orient\s*=\s*"?(landscape|portrait)', attrs, re.I)
    if not has_any and not om:
        return
    layout = None
    for child in doc.automaticstyles.childNodes:
        if (getattr(child, "qname", ("", ""))[1] == "page-layout"
                and child.getAttribute("name") == "WO_PageLayout"):
            layout = child
            break
    if layout is None:
        layout = PageLayout(name="WO_PageLayout")
        doc.automaticstyles.addElement(layout)
    # One properties element carries everything; replace the previous one
    # so a re-apply updates rather than duplicates.
    for old in layout.getElementsByType(PageLayoutProperties):
        layout.removeChild(old)
    w, h = tw("page-w"), tw("page-h")
    kwargs = {}
    if w and h:
        if (om and om.group(1).lower() == "landscape") != (w > h):
            w, h = h, w  # keep w/h consistent with the orientation flag
        kwargs.update(pagewidth=_odt_twips_to_len(w), pageheight=_odt_twips_to_len(h))
    kwargs["printorientation"] = om.group(1).lower() if om else "portrait"
    for attr, prop in (("margin-top", "margintop"), ("margin-bottom", "marginbottom"),
                       ("margin-left", "marginleft"), ("margin-right", "marginright")):
        if tw(attr):
            kwargs[prop] = _odt_twips_to_len(tw(attr))
    layout.addElement(PageLayoutProperties(**kwargs))
    std = None
    for child in doc.masterstyles.childNodes:
        if (getattr(child, "qname", ("", ""))[1] == "master-page"
                and child.getAttribute("name") == "Standard"):
            std = child
            break
    if std is None:
        doc.masterstyles.addElement(
            MasterPage(name="Standard", pagelayoutname="WO_PageLayout"))
    else:
        std.setAttribute("pagelayoutname", "WO_PageLayout")


def _master_page_section_to_html(section, resolve, pictures, changes=None) -> str:
    """Convert a master page header/footer section to HTML."""
    html_parts = []
    for child in section.childNodes:
        if child.qname == (TEXTNS, "p"):
            inner = _paragraph_inner_html(child, resolve, pictures, changes)
            html_parts.append(f"<p>{inner}</p>")
    return "\n".join(html_parts)


def _paragraph_to_html(el, resolve, pictures, presolve=None, changes=None) -> str:
    """Render a text:p or text:h element as a block-level HTML tag."""
    inner = _paragraph_inner_html(el, resolve, pictures, changes)
    pprops = presolve(el.getAttribute("stylename")) if presolve else None
    # Horizontal rule: an EMPTY paragraph whose style has only a bottom
    # border renders as <hr/> (mirror of the DOCX converter's heuristic).
    if (
        el.qname == (TEXTNS, "p")
        and pprops
        and pprops.get("borderbottom")
        and not (pprops.get("bordertop") or pprops.get("borderleft")
                 or pprops.get("borderright"))
        and not inner.strip()
    ):
        return "<hr/>"
    # Page break: an EMPTY paragraph with fo:break-before="page" renders as
    # the same marker the DOCX converter uses (<div class="page-break">).
    if (
        el.qname == (TEXTNS, "p")
        and pprops
        and (pprops.get("breakbefore") or "").lower() == "page"
        and not inner.strip()
    ):
        return '<div class="page-break"><br></div>'
    attrs = ""
    if pprops:
        css = _para_css(pprops)
        if css:
            attrs = ' style="' + ";".join(css) + '"'
    if el.qname == (TEXTNS, "h"):
        try:
            level = max(1, min(int(el.getAttribute("outlinelevel") or 1), 6))
        except ValueError:
            level = 1
        return f"<h{level}{attrs}>{inner}</h{level}>"
    return f"<p{attrs}>{inner}</p>"


def _paragraph_inner_html(el, resolve, pictures, changes=None) -> str:
    """Render paragraph/heading inline content (no <p>/<h> wrapper)."""
    style = resolve(el.getAttribute("stylename"))
    return _inline_html(el, resolve, style, pictures, changes)


def _collect_region_text(block) -> str:
    """Plain text of a text:insertion / text:deletion region block.

    Only the region's own text:p paragraphs count — teletype.extractText on
    the whole block would also swallow the office:change-info/dc:creator
    author text."""
    from odf import teletype
    parts: list[str] = []
    for sub in block.childNodes:
        if sub.nodeType != Node.ELEMENT_NODE:
            continue
        if sub.qname == (TEXTNS, "p"):
            parts.append(teletype.extractText(sub))
    return "".join(parts).strip()


def _collect_changes(doc) -> dict:
    """Parse the change registry into ``{change_id: {"author":.., "deleted":..}}``.

    Two registry shapes are understood:
      * ODF 1.2 ``text:tracked-changes`` with ``text:changed-region``
        (``xml:id``) holding ``text:insertion``/``text:deletion`` whose
        ``office:change-info`` carries ``dc:creator`` (what the writer emits,
        what LibreOffice writes);
      * the older ``office:changes`` form (text:change + change-id +
        office:change-info). Body change marks are resolved against this
        map; ids not present are ignored by the reader.
    """
    changes: dict = {}
    root = doc.text
    for child in root.childNodes:
        if child.nodeType != Node.ELEMENT_NODE:
            continue
        if child.qname == (TEXTNS, "tracked-changes"):
            for region in child.childNodes:
                if region.nodeType != Node.ELEMENT_NODE:
                    continue
                if region.qname != (TEXTNS, "changed-region"):
                    continue
                cid = region.attributes.get((XMLNS, "id"))
                if not cid:
                    cid = region.attributes.get((TEXTNS, "id"))
                if not cid:
                    continue
                author = ""
                deleted = ""
                for block in region.childNodes:
                    if block.nodeType != Node.ELEMENT_NODE:
                        continue
                    if block.qname == (TEXTNS, "insertion"):
                        author = _region_author(block)
                    elif block.qname == (TEXTNS, "deletion"):
                        author = _region_author(block)
                        deleted = _collect_region_text(block)
                changes[cid] = {"author": author, "deleted": deleted}
        elif child.qname == (OFFICENS, "changes"):
            # older office:changes form (foreign files)
            for c_el in child.childNodes:
                if c_el.nodeType != Node.ELEMENT_NODE:
                    continue
                if c_el.qname != (TEXTNS, "change"):
                    continue
                cid = c_el.getAttribute("changeid")
                if not cid:
                    continue
                author = ""
                deleted = ""
                for info in c_el.childNodes:
                    if info.nodeType != Node.ELEMENT_NODE:
                        continue
                    if info.qname != (OFFICENS, "change-info"):
                        continue
                    for cr in info.childNodes:
                        if cr.nodeType != Node.ELEMENT_NODE:
                            continue
                        if cr.qname == (DCNS, "creator"):
                            from odf import teletype
                            author = teletype.extractText(cr)
                        elif cr.qname == (DCNS, "description"):
                            from odf import teletype
                            deleted = teletype.extractText(cr)
                changes[cid] = {"author": author, "deleted": deleted}
    return changes


def _region_author(block) -> str:
    """dc:creator from the office:change-info child of a change block."""
    from odf import teletype
    for sub in block.childNodes:
        if sub.nodeType != Node.ELEMENT_NODE:
            continue
        if sub.qname == (OFFICENS, "change-info"):
            for cr in sub.childNodes:
                if (cr.nodeType == Node.ELEMENT_NODE
                        and cr.qname == (DCNS, "creator")):
                    return teletype.extractText(cr)
    return ""


def _annotation_meta(ann_el) -> tuple[str | None, str]:
    """(author, body) of an ``office:annotation``, or (None, '') when it
    has no ``dc:creator`` (such annotations are ignored by the reader)."""
    from odf import teletype

    author = None
    paras: list[str] = []
    for c in ann_el.childNodes:
        if c.nodeType != Node.ELEMENT_NODE:
            continue
        if c.qname == (DCNS, "creator"):
            author = teletype.extractText(c)
        elif c.qname == (TEXTNS, "p"):
            paras.append(teletype.extractText(c))
    if author is None:
        return None, ""
    return author, "\n".join(paras)


def _inline_html(el, resolve, base, pictures, changes=None) -> str:
    """Render the inline (run-level) content of an element to HTML.

    ``base`` is the effective character-flag dict inherited from the
    paragraph style; character styles on <span> override per property.
    ``pictures`` maps ODT package paths to (mime, bytes) for draw:image
    lookups. An ``office:annotation`` child anchors the runs accumulated
    before it into ``<span class="comment" data-author=.. data-comment=..>``
    (one annotation per paragraph is supported; multiple are best-effort).
    """
    out: list[str] = []
    pending: list[str] = []
    bm_name: str | None = None
    bm_start_idx: int | None = None
    in_change: str | None = None   # open text:change-id region

    def flush_comment(author: str, body: str) -> None:
        """Wrap the runs accumulated before an annotation in a comment span."""
        out.append(
            f'<span class="comment" data-author="{escape(author)}" '
            f'data-comment="{escape(body)}">{"".join(pending)}</span>'
        )
        pending.clear()

    def flush_change() -> None:
        """Close an open tracked-change region.

        Runs accumulated between text:change-start and text:change-end become
        an <ins> (tracked insertion). An EMPTY region whose registry entry
        carries deleted text is a tracked deletion and is re-emitted as <del>
        with the removed text recovered from the registry. Authors come from
        dc:creator; ids not registered never open a region."""
        nonlocal in_change
        if in_change is None:
            return
        cid = in_change
        in_change = None
        if pending:
            author = ((changes or {}).get(cid) or {}).get("author", "")
            out.append(
                f'<ins class="track-insert" data-author="{escape(author)}">'
                + "".join(pending)
                + "</ins>"
            )
            pending.clear()
            return
        meta = (changes or {}).get(cid)
        if meta and meta.get("deleted"):
            out.append(
                f'<del class="track-delete" data-author="{escape(meta.get("author") or "")}">'
                + escape(meta["deleted"])
                + "</del>"
            )

    for child in el.childNodes:
        if child.nodeType == Node.TEXT_NODE:
            pending.append(_wrap(escape(child.data), base))
            continue
        qname = child.qname
        if qname == (TEXTNS, "change-start"):
            cid = child.getAttribute("changeid")
            if cid and (changes or {}).get(cid) is not None:
                flush_change()                # close a malformed previous region
                out.append("".join(pending))  # pre-region content stays outside
                pending.clear()
                in_change = cid
            continue
        if qname == (TEXTNS, "change-end"):
            flush_change()
            continue
        if qname == (TEXTNS, "deletion"):
            # LibreOffice-style inline deletion (region content between marks)
            from odf import teletype
            deleted = teletype.extractText(child).strip()
            author = ""
            if in_change:
                author = ((changes or {}).get(in_change) or {}).get("author", "")
            elif changes:
                author = next(iter(changes.values())).get("author", "")
            if deleted:
                out.append(
                    f'<del class="track-delete" data-author="{escape(author)}">'
                    + escape(deleted)
                    + "</del>"
                )
            continue
        if qname == (OFFICENS, "annotation"):
            author, body = _annotation_meta(child)
            if author is not None:
                flush_comment(author, body)
            # annotations without dc:creator are ignored entirely (their text
            # must not leak into the body)
            continue
        if qname == (TEXTNS, "s"):  # repeated space
            try:
                count = max(1, int(child.getAttribute("c") or 1))
            except ValueError:
                count = 1
            pending.append("&nbsp;" * count)
        elif qname == (TEXTNS, "tab"):
            pending.append("&emsp;")
        elif qname == (TEXTNS, "line-break"):
            pending.append("<br/>")
        elif qname == (TEXTNS, "page-number"):
            # Page number field - map to HTML span
            pending.append('<span class="page-number"></span>')
        elif qname == (TEXTNS, "bookmark-ref"):
            refname = child.getAttribute("refname") or ""
            inner = _inline_html(child, resolve, base, pictures, changes)
            pending.append(f'<a href="#{escape(refname)}">{inner}</a>')
        elif qname in ((TEXTNS, "bookmark-start"), (TEXTNS, "reference-mark-start")):
            # LibreOffice targets cross-references at text:reference-mark*
            # elements, not only text:bookmark* — both families are anchors.
            bm_name = child.getAttribute("name") or ""
            bm_start_idx = len(pending)
            continue
        elif qname in ((TEXTNS, "bookmark-end"), (TEXTNS, "reference-mark-end")):
            if bm_start_idx is not None:
                content = "".join(pending[bm_start_idx:])
                del pending[bm_start_idx:]
                pending.append(
                    f'<span class="bookmark" data-name="{escape(bm_name)}">{content}</span>'
                )
                bm_start_idx = None
                bm_name = None
            continue
        elif qname in ((TEXTNS, "bookmark"), (TEXTNS, "reference-mark")):
            bname = child.getAttribute("name") or ""
            inner = _inline_html(child, resolve, base, pictures, changes)
            pending.append(f'<span class="bookmark" data-name="{escape(bname)}">{inner}</span>')
        elif qname == (TEXTNS, "span"):
            flags = dict(base)
            span_flags = resolve(child.getAttribute("stylename"))
            for key in ("bold", "italic", "underline", "strike", "vert",
                        "small_caps", "all_caps", "color", "bg",
                        "font_family", "font_size"):
                if span_flags[key] is not None:
                    flags[key] = span_flags[key]
            pending.append(_inline_html(child, resolve, flags, pictures, changes))
        elif qname == (TEXTNS, "a"):
            href = child.getAttribute("href")
            inner = _inline_html(child, resolve, base, pictures, changes)
            if href:
                inner = f'<a href="{escape(href)}">{inner}</a>'
            pending.append(inner)
        elif qname == (DRAWNS, "frame"):
            obj = _odt_frame_object(child, pictures)
            pending.append(obj if obj is not None else _frame_to_html(child, pictures))
        elif qname == (TEXTNS, "note"):
            note_html = _note_to_html(child, resolve, pictures)
            if note_html:
                pending.append(note_html)
        else:
            # Unknown inline node (drawing text-box…):
            # descend so its text is not silently dropped.
            pending.append(_inline_html(child, resolve, base, pictures, changes))
    out.append("".join(pending))
    return "".join(out)


def _note_to_html(note_el, resolve, pictures, changes=None) -> str:
    """Render a ``text:note`` (footnote or endnote) as the HTML contract
    ``<sup class="{cls}-citation">[N]</sup><span class="{cls}">BODY</span>``.

    Notes whose ``text:note-class`` is neither ``footnote`` nor ``endnote``
    are ignored (return ``''``) so their text never leaks into the body.
    ``BODY`` is the note-body's paragraphs joined with ``<br/>``.
    """
    noteclass = note_el.getAttribute("noteclass")
    if noteclass not in ("footnote", "endnote"):
        return ""
    citation = ""
    body_html = ""
    for subchild in note_el.childNodes:
        if subchild.nodeType != Node.ELEMENT_NODE:
            continue
        if subchild.qname == (TEXTNS, "note-citation"):
            citation = "".join(
                c.data for c in subchild.childNodes if c.nodeType == Node.TEXT_NODE
            )
        elif subchild.qname == (TEXTNS, "note-body"):
            paras: list[str] = []
            for p_el in subchild.childNodes:
                if p_el.nodeType != Node.ELEMENT_NODE:
                    continue
                if p_el.qname in ((TEXTNS, "p"), (TEXTNS, "h")):
                    html = _paragraph_inner_html(p_el, resolve, pictures, changes)
                    if html:
                        paras.append(html)
                else:  # unknown body child: descend so text is not dropped
                    html = _inline_html(p_el, resolve, {}, pictures, changes)
                    if html:
                        paras.append(html)
            body_html = "<br/>".join(paras)
    if not citation and not body_html:
        return ""
    cls = "footnote" if noteclass == "footnote" else "endnote"
    return (
        f'<sup class="{cls}-citation">[{citation}]</sup>'
        f'<span class="{cls}">{body_html}</span>'
    )


def _wrap(text: str, flags: dict) -> str:
    """Apply character formatting flags around already-escaped text.

    Mirrors the DOCX converter's ``_wrap_run_text`` so both formats emit
    the same HTML contract: ``<sup>/<sub>/<strike>/<code>`` + one
    ``<span style=...>`` holding font-family/size, small-caps, all-caps,
    colour and highlight.
    """
    if flags.get("vert") == "sup":
        text = f"<sup>{text}</sup>"
    elif flags.get("vert") == "sub":
        text = f"<sub>{text}</sub>"
    if flags.get("strike"):
        text = f"<strike>{text}</strike>"
    fam = flags.get("font_family")
    if fam and fam.lower().strip() in _MONO_FONTS:
        text = f"<code>{text}</code>"
    styles: list[str] = []
    if fam and fam.lower().strip() not in _MONO_FONTS:
        styles.append(f"font-family:{fam}")
    if flags.get("font_size"):
        styles.append(f"font-size:{flags['font_size']}")
    if flags.get("small_caps"):
        styles.append("font-variant:small-caps")
    if flags.get("all_caps"):
        styles.append("text-transform:uppercase")
    if flags.get("color"):
        styles.append(f"color:{flags['color']}")
    if flags.get("bg"):
        styles.append(f"background-color:{flags['bg']}")
    if styles:
        text = f'<span style="{"; ".join(styles)}">{text}</span>'
    for key, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
        if flags.get(key):
            text = f"<{tag}>{text}</{tag}>"
    return text


def _list_items_to_html(list_el, resolve, kinds, pictures) -> list[str]:
    items: list[str] = []
    for child in list_el.childNodes:
        if child.qname == (TEXTNS, "list-item"):
            body: list[str] = []
            for c in child.childNodes:
                if c.nodeType != Node.ELEMENT_NODE:
                    continue
                if c.qname == (TEXTNS, "p"):
                    body.append(_paragraph_inner_html(c, resolve, pictures))
                elif c.qname == (TEXTNS, "h"):
                    body.append(_paragraph_inner_html(c, resolve, pictures))
                elif c.qname == (TEXTNS, "list"):
                    # nested list: its own <ul>/<ol> inside this <li>
                    nested = odt_list_to_html(c, resolve, kinds, pictures)
                    body.append(nested)
            items.append("<li>" + "".join(body) + "</li>")
    return items


def _int_attr(el, name: str) -> int | None:
    """Read an integer attribute by its odfpy camelCase name.

    ``number-columns-spanned`` etc. are read as ``numbercolumnsspanned``.
    Returns None when absent or not parseable as an integer. Reads are
    tolerant: an attribute not allowed on the element type (e.g. a covered
    cell carries no span attrs) returns None instead of raising.
    """
    try:
        val = el.getAttribute(name)
    except (ValueError, AttributeError):
        return None
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _table_rows(table_el):
    """Yield the ``table:table-row`` elements of a table.

    LibreOffice wraps header rows in ``table:table-header-rows`` and may use
    ``table:table-rows`` group elements; those wrappers are descended into so
    every real row is visited exactly once.
    """
    for child in table_el.childNodes:
        if child.nodeType != Node.ELEMENT_NODE:
            continue
        qname = child.qname
        if qname == (TABLENS, "table-row"):
            yield child
        elif qname in ((TABLENS, "table-header-rows"), (TABLENS, "table-rows")):
            yield from _table_rows(child)


def _cell_to_html(cell, resolve, pictures, presolve=None, cellresolve=None,
                  colws=None, colpos=None) -> str:
    """Render one table cell as a ``<td>`` (``''`` for covered cells).

    A ``covered-table-cell`` is the ODF placeholder for a slot already taken by
    a colspan/rowspan on an earlier cell, so it renders as nothing — the span
    attribute of the covering cell accounts for that column.

    ``table:number-columns-spanned`` / ``table:number-rows-spanned`` become
    ``colspan`` / ``rowspan`` attributes so merges survive into the editor.
    Nested tables inside a cell render as a nested ``<table>``. Cell shading
    (``fo:background-color``) and explicit borders (``fo:border``) become a
    ``style`` attribute; the cell width is taken from its table column.
    """
    if cell.qname == (TABLENS, "covered-table-cell"):
        return ""
    chunks: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            chunks.append("<br/>".join(pending))
            pending.clear()

    for c in cell.childNodes:
        if c.nodeType != Node.ELEMENT_NODE:
            continue
        if c.qname == (TEXTNS, "p"):
            pending.append(_paragraph_to_html(c, resolve, pictures, presolve))
        elif c.qname == (TABLENS, "table"):
            flush()
            chunks.append(_table_to_html(c, resolve, pictures, presolve, cellresolve))
    flush()
    attrs = []
    colspan = _int_attr(cell, "numbercolumnsspanned") or 1
    rowspan = _int_attr(cell, "numberrowsspanned") or 1
    if colspan > 1:
        attrs.append(f'colspan="{colspan}"')
    if rowspan > 1:
        attrs.append(f'rowspan="{rowspan}"')
    cprops = (cellresolve or {}).get(cell.getAttribute("stylename")) if cellresolve else None
    if cprops:
        style_bits: list[str] = []
        bg = cprops.get("backgroundcolor")
        if bg:
            style_bits.append("background-color:" + bg)
        bord = _norm_border(cprops.get("border"))
        if bord:
            style_bits.append("border:" + bord)
        if style_bits:
            attrs.append('style="' + ";".join(style_bits) + '"')
    if colws and colpos is not None and colpos < len(colws) and colws[colpos]:
        attrs.append(f'width="{colws[colpos]}"')
    open_tag = "<td " + " ".join(attrs) + ">" if attrs else "<td>"
    return open_tag + "".join(chunks) + "</td>"


def _build_cell_resolver(doc):
    """Map table-cell / table / table-column style names to their properties.

    ``backgroundcolor``/``border`` come from ``style:table-cell-properties``,
    ``twidth`` from ``style:table-properties`` and ``cwidth`` from
    ``style:table-column-properties`` — matching the HTML contract used by
    the writer (cell style + column widths + table width).
    """
    out: dict[str, dict] = {}
    for root in (doc.styles, doc.automaticstyles):
        for el in root.childNodes:
            if el.qname != (STYLENS, "style"):
                continue
            fam = el.getAttribute("family")
            if fam not in ("table-cell", "table", "table-column"):
                continue
            props: dict = {}
            for child in el.childNodes:
                if child.qname == (STYLENS, "table-cell-properties"):
                    props["backgroundcolor"] = _raw_attr(child, "background-color", "backgroundcolor")
                    props["border"] = _raw_attr(child, "border")
                elif child.qname == (STYLENS, "table-properties"):
                    props["twidth"] = _raw_attr(child, "width")
                elif child.qname == (STYLENS, "table-column-properties"):
                    props["cwidth"] = _raw_attr(child, "column-width", "columnwidth")
            if props:
                out[el.getAttribute("name")] = props
    return out


def _px_of(value) -> int | None:
    """Convert an ODF/SVG length ('96px', '2.5cm', '18pt') to integer px."""
    m = re.match(r"^\s*([\d.]+)\s*(px|pt|cm|mm|in)?\s*$", value or "")
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "px").lower()
    if unit == "pt":
        num *= 4 / 3
    elif unit == "cm":
        num *= 96 / 2.54
    elif unit == "mm":
        num *= 96 / 25.4
    elif unit == "in":
        num *= 96
    return round(num)


def _norm_border(value) -> str | None:
    """Normalise an ODF border to 'Npt solid #hex', or None."""
    m = re.search(
        r"([\d.]+)\s*(cm|mm|in|pt|px)?\s*solid\s*(#[0-9a-fA-F]{3,8})",
        value or "",
    )
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "pt").lower()
    if unit == "cm":
        num *= 28.3465
    elif unit == "mm":
        num *= 2.83465
    elif unit == "in":
        num *= 72
    elif unit == "px":
        num *= 0.75
    return f"{round(num, 2):g}pt solid {m.group(3).lower()}"


def _table_to_html(table_el, resolve, pictures, presolve=None, cellresolve=None, caption=None) -> str:
    rows: list[str] = []
    cellresolve = cellresolve or {}
    tprops = cellresolve.get(table_el.getAttribute("stylename")) or {}
    tw = _px_of(tprops.get("twidth"))
    # Column widths (LibreOffice keeps cell widths on table:table-column).
    colws: list[int] = []
    for col in table_el.childNodes:
        if col.nodeType != Node.ELEMENT_NODE:
            continue
        if col.qname == (TABLENS, "table-column"):
            cw = _px_of(cellresolve.get(col.getAttribute("stylename") or "").get("cwidth")) \
                if cellresolve.get(col.getAttribute("stylename") or "") else None
            repeat = _int_attr(col, "numbercolumnsrepeated") or 1
            colws.extend([cw] * max(1, repeat))
        elif col.qname == (TABLENS, "table-column-group"):
            for sub in col.childNodes:
                if sub.nodeType == Node.ELEMENT_NODE and sub.qname == (TABLENS, "table-column"):
                    props = cellresolve.get(sub.getAttribute("stylename") or "")
                    cw = _px_of(props.get("cwidth")) if props else None
                    repeat = _int_attr(sub, "numbercolumnsrepeated") or 1
                    colws.extend([cw] * max(1, repeat))
    for row in _table_rows(table_el):
        cells: list[str] = []
        colpos = 0
        for cell in row.childNodes:
            if cell.nodeType != Node.ELEMENT_NODE:
                continue
            if cell.qname not in ((TABLENS, "table-cell"),
                                  (TABLENS, "covered-table-cell")):
                continue
            cell_html = _cell_to_html(cell, resolve, pictures, presolve,
                                      cellresolve, colws, colpos)
            colspan = _int_attr(cell, "numbercolumnsspanned") or 1
            if colspan < 1:
                colspan = 1
            colpos += colspan
            if not cell_html:
                continue  # covered cell: slot taken by a span
            repeat = _int_attr(cell, "numbercolumnsrepeated") or 1
            if repeat > 1:
                cells.extend([cell_html] * repeat)
            else:
                cells.append(cell_html)
        # A row whose cells are all covered-table-cell placeholders (fully
        # consumed by a rowspan from above) still renders as an empty <tr> so
        # the grid keeps its row count.
        row_html = "<tr>" + "".join(cells) + "</tr>"
        n_repeat = _int_attr(row, "numberrowsrepeated") or 1
        rows.extend([row_html] * max(1, n_repeat))
    head = "<table>"
    if tw:
        head = f'<table width="{tw}">'
    table_html = head + "".join(rows) + "</table>"
    if caption:
        return f"<figure>{table_html}<figcaption>{escape(caption)}</figcaption></figure>"
    return table_html


def odt_list_to_html(list_el, resolve, kinds, pictures) -> str:
    """Render a (possibly nested) text:list element as a <ul>/<ol> block."""
    kind = kinds.get(list_el.getAttribute("stylename"), "ul")
    items = []
    for child in list_el.childNodes:
        if child.qname == (TEXTNS, "list-item"):
            body = []
            for c in child.childNodes:
                if c.nodeType != Node.ELEMENT_NODE:
                    continue
                if c.qname in ((TEXTNS, "p"), (TEXTNS, "h")):
                    body.append(_paragraph_inner_html(c, resolve, pictures))
                elif c.qname == (TEXTNS, "list"):
                    body.append(odt_list_to_html(c, resolve, kinds, pictures))
            items.append("<li>" + "".join(body) + "</li>")
    return f"<{kind}>" + "".join(items) + f"</{kind}>"


# --------------------------------------------------------------------------
# HTML -> ODT
# --------------------------------------------------------------------------

_TAG_TABLE = re.compile(r"<figure>.*?</figure>|<table[^>]*>.*?</table>", re.S)


def html_to_odt(html_fragment: str) -> bytes:
    """Convert an HTML fragment into ODT bytes."""
    # A <section data-columns> wrapper carries section-column layout
    # (mapped to a text:section with text:columns); unwrap it before body
    # processing and re-apply it around the whole body on output.
    section_cols = None
    sec_m = re.match(r"<section([^>]*)>(.*)</section>", html_fragment, re.S | re.I)
    if sec_m:
        sattrs = sec_m.group(1)
        cm = re.search(r'data-columns\s*=\s*"?(\d+)', sattrs)
        if cm:
            section_cols = int(cm.group(1))
        html_fragment = sec_m.group(2)
    # Tables split out the same way the DOCX converter does it: python-odf
    # tables and paragraphs share the body, but interleaving complicates
    # the body, so tables are appended at the end.
    tables_html = _TAG_TABLE.findall(html_fragment)
    body = _TAG_TABLE.sub("", html_fragment)

    doc = OpenDocumentText()
    w = _OdtWriter(doc)

    # The page-setup marker (document page geometry, F-090/F-091/F-092)
    # rides at body start; apply it to the page layout and strip it from
    # the body before header/footer extraction. Applied before the header
    # so add_header reuses the same WO_PageLayout instead of a fresh one.
    ps_m = _PAGE_SETUP_RE.match(body)
    if ps_m:
        _apply_page_setup_marker_odt(doc, ps_m.group(1))
        body = body[ps_m.end():]

    # Extract header and footer if present
    header_content = None
    footer_content = None

    # Extract header from the beginning
    header_match = re.match(r'<header([^>]*)>(.*?)</header>', body, re.S | re.I)
    if header_match:
        header_content = header_match.group(2)
        body = body[header_match.end():]

    # Extract footer from the end (search in remaining body)
    body = body.strip()
    footer_match = re.search(r'<footer([^>]*)>(.*?)</footer>', body, re.S | re.I)
    if footer_match:
        footer_content = footer_match.group(2)
        body = body[:footer_match.start()].strip()

    ops = _tokenize_body(body)

    # Process header if present - add to master page
    if header_content:
        w.add_header(header_content)

    # Process footer if present - add to master page
    if footer_content:
        w.add_footer(footer_content)

    for op in ops:
        kind = op[0]
        if kind == "hr":
            w.add_hr()
            continue
        if kind == "pagebreak":
            w.add_page_break()
            continue
        if kind == "toc":
            w.add_table_of_contents(op[1])
            continue
        if kind == "sectionbreak":
            w.add_section_break()
            continue
        if kind == "object":
            w.add_object(op[1], op[2], op[3])
            continue
        if kind == "list":
            w.add_list(op[1])
            continue
        if kind == "blockquote":
            # Chrome's execCommand indent -> left-indented paragraph (parity
            # with the DOCX converter).
            w.add_paragraph(op[2], level=0, props={"margin-left": 24.0})
            continue
        if kind == "h":
            w.add_paragraph(op[3], level=op[1], props=_para_props(op[2]))
            continue
        if kind == "p":
            w.add_paragraph(op[2], level=0, props=_para_props(op[1]))
        # 'hr' and 'pagebreak' are intentionally dropped: the ODT writer's
        # mission is the same as before (no rule/page-break mapping).

    # Tag-less input (raw text typed into an empty contenteditable).
    if body.strip() and not any(
        op[0] in ("list", "p", "h", "blockquote") for op in ops
    ):
        w.add_paragraph(body, level=0, props=None)

    for tbl_html in tables_html:
        w.add_table(tbl_html)

    w.emit_changes()

    if section_cols and section_cols > 1:
        from odf.text import Section
        sec = Section(name="WOSection")
        sec.attributes[(TEXTNS, "columns")] = str(section_cols)
        tracked = [
            ch for ch in doc.text.childNodes
            if ch.qname == (TEXTNS, "tracked-changes")
        ]
        content = [
            ch for ch in doc.text.childNodes
            if ch.qname != (TEXTNS, "tracked-changes")
        ]
        while doc.text.childNodes:
            doc.text.removeChild(doc.text.childNodes[0])
        for ch in content:
            sec.addElement(ch)
        doc.text.addElement(sec)
        for tc in tracked:
            doc.text.insertBefore(tc, sec)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _para_props(open_tag_and_attrs: str) -> dict:
    """Parse paragraph-level CSS props from a block open tag.

    Mirrors the DOCX converter's ``_parse_para_props``: recognized keys are
    text-align, line-height (multiple), margin-left/right, margin-top/
    bottom, text-indent (point floats) and direction / page-break-before.
    """
    props: dict = {}
    m = re.search(r'style="([^"]*)"', open_tag_and_attrs)
    style = m.group(1) if m else ""
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if prop == "line-height":
            lm = re.match(r"^(\d+(?:\.\d+)?)$", val)
            if lm:
                v = float(lm.group(1))
                if v > 0 and abs(v - 1.0) > 1e-6:
                    props["line-height"] = v
        elif prop in ("margin-left", "margin-right", "margin-top",
                      "margin-bottom", "text-indent"):
            v = _odt_len_to_pt(val)
            if v:
                props[prop] = v
        elif prop == "direction" and val.lower() == "rtl":
            props["direction"] = "rtl"
        elif prop == "page-break-before" and val.lower() in ("always", "page"):
            props["page-break-before"] = True
        elif prop == "text-align" and val in ("center", "right"):
            props["text-align"] = val
    if 'dir="rtl"' in open_tag_and_attrs:
        props["direction"] = "rtl"
    return props


class _OdtWriter:
    """Accumulates HTML blocks into an OpenDocumentText with shared styles."""

    def __init__(self, doc: OpenDocumentText) -> None:
        self.doc = doc
        self.cur = self.doc.text  # current content parent (switched on section breaks)
        self._sec_count = 0
        self._char_styles: dict[tuple[bool, bool, bool], str] = {}
        self._para_styles: dict[str, str] = {}
        self._ol_style: str | None = None
        self._hr_style: str | None = None
        self._pb_style: str | None = None
        self._tc_styles: dict[tuple, str] = {}
        self._col_styles: dict[int, str] = {}
        self._tbl_styles: dict[float, str] = {}
        self._img_n = 0
        self._note_n = 0
        self._change_n = 0
        self._changes: list[tuple[str, str, str, str]] = []  # (change_id, author, region_text, kind)

    # -- character styles -------------------------------------------------
    def char_style(self, token: dict) -> str | None:
        """Return (creating if needed) an ODF character style for a token.

        The style is keyed by the full formatting tuple (bold/italic/
        underline/strike/vert/caps/colour/background/font), so identical
        formatting reuses one style element — the same dedup the old
        b/i/u-only version did, just with more dimensions.
        """
        fam = token.get("font_family")
        if token.get("code"):
            fam = "Consolas"
        key = (
            bool(token.get("bold")), bool(token.get("italic")), bool(token.get("underline")),
            bool(token.get("strike")), bool(token.get("small_caps")), bool(token.get("all_caps")),
            token.get("vert"), token.get("color"), token.get("bg"), fam, token.get("font_size"),
        )
        if key in self._char_styles:
            return self._char_styles[key]
        has_any = any((
            key[0], key[1], key[2], key[3], key[4], key[5], key[6], key[7],
            key[8], key[9], key[10],
        ))
        if not has_any:
            return None
        # Preserve the historic WO_{b}{i}{u} name (1=on/0=off) for the
        # b/i/u-only styles so pre-existing consumers/tests keep working;
        # extended formatting gets a numeric suffix.
        base = f"WO_{int(bool(token.get('bold')))}{int(bool(token.get('italic')))}{int(bool(token.get('underline')))}"
        extended = any((key[3], key[4], key[5], key[6], key[7], key[8], key[9], key[10]))
        name = f"{base}_{len(self._char_styles)}" if extended else base
        props: list[tuple[str, str]] = []
        if token.get("bold"):
            props.append(("fontweight", "bold"))
        if token.get("italic"):
            props.append(("fontstyle", "italic"))
        if token.get("underline"):
            props.append(("textunderlinestyle", "solid"))
        if token.get("strike"):
            props.append(("textlinethroughstyle", "solid"))
        if token.get("small_caps"):
            props.append(("fontvariant", "small-caps"))
        if token.get("all_caps"):
            props.append(("texttransform", "uppercase"))
        vert = token.get("vert")
        if vert == "sup":
            props.append(("textposition", "super"))
        elif vert == "sub":
            props.append(("textposition", "sub"))
        if token.get("color"):
            props.append(("color", token["color"]))
        if token.get("bg"):
            props.append(("backgroundcolor", token["bg"]))
        if fam:
            props.append(("fontfamily", fam))
        if token.get("font_size"):
            props.append(("fontsize", token["font_size"]))
        style = Style(name=name, family="text")
        style.addElement(TextProperties(**dict(props)))
        self.doc.automaticstyles.addElement(style)
        self._char_styles[key] = name
        return name

    def para_style(self, props: dict) -> str | None:
        """Return (creating if needed) an ODF paragraph style for props.

        ``props`` uses the ``_para_props`` key set (text-align, line-height
        multiple, margin-*, text-indent, direction, page-break-before).
        A lone alignment keeps the historic ``WO_Center``/``WO_Right``
        name; anything richer gets a numeric suffix.
        """
        if not props:
            return None
        key = tuple(sorted(props.items()))
        if key in self._para_styles:
            return self._para_styles[key]
        align = props.get("text-align")
        name = (
            "WO_" + align.capitalize()
            if align and len(props) == 1
            else f"WO_A{len(self._para_styles)}"
        )
        pp: dict = {}
        if align:
            pp["textalign"] = align
        if props.get("line-height"):
            pp["lineheight"] = f"{float(props['line-height']) * 100:g}%"
        for csskey, attr in (
            ("margin-left", "marginleft"), ("margin-right", "marginright"),
            ("margin-top", "margintop"), ("margin-bottom", "marginbottom"),
            ("text-indent", "textindent"),
        ):
            v = props.get(csskey)
            if v:
                pp[attr] = f"{v:g}pt"
        if props.get("direction") == "rtl":
            pp["writingmode"] = "rtl"
        if props.get("page-break-before"):
            pp["breakbefore"] = "page"
        style = Style(name=name, family="paragraph")
        style.addElement(ParagraphProperties(**pp))
        self.doc.automaticstyles.addElement(style)
        self._para_styles[key] = name
        return name

    def add_paragraph(self, html: str, level: int = 0, props: dict | None = None) -> None:
        style_name = self.para_style(props) if props else None
        if level:
            el = H(outlinelevel=level)
            if style_name:
                el.setAttribute("stylename", style_name)
        else:
            el = P()
            if style_name:
                el.setAttribute("stylename", style_name)
        self._fill(el, html)
        self.cur.addElement(el)

    def _fill(self, el, html: str) -> None:
        """Add styled text runs, hyperlinks, images, notes, and anchored
        comments parsed from an inline HTML fragment."""
        for token in _inline_tokens(html):
            if token["type"] == "image":
                self.add_image(el, token)
                continue
            if token["type"] == "footnote":
                self._add_note(el, token)
                continue
            if token["type"] == "comment":
                self._add_comment(el, token)
                continue
            if token["type"] == "track":
                self._add_track_change(el, token)
                continue
            if token["type"] == "bookmark":
                name = token.get("name") or ""
                bm_start = BookmarkStart(name=name)
                bm_end = BookmarkEnd(name=name)
                el.addElement(bm_start)
                self._fill(el, token.get("html") or "")
                el.addElement(bm_end)
                continue
            text = token["text"]
            style_name = self.char_style(token)
            href = token.get("href")
            if href:
                if href.startswith("#"):
                    # in-document cross-reference -> text:bookmark-ref
                    # (OnlyOffice/libreoffice always carry text:reference-format)
                    ref = BookmarkRef(refname=href[1:], text=text, referenceformat="text")
                    el.addElement(ref)
                else:
                    # ODF hyperlinks are text:a elements carrying xlink:href.
                    a = A(href=href, type="simple")
                    if style_name:
                        a.addElement(Span(text=text, stylename=style_name))
                    else:
                        a.addText(text)
                    el.addElement(a)
            elif style_name:
                el.addElement(Span(text=text, stylename=style_name))
            else:
                el.addText(text)

    def add_image(self, para_el, token: dict) -> None:
        """Embed a data-URI <img> as a draw:frame/draw:image in the paragraph.

        Only ``data:`` URIs are embeddable server-side; http(s)/relative
        src values are skipped (no network fetching in a converter).
        Dimensions come from the <img> attributes or the image's intrinsic
        pixel size when it can be sniffed.
        """
        mime, content = _decode_data_uri(token.get("src") or "")
        if content is None:
            return
        width = token.get("width")
        height = token.get("height")
        if not width or not height:
            dims = _image_dimensions(mime, content)
            if dims:
                width = width or dims[0]
                height = height or dims[1]
        manifest = self.doc.addPictureFromString(content, mime)
        self._img_n += 1
        frame = Frame(name=f"WO_Picture_{self._img_n}", anchortype="as-char")
        if width:
            frame.setAttribute("width", f"{width}px")
        if height:
            frame.setAttribute("height", f"{height}px")
        # Accessible name / alt text lives in svg:title (ODF standard).
        alt = (token.get("alt") or "").strip()
        if alt:
            frame.addElement(Element(qname=(SVGNS, "title"), text=alt))
        frame.addElement(Image(href=manifest))
        para_el.addElement(frame)

    def _add_comment(self, para_el, token: dict) -> None:
        """Append an anchored comment to the current paragraph.

        The token's inner HTML becomes the anchored runs inside the SAME
        ``<text:p>``; right after those runs an ``<office:annotation>`` holds
        the ``dc:creator`` (author), a ``dc:date`` and the body in a
        ``<text:p>`` (ODF review notes convention). odfpy validates
        attributes against the element type, so the dc children are built
        with plain ``Element`` qnames and text attached via ``addText``.
        """
        self._fill(para_el, token.get("html") or "")
        ann = Annotation()
        creator = Element(qname=(DCNS, "creator"))
        creator.addText(token.get("author") or "")
        ann.addElement(creator)
        date_el = Element(qname=(DCNS, "date"))
        date_el.addText("2026-01-01T00:00:00")
        ann.addElement(date_el)
        body_p = P()
        body_p.addText(token.get("body") or "")
        ann.addElement(body_p)
        para_el.addElement(ann)

    def _add_track_change(self, para_el, token: dict) -> None:
        """Mark a tracked insertion/deletion on the current paragraph.

        Both kinds emit ``text:change-start``/``text:change-end`` marks with
        a registered change id. Inserted runs sit between the marks; the
        removed text of a deletion travels ONLY in the registry (odfpy
        refuses an inline ``text:deletion`` inside ``text:p``) and the
        reader re-emits it at the change-end mark (parity contract)."""
        self._change_n += 1
        cid = f"ct{self._change_n}"
        author = token.get("author") or ""
        inner = token.get("html") or ""
        from html import unescape as _unescape
        region_text = _unescape(_inline_to_text(inner))
        self._changes.append((cid, author, region_text, token.get("kind") or "insert"))
        para_el.addElement(ChangeStart(changeid=cid))
        if token.get("kind") == "insert":
            self._fill(para_el, inner)
        para_el.addElement(ChangeEnd(changeid=cid))

    def emit_changes(self) -> None:
        """Insert the ``text:tracked-changes`` registry at the top of body.

        One ``text:changed-region`` (xml:id) per change with a
        ``text:insertion``/``text:deletion`` block whose ``office:change-info``
        carries ``dc:creator`` — the ODF 1.2/LibreOffice-readable model the
        reader resolves the body marks against. (odfpy's ``text:change``
        allows no children, so the office:changes form is not buildable;
        this model is what LibreOffice itself writes.)"""
        if not self._changes:
            return
        tc = TrackedChanges()
        for cid, author, region_text, kind in self._changes:
            region = Element(
                qname=(TEXTNS, "changed-region"),
                qattributes={(XMLNS, "id"): cid},
            )
            change_block = Deletion() if kind == "delete" else Insertion()
            body_p = P()
            body_p.addText(region_text)
            change_block.insertBefore(body_p, None)
            info = Element(qname=(OFFICENS, "change-info"))
            creator = Element(qname=(DCNS, "creator"))
            creator.addText(author)
            info.insertBefore(creator, None)
            change_block.insertBefore(info, None)  # DOM bypass (odfpy schema)
            region.insertBefore(change_block, None)
            tc.addElement(region)
        if self.doc.text.childNodes:
            self.doc.text.insertBefore(tc, self.doc.text.childNodes[0])
        else:
            self.doc.text.addElement(tc)

    def _add_note(self, para_el, token: dict) -> None:
        """Append a footnote/endnote as a text:note element.

        ``token`` is the DOCX-parity ``type: "footnote"`` token with
        ``kind`` (footnote/endnote), ``citation`` (the bracketed number,
        e.g. ``[1]``) and ``body`` (the note body HTML).
        """
        kind = token.get("kind") or "footnote"
        self._note_n += 1
        note = Note(noteclass=kind, id=f"ftn{self._note_n}")
        citation = (token.get("citation") or "").strip().strip("[]")
        b = NoteBody()
        # Fill the note body with the paragraph machinery.
        self._fill_note_body(b, token.get("body") or "")
        note.addElement(NoteCitation(text=citation))
        note.addElement(b)
        para_el.addElement(note)

    def _fill_note_body(self, note_body_el, html: str) -> None:
        """Fill a text:note-body with paragraphs parsed from HTML.

        Body paragraphs use the same paragraph machinery as regular body
        text so formatting/links/images inside notes survive; ``<br/>``
        splits into separate <text:p> elements (mirrors the DOCX
        writer's note-body handling). Nested notes are not supported
        (notes cannot contain notes).
        """
        for frag in re.split(r"<br\s*/?>", html or ""):
            p = P()
            self._fill(p, frag)
            note_body_el.addElement(p)


    def _new_list(self, kind: str):
        list_el = List()
        if kind == "ol":
            if self._ol_style is None:
                self._ol_style = "WO_NumberedList"
                ls = ListStyle(name=self._ol_style)
                ls.addElement(ListLevelStyleNumber(level=1, numformat="1"))
                self.doc.automaticstyles.addElement(ls)
            list_el.setAttribute("stylename", self._ol_style)
        return list_el

    def _build_list(self, tree: dict):
        list_el = self._new_list(tree.get("kind") or "ul")
        for item in tree.get("items", []):
            li = ListItem()
            p = P()
            self._fill(p, item.get("frag", ""))
            li.addElement(p)
            for sub in item.get("sub", []):
                li.addElement(self._build_list(sub))
            list_el.addElement(li)
        return list_el

    def add_hr(self) -> None:
        """A horizontal rule: an empty paragraph with a bottom border."""
        if self._hr_style is None:
            self._hr_style = "WO_HrRule"
            style = Style(name=self._hr_style, family="paragraph")
            style.addElement(ParagraphProperties(borderbottom="0.6pt solid #555555"))
            self.doc.automaticstyles.addElement(style)
        el = P()
        el.setAttribute("stylename", self._hr_style)
        self.cur.addElement(el)

    def add_page_break(self) -> None:
        """A page break: an empty paragraph with fo:break-before="page"."""
        if self._pb_style is None:
            self._pb_style = "WO_PageBreak"
            style = Style(name=self._pb_style, family="paragraph")
            style.addElement(ParagraphProperties(breakbefore="page"))
            self.doc.automaticstyles.addElement(style)
        el = P()
        el.setAttribute("stylename", self._pb_style)
        self.cur.addElement(el)

    def add_table_of_contents(self, title: str = "") -> None:
        """A table of contents: an ODF text:table-of-content element.

        Round-trips as <nav class="toc" data-title="..."> (the actual
        entries are regenerated by the word processor).
        """
        from odf.text import TableOfContent
        toc = TableOfContent(name=title or "Table of Contents")
        self.cur.addElement(toc)

    def add_section_break(self) -> None:
        """A section break: start a new text:section that receives all
        subsequent content (ODF models a section break as a nested
        text:section). Round-trips as <hr class="section-break">."""
        self._sec_count += 1
        from odf.text import Section
        sec = Section(name=f"WOSection{self._sec_count}")
        self.doc.text.addElement(sec)
        self.cur = sec

    def add_object(self, typ: str, label: str, content: str) -> None:
        """Emit a draw:frame holding an embedded-object placeholder.

        The object type is stored in draw:frame/@draw:name ("object:TYPE")
        and any text content in a draw:text-box, so odt_to_html recovers a
        <div class="object" data-type="..."> marker.
        """
        from odf.draw import TextBox
        from odf.text import P
        frame = Frame(name=f"object-{typ}")
        frame.setAttribute("width", "5cm")
        frame.setAttribute("height", "2cm")
        tb = TextBox()
        para = P()
        if content:
            para.addText(content)
        tb.addElement(para)
        frame.addElement(tb)
        self.cur.addElement(frame)

    def add_header(self, content_html: str) -> None:
        """Add a header to the document's master page.

        Creates a master page style with style:header containing the content.
        """
        from odf.style import Header, MasterPage, PageLayout
        from odf.text import P

        # Create a page layout if we don't have one
        if not any(el.getAttribute("name") == "WO_PageLayout"
                   for el in self.doc.automaticstyles.childNodes
                   if el.qname == (STYLENS, "page-layout")):
            pageLayout = PageLayout(name="WO_PageLayout")
            self.doc.automaticstyles.addElement(pageLayout)

        # Create or update master page with header
        master_name = "WO_Master"
        master = None
        for el in self.doc.masterstyles.childNodes:
            if el.qname == (STYLENS, "master-page") and el.getAttribute("name") == master_name:
                master = el
                break

        if master is None:
            master = MasterPage(name=master_name, pagelayoutname="WO_PageLayout")
            self.doc.masterstyles.addElement(master)

        # Remove existing header if any and add new content
        for child in master.childNodes[:]:
            if child.qname == (STYLENS, "header"):
                master.removeChildNode(child)

        header = Header()

        # Parse the header content into paragraphs
        for p_match in re.finditer(r'<p([^>]*)>(.*?)</p>', content_html, re.S | re.I):
            p_inner = p_match.group(2)
            p = P()
            # Add runs to the paragraph
            self._add_runs_to_element(p, p_inner)
            header.addElement(p)

        master.addElement(header)

    def add_footer(self, content_html: str) -> None:
        """Add a footer to the document's master page.

        Creates a master page style with style:footer containing the content.
        """
        from odf.style import Footer, MasterPage, PageLayout
        from odf.text import P

        # Create a page layout if we don't have one
        if not any(el.getAttribute("name") == "WO_PageLayout"
                   for el in self.doc.automaticstyles.childNodes
                   if el.qname == (STYLENS, "page-layout")):
            pageLayout = PageLayout(name="WO_PageLayout")
            self.doc.automaticstyles.addElement(pageLayout)

        # Create or update master page with footer
        master_name = "WO_Master"
        master = None
        for el in self.doc.masterstyles.childNodes:
            if el.qname == (STYLENS, "master-page") and el.getAttribute("name") == master_name:
                master = el
                break

        if master is None:
            master = MasterPage(name=master_name, pagelayoutname="WO_PageLayout")
            self.doc.masterstyles.addElement(master)

        # Remove existing footer if any and add new content
        for child in master.childNodes[:]:
            if child.qname == (STYLENS, "footer"):
                master.removeChildNode(child)

        footer = Footer()

        # Parse the footer content into paragraphs
        for p_match in re.finditer(r'<p([^>]*)>(.*?)</p>', content_html, re.S | re.I):
            p_inner = p_match.group(2)
            p = P()
            # Add runs to the paragraph
            self._add_runs_to_element(p, p_inner)
            footer.addElement(p)

        master.addElement(footer)

    def _add_runs_to_element(self, parent, content_html: str) -> None:
        """Add text runs to an ODF element from HTML content.

        Handles page number fields (<span class="page-number">) specially.
        """
        from odf.element import Element
        from odf.namespaces import TEXTNS

        pos = 0
        while pos < len(content_html):
            # Check for page number span
            pn_match = re.match(r'<span\s+class="page-number"[^>]*></span>', content_html[pos:], re.I | re.S)
            if pn_match:
                # Add any preceding text
                if pn_match.start() > 0:
                    text = content_html[pos:pos + pn_match.start()]
                    text = re.sub(r'<[^>]+>', '', text)
                    if text:
                        parent.addText(text)

                # Add page number field
                # In ODT, page number is a text:page-number element
                pn_el = Element(qname=(TEXTNS, "page-number"))
                pn_el.setAttrNS(TEXTNS, "select-page", "current")
                pn_el.addText("1")  # Initial value placeholder
                parent.addElement(pn_el)

                pos += pn_match.end()
            else:
                # Regular text - find next tag or end
                tag_match = re.search(r'<', content_html[pos:])
                if tag_match:
                    text = content_html[pos:pos + tag_match.start()]
                    text = re.sub(r'<[^>]+>', '', text)
                    if text:
                        parent.addText(text)
                    pos += tag_match.start()
                else:
                    # Remaining text
                    text = content_html[pos:]
                    text = re.sub(r'<[^>]+>', '', text)
                    if text:
                        parent.addText(text)
                    break

    def add_list(self, tree: dict) -> None:
        """Append a (possibly nested) list tree to the body.

        ``tree`` is the ``parse_list_at`` shape: {'kind', 'items':
        [{'frag', 'sub': [tree, ...]}, ...]}. Nested lists become nested
        ``text:list`` elements inside their parent ``text:list-item`` so
        outline levels reach LibreOffice unchanged.
        """
        self.cur.addElement(self._build_list(tree))

    @staticmethod
    def _parse_row(html: str) -> list[dict]:
        """Split one HTML ``<tr>`` into its cells.

        Each entry carries the cell's inner html plus its colspan/rowspan
        (parsed from the opening tag, defaults to 1), plus the parsed cell
        ``style`` (background-color / border) / ``width`` (px). Both ``<td>``
        and ``<th>`` map to ODF table cells.
        """
        cells: list[dict] = []
        for m in re.finditer(r"<t[dh]([^>]*)>(.*?)</t[dh]>", html, re.S):
            attrs, body = m.group(1), m.group(2)
            cells.append({
                "html": body,
                "colspan": _span_attr(attrs, "colspan"),
                "rowspan": _span_attr(attrs, "rowspan"),
                "bg": _odt_cell_bg(attrs),
                "border": _odt_cell_border(attrs),
                "width": _odt_cell_width(attrs),
            })
        return cells

    def cell_style(self, bg, border) -> str | None:
        """Return (creating if needed) an ODF table-cell style with the given
        background colour / border."""
        key = (bg, border)
        name = self._tc_styles.get(key)
        if name:
            return name
        if not bg and not border:
            return None
        name = f"WO_Tc{len(self._tc_styles)}"
        style = Style(name=name, family="table-cell")
        props_el = TableCellProperties()
        if bg:
            props_el.setAttribute("backgroundcolor", bg)
        if border:
            props_el.setAttribute("border", border)
        style.addElement(props_el)
        self.doc.automaticstyles.addElement(style)
        self._tc_styles[key] = name
        return name

    def _column_style(self, width_px: int) -> str:
        """Return (creating if needed) an ODF table-column style for a width."""
        name = self._col_styles.get(width_px)
        if name:
            return name
        name = f"WO_Col{len(self._col_styles)}"
        style = Style(name=name, family="table-column")
        props_el = TableColumnProperties()
        props_el.setAttribute("columnwidth", f"{width_px}px")
        style.addElement(props_el)
        self.doc.automaticstyles.addElement(style)
        self._col_styles[width_px] = name
        return name

    def add_table(self, html: str) -> None:
        """Build an ODF ``<table:table>`` from an HTML ``<table>`` fragment.

        colspan/rowspan become ``table:number-columns-spanned`` /
        ``table:number-rows-spanned``; covered-table-cell placeholders are
        emitted so the grid stays rectangular (matching what LibreOffice
        expects). Rowspans leaving a hole in a later row fill that slot with
        a covered cell too.
        """
        # A <figure><figcaption> wrapper becomes an ODF caption: a
        # preceding text:p with text:sequence-name (extracted here, before
        # the figure wrapper is unwrapped for table parsing).
        caption = None
        fig_m = re.search(r"<figure[^>]*>(.*?)</figure>", html, re.S)
        if fig_m:
            inner = fig_m.group(1)
            fc = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", inner, re.S)
            if fc:
                caption = _inline_to_text(fc.group(1)).strip()
            html = fig_m.group(1)
        row_htmls = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
        if not row_htmls:
            return
        rows = [self._parse_row(r) for r in row_htmls]
        ncols = 0
        for r in rows:
            ncols = max(ncols, sum(c["colspan"] for c in r))

        table = Table()
        # Table width (style:table-properties fo:width on an automatic style).
        tw = re.search(r"<table[^>]*\bwidth\s*=\s*[\"']?(\d+(\.\d+)?)", html)
        if tw:
            tname = self._table_style(float(tw.group(1)))
            table.setAttribute("stylename", tname)
        # Column widths: LibreOffice keeps cell widths on table:table-column.
        col_widths = _odt_col_widths(rows, ncols)
        if any(col_widths):
            for wcol in col_widths:
                col_el = TableColumn()
                if wcol:
                    col_el.setAttribute("stylename", self._column_style(wcol))
                table.addElement(col_el)
        vertical: dict[int, int] = {}  # col -> rows still covered from above
        for r in rows:
            row_el = TableRow()
            col = 0
            for cell in r:
                # Emit covered cells for slots still held by a rowspan above.
                while vertical.get(col, 0):
                    row_el.addElement(CoveredTableCell())
                    vertical[col] -= 1
                    if vertical[col] <= 0:
                        del vertical[col]
                    col += 1
                cel = TableCell()
                if cell["colspan"] > 1:
                    cel.setAttribute("numbercolumnsspanned", str(cell["colspan"]))
                if cell["rowspan"] > 1:
                    cel.setAttribute("numberrowsspanned", str(cell["rowspan"]))
                    for k in range(cell["colspan"]):
                        vertical[col + k] = cell["rowspan"] - 1
                cname = self.cell_style(cell["bg"], cell["border"])
                if cname:
                    cel.setAttribute("stylename", cname)
                p = P()
                self._fill(p, cell["html"])
                cel.addElement(p)
                row_el.addElement(cel)
                col += 1
                for _ in range(1, cell["colspan"]):
                    row_el.addElement(CoveredTableCell())
            table.addElement(row_el)
        self.cur.addElement(table)
        if caption:
            cap_p = P()
            cap_p.attributes[(TEXTNS, "sequence-name")] = "Table"
            cap_p.addText(caption)
            self.cur.insertBefore(cap_p, table)

    def _table_style(self, width_px: float) -> str:
        """Return (creating if needed) a table style fixing the table width."""
        name = self._tbl_styles.get(width_px)
        if name:
            return name
        name = f"WO_Tbl{len(self._tbl_styles)}"
        style = Style(name=name, family="table")
        props_el = TableProperties()
        props_el.setAttribute("width", f"{width_px:g}px")
        style.addElement(props_el)
        self.doc.automaticstyles.addElement(style)
        self._tbl_styles[width_px] = name
        return name


_ODT_ATTR_TAG = re.compile(r"<t[dh][^>]*\b(\w+)\s*=\s*[\"']([^\"']*)[\"']", re.S)


def _odt_cell_bg(attrs: str) -> str | None:
    """Parse a cell tag's background-color declaration to #rrggbb or None."""
    m = re.search(r"background(?:-color)?\s*:\s*([^;\"\s]+)", attrs or "")
    if not m:
        return None
    return _normalize_color(m.group(1).strip())


def _odt_cell_border(attrs: str) -> str | None:
    """Parse a cell tag's border declaration to 'Npt solid #hex' or None."""
    return _parse_border(attrs or "")


def _odt_cell_width(attrs: str) -> int | None:
    """Parse a cell tag's width attribute to integer px or None."""
    m = re.search(r"\bwidth\s*=\s*[\"']?(\d+(\.\d+)?)", attrs or "")
    if not m:
        return None
    try:
        return round(float(m.group(1)))
    except ValueError:
        return None


def _odt_col_widths(rows: list[list[dict]], ncols: int) -> list[int]:
    """Per-grid-column width (px) from the first cell in the column that
    specifies one; 0 means 'no explicit width'."""
    widths = [0] * ncols
    for r in rows:
        col = 0
        for c in r:
            if c["width"] and col < ncols and not widths[col]:
                widths[col] = c["width"]
            col += c["colspan"]
    return widths


def _span_attr(attrs: str, name: str) -> int:
    """Parse ``name="N"`` out of an HTML tag's attribute string (>= 1)."""
    m = re.search(name + r'=["\'](\d+)["\']', attrs)
    if not m:
        return 1
    try:
        return max(1, int(m.group(1)))
    except ValueError:
        return 1


# --------------------------------------------------------------------------
# Inline HTML -> runs (same semantics as the DOCX converter)
#
# ``_inline_tokens`` is shared with the DOCX converter so both writers parse
# the same HTML contract (footnotes/endnotes included).
# --------------------------------------------------------------------------


def _styled_runs(html: str) -> list[tuple[str, bool, bool, bool]]:
    """Legacy view: text-only runs (images excluded), used by callers that
    only care about character formatting."""
    return [(t["text"], t["bold"], t["italic"], t["underline"])
            for t in _inline_tokens(html) if t["type"] == "text"]
