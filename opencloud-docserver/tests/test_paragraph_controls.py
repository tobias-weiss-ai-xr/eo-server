"""Paragraph controls: multilevel lists (F-032) and line spacing (F-039).

Feature register audit, WO-REG-PARA-1 (home.para cluster).

- F-032 Multilevel list: nested <ul>/<ol> authoring markers
  (insertUnorderedList / insertOrderedList in the toolbar) and the
  serialization contract. Nested lists round-trip exactly through DOCX
  (Word "List Bullet/Number [n]" styles carry the outline level) and ODT
  (nested text:list inside text:list-item) in both directions.
  NOTE: DOCX levels are encoded as python-docx per-level list styles whose
  independent per-level restart is structural nesting evidence at L1, not a
  claim of Word outline-continuation rendering ("1.1") at L2 — see the
  feature's divergence entry.
- F-039 Line spacing: the #line-spacing select emits a `lineHeight` command
  that sets `line-height: <n>` on the block(s); the sanitizer whitelist
  keeps it and the converters map it to w:spacing/w:lineRule (DOCX) and
  fo:line-height (ODT) and back. "1.0" clears to the document default.

Paragraph shading / borders (the home.para shading feature) is NOT
exercised here: it is a documented gap (no authoring surface, no
paragraph-level serialization), so it is resolved by a `divergence:` entry
in features.yaml instead of a coverage claim.

feature register: F-032 F-039
"""

from __future__ import annotations

import io
import re
import zipfile

from src.editor.converter import docx_to_html, html_to_docx
from src.editor.odt_converter import html_to_odt, odt_to_html
from src.editor.sanitize import sanitize_html


# --------------------------------------------------------------------------
# F-032 Multilevel list
# --------------------------------------------------------------------------


def test_multilevel_bullet_list_docx_roundtrip():
    """A level-2 bullet list round-trips through DOCX with nesting intact."""
    html = (
        "<ul><li>item 1<ul><li>sub 1</li><li>sub 2</li></ul></li>"
        "<li>item 2</li></ul>"
    )
    out = docx_to_html(html_to_docx(html)).replace("\n", "")
    assert out == (
        "<ul><li>item 1<ul><li>sub 1</li><li>sub 2</li></ul></li>"
        "<li>item 2</li></ul>"
    ), out


def test_multilevel_numbered_list_docx_roundtrip():
    """A level-2 numbered list round-trips through DOCX with nesting intact."""
    html = "<ol><li>one<ol><li>1.1</li></ol></li><li>two</li></ol>"
    out = docx_to_html(html_to_docx(html)).replace("\n", "")
    assert out == "<ol><li>one<ol><li>1.1</li></ol></li><li>two</li></ol>", out


def test_multilevel_list_docx_encodes_levels_as_numbered_styles():
    """The DOCX bytes carry the outline level via the Word list styles.

    Sub-items reference ListBullet2 / ListNumber2 (styleId), the level-2
    variants of the python-docx template list styles, and the package
    carries word/numbering.xml. This is the L1 structural encoding behind
    F-032's multilevel round-trip.
    """
    bullet = html_to_docx("<ul><li>a<ul><li>a.1</li></ul></li></ul>")
    z = zipfile.ZipFile(io.BytesIO(bullet))
    doc = z.read("word/document.xml").decode()
    assert 'w:pStyle w:val="ListBullet2"' in doc
    assert 'w:pStyle w:val="ListBullet"' in doc
    assert "word/numbering.xml" in z.namelist()

    numbered = html_to_docx("<ol><li>one<ol><li>1.1</li></ol></li></ol>")
    z2 = zipfile.ZipFile(io.BytesIO(numbered))
    doc2 = z2.read("word/document.xml").decode()
    assert 'w:pStyle w:val="ListNumber2"' in doc2
    assert 'w:pStyle w:val="ListNumber"' in doc2


def test_multilevel_list_odt_roundtrip():
    """Nested <ul>/<ol> round-trips through ODT with nesting intact."""
    bullet = (
        "<ul><li>item 1<ul><li>sub 1</li><li>sub 2</li></ul></li>"
        "<li>item 2</li></ul>"
    )
    out_b = odt_to_html(html_to_odt(bullet)).replace("\n", "")
    assert out_b == (
        "<ul><li>item 1<ul><li>sub 1</li><li>sub 2</li></ul></li>"
        "<li>item 2</li></ul>"
    ), out_b

    numbered = "<ol><li>one<ol><li>1.1</li></ol></li><li>two</li></ol>"
    out_o = odt_to_html(html_to_odt(numbered)).replace("\n", "")
    assert out_o == "<ol><li>one<ol><li>1.1</li></ol></li><li>two</li></ol>", out_o


def test_multilevel_list_odt_encodes_nested_text_lists():
    """Nested lists become nested text:list inside text:list-item in ODT."""
    odt = html_to_odt("<ol><li>one<ol><li>1.1</li></ol></li></ol>")
    xml = zipfile.ZipFile(io.BytesIO(odt)).read("content.xml").decode()
    # a text:list (with attrs) nested inside a text:list-item body
    assert re.search(r"<text:list-item[^>]*>.*?<text:list[\s>]", xml, re.S), xml


# --------------------------------------------------------------------------
# F-039 Line spacing
# --------------------------------------------------------------------------


def test_line_spacing_docx_roundtrip():
    """line-height:<n> survives HTML -> DOCX -> HTML."""
    out = docx_to_html(html_to_docx('<p style="line-height:1.5">spaced</p>'))
    assert "line-height:1.5" in out, out


def test_line_spacing_writes_wspacing_line_auto():
    """The DOCX bytes carry the multiple as w:spacing/line + w:lineRule=auto.

    1.5 multiples -> w:line="360" (1.5 * 240) with lineRule auto.
    """
    docx = html_to_docx('<p style="line-height:1.5">spaced</p>')
    xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode()
    m = re.search(r"<w:spacing\b[^>]*/>", xml)
    assert m, "no w:spacing in DOCX"
    assert 'w:line="360"' in m.group(0)
    assert 'w:lineRule="auto"' in m.group(0)


def test_line_spacing_multiple_values_docx_roundtrip():
    """1.15 / 2.5 multiples survive the DOCX round-trip too."""
    for mult in ("1.15", "2.5"):
        html = f'<p style="line-height:{mult}">x</p>'
        out = docx_to_html(html_to_docx(html))
        assert f"line-height:{mult}" in out, (mult, out)


def test_line_spacing_odt_roundtrip():
    """line-height:<n> survives HTML -> ODT -> HTML."""
    out = odt_to_html(html_to_odt('<p style="line-height:1.5">spaced</p>'))
    assert "line-height:1.5" in out, out


def test_line_spacing_odt_writes_percentage():
    """The ODT bytes carry the multiple as fo:line-height percentage."""
    odt = html_to_odt('<p style="line-height:1.5">spaced</p>')
    xml = zipfile.ZipFile(io.BytesIO(odt)).read("content.xml").decode()
    assert 'fo:line-height="150%"' in xml


def test_line_spacing_survives_sanitizer():
    """The sanitizer whitelist keeps line-height so the editor's inline
    style persists to the saved HTML."""
    out = sanitize_html('<p style="line-height:1.5">spaced</p>')
    assert "line-height" in out
    assert "1.5" in out, out


def test_line_spacing_single_means_default():
    """line-height:1 (and no value) is the document default: it is not
    written as an explicit multiple and round-trips to a plain paragraph."""
    out = docx_to_html(html_to_docx('<p style="line-height:1">plain</p>'))
    assert out == "<p>plain</p>", out


# --------------------------------------------------------------------------
# Paragraph shading / borders — current behaviour pin (NOT a feature claim)
# --------------------------------------------------------------------------
#
# # NOTE: existing behaviour — Paragraph shading / borders are a documented
# gap (see the divergence entry for the home.para shading feature in
# the wo-test-harness repo's harness-graph/features.yaml): there is no authoring surface and no
# paragraph-level serialization in either direction. These checks pin the
# current behaviour so a future implementation has to flip them deliberately.
# They deliberately carry NO feature-id marker: that register entry stays
# `parity: missing`, resolved by divergence, not by a coverage edge.


def test_paragraph_shading_and_borders_not_serialized_from_html():
    """`background-color` / `border` on a <p> are dropped by both converters."""
    for html in (
        '<p style="background-color:#fff000">shaded para</p>',
        '<p style="border:1pt solid #ff0000">bordered para</p>',
    ):
        out_docx = docx_to_html(html_to_docx(html))
        assert "background-color" not in out_docx and "border:" not in out_docx, out_docx
        assert "shaded para" in out_docx or "bordered para" in out_docx
        out_odt = odt_to_html(html_to_odt(html))
        assert "background-color" not in out_odt and "border:" not in out_odt, out_odt
        assert "shaded para" in out_odt or "bordered para" in out_odt


def test_paragraph_shading_and_borders_not_serialized_to_html():
    """w:pPr/w:shd and w:pBdr in a source DOCX are dropped on conversion."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    buf = io.BytesIO()
    doc = Document()
    p = doc.add_paragraph("shaded docx para")
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), "00FF00")
    pPr.append(shd)
    pBdr = OxmlElement("w:pBdr")
    top = OxmlElement("w:top")
    top.set(qn("w:val"), "single")
    top.set(qn("w:sz"), "8")
    top.set(qn("w:color"), "FF0000")
    pBdr.append(top)
    pPr.append(pBdr)
    doc.save(buf)

    out = docx_to_html(buf.getvalue())
    assert out == "<p>shaded docx para</p>", out
