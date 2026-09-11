"""TDD test for OCR plugin UI (plugins.ocr feature).

feature register: plugins.ocr

RED: fails until index.html has an OCR button (data-cmd="ocrRun"),
editor.js wires the command, and router.py exposes
POST /api/documents/{doc_id}/ai/ocr.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")


def test_ocr_button_present_in_html():
    """OCR button exists in the Plugins tab with data-cmd=\"ocrRun\"."""
    assert 'id="btn-ocr"' in HTML, "missing #btn-ocr in index.html"
    assert 'data-cmd="ocrRun"' in HTML, "missing data-cmd=ocrRun in index.html"


def test_ocr_button_i18n():
    """OCR button has i18n attributes."""
    assert 'data-i18n="Plugins.Ocr"' in HTML, "missing Plugins.Ocr i18n key on OCR button"


def test_editor_js_wires_ocr_command():
    """OCR command is handled in editor.js."""
    assert 'cmd === "ocrRun"' in JS, "editor.js missing OCR command handler"
    assert 'openAiPropose' in JS, "editor.js missing openAiPropose reference"
    assert "OCR" in JS, "editor.js missing OCR instruction"


def test_ocr_i18n_keys():
    """OCR i18n keys exist in i18n.js."""
    assert '"Plugins.Ocr"' in I18N, "i18n.js missing Plugins.Ocr key"


def test_ocr_router_endpoint():
    """OCR endpoint is registered in router.py (task: /ai/ocr)."""
    ROUTER = Path(__file__).resolve().parent.parent / "src" / "editor" / "router.py"
    content = ROUTER.read_text(encoding="utf-8")
    assert '@router.post("/api/documents/{doc_id}/ai/ocr")' in content, \
        "router.py missing OCR endpoint registration"
    assert 'def ocr_document' in content, "router.py missing ocr_document function"


def test_ocr_uses_model_registry_pattern():
    """OCR routes through MODEL_REGISTRY; unregistered model returns 503."""
    ROUTER = Path(__file__).resolve().parent.parent / "src" / "editor" / "router.py"
    content = ROUTER.read_text(encoding="utf-8")
    assert 'ai_propose_run' in content, "OCR should use ai_propose_run (model registry)"
    assert 'model_name=str(request.query_params.get("model", "default"))' in content, \
        "OCR should pass model name from query parameter"