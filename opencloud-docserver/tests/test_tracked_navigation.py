"""Wiring test for tracked-change prev/next navigation (collab.prev-change /
collab.next-change).

feature register: collaboration engine — tracked-change navigation.

These are static smoke tests (no JS runner in this env): they assert the
buttons are real (data-cmd, not data-stub), the named handlers exist and are
dispatched from runCommand, the navigation walks the track-change spans, and
the i18n status keys resolve. Mirrors tests/test_file_menu.py.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")


def test_buttons_promoted_from_stub_to_real_cmd():
    """Prev/next change buttons ship data-cmd, not data-stub (loud, not silent)."""
    assert 'data-cmd="prevTrackedChange"' in HTML, "prevTrackedChange button missing data-cmd"
    assert 'data-cmd="nextTrackedChange"' in HTML, "nextTrackedChange button missing data-cmd"
    assert 'data-stub="collab.prev-change"' not in HTML, "prev button still a silent stub"
    assert 'data-stub="collab.next-change"' not in HTML, "next button still a silent stub"


def test_handlers_declared_and_dispatched():
    """Named handlers exist and runCommand routes both commands to them."""
    assert "function prevTrackedChange" in JS, "editor.js missing function prevTrackedChange"
    assert "function nextTrackedChange" in JS, "editor.js missing function nextTrackedChange"
    assert 'cmd === "prevTrackedChange"' in JS, "runCommand does not dispatch prevTrackedChange"
    assert 'cmd === "nextTrackedChange"' in JS, "runCommand does not dispatch nextTrackedChange"


def test_navigation_walks_track_change_spans():
    """Navigation targets the ins.track-insert / del.track-delete spans."""
    assert "ins.track-insert, del.track-delete" in JS, \
        "navigation must select the track-change spans"
    assert "navigateTrackedChange" in JS, "missing shared navigateTrackedChange helper"
    assert "scrollIntoView" in JS, "navigation must scroll the change into view"
    assert "nav-flash" in JS, "navigation must flash the target (nav-panel precedent)"
    assert "selectNodeContents" in JS, "navigation must select the change contents"


def test_navigation_is_loud_not_silent():
    """Empty / edge cases report via setStatus — never a silent no-op."""
    assert 'setStatus(t("Status.NoTrackedChanges"))' in JS
    assert 'setStatus(t("Status.NoPrevChange"))' in JS
    assert 'setStatus(t("Status.NoNextChange"))' in JS
    # success path reports the direction (reuses the existing button labels)
    assert 't("Collab.NextChange")' in JS
    assert 't("Collab.PrevChange")' in JS


def test_i18n_keys_present():
    """Status + button labels resolve in both catalogs."""
    for key in ["Collab.PrevChange", "Collab.NextChange",
                "Status.NoTrackedChanges", "Status.NoPrevChange",
                "Status.NoNextChange"]:
        assert key in I18N, f"i18n.js missing key {key}"
