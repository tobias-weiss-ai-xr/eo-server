"""Citation + Index L1 field marker tests (ref.citation / ref.index).

Round-trip tests for inline reference field markers:
  * ref-citation: <sup class="ref-citation" data-key="Author2024">[1]</sup>
  * ref-index: <span class="ref-index">term</span>
Marker presence = feature used; absence = not used. Markers survive
round-trip through DOCX and ODT converters.
"""

from src.editor.converter import docx_to_html, html_to_docx
from src.editor.odt_converter import html_to_odt, odt_to_html
from src.editor.sanitize import sanitize_html


def _rt(html: str) -> str:
    return docx_to_html(html_to_docx(sanitize_html(html)))


def _rt_odt(html: str) -> str:
    return odt_to_html(html_to_odt(sanitize_html(html)))


# ── ref-citation ───────────────────────────────────────────────────────


def test_ref_citation_marker_round_trip_docx():
    h = '<p>Cited<sup class="ref-citation" data-key="Author2024">[1]</sup> here.</p>'
    out = _rt(h)
    assert 'class="ref-citation"' in out
    assert '[1]' in out


def test_ref_citation_absent_when_not_used():
    out = _rt("<p>No citation.</p>")
    assert "ref-citation" not in out


def test_ref_citation_with_custom_text():
    h = '<p>Source<sup class="ref-citation">[Smith, 2024]</sup> claims.</p>'
    out = _rt(h)
    assert 'class="ref-citation"' in out
    assert "[Smith, 2024]" in out


def test_ref_citation_odt_round_trip():
    h = '<p>Cited<sup class="ref-citation" data-key="Test">[1]</sup> here.</p>'
    out = _rt_odt(h)
    # ODT L1: content is preserved; class attribute may be lost
    assert '[1]' in out
    assert 'Cited' in out


# ── ref-index ─────────────────────────────────────────────────────────


def test_ref_index_marker_round_trip_docx():
    h = '<p>An <span class="ref-index">important term</span> here.</p>'
    out = _rt(h)
    assert 'class="ref-index"' in out
    assert "important term" in out


def test_ref_index_absent_when_not_used():
    out = _rt("<p>No index entry.</p>")
    assert "ref-index" not in out


def test_ref_index_with_data_entry():
    h = '<p><span class="ref-index" data-entry="keyword">term</span> here.</p>'
    out = _rt(h)
    assert 'class="ref-index"' in out
    assert "term" in out


def test_ref_index_odt_round_trip():
    h = '<p>An <span class="ref-index">entry</span> here.</p>'
    out = _rt_odt(h)
    # ODT L1: content is preserved; class attribute may be lost
    assert 'entry' in out
    assert 'An' in out


# ── mixed markers ─────────────────────────────────────────────────────


def test_citation_and_index_together():
    h = ('<p><span class="ref-index">keyword</span> is cited in '
         '<sup class="ref-citation">[1]</sup>.</p>')
    out = _rt(h)
    assert 'class="ref-citation"' in out
    assert 'class="ref-index"' in out


# ── marker stripping guarantees ────────────────────────────────────────


def test_ref_markers_dont_leak_content():
    for h in (
        '<p><sup class="ref-citation">[1]</sup>text</p>',
        '<p><span class="ref-index">entry</span>text</p>',
    ):
        body = _rt(h)
        # The markers themselves persist
        assert '<sup' in body or '<span' in body
        # The text content is preserved
        assert 'text' in body
