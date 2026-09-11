"""TDD test for Draw tab tools (WO-FEA-DRAW-1).

feature register: draw.select/pen/highlighter/eraser/ink/ink-thickness + inkMode

Draw-0 shipped the ink canvas layer (toggleInk/select/pen/highlighter/eraser
+ cycling ink color/thickness). Draw-1 adds the canonical ``setInkMode``
setter (the ``data-cmd="inkMode"`` Draw master toggle) and active-state
feedback for the ink tool buttons, keeping the per-tool ``toggleInk`` buttons
and the ink color/thickness wiring intact.

This is an executable smoke test (no JS runner in this env): it reads the
web files as constants and asserts the wiring, mirroring test_file_menu.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")


# --- gate requirements (acceptance command) ---------------------------------

def test_ink_mode_command_present_in_html():
    """The Draw master toggle ships data-cmd=\"inkMode\" (gate grep)."""
    assert 'data-cmd="inkMode"' in HTML, "missing data-cmd=inkMode in index.html"


def test_set_ink_mode_function_in_js():
    """editor.js implements setInkMode (gate grep)."""
    assert "function setInkMode" in JS, "missing function setInkMode in editor.js"


# --- draw tab buttons are real (not stubs) ----------------------------------

def test_draw_buttons_are_real_commands():
    """All draw tab buttons must be real data-cmd, not data-stub."""
    for bt in ["draw.select", "draw.pen", "draw.highlighter", "draw.eraser",
               "draw.ink", "draw.ink-thickness"]:
        assert f'data-stub="{bt}"' not in HTML, f"{bt} still a stub"


def test_draw_tab_tool_commands_wired():
    """Per-tool buttons use toggleInk; master toggle uses inkMode."""
    assert 'data-cmd="toggleInk"' in HTML, "toggleInk command missing"
    assert 'data-cmd="inkColor"' in HTML, "inkColor command missing"
    assert 'data-cmd="inkThickness"' in HTML, "inkThickness command missing"
    # The four tool buttons carry their tool as data-value.
    for tool in ["pen", "highlighter", "eraser"]:
        assert f'data-cmd="toggleInk" data-value="{tool}"' in HTML, \
            f"toggleInk button for {tool} missing"


def test_draw_tab_handlers_in_js():
    """editor.js implements the ink command handlers."""
    assert "function toggleInk" in JS, "toggleInk function missing"
    assert "function setInkMode" in JS, "setInkMode function missing"
    assert "function setInkColor" in JS, "setInkColor function missing"
    assert "function setInkThickness" in JS, "setInkThickness function missing"


def test_ink_mode_wired_in_run_command():
    """runCommand dispatches the inkMode command to setInkMode."""
    assert 'cmd === "inkMode"' in JS, "inkMode not wired in runCommand"
    assert 'setInkMode(value)' in JS, "inkMode does not call setInkMode"


def test_set_ink_mode_delegates_canvas_state():
    """setInkMode is the canonical setter: shows/hides the canvas and sets inkMode."""
    # setInkMode sets inkMode, toggles the .drawing class and hidden attribute,
    # and calls updateActiveStates for button feedback.
    assert "inkMode = mode" in JS, "setInkMode must set inkMode"
    assert "inkCanvas.hidden" in JS, "setInkMode must show/hide the canvas"
    assert "classList.add(\"drawing\")" in JS or 'classList.add("drawing")' in JS, \
        "setInkMode must toggle the drawing class"
    assert "updateActiveStates()" in JS, "setInkMode must refresh button states"


def test_toggle_ink_delegates_to_set_ink_mode():
    """toggleInk wraps setInkMode (one canvas-state code path)."""
    assert "setInkMode(null)" in JS, "toggleInk must delegate off to setInkMode"


# --- i18n keys (en + de) -----------------------------------------------------

def test_draw_tab_i18n_keys_present():
    """i18n catalog has Draw tab keys for en and de, including the new Draw.Mode."""
    for key in ["Draw.Select", "Draw.Pen", "Draw.Highlighter", "Draw.Eraser",
                "Draw.Ink", "Draw.InkThickness", "Draw.Mode"]:
        assert key in I18N, f"Missing i18n key: {key} (en)"
    for key in ["Draw.SelectTitle", "Draw.PenTitle", "Draw.HighlighterTitle",
                "Draw.EraserTitle", "Draw.InkTitle", "Draw.InkThicknessTitle",
                "Draw.ModeTitle"]:
        assert key in I18N, f"Missing i18n key: {key} (en)"
    # German keys live in the LOCALE_DE object.
    assert "LOCALE_DE" in I18N, "German locale object missing"
    de = I18N.split("LOCALE_DE")[1]
    for key in ["Draw.Select", "Draw.Pen", "Draw.Highlighter", "Draw.Eraser",
                "Draw.Ink", "Draw.InkThickness", "Draw.Mode",
                "Draw.ModeTitle"]:
        assert key in de, f"Missing i18n key: {key} (de)"


# --- ink canvas overlay + active-state feedback ------------------------------

def test_ink_canvas_element_present():
    """An ink canvas overlay must exist in the DOM."""
    assert 'id="ink-canvas"' in HTML or 'class="ink-canvas"' in HTML, \
        "Ink canvas element missing"
    assert "<canvas" in HTML, "No canvas element for ink"


def test_ink_canvas_styles_present():
    """CSS must position and style the ink canvas."""
    assert ".ink-canvas" in CSS or "#ink-canvas" in CSS, "Ink canvas CSS missing"


def test_ink_buttons_have_aria_pressed():
    """Ink tool buttons declare aria-pressed for active-state feedback."""
    # The Draw master toggle and the per-tool buttons carry aria-pressed so
    # updateActiveStates can mirror the active ink tool for screen readers.
    assert 'data-i18n-title="Draw.ModeTitle" aria-pressed="false"' in HTML, \
        "Draw master button must declare aria-pressed"
    assert 'data-i18n-title="Draw.PenTitle" aria-pressed="false"' in HTML, \
        "Pen tool button must declare aria-pressed"


def test_update_active_states_handles_ink():
    """updateActiveStates reflects the ink tool on the toolbar buttons."""
    assert 'cmd === "toggleInk"' in JS, \
        "updateActiveStates must handle toggleInk buttons"
    assert 'cmd === "inkMode"' in JS, \
        "updateActiveStates must handle the inkMode master button"
