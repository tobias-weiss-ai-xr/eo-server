"""Borrow the World-Office ``wo-conformance`` corpus as round-trip coverage.

The corpus lives in the wo-test-harness repo (crate ``wo-conformance``,
``conformance/corpus/cases``; 30 real ``.docx`` documents
covering bold/italic/fonts/alignment/spacing/page-breaks/tables/…).

What we reuse vs. what we don't:

* **Reused** — the ``.docx`` *source documents* are genuine Word files fed
  through the canonical Python converter as real-world regression inputs.
* **Not reused** — ``*.truth.json`` / ``*.engine.json`` are LibreOffice
  visual-IR (rendered boxes/positions) captured for the *deprecated* Rust
  ``wo-docx-renderer``. They are not oracles for our HTML contract, so this
  test asserts converter-level round-trip fidelity instead (no crash, non-empty
  output, and that the bulk of paragraph text survives the docx→html→docx→html
  round-trip).
"""

from __future__ import annotations

import glob
import os
import re
import zipfile

import pytest
from docx import Document

from src.editor.converter import docx_to_html, html_to_docx

def _find_corpus() -> str | None:
    """Locate the wo-conformance corpus (moved to the wo-test-harness repo).

    Resolution order (first hit wins):
      1. WO_CONFORMANCE_CORPUS env var (explicit path to corpus/cases)
      2. sibling wo-test-harness checkout next to this server repo
      3. legacy in-repo path (this repo used to embed the crate)
    Returns None (-> tests skip) when no corpus is present in this checkout.
    """
    env = os.environ.get("WO_CONFORMANCE_CORPUS")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, "..", "..", "..", "wo-test-harness", "conformance", "corpus", "cases"),
        os.path.join(here, "..", "..", "core", "crates", "wo-conformance", "corpus", "cases"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.normpath(c)
    return None


_CORPUS = _find_corpus()


def _docx_files() -> list[str]:
    if not os.path.isdir(_CORPUS):
        return []
    return sorted(glob.glob(os.path.join(_CORPUS, "*.docx")))


_DOCX_FILES = _docx_files()

pytestmark = pytest.mark.skipif(
    not _DOCX_FILES,
    reason="wo-conformance corpus not present in this checkout",
)


def _norm(s: str) -> str:
    return s.replace("\u00a0", " ")


def _plain(html: str) -> str:
    """Rough plain-text of generated HTML: strip tags, unescape common
    entities. Used so inline formatting (<span> splits) does not break the
    contiguous-text fidelity check."""
    text = re.sub(r"<[^>]+>", "", html)
    for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                    ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(ent, ch)
    return _norm(text)


@pytest.mark.parametrize(
    "path", _DOCX_FILES, ids=[os.path.basename(p) for p in _DOCX_FILES]
)
def test_corpus_docx_roundtrip(path: str) -> None:
    raw = open(path, "rb").read()
    # It really is a DOCX (zip) document.
    assert zipfile.is_zipfile(path)

    doc = Document(path)
    texts = [_norm(p.text) for p in doc.paragraphs if p.text and p.text.strip()]

    html = docx_to_html(raw)
    assert isinstance(html, str), f"conversion crashed for {path}"
    if texts:
        assert html.strip(), f"empty HTML for a non-empty document: {path}"

    # Round-trip back to DOCX and render again without error.
    docx2 = html_to_docx(html)
    assert isinstance(docx2, bytes) and docx2[:2] == b"PK"
    html2 = docx_to_html(docx2)
    assert isinstance(html2, str)

    # Text fidelity: most paragraph text should survive the round-trip.
    # (Only enforced for documents that actually contain text; an empty
    # document legitimately round-trips to empty HTML.)
    if texts:
        assert html2.strip(), f"empty HTML after round-trip for {path}"
        plain2 = _plain(html2)
        kept = sum(1 for t in texts if t in plain2)
        assert kept >= max(1, int(0.8 * len(texts))), (
            f"only {kept}/{len(texts)} paragraphs survived round-trip for {path}"
        )
