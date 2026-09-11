"""TDD test for Draw tab stroke select + delete/move (WO-FEA-DRAW-2).

feature register: draw.select (stroke select, delete, move)

RED: fails until the Select button ships a real inkSelect command and
editor.js keeps a stroke registry that renders via redrawInk() so selected
strokes can be hit-tested, translated on drag, and removed on Delete.
Mirrors tests/test_ink_canvas.py + tests/test_file_menu.py: reads the web
files as constants and asserts the wiring contract, because no JS test
runner is available in this environment.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")


def test_select_button_is_a_real_ink_command():
    """The Select button must ship data-cmd=inkSelect (not a stub, not a
    bare toggleInk data-value)."""
    assert 'data-cmd="inkSelect"' in HTML, "Select button missing data-cmd=inkSelect"
    assert 'data-stub="draw.select"' not in HTML, "draw.select must not be a stub"
    # id + i18n title stay for wiring tests below.
    assert 'id="btn-ink-select"' in HTML, "Select button lost its id"


def test_ink_select_handler_wired_in_js():
    """editor.js must implement inkSelect and route it in runCommand on the
    same bus as the other ink commands."""
    assert "function inkSelect" in JS, "inkSelect function missing"
    assert 'cmd === "inkSelect"' in JS, "inkSelect not dispatched in runCommand"
    assert "setStatus" in JS.split("function inkSelect")[1], \
        "inkSelect must report via setStatus (no silent no-op)"


def test_stroke_registry_supports_select_delete_move():
    """Selecting a stroke needs a stroke list + replay renderer so a click
    can hit-test, a drag can translate points, and Delete can remove one."""
    assert "inkStrokes" in JS, "stroke registry (inkStrokes) missing"
    assert "function redrawInk" in JS, "replay renderer redrawInk missing"
    assert "function hitTestStroke" in JS, "hit-test function missing"
    # Move: dragging translates the selected stroke's points and re-renders.
    assert "selectedInk" in JS, "selected-stroke tracking missing"
    assert "selectDrag" in JS, "drag state for move missing"
    assert ".points" in JS.split("function moveSelectedStroke")[1], \
        "moveSelectedStroke must translate stroke points"
    # Delete: stroke removed from the registry + canvas re-rendered.
    assert "function deleteSelectedInk" in JS, "delete handler missing"
    assert ".splice(selectedInk, 1)" in JS, "delete must remove the stroke from inkStrokes"

    # Canvas must get the focused events to select only in select mode.
    assert 'inkMode === "select"' in JS, "select pointer routing via inkMode missing"


def test_delete_move_keyboard_wiring():
    """Delete/Backspace removes the selected stroke, Escape clears the
    selection; both only act while a stroke is actually selected."""
    assert 'ev.key === "Delete"' in JS, "Delete key handling missing"
    assert 'ev.key === "Backspace"' in JS, "Backspace key handling missing"
    assert "selectedInk < 0" in JS, "guards missing (no-op when nothing selected)"


def test_select_draw_i18n_keys_present():
    """Draw.Select keys exist in both locales (unchanged from DRAW-0)."""
    for key in ["Draw.Select", "Draw.SelectTitle"]:
        assert key in I18N, f"Missing i18n key: {key} (en)"
        assert key in I18N.split("LOCALE_DE")[1], f"Missing i18n key: {key} (de)"


def test_other_ink_tools_still_wired():
    """Promoting select must not regress pen/highlighter/eraser."""
    assert 'data-cmd="toggleInk"' in HTML, "toggleInk command missing"
    assert "function toggleInk" in JS, "toggleInk function missing"
    assert 'data-cmd="inkColor"' in HTML and "function setInkColor" in JS
    assert 'data-cmd="inkThickness"' in HTML and "function setInkThickness" in JS
