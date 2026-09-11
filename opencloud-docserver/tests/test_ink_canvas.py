"""TDD test for Draw tab ink canvas layer (WO-FEA-DRAW-0).

feature register: draw.select/pen/highlighter/eraser/ink/ink-thickness

RED: fails until index.html has real draw commands and editor.js wires them.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")


def test_draw_buttons_are_real_commands():
    """Draw tab: all six buttons must be real data-cmd, not data-stub."""
    for bt in ["draw.select", "draw.pen", "draw.highlighter", "draw.eraser",
               "draw.ink", "draw.ink-thickness"]:
        assert f'data-stub="{bt}"' not in HTML, \
            f"{bt} still a stub — promote to real command"


def test_draw_tab_ink_commands_wired():
    """Ink mode commands must be wired in index.html."""
    assert 'data-cmd="toggleInk"' in HTML, "toggleInk command missing"
    assert 'data-cmd="inkColor"' in HTML, "inkColor command missing"
    assert 'data-cmd="inkThickness"' in HTML, "inkThickness command missing"


def test_draw_tab_ink_handlers_in_js():
    """editor.js must implement the ink command handlers."""
    assert "function toggleInk" in JS, "toggleInk function missing"
    assert "inkColor" in JS, "inkColor handling missing"
    assert "inkThickness" in JS, "inkThickness handling missing"


def test_draw_tab_i18n_keys_present():
    """i18n catalog must have Draw tab keys for en and de."""
    for key in ["Draw.Select", "Draw.Pen", "Draw.Highlighter", "Draw.Eraser",
                "Draw.Ink", "Draw.InkThickness"]:
        assert key in I18N, f"Missing i18n key: {key} (en)"
    # German keys are in LOCALE_DE object
    assert "LOCALE_DE" in I18N, "German locale object missing"
    for key in ["Draw.Select", "Draw.Pen", "Draw.Highlighter", "Draw.Eraser",
                "Draw.Ink", "Draw.InkThickness"]:
        assert key in I18N.split("LOCALE_DE")[1], f"Missing i18n key: {key} (de)"


def test_ink_canvas_element_present():
    """An ink canvas overlay must exist in the DOM."""
    assert 'id="ink-canvas"' in HTML or 'class="ink-canvas"' in HTML, \
        "Ink canvas element missing"
    assert "<canvas" in HTML, "No canvas element for ink"


def test_ink_canvas_styles_present():
    """CSS must position and style the ink canvas."""
    assert ".ink-canvas" in CSS or "#ink-canvas" in CSS, \
        "Ink canvas CSS missing"
