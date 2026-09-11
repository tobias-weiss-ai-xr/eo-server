"""TDD test for chat panel UI (collab.chat feature).

feature register: WO-FEA-CB-1

Tests the chat side panel toggle wiring: button, panel HTML, JS function,
and i18n keys similar to the nav-panel pattern.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")


def test_chat_button_present_in_html():
    """Chat button exists on Collaboration tab with data-cmd wiring."""
    assert 'id="btn-chat-toggle"' in HTML, "missing #btn-chat-toggle in index.html"
    assert 'data-cmd="toggleChat"' in HTML, "missing data-cmd=\"toggleChat\" in index.html"
    assert 'data-i18n="Collab.Chat"' in HTML, "missing i18n key Collab.Chat on chat button"


def test_chat_button_no_stub():
    """Chat button must be real (no data-stub attribute)."""
    import re
    # Check that the chat button doesn't have data-stub
    collab_section = HTML.split('class="ribbon-page" data-tab="collaboration"')[1].split("</div>")[0]
    chat_btn_match = re.search(r'<button[^>]*data-i18n="Collab\.Chat"[^>]*>', collab_section)
    assert chat_btn_match, "Chat button with Collab.Chat not found in collaboration tab"
    assert 'data-stub' not in chat_btn_match.group(0), "Chat button should not have data-stub"


def test_chat_panel_present_in_html():
    """Chat panel element exists in HTML."""
    assert 'id="chat-panel"' in HTML, "missing #chat-panel in index.html"
    assert 'class="chat-panel"' in HTML, "missing class chat-panel"
    assert 'hidden' in HTML[HTML.index('id="chat-panel"'):HTML.index('id="chat-panel"') + 100], \
        "chat-panel should be hidden by default"


def test_chat_panel_structure():
    """Chat panel has title and message list structure."""
    assert 'chat-panel-title' in HTML, "missing chat panel title element"
    assert 'chat-panel-list' in HTML, "missing chat panel message list"


def test_editor_js_wires_chat_commands():
    """editor.js has the toggleChat function and wiring."""
    assert 'function toggleChat' in JS, "editor.js missing function toggleChat"
    assert 'btn-chat-toggle' in JS, "editor.js does not reference btn-chat-toggle"


def test_chat_panel_css_exists():
    """style.css has styles for chat panel."""
    assert '#chat-panel' in CSS or '.chat-panel' in CSS, "missing chat-panel styles in CSS"


def test_chat_i18n_keys_exist():
    """i18n.js has chat panel translation keys."""
    assert '"Collab.Chat"' in I18N, "missing Collab.Chat in i18n.js"
    assert '"ChatPanel.Title"' in I18N, "missing ChatPanel.Title in i18n.js"


def test_runCommand_handles_toggleChat():
    """runCommand has a case for toggleChat command."""
    assert 'cmd === "toggleChat"' in JS, "runCommand does not handle toggleChat"
    assert 'toggleChat()' in JS, "toggleChat not called in runCommand"


def test_chat_panel_setStatus():
    """toggleChat function calls setStatus."""
    # Find the toggleChat function and check it calls setStatus
    toggle_start = JS.index('function toggleChat')
    # Find the matching closing brace by counting braces
    brace_count = 0
    toggle_end = toggle_start
    for i in range(toggle_start, len(JS)):
        if JS[i] == '{':
            brace_count += 1
        elif JS[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                toggle_end = i + 1
                break
    toggle_code = JS[toggle_start:toggle_end]
    assert 'setStatus' in toggle_code, "toggleChat should call setStatus"
