"""TDD test for the Photo Editor plugin — canvas filter dialog
(plugins.photoeditor).

RED: fails until index.html ships the #photo-editor-dialog with a
data-cmd="photoEditor" button and editor.js wires the filter pipeline.
Executable smoke test: no JS test runner (Playwright/jsdom) is available
in this environment, so we read the web files as constants and assert
the wiring, mirroring tests/test_file_menu.py.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")


def test_photo_editor_button_is_a_real_command():
    """The Plugins ribbon button must ship data-cmd, not a silent stub."""
    assert 'data-cmd="photoEditor"' in HTML, "photo editor must be a real command"
    assert 'data-stub="plugins.photoeditor"' not in HTML, \
        "plugins.photoeditor must no longer be a stub button"


def test_photo_editor_dialog_present_in_html():
    """Canvas filter dialog with canvas, file input and filter controls."""
    assert 'id="photo-editor-dialog"' in HTML, "missing #photo-editor-dialog"
    assert 'id="photo-editor-canvas"' in HTML, "missing canvas"
    assert 'id="photo-editor-file"' in HTML, "missing image file input"
    assert 'id="photo-editor-brightness"' in HTML
    assert 'id="photo-editor-contrast"' in HTML
    assert 'id="photo-editor-saturate"' in HTML
    assert 'name="photo-editor-preset"' in HTML
    assert 'id="btn-photo-editor-reset"' in HTML
    assert 'id="btn-photo-editor-cancel"' in HTML


def test_photo_editor_wired_in_editor_js():
    """runCommand dispatches photoEditor; open/close/filter/draw live."""
    assert 'cmd === "photoEditor"' in JS, "photoEditor must be in runCommand"
    assert "openPhotoEditorDialog" in JS
    assert "closePhotoEditorDialog" in JS
    assert "photoEditorDraw" in JS
    assert "photoEditorResetFilters" in JS
    assert "photoEditorFilterString" in JS
    # Tab-trapping DIALOG_IDS list must know the new modal.
    assert "photo-editor-dialog" in JS


def test_photo_editor_filters_are_real():
    """Sliders + presets build a real canvas filter string, not a no-op."""
    assert "ctx.filter" in JS, "filters must be applied to the canvas context"
    assert "brightness(" in JS and "contrast(" in JS and "saturate(" in JS
    assert "grayscale(1)" in HTML and "sepia(1)" in HTML
    assert "invert(1)" in HTML and "blur(" in HTML
    # Reset must clear sliders back to neutral 100.
    assert 'photoEditorBrightness.value = "100"' in JS
    assert 'photoEditorContrast.value = "100"' in JS
    assert 'photoEditorSaturate.value = "100"' in JS


def test_photo_editor_i18n_keys():
    """Dialog strings ship in both built-in catalogs (en + de)."""
    for key in ["Plugins.PhotoEditor", "PhotoEditor.ChooseFile",
                "PhotoEditor.Empty", "PhotoEditor.Brightness",
                "PhotoEditor.Contrast", "PhotoEditor.Saturation",
                "PhotoEditor.Reset", "PhotoEditor.Close",
                "PhotoEditor.PresetGrayscale"]:
        assert key in I18N, f"i18n.js missing {key}"
        assert f'"{key}":' in I18N, f"i18n.js has no value for {key}"


def test_photo_editor_dialog_styled():
    """Dialog has its own stylesheet rules for canvas wrap + controls."""
    assert ".photo-editor-canvas-wrap" in CSS
    assert "#photo-editor-canvas" in CSS
    assert ".photo-editor-controls" in CSS
