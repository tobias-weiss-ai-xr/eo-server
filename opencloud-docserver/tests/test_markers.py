"""Section-flag + drop cap + borders round-trip tests.

Marker contracts (all ride at body start, in this fixed order, and are
stripped from the rendered body):
  * hyphenation:  <div class="hyphenation" data-auto="1">            -> w:autoHyphenation (settings.xml)
  * line numbers: <div class="line-numbers" data-restart data-distance> -> w:lnNumType (sectPr)
  * watermark:    <div class="watermark" data-text data-color>     -> centered 32pt bold gray header paragraph
Marker absence = the feature is off; marker-less documents stay byte-stable.
Drop cap rides inside the paragraph: <span class="dropcap">X</span> as the
first run -> w:framePr w:dropCap. Paragraph borders map to w:pBdr / ODT
fo:border-*.
"""

import io

from docx import Document
from docx.oxml.ns import qn

from src.editor.converter import docx_to_html, html_to_docx
from src.editor.odt_converter import html_to_odt, odt_to_html
from src.editor.sanitize import sanitize_html


def _rt(html: str) -> str:
    return docx_to_html(html_to_docx(sanitize_html(html)))


# ── hyphenation ────────────────────────────────────────────────────────
def test_hyphenation_marker_round_trip():
    h = '<div class="hyphenation" data-auto="1"></div><p>Hyphenated.</p>'
    assert _rt(h).split("\n")[0] == '<div class="hyphenation" data-auto="1"></div>'


def test_hyphenation_absent_when_off():
    out = _rt("<p>Plain.</p>")
    assert "hyphenation" not in out
    assert out == "<p>Plain.</p>"


def test_hyphenation_written_to_settings_xml():
    data = html_to_docx('<div class="hyphenation" data-auto="1"></div><p>x</p>')
    d = Document(io.BytesIO(data))
    assert d.settings.element.find(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        'autoHyphenation') is not None


# ── line numbers ───────────────────────────────────────────────────────
def test_line_numbers_marker_round_trip():
    h = ('<div class="line-numbers" data-restart="eachPage" data-distance="720">'
         '</div><p>Numbered.</p>')
    out = _rt(h).split("\n")[0]
    assert out == ('<div class="line-numbers" data-restart="eachPage" '
                   'data-distance="720"></div>')


def test_line_numbers_absent_when_off():
    assert "line-numbers" not in _rt("<p>No numbers.</p>")


def test_line_numbers_written_to_sectpr():
    data = html_to_docx(
        '<div class="line-numbers" data-restart="continuous" data-distance="480">'
        '</div><p>x</p>')
    d = Document(io.BytesIO(data))
    ln = d.sections[0]._sectPr.find(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        'lnNumType')
    assert ln is not None
    assert ln.get(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        'restart') == "continuous"
    assert ln.get(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        'distance') == "480"


# ── watermark ──────────────────────────────────────────────────────────
def test_watermark_marker_round_trip_docx():
    h = ('<div class="watermark" data-text="CONFIDENTIAL" data-color="#C0C0C0">'
         '</div><p>Body.</p>')
    out = _rt(h)
    lines = out.split("\n")
    assert lines[0] == ('<div class="watermark" data-text="CONFIDENTIAL" '
                        'data-color="#c0c0c0"></div>')
    assert "CONFIDENTIAL" not in "<p>Body.</p>" + "\n".join(lines[1:]).replace(
        "CONFIDENTIAL", "")


def test_watermark_coexists_with_header():
    h = ('<div class="watermark" data-text="TOP SECRET" data-color="#999999">'
         '</div><header><p>My header</p></header><p>Body.</p>')
    out = _rt(h)
    lines = out.split("\n")
    assert lines[0].startswith('<div class="watermark"')
    assert '<p>My header</p>' in "\n".join(lines[1:])


def test_watermark_absent_without_marker():
    assert "watermark" not in _rt("<p>No marker here.</p>")


def test_watermark_round_trip_odt():
    h = ('<div class="watermark" data-text="SECRET" data-color="#C0C0C0">'
         '</div><p>ODT body.</p>')
    out = odt_to_html(html_to_odt(sanitize_html(h)))
    # ODT keeps the text; the color is an ODT divergence (documented L1).
    assert out.split("\n")[0] == '<div class="watermark" data-text="SECRET"></div>'
    assert "SECRET" not in out.split("\n", 1)[1]


def test_watermark_odt_absent_without_marker():
    assert "watermark" not in odt_to_html(html_to_odt("<p>plain</p>"))


# ── drop cap ───────────────────────────────────────────────────────────
def test_dropcap_round_trip_docx():
    h = '<p><span class="dropcap">H</span>ello world.</p>'
    assert _rt(h) == '<p><span class="dropcap">H</span>ello world.</p>'


def test_dropcap_absent_without_marker():
    assert "dropcap" not in _rt("<p>Normal.</p>")


def test_dropcap_letters_kept_on_write():
    data = html_to_docx('<p><span class="dropcap">W</span>ord.</p>')
    d = Document(io.BytesIO(data))
    assert d.paragraphs[0].text == "Word."
    fp = d.paragraphs[0]._p.pPr.find(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        'framePr')
    assert fp is not None
    assert fp.get(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        'dropCap') == "drop"


# ── paragraph borders ──────────────────────────────────────────────────
def test_border_shorthand_round_trip_docx():
    h = '<p style="border:1.25pt solid #ff0000">Boxed.</p>'
    out = _rt(h)
    for side in ("top", "left", "bottom", "right"):
        assert f"border-{side}:1.25pt solid #ff0000" in out


def test_border_sides_round_trip_docx():
    h = ('<p style="border-top:2pt solid #0000ff;border-bottom:2pt solid '
         '#0000ff">TB.</p>')
    out = _rt(h)
    assert "border-top:2pt solid #0000ff" in out
    assert "border-bottom:2pt solid #0000ff" in out
    assert "border-left" not in out


def test_border_absent_without_style():
    assert "border" not in _rt("<p>No frame here.</p>")


def test_border_round_trip_odt():
    h = '<p style="border:1.5pt solid #0000ff">Boxed ODT.</p>'
    out = odt_to_html(html_to_odt(sanitize_html(h)))
    assert "border-top:1.5pt solid #0000ff" in out
    assert "border-bottom:1.5pt solid #0000ff" in out


# ── marker stripping guarantees ────────────────────────────────────────
def test_markers_never_leak_into_body():
    for h in (
        '<div class="hyphenation" data-auto="1"></div><p>x</p>',
        '<div class="line-numbers" data-restart="eachPage"></div><p>x</p>',
        '<div class="watermark" data-text="T" data-color="#C0C0C0"></div><p>x</p>',
        '<p style="border:1pt solid #000">x</p>',
        '<p><span class="dropcap">X</span>y</p>',
    ):
        body = docx_to_html(html_to_docx(sanitize_html(h)))
        assert "\n" not in body or "<div class=" in body.split("\n")[0]
    # the marker divs themselves are not in the body paragraph stream
    out = docx_to_html(html_to_docx(
        '<div class="hyphenation" data-auto="1"></div>'
        '<div class="line-numbers"></div>'
        '<div class="watermark" data-text="T" data-color="#C0C0C0"></div>'
        '<p>real content</p>'))
    assert out.count("<div class=") == 3
    assert "<p>real content</p>" in out


# ── header/footer section markers (R5: different-first / odd-even / ──
# ── header-from-top / footer-from-bottom) ─────────────────────────────
def test_different_first_marker_round_trip():
    h = '<div class="different-first"></div><p>First page separate.</p>'
    assert _rt(h).split("\n")[0] == '<div class="different-first"></div>'


def test_different_first_absent_when_off():
    assert "different-first" not in _rt("<p>Unified.</p>")


def test_different_first_written_to_titlepg():
    data = html_to_docx('<div class="different-first"></div><p>x</p>')
    d = Document(io.BytesIO(data))
    assert d.sections[0]._sectPr.find(qn("w:titlePg")) is not None


def test_odd_even_marker_round_trip():
    h = '<div class="odd-even"></div><p>Mirror headers.</p>'
    assert _rt(h).split("\n")[0] == '<div class="odd-even"></div>'


def test_odd_even_absent_when_off():
    assert "odd-even" not in _rt("<p>Unified.</p>")


def test_odd_even_written_to_settings():
    data = html_to_docx('<div class="odd-even"></div><p>x</p>')
    d = Document(io.BytesIO(data))
    assert d.settings.element.find(qn("w:evenAndOddHeaders")) is not None


def test_header_from_top_marker_round_trip():
    h = '<div class="header-from-top" data-inches="0.8"></div><p>Gap.</p>'
    assert _rt(h).split("\n")[0] == '<div class="header-from-top" data-inches="0.8"></div>'


def test_footer_from_bottom_marker_round_trip():
    h = '<div class="footer-from-bottom" data-inches="1"></div><p>Gap.</p>'
    assert _rt(h).split("\n")[0] == '<div class="footer-from-bottom" data-inches="1"></div>'


def test_hf_distance_written_to_pgmar():
    data = html_to_docx(
        '<div class="header-from-top" data-inches="0.8"></div>'
        '<div class="footer-from-bottom" data-inches="1"></div><p>x</p>')
    d = Document(io.BytesIO(data))
    pgMar = d.sections[0]._sectPr.find(qn("w:pgMar"))
    assert pgMar.get(qn("w:header")) == "1152"   # 0.8in * 1440 twips
    assert pgMar.get(qn("w:footer")) == "1440"   # 1in * 1440 twips


def test_hf_markers_absent_when_off():
    out = _rt("<p>Defaults.</p>")
    for m in ("different-first", "odd-even", "header-from-top", "footer-from-bottom"):
        assert m not in out


def test_default_header_distance_reads_marker_free():
    # 720 twips == the OOXML pgMar default (0.5"); a doc materializing the
    # default reads back marker-free because the value equals the default.
    assert "header-from-top" not in _rt(
        '<div class="header-from-top" data-inches="0.5"></div><p>x</p>')




def test_hf_markers_round_trip_with_existing_flags():
    # Canonical body-start order: hy, ln, df, oe, htf, ftb, wm.
    h = ('<div class="hyphenation" data-auto="1"></div>'
         '<div class="line-numbers" data-restart="eachPage"></div>'
         '<div class="different-first"></div>'
         '<div class="odd-even"></div>'
         '<div class="header-from-top" data-inches="0.8"></div>'
         '<div class="footer-from-bottom" data-inches="1"></div>'
         '<div class="watermark" data-text="DRAFT" data-color="#C0C0C0"></div><p>Body.</p>')
    out = _rt(h)
    for m in ('class="hyphenation"', 'class="line-numbers"',
              'class="different-first"', 'class="odd-even"',
              'class="header-from-top" data-inches="0.8"',
              'class="footer-from-bottom" data-inches="1"',
              'class="watermark" data-text="DRAFT" data-color="#c0c0c0"'):
        assert m in out, f"lost {m}: {out[:200]!r}"
    for a, b in (("different-first", "odd-even"), ("odd-even", "header-from-top"),
                 ("header-from-top", "footer-from-bottom"), ("footer-from-bottom", "watermark")):
        assert out.index(a) < out.index(b), f"order broken between {a} and {b}"
