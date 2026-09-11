"""TDD test for File-Menu UI (editor-cloud-ui T6).

feature register: F-005 F-078 F-079 (menu surface wiring)

RED: fails until index.html has a file menu (New/Open/Export/Print) and
editor.js wires the commands. This is an executable smoke test because no
JS test runner (Playwright/jsdom) is available in this environment.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")


def test_file_menu_present_in_html():
    for el in ["btn-new", "btn-open", "btn-export", "btn-print"]:
        assert f'id="{el}"' in HTML, f"missing #{el} in index.html"


def test_export_submenu_formats_present():
    for fmt in ["pdf", "odt", "html", "docx"]:
        assert f'data-export="{fmt}"' in HTML, f"missing export format {fmt}"


def test_editor_js_wires_file_commands():
    assert "doNewDocument" in JS, "editor.js missing doNewDocument"
    assert "doExport" in JS, "editor.js missing doExport"
    assert "doPrint" in JS, "editor.js missing doPrint"
    # each command must be hooked to a DOM element
    assert "btn-new" in JS, "editor.js does not reference btn-new"
    assert "btn-export" in JS, "editor.js does not reference btn-export"
    assert "btn-print" in JS, "editor.js does not reference btn-print"


def test_inline_text_format_buttons_present():
    """code / small-caps / all-caps toolbar commands exist (T6 UI)."""
    for cmd in ["strikeThrough", "smallCaps", "allCaps", "code",
                "superscript", "subscript"]:
        assert f'data-cmd="{cmd}"' in HTML, f"missing button data-cmd={cmd} in index.html"
    for fn in ["toggleInlineCSS", "toggleMonospace", "fontIsMono", "spanStyleActive"]:
        assert fn in JS, f"editor.js missing {fn}"


def test_paragraph_format_commands_present():
    """RTL button + block-level paragraph commands exist (T8 UI)."""
    assert 'data-cmd="directionRtl"' in HTML
    assert "toggleBlockDirection" in JS
    assert "applyLineHeight" in JS


def test_insert_date_button_wired():
    """Date/time insert button + command exist (insert parity T27)."""
    assert 'id="btn-datetime"' in HTML
    assert "insertDate" in JS
    assert 'id="btn-hr"' in HTML


def test_image_resize_fields_wired():
    """Image dialog exposes width/height resize inputs wired into confirm."""
    assert 'id="image-width"' in HTML and 'id="image-height"' in HTML
    assert 'id="image-size-fields"' in HTML
    assert "dims.push(" in JS  # confirmImageDialog attaches width attr
    assert "Image.Width" in I18N and "Image.Height" in I18N


def test_version_history_wired():
    """Version history: menu entry, dialog, list+restore logic, i18n (T30)."""
    assert 'id="btn-history"' in HTML, "File menu missing History entry"
    assert 'id="version-history-dialog"' in HTML, "missing version-history dialog"
    assert 'id="version-list"' in HTML and 'id="version-error"' in HTML
    for fn in ["openVersionHistory", "closeVersionHistory", "restoreVersion",
               "renderVersionList", "formatVersionDate"]:
        assert fn in JS, f"editor.js missing {fn}"
    assert 'api("versions")' in JS, "version list must call the versions endpoint"
    assert 'api("versions/"' in JS, "restore must call the restore endpoint"
    for key in ["VersionHistory.Title", "VersionHistory.Restore",
                "VersionHistory.Current", "VersionHistory.Empty"]:
        assert key in JS, f"editor.js missing i18n key {key}"


def test_insert_menu_buttons_present_and_wired():
    """Insert-surface buttons exist and emit their commands (F-078/F-079)."""
    for el in ["btn-symbol", "btn-datetime"]:
        assert f'id="{el}"' in HTML, f"missing #{el} in index.html"
        assert el in JS, f"editor.js does not reference {el}"
    assert 'cmd === "insertSymbol"' in JS or "insertSymbol" in JS
    assert "insertDate" in JS


# ---------------------------------------------------------------------------
# Right-click context menu (OO parity surface)
# ---------------------------------------------------------------------------

def test_context_menu_present_in_html():
    """#ctx-menu exists with the honest OO-subset item set, in OO order."""
    assert 'id="ctx-menu"' in HTML, "missing #ctx-menu in index.html"
    order = ["cut", "copy", "paste", "pagebreak", "comment", "link"]
    positions = [HTML.index(f'data-ctx="{a}"') for a in order]
    assert positions == sorted(positions), "context menu items out of OO order"
    for key in ["Ctx.Cut", "Ctx.Copy", "Ctx.Paste", "Ctx.PageBreakBefore",
                "Ctx.AddComment", "Ctx.Link"]:
        assert key in I18N, f"i18n.js missing {key} (en)"
        assert key in HTML, f"index.html missing data-i18n {key}"


def test_context_menu_wired_in_editor_js():
    """contextmenu opens + positions the menu; each action runs a real command."""
    assert 'editorEl.addEventListener("contextmenu"' in JS, \
        "editor.js must intercept contextmenu on #editor"
    assert 'e.preventDefault();' in JS
    assert "document.execCommand(action)" in JS, "cut/copy must run execCommand"
    assert 'navigator.clipboard.readText()' in JS, "paste must read the clipboard"
    assert 'emitCommand("insertPageBreak")' in JS
    assert "openCommentDialog()" in JS and "insertLink()" in JS
    assert 'ctxMenu.addEventListener("mousedown", (e) => e.preventDefault())' in JS, \
        "menu presses must not clear the editor selection (round-10 doctrine)"
    assert 'if (e.key === "Escape") closeCtx()' in JS


def test_context_menu_css_matches_oo_geometry():
    """OO golden: 210px panel, 26px rows, label column at +38px."""
    assert "#ctx-menu" in CSS
    assert "width: 210px" in CSS
    assert "position: fixed" in CSS
    assert "padding: 5px 14px 5px 38px" in CSS, "label column must sit at OO's +38px"
    assert "#ctx-menu[hidden] { display: none; }" in CSS


def test_note_pagefield_headerfooter_authoring_buttons():
    """F-073 F-074 F-084 F-085: authoring UI for the note / PAGE-field /
    header-footer contracts the converters already round-trip. The buttons
    must exist on the Insert page, wire to real commands, and emit the
    exact HTML contracts the converter parses."""
    html = HTML
    js = JS
    i18n = I18N
    for el in ["btn-footnote", "btn-endnote", "btn-pagenumber", "btn-header", "btn-footer"]:
        assert f'id="{el}"' in html, f"missing authoring button {el}"
    for cmd in ["insertFootnote", "insertEndnote", "insertPageNumber", "insertHeader", "insertFooter"]:
        assert f'"{cmd}"' in js, f"command {cmd} missing in editor.js"
    # exact serialization contracts (converter.py / odt_converter.py):
    # classes are built from the footnote/endnote ternary + '-citation'
    assert '"footnote" : "endnote"' in js and '-citation">[1]</sup>' in js
    assert 'class="page-number"' in js
    assert 'page-header' in js and 'page-footer' in js
    # header/footer: one per document, focused on re-press (selector built
    # from tag + '.page-' + tag)
    assert ':scope > ' in js and 'page-header' in js and 'page-footer' in js
    for key in ["Toolbar.FootnoteTitle", "Toolbar.EndnoteTitle", "Toolbar.PageNumberTitle", "Toolbar.HeaderTitle", "Toolbar.FooterTitle"]:
        assert key in i18n, f"missing i18n key {key}"


def test_oo_tab_strip_matches_golden():
    """The tab strip mirrors OO's build: same tabs, same order, same golden
    label centers (--qa-x). Review is gone — OO covers it with Collaboration."""
    for tab, x in [("home", "92"), ("insert", "150"), ("draw", "205"),
                   ("layout", "264"), ("references", "340"),
                   ("collaboration", "436"), ("protection", "528"),
                   ("view", "594"), ("plugins", "654"), ("ai", "705")]:
        assert f'data-tab="{tab}"' in HTML, f"missing tab {tab}"
        assert f'--qa-x: {x}px' in HTML, f"tab {tab} not at golden center {x}"


def test_real_features_live_on_oo_tabs():
    """Moved features: TOC/footnote/endnote on References, review controls on
    Collaboration — same element ids (tests and commands are the contract)."""
    refs = HTML.split('class="ribbon-page" data-tab="references"')[1].split("</div>")[0]
    for el in ["btn-toc", "btn-footnote", "btn-endnote"]:
        assert f'id="{el}"' in refs, f"{el} must be on the References tab"
    collab = HTML.split('class="ribbon-page" data-tab="collaboration"')[1].split("</div>")[0]
    for el in ["btn-track-changes", "btn-review-changes", "btn-comment", "btn-comments"]:
        assert f'id="{el}"' in collab, f"{el} must be on the Collaboration tab"
    assert 'data-tab="review"' not in HTML, "Review tab dissolved into Collaboration"


def test_stub_buttons_are_loud_and_documented():
    """Every OO-parity stub carries data-stub="<ref>" and the generic handler
    reports it via setStatus — no silent no-op buttons. The refs ARE the
    iteration backlog; promoting one = id + handler + drop the attribute."""
    import re
    stubs = re.findall(r'data-stub="([^"]+)"', HTML)
    # Registry floor = the ledger's live stub count (15 after plugins.browse/
    # plugins.manage promoted to real buttons in WO-FEA-PLUG-0) + the "<ref>"
    # documentation example; drops below the known inventory mean silent stub
    # removal without a promotion — the reconcile CI gate re-asserts this via
    # the census (stubs 15, ledger stub 0).
    assert len(stubs) >= 16, f"expected the full stub registry, got {len(stubs)}"
    assert len(stubs) == len(set(stubs)), "duplicate stub ref"
    assert 'button[data-stub]' in JS, "generic stub handler missing"
    assert "setStatus" in JS
    assert '"Stub.NotImplemented"' in I18N


def test_view_tab_real_controls_wired():
    """View tab reuses the single-source toggles + adds a real ruler toggle."""
    for el in ["btn-ruler-toggle", "btn-view-fullscreen", "btn-view-theme", "btn-view-fit", "btn-ai-review-tab"]:
        assert f'id="{el}"' in HTML
        assert f'"{el}"' in JS, f"{el} not wired in editor.js"
    assert '.querySelector(".ruler")' in JS


def test_header_footer_contextual_tab():
    """OO parity: double-clicking the page header/footer reveals a contextual
    Header & Footer tab; it stays hidden until then. Close button + real
    page-number/date commands + documented stubs for OO's options."""
    import re
    m = re.search(r'<button[^>]*data-tab="header-footer"[^>]*>', HTML)
    assert m and " hidden" in m.group(0), "H&F tab must exist and be hidden by default"
    page = HTML.split('class="ribbon-page" data-tab="header-footer"')[1].split("</div>")[0]
    for el in ("btn-hf-close", "btn-hf-pagenumber", "btn-hf-datetime"):
        assert f'id="{el}"' in page, f"{el} must be on the H&F tab"
    # R5: the four option stubs became real section-marker commands.
    for cmd in ("toggleDifferentFirst", "toggleOddEven",
                "toggleHeaderFromTop", "toggleFooterFromBottom"):
        assert f'data-cmd="{cmd}"' in page, f"{cmd} must be a real command on the H&F tab"
    for anchor in ("enterHFMode", "exitHFMode", '"btn-hf-close"', 'dblclick', '.page-header',
                   "toggleDifferentFirst", "toggleOddEven",
                   "toggleHeaderFromTop", "toggleFooterFromBottom"):
        assert anchor in JS, f"H&F wiring missing: {anchor}"
    assert '"Tab.HeaderFooter"' in I18N and '"HF.Close"' in I18N
