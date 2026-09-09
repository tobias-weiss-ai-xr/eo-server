"""View-cluster controls: zoom, fullscreen, dark theme (and audit of fit/rulers).

feature register: F-110 F-112 F-114 (view controls surface + wiring)

View controls are id-based toolbar buttons (NOT data-cmd commands), so they
bypass the harness-graph command inventory — this static audit of the shipped
web assets is the executable smoke test (no JS test runner is available in
this environment, mirroring test_file_menu.py).

Layers exercised per feature (marker -> command -> css):
  * F-110 Zoom controls — #btn-zoom-in/out/reset -> applyZoom() (CSS zoom of
    the editing surface, clamped 0.5..2.0, persisted to localStorage 'wo-zoom',
    reset label shows the live percentage). Live-browser evidence for the zoom
    increase already exists in tests/e2e/test_cloud_editor_e2e.py
    (test_view_controls_zoom_theme_fullscreen).
  * F-112 Full screen   — #btn-fullscreen -> toggleFullscreen() (toggles the
    body.fullscreen class that drives layout expansion; the browser Fullscreen
    API is attempted best-effort) + body.fullscreen rules in style.css.
    Live-browser evidence: same e2e test toggles the class via the button.
  * F-114 Dark theme    — #btn-theme -> toggleTheme()/applyTheme() (flips the
    html.light class, persists localStorage 'wo-theme', default = light) +
    :root dark palette / html.light override in style.css. Live-browser
    evidence: same e2e test observes the body background colour change.

NOT covered here (audit documented in features.yaml divergences instead):
  * Fit page / fit width — #btn-zoom-fit does a simple fit-width estimate;
    no dedicated fit-page/fit-width machinery beyond that.
  * Rulers / guides      — index.html ships a decorative visual-parity ruler
    (.ruler + inline SVG mm/cm scale, aria-hidden, no interaction); coverage
    intentionally not pinned, F-ids stay absent from this file (the seeder
    regex treats any F-### token as a coverage marker).

The theme/fullscreen buttons intentionally remain without F-id markers here:
their coverage lives in features.yaml divergences. F-### tokens must stay out
of this file unless a feature gains a real test.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F-110 Zoom controls
# ---------------------------------------------------------------------------

def test_zoom_controls_surfaces_present():
    """Zoom in / out / reset toolbar buttons exist with shortcut metadata."""
    for btn_id, key in [("btn-zoom-in", "Control+Plus"),
                        ("btn-zoom-out", "Control+Minus")]:
        assert f'id="{btn_id}"' in HTML, f"missing #{btn_id} in index.html"
        assert f'aria-keyshortcuts="{key}"' in HTML, \
            f"#{btn_id} missing aria-keyshortcuts {key}"
    assert 'id="btn-zoom-reset"' in HTML, "missing #btn-zoom-reset in index.html"
    # reset label starts at 100% (the zoom-neutral state); .sb = OO-golden slot
    assert '<button class="sb" id="btn-zoom-reset" style="--sb-x: 1349px" ' \
           'title="Reset zoom" aria-label="Reset zoom">100%</button>' in HTML


def test_zoom_command_applyzoom_wired():
    """editor.js defines applyZoom() with clamp, label and persistence."""
    assert "function applyZoom()" in JS, "editor.js missing applyZoom()"
    assert 'localStorage.getItem("wo-zoom")' in JS, "zoom not persisted/restored"
    assert "Math.min(2, Math.max(0.5, zoomLevel))" in JS, \
        "zoom not clamped to 0.5..2.0 (# NOTE: existing behaviour — hard clamp)"
    assert 'editor.style.zoom = String(zoomLevel)' in JS, \
        "zoom not applied as CSS zoom on the editing surface"
    assert 'Math.round(zoomLevel * 100) + "%"' in JS, \
        "reset button label does not show live percentage"
    assert 'localStorage.setItem("wo-zoom", String(zoomLevel))' in JS, \
        "zoom level not persisted"


def test_zoom_buttons_hooked_to_applyzoom():
    """Each zoom button must be bound to a click handler that mutates zoom."""
    for btn_id, delta in [("btn-zoom-in", "zoomLevel += 0.1"),
                          ("btn-zoom-out", "zoomLevel -= 0.1"),
                          ("btn-zoom-reset", "zoomLevel = 1")]:
        assert f'getElementById("{btn_id}")' in JS, \
            f"editor.js does not reference {btn_id}"
        assert delta in JS, f"editor.js missing zoom step/reset for {btn_id}"
        assert "applyZoom()" in JS


# ---------------------------------------------------------------------------
# F-112 Full screen
# ---------------------------------------------------------------------------

def test_fullscreen_surface_present():
    assert 'id="btn-fullscreen"' in HTML, "missing #btn-fullscreen in index.html"
    assert "Toggle fullscreen" in HTML, "#btn-fullscreen not labelled as toggle"


def test_fullscreen_wiring():
    """toggleFullscreen() toggles body.fullscreen and tries the browser API."""
    assert "function toggleFullscreen()" in JS, "editor.js missing toggleFullscreen()"
    assert 'document.body.classList.toggle("fullscreen")' in JS, \
        "fullscreen class not toggled on <body>"
    assert "document.documentElement.requestFullscreen" in JS, \
        "no requestFullscreen attempt"
    assert "document.exitFullscreen" in JS, "no exitFullscreen handling"
    assert 'getElementById("btn-fullscreen")' in JS, \
        "editor.js does not reference btn-fullscreen"
    assert 'addEventListener("click", toggleFullscreen)' in JS, \
        "btn-fullscreen not bound to toggleFullscreen"


def test_fullscreen_css_expands_surface():
    """body.fullscreen rules must expand the editing surface to the viewport."""
    assert "body.fullscreen { overflow: hidden; }" in CSS, \
        "missing body.fullscreen overflow rule"
    assert "body.fullscreen main," in CSS, "missing fullscreen main selector"
    assert "body.fullscreen #editor {" in CSS, "missing fullscreen #editor selector"
    assert "position: fixed;" in CSS, "fullscreen surface not fixed-positioned"
    assert "inset: 48px 0 0 0;" in CSS, "fullscreen surface not full-bleed below toolbar"


# ---------------------------------------------------------------------------
# F-114 Dark interface theme
# ---------------------------------------------------------------------------

def test_theme_surface_present():
    assert 'id="btn-theme"' in HTML, "missing #btn-theme in index.html"
    assert "Toggle dark/light theme" in HTML, \
        "#btn-theme not labelled as dark/light toggle"
    assert 'aria-pressed="false"' in HTML, \
        "theme button initial state not unpressed (light default)"
    # the shell advertises the light theme-color until the theme is toggled
    assert 'name="theme-color" content="#f2f2f2"' in HTML, \
        "no light theme-color meta for the default state"


def test_theme_command_wired():
    """applyTheme()/toggleTheme() flip html.light and persist the choice."""
    assert "function applyTheme()" in JS, "editor.js missing applyTheme()"
    assert "function toggleTheme()" in JS, "editor.js missing toggleTheme()"
    assert 'localStorage.getItem("wo-theme")' in JS, "theme not persisted/restored"
    assert 'localStorage.setItem("wo-theme",' in JS, "theme choice not stored"
    assert 'document.documentElement.classList.toggle("light", !dark)' in JS, \
        "html.light class not toggled by theme state"
    assert 'setAttribute("aria-pressed", String(dark))' in JS, \
        "theme button pressed state not kept in sync"
    assert 'getElementById("btn-theme")' in JS, \
        "editor.js does not reference btn-theme"
    assert 'addEventListener("click", toggleTheme)' in JS, \
        "btn-theme not bound to toggleTheme"


def test_theme_css_dark_and_light_palettes():
    """Light is the default :root-override palette (OO-golden-matched);
    dark comes back via localStorage 'wo-theme'="dark" + html class removal."""
    assert ":root {" in CSS, "missing :root block"
    assert "--bg: #1e1e28" in CSS, "missing dark default background"
    assert "html.light {" in CSS, "missing html.light theme override"
    assert "--bg: #f3f3f3" in CSS, "missing light background override (OO chrome)"
    # the body and editor must consume the themed variables, not hard-coded colors
    assert "background: var(--bg)" in CSS, "body/background not themed via var(--bg)"
    assert "--paper" in CSS and "--ink" in CSS, \
        "paper/ink variables (used by #editor) missing from theme palette"
