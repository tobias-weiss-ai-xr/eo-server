"""TDD test for Plugin Host UI (plugins.browse / plugins.manage).

feature register: F-xxx (plugin host surface)

RED: fails until index.html has real plugin buttons and
editor.js wires the commands and router.py exposes the registry API.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
ROUTER = (WEB.parent / "src" / "editor" / "router.py").read_text(encoding="utf-8")


def test_plugins_tab_buttons_have_real_commands():
    """Plugin buttons are real (data-cmd), not stubs (data-stub)."""
    assert 'data-cmd="browsePlugins"' in HTML, "browsePlugins missing data-cmd"
    assert 'data-cmd="managePlugins"' in HTML, "managePlugins missing data-cmd"
    assert 'data-stub="plugins.browse"' not in HTML, "browsePlugins should not be a stub"
    assert 'data-stub="plugins.manage"' not in HTML, "managePlugins should not be a stub"


def test_plugins_i18n_keys_present():
    """Plugin dialog keys exist in i18n catalog."""
    assert '"Plugins.Browse"' in I18N, "missing Plugins.Browse translation key"
    assert '"Plugins.Manage"' in I18N, "missing Plugins.Manage translation key"


def test_editor_js_wires_plugin_commands():
    """Plugin commands are wired to handler functions."""
    assert "browsePlugins" in JS, "editor.js does not reference browsePlugins"
    assert "managePlugins" in JS, "editor.js does not reference managePlugins"
    assert "function browsePlugins" in JS, "editor.js missing function browsePlugins"
    assert "function managePlugins" in JS, "editor.js missing function managePlugins"


def test_router_exposes_plugins_registry():
    """Plugin registry API endpoint exists AND ships the catalog."""
    assert '"/api/plugins"' in ROUTER, "missing /api/plugins route in router.py"
    assert "list_plugins" in ROUTER, "missing list_plugins function in router.py"
    assert '"id": "ocr"' in ROUTER, "registry must list the ocr plugin"
    assert '"id": "photoeditor"' in ROUTER, "registry must list the photoeditor plugin"


def test_plugins_handlers_open_real_dialog():
    """Plugin handlers open a real dialog listing the registry — a status
    message is NOT a real handler (loud-stub doctrine)."""
    # The dialog and list must exist in the DOM.
    assert 'id="plugins-dialog"' in HTML, "missing #plugins-dialog"
    assert 'id="plugins-list"' in HTML, "missing #plugins-list"
    # Handlers must fetch the registry and open the dialog.
    assert 'fetch("/api/plugins")' in JS, "browse/manage must fetch the registry"
    assert '_renderPluginsDialog' in JS, "missing dialog renderer"
    assert 'classList.add("open")' in JS.split("async function browsePlugins")[1], \
        "browsePlugins must open the dialog"
    # The old status-stub messages must be gone.
    assert 'Browse plugins dialog would open here' not in JS
    assert 'Manage plugins dialog would open here' not in JS