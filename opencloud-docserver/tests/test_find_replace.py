"""
feature register: F-062 F-063 F-064

Tests for Find/Replace, Rich Paste, and Keyboard Shortcuts.
Since these are primarily client-side JS implementations in editor.js,
this test verifies the presence of the necessary UI elements and JS hooks.
"""

import pytest
from src.main import app
from fastapi.testclient import TestClient
from types import SimpleNamespace

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_app_state():
    """Mock the app state config, store, and sessions required by the editor router."""
    app.state.config = SimpleNamespace(wopi_host="http://localhost:8080", public_url="http://localhost:3000")
    app.state.store = SimpleNamespace(get=lambda id: {"name": "Test Doc"} if id else None)
    app.state.sessions = SimpleNamespace(get=lambda id: None)

def test_find_replace_ui_presence():

    """F-062: Verify Find and Replace UI elements exist in index.html"""
    response = client.get("/editor")
    html = response.text
    
    assert 'id="btn-find"' in html
    assert 'id="btn-find-next"' in html
    assert 'id="btn-find-prev"' in html
    assert 'id="btn-find-replace"' in html
    assert 'id="btn-find-replace-all"' in html

def test_rich_paste_hook_presence():
    """F-063: Verify paste handling exists in editor.js"""
    response = client.get("/static/editor.js")
    js = response.text
    
    # Verify that 'insertFromPaste' is handled
    assert 'insertFromPaste' in js
    # Verify it currently only handles text/plain (proving partial parity)
    assert 'getData("text/plain")' in js

def test_shortcuts_hooks_presence():
    """F-064: Verify core keyboard shortcuts are implemented in editor.js"""
    response = client.get("/static/editor.js")
    js = response.text
    
    # Ctrl+F
    assert 'ev.key.toLowerCase() === "f"' in js
    # Ctrl+S
    assert 'k === "s"' in js
    # Ctrl+Z / Ctrl+Y
    assert 'k === "z"' in js
    assert 'k === "y"' in js
    # Headings (Ctrl+Alt+1-3)
    assert 'ev.altKey && /^[0-3]$/.test(ev.key)' in js
    # Lists (Ctrl+Shift+7/8)
    assert 'ev.code === "Digit7" || ev.code === "Digit8"' in js


def test_find_panel_oo_anatomy():
    """Find surface is an OO-style floating top-right panel, not a modal."""
    html = client.get("/editor").text
    assert 'class="dialog-overlay find-overlay"' in html, \
        "find-dialog must carry the find-overlay panel class"
    assert 'aria-modal="true"' not in html.split('id="find-dialog"')[1][:400], \
        "find panel is non-modal (document stays interactive)"
    assert 'find-title' in html  # visually hidden a11y title
    assert 'btn-find-close' in html
    css = ""
    import re
    m = re.search(r'<style>(.*?)</style>', html, re.S)
    assert m, "inline style block missing"
    css = m.group(1)
    assert "pointer-events: none" in css and "#find-dialog" in css
    assert "width: 234px" in css, "panel width must match OO's 234px"
    assert "height: 28px" in css, "input row must match OO's 28px"
    # old modal anatomy must be gone
    assert "width: 380px" not in css
