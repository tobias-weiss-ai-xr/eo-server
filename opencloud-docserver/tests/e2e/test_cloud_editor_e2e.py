"""End-to-end cloud editor test (T5).

Drives the REAL editor in a real browser (Playwright/chromium) through a mock
OpenCloud/Nextcloud WOPI host:

  * the editor loads a DOCX from the mock host (client / WOPI mode);
  * TWO browser sessions edit the SAME document and the characters converge
    in real time (server-side character CRDT + poll push);
  * a save forwards the converted bytes back to the mock host (open -> edit
    -> save -> host loop proven in a browser, not just at the API level);
  * the editor notifies its embedding host via postMessage (woopi bridge);
  * Insert > page break via the toolbar button lands in the saved document.

feature register: F-076 (page break surface + serialization)

Run: pytest tests/e2e/test_cloud_editor_e2e.py
"""

from __future__ import annotations

import base64
import io
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import uvicorn
from docx import Document
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.config import Config
from src.editor.converter import docx_to_html
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore
from src.wopi.testhost import app as mock_host_app
from src.wopi.testhost import reset_store

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_app(tmp_path: Path) -> FastAPI:
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")
    store = DocumentStore(db, content)
    cfg = Config(database=db, content_dir=content, jwt_secret="test-secret")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.sessions = SessionRegistry()
        app.state.config = cfg
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(editor_router)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    return app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# A trivial host page that embeds the editor in an <iframe> and records every
# postMessage the editor sends upward — this is how OpenCloud/Nextcloud would
# receive the "woopi" bridge messages.
PARENT_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<iframe id="ed" src="__EDITOR_URL__" style="width:100%;height:600px;border:0"></iframe>
<script>
  window.__msgs = [];
  window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'woopi') window.__msgs.push(e.data);
  });
</script>
</body></html>"""

parent_app = FastAPI()


@parent_app.get("/")
async def parent_index(request: Request) -> HTMLResponse:
    editor_url = request.query_params.get("editor", "")
    return HTMLResponse(PARENT_HTML.replace("__EDITOR_URL__", editor_url))


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    doc_port = _free_port()
    host_port = _free_port()
    parent_port = _free_port()

    doc_srv = uvicorn.Server(uvicorn.Config(_make_app(tmp), host="127.0.0.1", port=doc_port, log_level="error"))
    host_srv = uvicorn.Server(uvicorn.Config(mock_host_app, host="127.0.0.1", port=host_port, log_level="error"))
    parent_srv = uvicorn.Server(uvicorn.Config(parent_app, host="127.0.0.1", port=parent_port, log_level="error"))

    for s in (doc_srv, host_srv, parent_srv):
        threading.Thread(target=s.run, daemon=True).start()

    for port in (doc_port, host_port, parent_port):
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.05)

    reset_store()
    seed = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{host_port}/_host/files",
                data=json.dumps(
                    {"name": "e2e.docx", "data": base64.b64encode(_docx_bytes("E2E base text")).decode()}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=10,
        ).read()
    )
    yield {
        "doc_port": doc_port,
        "host_port": host_port,
        "parent_port": parent_port,
        "doc_id": seed["id"],
        "token": seed["access_token"],
    }
    doc_srv.should_exit = True
    host_srv.should_exit = True
    parent_srv.should_exit = True


def _editor_url(servers: dict, seed: dict | None = None) -> str:
    seed = seed or servers
    wopi_src = f"http://127.0.0.1:{servers['host_port']}/wopi/files/{seed['doc_id']}"
    return (
        f"http://127.0.0.1:{servers['doc_port']}/editor/{seed['doc_id']}"
        f"?access_token={seed['token']}&WOPISrc={urllib.parse.quote(wopi_src, safe='')}"
    )


def _parent_url(servers: dict, seed: dict | None = None, extra: str = "") -> str:
    # `extra` rides on the editor iframe URL (e.g. "&record=1" for the
    # command recorder) — the parent page only forwards ?editor=.
    return f"http://127.0.0.1:{servers['parent_port']}/?editor={urllib.parse.quote(_editor_url(servers, seed) + extra, safe='')}"


def _seed_doc(servers: dict, name: str = "t.docx", text: str = "E2E base text") -> dict:
    """Create a fresh document in the mock host and return its seed."""
    resp = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{servers['host_port']}/_host/files",
                data=json.dumps(
                    {"name": name, "data": base64.b64encode(_docx_bytes(text)).decode()}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=10,
        ).read()
    )
    return {"doc_id": resp["id"], "token": resp["access_token"]}


def _frame_text(frame) -> str:
    return frame.locator("#editor").inner_text()


def _open_ribbon_tab(frame, tab: str) -> None:
    """Bring a ribbon tab's controls on-screen (idempotent).

    DOM click, not a hit-tested one: the ACTIVE page's controls legitimately
    overlap the right-hand tab strip (pre-existing layout; tabs there stay
    clickable at their visible edge for humans, but Playwright's actionability
    check rejects the covered center)."""
    frame.evaluate(
        "t => { const el = document.querySelector(`.ribbon-tab[data-tab='${t}']`);"
        " if (el) el.click(); }",
        tab,
    )


def _post_sync(servers: dict, seed: dict, text: str) -> None:
    urllib.request.urlopen(
        urllib.request.Request(
            f"http://127.0.0.1:{servers['doc_port']}/api/documents/{seed['doc_id']}/collab/sync",
            data=json.dumps({"client_id": "probe", "text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=10,
    ).read()


def _wait(predicate, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise AssertionError(f"condition not met within {timeout}s")


def _host_text(servers: dict, seed: dict | None = None) -> str:
    seed = seed or servers
    url = (
        f"http://127.0.0.1:{servers['host_port']}/wopi/files/{seed['doc_id']}/contents"
        f"?access_token={seed['token']}"
    )
    data = urllib.request.urlopen(url, timeout=10).read()
    return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)


def _host_html(servers: dict, seed: dict | None = None) -> str:
    """Convert the host DOCX back to HTML so markup (color, <sup>) is visible."""
    seed = seed or servers
    url = (
        f"http://127.0.0.1:{servers['host_port']}/wopi/files/{seed['doc_id']}/contents"
        f"?access_token={seed['token']}"
    )
    data = urllib.request.urlopen(url, timeout=10).read()
    return docx_to_html(data)


def test_two_users_collaborate_save_and_notify_host(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "collab.docx")
            ctx_a = browser.new_context()
            ctx_b = browser.new_context()
            parent_a = ctx_a.new_page()
            parent_b = ctx_b.new_page()

            errors = []
            parent_a.on("pageerror", lambda e: errors.append(str(e)))

            parent_a.goto(_parent_url(servers, seed))
            parent_b.goto(_parent_url(servers, seed))

            frame_a = parent_a.frame("ed")
            frame_b = parent_b.frame("ed")
            frame_a.locator("#editor").wait_for(state="visible", timeout=45000)
            frame_b.locator("#editor").wait_for(state="visible", timeout=45000)
            assert "E2E base text" in _frame_text(frame_a)

            # Live push: a hub change converges in BOTH browsers (real-time).
            _post_sync(servers, seed, "LIVEPROBE_X")
            _wait(
                lambda: "LIVEPROBE_X" in _frame_text(frame_a) and "LIVEPROBE_X" in _frame_text(frame_b)
            )

            # Presence: both editors show each other as collaborators (chips),
            # and a remote caret is rendered for the peer.
            assert frame_a.locator("#collab-peers .peer-chip").count() >= 2
            assert frame_b.locator("#collab-peers .peer-chip").count() >= 2
            assert frame_a.locator(".remote-caret").count() >= 1

            # User A types -> User B converges.
            frame_a.locator("#editor").click()
            frame_a.locator("#editor").press("End")
            frame_a.locator("#editor").press_sequentially(" from A")
            _wait(lambda: "from A" in _frame_text(frame_a))
            _wait(lambda: "from A" in _frame_text(frame_b))

            # User B types -> User A converges (bidirectional).
            frame_b.locator("#editor").click()
            frame_b.locator("#editor").press("End")
            frame_b.locator("#editor").press_sequentially(" +B")
            _wait(lambda: "+B" in _frame_text(frame_a))

            # Save -> converted bytes reach the mock WOPI host.
            frame_a.locator("#btn-save").click()
            _wait(lambda: "from A" in _host_text(servers, seed) and "+B" in _host_text(servers, seed))
            host_text = _host_text(servers, seed)
            assert "from A" in host_text and "+B" in host_text

            # Editor notified its embedding host via postMessage (woopi bridge).
            msgs = parent_a.evaluate("window.__msgs")
            assert any(
                m.get("type") == "woopi" and m.get("action") in ("editing", "saved") for m in msgs
            )
        finally:
            ctx_a.close()
            ctx_b.close()
            browser.close()


def _word_count(frame):
    import re
    txt = frame.locator("#word-count").inner_text()
    m = re.search(r"(\d+)", txt)
    return int(m.group(1)) if m else 0


def test_status_bar_word_count_and_save_indicator(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "status.docx")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            # The status bar shows a live word count for the loaded document.
            wc0 = _word_count(frame)
            assert wc0 > 0, "status bar must show a word count"

            # Typing updates the count live.
            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            frame.locator("#editor").press_sequentially(" extra words here")
            _wait(lambda: _word_count(frame) > wc0 + 2)

            # Typing flips the save indicator away from a saved state.
            status0 = frame.locator("#status").inner_text().strip().lower()
            assert status0 != "ready", f"status should show unsaved, got {status0!r}"

            # Saving returns the indicator to saved/ready.
            frame.locator("#btn-save").click()
            _wait(
                lambda: frame.locator("#status").inner_text().strip().lower()
                in ("saved", "ready")
            )
        finally:
            ctx.close()
            browser.close()


def test_view_controls_zoom_theme_fullscreen(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "view.docx")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            # Zoom in scales only the editing surface (inline zoom grows).
            z0 = float(frame.locator("#editor").evaluate("el => parseFloat(el.style.zoom || '1')"))
            frame.locator("#btn-zoom-in").click()
            z1 = float(frame.locator("#editor").evaluate("el => parseFloat(el.style.zoom || '1')"))
            assert z1 > z0, f"zoom should increase: {z0} -> {z1}"
            # Content is unaffected by zoom.
            assert _frame_text(frame).strip()

            # Theme toggle flips the page background colour.
            bg0 = frame.evaluate("getComputedStyle(document.body).backgroundColor")
            frame.locator("#btn-theme").click()
            bg1 = frame.evaluate("getComputedStyle(document.body).backgroundColor")
            assert bg0 != bg1, f"theme should change bg: {bg0} -> {bg1}"

            # Fullscreen toggles the body class (browser Fullscreen API is
            # best-effort; the class drives the layout expansion).
            assert "fullscreen" not in frame.evaluate("document.body.className")
            frame.locator("#btn-fullscreen").click()
            assert "fullscreen" in frame.evaluate("document.body.className")
        finally:
            ctx.close()
            browser.close()


def test_insert_link_roundtrip(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "link.docx")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            frame.locator("#editor").press_sequentially("Visit our site")
            frame.locator("#editor").select_text()  # select all document text
            _open_ribbon_tab(frame, "insert")
            frame.locator("#btn-link").click()
            frame.locator("#link-url").fill("https://example.com")
            frame.locator("#btn-link-ok").click()
            frame.locator("#editor a[href='https://example.com']").wait_for(state="attached", timeout=5000)
            assert frame.locator("#editor a").first.get_attribute("href") == "https://example.com"

            # Save -> the link survives the round-trip back to the host.
            _open_ribbon_tab(frame, "home")
            frame.locator("#btn-save").click()
            _wait(lambda: "example.com" in _host_text(servers, seed))
        finally:
            ctx.close()
            browser.close()


def test_format_color_highlight_superscript(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "color.docx")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            frame.locator("#editor").press_sequentially("Color me x2")

            # Select the word "Color" and apply red via the colour picker.
            frame.locator("#editor").select_text()
            frame.locator("#text-color").fill("#ff0000")
            frame.locator("#text-color").dispatch_event("change")
            # Select the whole contenteditable so superscript applies visibly,
            # then check the styling spans survive.
            frame.locator("#editor").select_text()
            # quick-access duplicate: pick the app-row one deterministically
            frame.locator("button[data-cmd='superscript']").first.click()

            # The styling is visible in the DOM.
            colored = frame.locator("#editor span[style*='color']").count()
            assert colored >= 1, "expected a colored span in the editor"
            assert frame.locator("#editor sup").count() >= 1, "expected sup in the editor"

            # Save -> the styling persists back to the host bytes.
            frame.locator("#btn-save").click()
            _wait(lambda: "ff0000" in _host_html(servers, seed) and "<sup>" in _host_html(servers, seed))
        finally:
            ctx.close()
            browser.close()


def test_table_merge_and_column_ops(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "table.docx")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            _open_ribbon_tab(frame, "insert")
            frame.locator("#btn-table").click()
            frame.locator("#table-rows").fill("2")
            frame.locator("#table-cols").fill("2")
            frame.locator("#btn-table-ok").click()
            frame.locator("#editor table").wait_for(state="attached", timeout=5000)
            assert frame.locator("#editor table tr").count() == 2

            # Select from the 1st cell of row 1 to the 2nd cell, then merge.
            frame.evaluate("""() => {
              const rows = document.querySelectorAll('#editor table tr');
              const c0 = rows[0].cells[0];
              const c1 = rows[0].cells[1];
              const endNode = (c1.firstChild && c1.firstChild.nodeType === 3)
                ? c1.firstChild : c1;
              const r = document.createRange();
              r.setStart(c0.firstChild || c0, 0);
              r.setEnd(endNode, endNode.nodeType === 3 ? endNode.length : 1);
              const s = window.getSelection();
              s.removeAllRanges();
              s.addRange(r);
            }""")
            frame.locator("#btn-table-ops").click()
            frame.locator("#op-merge").click()
            merged = frame.locator("#editor tr:first-child td").first
            merged.wait_for(state="attached", timeout=5000)
            colspan = merged.get_attribute("colspan")
            assert colspan == "2", f"expected merged colspan=2, got {colspan}"

            # Delete the 2nd column: click its cell in the second row, then act.
            frame.locator("#editor tr").nth(1).locator("td").nth(1).click()
            frame.locator("#btn-table-ops").click()
            frame.locator("#op-del-col").click()
            assert frame.locator("#editor tr").nth(1).locator("td").count() == 1

            # Save -> the merged colspan survives round-trip to the host.
            _open_ribbon_tab(frame, "home")
            frame.locator("#btn-save").click()
            _wait(lambda: "colspan" in _host_html(servers, seed).lower())
        finally:
            ctx.close()
            browser.close()


def test_insert_hr_pagebreak_symbol(servers):
    """Insert HR, page break and a symbol; all survive save via the host."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "insert-misc.docx")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            frame.locator("#editor").click()
            frame.locator("#editor").press("End")

            # Horizontal rule.
            _open_ribbon_tab(frame, "insert")
            frame.locator(".ribbon-tab[data-tab='insert']").click()
            frame.locator("#btn-hr").click()
            frame.locator("#editor hr").wait_for(state="attached", timeout=5000)
            assert frame.locator("#editor hr").count() == 1

            # Page break marker.
            _open_ribbon_tab(frame, "layout")
            frame.locator(".ribbon-tab[data-tab='layout']").click()
            frame.locator("#btn-page-break").click()
            frame.locator("#editor div.page-break").wait_for(state="attached", timeout=5000)
            assert frame.locator("#editor div.page-break").count() == 1

            # Symbol picker -> first symbol (§) inserted as text.
            _open_ribbon_tab(frame, "insert")
            frame.locator("#btn-symbol").click()
            frame.locator("#symbol-dialog .symbol-btn").first.click()
            _wait(lambda: "§" in _frame_text(frame))

            # Date/time insert -> ISO date appears as text.
            frame.locator("#btn-datetime").click()
            _wait(lambda: "2026-" in _frame_text(frame))

            # Save -> markers + symbol + date reach the host DOCX.
            _open_ribbon_tab(frame, "home")
            frame.locator("#btn-save").click()
            _wait(lambda: (
                "<hr" in _host_html(servers, seed)
                and "page-break" in _host_html(servers, seed)
                and "§" in _host_html(servers, seed)
                and "2026-" in _host_html(servers, seed)
            ))

            # Reload -> they come back from the host into the editor.
            parent.reload()
            _wait(lambda: parent.frame("ed") is not None)
            frame2 = parent.frame("ed")
            frame2.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: frame2.locator("#editor hr").count() == 1)
            assert frame2.locator("#editor div.page-break").count() == 1
            assert "§" in _frame_text(frame2)
        finally:
            ctx.close()
            browser.close()


def test_file_menu_export_odt_and_new_document(servers):
    """Export ODT from the File menu; New clears + persists the editor."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "file-ops.docx", "Exportable body")
            ctx = browser.new_context(accept_downloads=True)
            page = ctx.new_page()
            # Accept the New-document confirm().
            page.on("dialog", lambda d: d.accept())
            page.goto(_parent_url(servers, seed))
            frame = page.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            # --- Export ODT via File > Export > ODT -> downloadable archive.
            frame.locator("#btn-file").click()
            frame.locator("#btn-export").hover()
            with page.expect_download(timeout=10000) as dl_info:
                frame.locator("button[data-export='odt']").click()
            dl = dl_info.value
            assert dl.suggested_filename.endswith(".odt"), dl.suggested_filename
            path = dl.path()
            data = path.read_bytes()
            assert data[:2] == b"PK", "ODT export must be a zip"
            import zipfile as _zipfile
            with _zipfile.ZipFile(io.BytesIO(data)) as zf:
                content = zf.read("content.xml").decode("utf-8", "replace")
            assert "Exportable body" in content

            # --- New: confirm -> editor cleared.
            frame.locator("#btn-file").click()
            frame.locator("#btn-new").click()
            _wait(lambda: frame.locator("#editor").inner_text().strip() == "")
            assert frame.locator("#editor").inner_text().strip() == ""

            # Save -> the blank document reaches the host too.
            frame.locator("#btn-save").click()
            _wait(lambda: _host_text(servers, seed).strip() == "")
        finally:
            ctx.close()
            browser.close()


def test_offline_queue_and_resync(servers):
    """An offline save queues a local snapshot; it flushes on reconnect."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "offline.docx", "Offline seed")
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(_parent_url(servers, seed))
            frame = page.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Offline seed" in _frame_text(frame))

            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            frame.locator("#editor").press_sequentially(" queued edits")

            # Go offline -> Save fails -> snapshot queued locally + indicator.
            ctx.set_offline(True)
            frame.locator("#btn-save").click()
            _wait(lambda: not frame.locator("#offline-indicator").evaluate(
                "el => el.hidden"))
            queued = frame.evaluate(
                "JSON.parse(localStorage.getItem('wo-offline-queue') || 'null')")
            assert queued and queued["docId"] == seed["doc_id"], queued
            assert "queued edits" in queued["html"]

            # Back online -> the queued snapshot flushes to the host.
            ctx.set_offline(False)
            frame.evaluate("window.dispatchEvent(new Event('online'))")
            _wait(lambda: "queued edits" in _host_text(servers, seed))
            _wait(lambda: frame.evaluate(
                "localStorage.getItem('wo-offline-queue') === null"))
            assert frame.locator("#offline-indicator").evaluate("el => el.hidden")
        finally:
            ctx.close()
            browser.close()


def test_inline_format_commands_code_caps_strike(servers):
    """Code / small-caps / all-caps / strike round-trip in the browser."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "inlinefmt.docx", "plain base")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "plain base" in _frame_text(frame))

            # Type a line, then wrap one word in inline code (monospace).
            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            frame.locator("#editor").press_sequentially(" code SC UP strike ")
            frame.locator("#editor").evaluate("""() => {
              const ed = document.getElementById('editor');
              const t = ed.querySelector('p:last-of-type');
              const range = document.createRange();
              range.selectNodeContents(t);
              const sel = window.getSelection();
              sel.removeAllRanges(); sel.addRange(range);
            }""")
            frame.locator("button.rb[data-cmd='code']").click()
            frame.locator("#editor").evaluate("""() => {
              const ed = document.getElementById('editor');
              const t = ed.querySelector('p:last-of-type');
              const range = document.createRange();
              range.selectNodeContents(t);
              const sel = window.getSelection();
              sel.removeAllRanges(); sel.addRange(range);
            }""")
            frame.locator("button.rb[data-cmd='allCaps']").click()
            frame.locator("#editor").evaluate("""() => {
              const ed = document.getElementById('editor');
              const t = ed.querySelector('p:last-of-type');
              const range = document.createRange();
              range.selectNodeContents(t);
              const sel = window.getSelection();
              sel.removeAllRanges(); sel.addRange(range);
            }""")
            frame.locator("button.rb[data-cmd='strikeThrough']").click()

            html = frame.evaluate("document.getElementById('editor').innerHTML")
            assert "Consolas" in html or "monospace" in html.lower(), html
            assert "uppercase" in html, html
            assert "<s>" in html or "<strike>" in html, html

            # Save -> all three survive to the host DOCX.
            frame.locator("#btn-save").click()
            _wait(lambda: (
                ("Consolas" in _host_html(servers, seed) or
                 "<code>" in _host_html(servers, seed))
                and "uppercase" in _host_html(servers, seed)
                and ("<s>" in _host_html(servers, seed) or "<strike>" in _host_html(servers, seed))
            ))

        finally:
            ctx.close()
            browser.close()


def test_paragraph_rtl_and_line_spacing_roundtrip(servers):
    """RTL + line-spacing paragraph props persist through save + reload."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "parafmt.docx", "para base")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "para base" in _frame_text(frame))

            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            frame.locator("#editor").press_sequentially(" RTL line")

            # Apply 1.5 line spacing via the dropdown then RTL on the same block.
            frame.select_option("#line-spacing", "1.5")
            frame.locator("button.rb[data-cmd='directionRtl']").first.click()

            html = frame.evaluate("document.getElementById('editor').innerHTML")
            assert 'line-height: 1.5' in html, html
            assert 'direction: rtl' in html, html

            # Save -> both reach the host DOCX.
            frame.locator("#btn-save").click()
            _wait(lambda: (
                "line-height:1.5" in _host_html(servers, seed)
                and "direction:rtl" in _host_html(servers, seed)
            ))
            # Let any in-flight collab poll settle so it can't clobber the
            # just-saved DOCX before the reload reads it back (save vs poll
            # race), then confirm the host still holds the properties.
            _t_settle = time.time()
            while time.time() - _t_settle < 1.5:
                time.sleep(0.2)
            assert "line-height:1.5" in _host_html(servers, seed)
            assert "direction:rtl" in _host_html(servers, seed)

            # Reload from the host -> props survive.
            parent.reload()
            frame2 = parent.frame("ed")
            frame2.locator("#editor").wait_for(state="visible", timeout=45000)
            html2 = frame2.evaluate("document.getElementById('editor').innerHTML")
            assert 'line-height' in html2 and 'rtl' in html2.lower(), html2
        finally:
            ctx.close()
            browser.close()


def test_nested_list_tab_indent_roundtrip(servers):
    """Tab indents a list item into a nested list that survives save."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            seed = _seed_doc(servers, "lists.docx", "list base")
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "list base" in _frame_text(frame))

            frame.locator("#editor").click()
            frame.locator("#editor").press("End")
            # Start a bullet list on a fresh line: Enter, then the bullet
            # toolbar button (toggleList -> native execCommand) turns the new
            # paragraph into an <li>; type two items; Tab indents the second.
            frame.locator("#editor").press("Enter")
            frame.locator("#editor").press_sequentially("first item")
            frame.locator("button[data-cmd='insertUnorderedList']").first.click()
            frame.locator("#editor").press("End")
            frame.locator("#editor").press("Enter")
            frame.locator("#editor").press_sequentially("second item")
            frame.locator("#editor").press("Tab")
            # The live editor DOM may be transiently invalid (Chromium can
            # wrap the list in a <p> and put the nested <ul> beside the <li>);
            # the sanitizer normalises it on save, so assert on the host.
            frame.locator("#btn-save").click()
            _wait(lambda: (
                "<ul><li>second item</li></ul>" in _host_html(servers, seed).replace("\n", "")
                or "List Bullet 2" in _host_html(servers, seed)
            ))
        finally:
            ctx.close()
            browser.close()


def test_ai_propose_lands_as_tracked_change_accept_persists(servers):
    """Flagship v3 loop, driven through the real UI.

    AI tab > Grammar opens the propose dialog; the run posts /ai/propose; a
    registered scripted model (the server never calls a vendor) applies edit
    ops through the CRDT tool surface; the collab poll projects them as
    tracked-change spans in the review panel; Accept converges the text and
    the save persists it to the WOPI host.
    """
    from playwright.sync_api import sync_playwright
    from src.ai.propose import register_model

    seed = _seed_doc(servers, "ai.docx", text="Alpha beta gamma")

    class ScriptedModel:
        """One turn: replace the word 'beta' with 'check'; then done."""

        def __init__(self):
            self.n = 0

        def __call__(self, messages):
            self.n += 1
            if self.n > 1:
                return []
            return [{
                "name": "apply_ops",
                "arguments": {
                    "doc_id": seed["doc_id"],
                    "client_id": "agent=ai-propose:default",
                    "ops": [
                        {"t": "del", "at": 6, "end": 10},
                        {"t": "ins", "at": 6, "text": "check"},
                    ],
                },
            }]

    register_model("default", ScriptedModel())

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Alpha beta gamma" in _frame_text(frame))

            # AI tab > Grammar opens the propose dialog pre-filled.
            _open_ribbon_tab(frame, "ai")
            frame.locator("#btn-ai-grammar").click()
            assert frame.locator("#ai-propose-dialog").evaluate(
                "d => d.classList.contains('open')")
            frame.locator("#ai-propose-instruction").fill("Fix the casing")
            frame.locator("#btn-ai-propose-run").click()

            # The proposal converges as tracked changes (not a silent edit).
            _wait(lambda: frame.locator("#editor ins.track-insert").count() > 0)
            ins_text = frame.locator("#editor ins.track-insert").first.inner_text()
            assert ins_text == "check", f"tracked insertion got {ins_text!r}"
            del_text = frame.locator("#editor del.track-delete").first.inner_text()
            assert del_text == "beta", f"tracked deletion got {del_text!r}"
            # it surfaces in the existing review-changes flow
            assert frame.locator("#review-list .review-item").count() >= 2

            # Accept both changes (insertion first, then the deletion);
            # only once neither redline is pending does the text converge.
            frame.locator("#review-list .review-item button.primary").first.click()
            _wait(lambda: frame.locator("#review-list .review-item").count() == 1)
            frame.locator("#review-list .review-item button.primary").first.click()
            _wait(lambda: "Alpha check gamma" in _frame_text(frame))

            # Save persists the accepted proposal to the host.
            frame.locator("#btn-save").click()
            _wait(lambda: "Alpha check gamma" in _host_text(servers, seed))
        finally:
            ctx.close()
            browser.close()


def test_command_recorder_replay_dom_hash(servers):
    """v4-2: opt-in JSONL command recorder + deterministic replay.

    ?record=1 logs every emitCommand (with plain-text selection offsets).
    Replaying the log against a fresh load of the same document reproduces
    the exact editor DOM (hash equality). Without the flag nothing is
    recorded.
    """
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "rec.docx", text="Recorder alpha beta")

    _SET_RANGE = """([at, end]) => {
      const ed = document.getElementById('editor');
      ed.focus();
      let pos = 0, sn = null, so = 0, en = null, eo = 0;
      const w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        const len = n.data.length;
        if (!sn && pos + len >= at) { sn = n; so = at - pos; }
        if (sn && pos + len >= end) { en = n; eo = end - pos; break; }
        pos += len;
      }
      const r = document.createRange();
      if (!en) { r.setStart(sn, Math.min(so, sn.data.length)); r.collapse(true); }
      else { r.setStart(sn, so); r.setEnd(en, eo); }
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }"""

    def _bus(frame, cmd, value=None, at=None, end=None):
        """Set a selection (span at..end or collapsed caret at) and run cmd."""
        if at is not None:
            frame.evaluate(_SET_RANGE, [at, end if end is not None else at])
        frame.evaluate(
            "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
            " { detail: { command: c, value: v } }))",
            [cmd, value],
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()

            # opt-in check: without record=1 nothing is logged
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Recorder alpha beta" in _frame_text(frame))
            _bus(frame, "bold", at=9, end=14)
            assert frame.evaluate("window.__COMMAND_LOG__.length") == 0
            parent.reload()

            # recorded pass
            parent.goto(_parent_url(servers, seed, extra="&record=1"))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Recorder alpha beta" in _frame_text(frame))
            _bus(frame, "bold", at=9, end=14)      # bold the word "alpha"
            _bus(frame, "formatBlock", "H1", at=9)  # block under selection -> H1
            _bus(frame, "lineHeight", "1.5", at=0)  # caret at doc start
            _bus(frame, "insertHR", at=0)           # hr at caret
            log = frame.evaluate("window.__COMMAND_LOG__")
            assert [e["command"] for e in log] == [
                "bold", "formatBlock", "lineHeight", "insertHR"]
            h1 = frame.evaluate("commandDomHash()")
            jsonl = "\n".join(json.dumps(e) for e in log)

            # replay pass: fresh load, feed the JSONL back
            parent.goto(_parent_url(servers, seed, extra="&record=1"))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Recorder alpha beta" in _frame_text(frame))
            h2 = frame.evaluate(
                "jsonl => replayCommands(jsonl.split('\\n'))", jsonl)
            assert h1 == h2, f"DOM hash diverged on replay: {h1} != {h2}"
        finally:
            ctx.close()
            browser.close()


def test_page_setup_dialog_roundtrips_to_host(servers):
    """F-090/F-091/F-092: the page-setup dialog writes the marker, the live
    canvas follows the settings, and the save carries w:pgSz/w:pgMar."""
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "ps.docx", text="Page setup body")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Page setup body" in _frame_text(frame))

            _open_ribbon_tab(frame, "layout")
            frame.locator("#btn-page-setup").click()
            assert frame.locator("#page-setup-dialog").evaluate(
                "d => d.classList.contains('open')")
            frame.locator("#ps-size").select_option("11906x16838")  # A4
            frame.locator('input[name="ps-orient"][value="landscape"]').check()
            frame.locator("#ps-mt").fill("0.8")
            frame.locator("#ps-ml").fill("1.2")

            # marker at body start + canvas mapped (console/pageerror capture
            # rides on the parent page — Frame has no pageerror event)
            errors = []
            parent.on("pageerror", lambda e: errors.append(str(e)))
            parent.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            frame.locator("#btn-ps-apply").click()
            frame.wait_for_selector("#editor > div.page-setup", state="attached", timeout=5000)
            marker = frame.locator("#editor > div.page-setup")
            assert marker.get_attribute("data-page-w") == "16838"   # landscape A4
            assert marker.get_attribute("data-orient") == "landscape"
            assert marker.get_attribute("data-margin-top") == "1152"
            assert frame.evaluate(
                "getComputedStyle(document.documentElement)"
                ".getPropertyValue('--wo-page-w').trim()") == "1122.5px"
            assert not errors, errors

            frame.locator("#btn-save").click()
            _wait(lambda: "16838" in _host_html(servers, seed))
            host_html = _host_html(servers, seed)
            assert 'data-page-w="16838"' in host_html
            assert 'data-orient="landscape"' in host_html
            assert 'data-margin-top="1152"' in host_html
        finally:
            ctx.close()
            browser.close()


def test_change_case_and_font_step_via_bus(servers):
    """F-129 / F-131: changeCase (sentence/lower/upper/title) and
    fontSizeInc/fontSizeDec are real bus commands on a selection."""
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "case.docx", text="hello world test foo.")

    _SET_RANGE = """([at, end]) => {
      const ed = document.getElementById('editor');
      ed.focus();
      let pos = 0, sn = null, so = 0, en = null, eo = 0;
      const w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        const len = n.data.length;
        if (!sn && pos + len >= at) { sn = n; so = at - pos; }
        if (sn && pos + len >= end) { en = n; eo = end - pos; break; }
        pos += len;
      }
      const r = document.createRange();
      if (!en) { r.setStart(sn, Math.min(so, sn.data.length)); r.collapse(true); }
      else { r.setStart(sn, so); r.setEnd(en, eo); }
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }"""

    def _bus(frame, cmd, value=None, at=None, end=None):
        if at is not None:
            frame.evaluate(_SET_RANGE, [at, end if end is not None else at])
        frame.evaluate(
            "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
            " { detail: { command: c, value: v } }))",
            [cmd, value],
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "hello world test foo." in _frame_text(frame))

            n = len("hello world test foo.")

            # uppercase then lowercase
            _bus(frame, "changeCase", "upper", 0, n)
            _wait(lambda: "HELLO WORLD TEST FOO." in _frame_text(frame))
            _bus(frame, "changeCase", "lower", 0, n)
            _wait(lambda: "hello world test foo." in _frame_text(frame))
            # title + sentence
            _bus(frame, "changeCase", "title", 0, n)
            _wait(lambda: "Hello World Test Foo." in _frame_text(frame))
            _bus(frame, "changeCase", "sentence", 0, n)
            _wait(lambda: "Hello world test foo." in _frame_text(frame))

            # font step: measured px grows then shrinks back past the baseline
            def _fs_px():
                return frame.evaluate(
                    "(() => { const s = document.querySelector('#editor span[style*=font-size]');"
                    " return s ? parseFloat(getComputedStyle(s).fontSize) : 0 })()")
            _wait(lambda: _fs_px() == 0)
            _bus(frame, "fontSizeInc", None, 0, 5)
            _wait(lambda: _fs_px() > 0)
            inc = _fs_px()
            _bus(frame, "fontSizeDec", None, 0, 5)
            _wait(lambda: 0 < _fs_px() < inc)
        finally:
            ctx.close()
            browser.close()


def test_color_commands_via_bus_roundtrip_to_host(servers):
    """F-125/F-126/F-127: hiliteColor/foreColor/backColor are real bus
    commands (span[style] with styleWithCSS) and survive the save
    round-trip; the toolbar color inputs that back them exist."""
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "color.docx", text="Color me please")

    _SET_RANGE = """([at, end]) => {
      const ed = document.getElementById('editor');
      ed.focus();
      let pos = 0, sn = null, so = 0, en = null, eo = 0;
      const w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        const len = n.data.length;
        if (!sn && pos + len >= at) { sn = n; so = at - pos; }
        if (sn && pos + len >= end) { en = n; eo = end - pos; break; }
        pos += len;
      }
      const r = document.createRange();
      if (!en) { r.setStart(sn, Math.min(so, sn.data.length)); r.collapse(true); }
      else { r.setStart(sn, so); r.setEnd(en, eo); }
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }"""

    def _bus(frame, cmd, value=None, at=None, end=None):
        if at is not None:
            frame.evaluate(_SET_RANGE, [at, end if end is not None else at])
        frame.evaluate(
            "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
            " { detail: { command: c, value: v } }))",
            [cmd, value],
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Color me please" in _frame_text(frame))

            # the three picker surfaces exist
            for cid in ("text-color", "highlight-color", "shading-color"):
                assert frame.locator("#" + cid).count() == 1

            def _html():
                return frame.evaluate(
                    "document.getElementById('editor').innerHTML").lower()
            _bus(frame, "hiliteColor", "#ff00aa", 0, 2)   # "Co"
            _wait(lambda: "rgb(255, 0, 170)" in _html())
            _bus(frame, "foreColor", "#0055ff", 6, 8)     # "me"
            _wait(lambda: "rgb(0, 85, 255)" in _html())
            _bus(frame, "backColor", "#f0e68c", 9, 15)    # "please"
            _wait(lambda: "rgb(240, 230, 140)" in _html())

            frame.locator("#btn-save").click()
            _wait(lambda: "f0e68c" in _host_html(servers, seed).lower())
            host = _host_html(servers, seed).lower()
            assert "ff00aa" in host and "0055ff" in host and "f0e68c" in host
        finally:
            ctx.close()
            browser.close()


def test_wsb_promoted_commands_via_bus(servers):
    """WS-B promoted commands: section markers, multilevel, caption, ToF
    live preview, object dialog (textart), display mode, crossref dialog,
    AI translate — all real bus commands with a save round-trip to host."""
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "wsb.docx", text="Alpha beta gamma delta end.")

    _SET_RANGE = """([at, end]) => {
      const ed = document.getElementById('editor');
      ed.focus();
      let pos = 0, sn = null, so = 0, en = null, eo = 0;
      let last = null, lastLen = 0;
      const w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        last = n; lastLen = n.data.length;
        const len = n.data.length;
        if (!sn && pos + len >= at) { sn = n; so = at - pos; }
        if (sn && pos + len >= end) { en = n; eo = end - pos; break; }
        pos += len;
      }
      if (!sn) { sn = last; so = lastLen; }   // past the end: caret at EOF
      if (!en) { en = sn; eo = Math.min(so, sn.data.length); }
      const r = document.createRange();
      r.setStart(sn, Math.min(so, sn.data.length));
      r.setEnd(en, Math.min(eo, en.data.length));
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }"""

    def _bus(frame, cmd, value=None, at=None, end=None):
        if at is not None:
            frame.evaluate(_SET_RANGE, [at, end if end is not None else at])
        frame.evaluate(
            "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
            " { detail: { command: c, value: v } }))",
            [cmd, value],
        )

    def _html(frame):
        return frame.evaluate("document.getElementById('editor').innerHTML")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Alpha beta gamma delta end." in _frame_text(frame))

            # --- layout.* section markers: on -> save round-trip -> off ---
            _bus(frame, "toggleHyphenation")
            _bus(frame, "toggleLineNumbers")
            _bus(frame, "toggleWatermark")
            _wait(lambda: 'class="hyphenation"' in _html(frame).lower())
            _wait(lambda: 'class="line-numbers"' in _html(frame).lower())
            _wait(lambda: 'class="watermark"' in _html(frame).lower())
            frame.locator("#btn-save").click()
            _wait(lambda: 'class="hyphenation"' in _host_html(servers, seed).lower())
            host = _host_html(servers, seed).lower()
            assert 'class="line-numbers"' in host and 'class="watermark"' in host

            _bus(frame, "toggleWatermark")  # off again
            _wait(lambda: 'class="watermark"' not in _html(frame).lower())
            frame.locator("#btn-save").click()
            _wait(lambda: 'class="watermark"' not in _host_html(servers, seed).lower())
            host = _host_html(servers, seed).lower()
            assert 'class="hyphenation"' in host and 'class="line-numbers"' in host

            # --- heading + ToC live preview ---
            _bus(frame, "formatBlock", "H1", 0, 0)
            _wait(lambda: "<h1>" in _html(frame).lower())
            _bus(frame, "updateToc")
            _wait(lambda: '<nav class="toc"' in _html(frame).lower()
                  and 'class="toc-l1"' in _html(frame).lower())

            # --- caption outside a table -> centered caption paragraph ---
            _bus(frame, "insertCaption", None, 999999, 999999)
            _wait(lambda: _html(frame).lower().count("caption") >= 1)

            # --- displayMode cycles Original -> Final -> Markup ---
            _bus(frame, "displayMode")
            _wait(lambda: frame.evaluate(
                "document.getElementById('editor').dataset.viewMode") == "original")
            _bus(frame, "displayMode")
            _wait(lambda: frame.evaluate(
                "document.getElementById('editor').dataset.viewMode") == "final")
            _bus(frame, "displayMode")
            _wait(lambda: frame.evaluate(
                "document.getElementById('editor').dataset.viewMode") == "markup")

            # --- openCrossref opens the real crossref dialog ---
            _bus(frame, "openCrossref")
            frame.locator("#crossref-dialog").wait_for(state="visible", timeout=10000)

            # --- aiTranslate pre-fills the propose panel ---
            _bus(frame, "aiTranslate", "French", 0, 0)
            _wait(lambda: "French" in frame.locator("#ai-propose-instruction").input_value())
        finally:
            ctx.close()
            browser.close()


def test_wsb_multilevel_nested_list(servers):
    """home.multilevel: nesting a list item under its previous sibling
    produces the canonical <li><ol> subtree that round-trips to the host."""
    from playwright.sync_api import sync_playwright

    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("First item of list.")
    d.add_paragraph("Second item of list.")
    d.save(buf)
    resp = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{servers['host_port']}/_host/files",
                data=json.dumps(
                    {"name": "ml.docx", "data": base64.b64encode(buf.getvalue()).decode()}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=10,
        ).read()
    )
    seed = {"doc_id": resp["id"], "token": resp["access_token"]}

    _SET_RANGE = """([at, end]) => {
      const ed = document.getElementById('editor');
      ed.focus();
      let pos = 0, sn = null, so = 0, en = null, eo = 0;
      let last = null, lastLen = 0;
      const w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        last = n; lastLen = n.data.length;
        const len = n.data.length;
        if (!sn && pos + len >= at) { sn = n; so = at - pos; }
        if (sn && pos + len >= end) { en = n; eo = end - pos; break; }
        pos += len;
      }
      if (!sn) { sn = last; so = lastLen; }
      if (!en) { en = sn; eo = Math.min(so, sn.data.length); }
      const r = document.createRange();
      r.setStart(sn, Math.min(so, sn.data.length));
      r.setEnd(en, Math.min(eo, en.data.length));
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }"""

    def _bus(frame, cmd, value=None, at=None, end=None):
        if at is not None:
            frame.evaluate(_SET_RANGE, [at, end if end is not None else at])
        frame.evaluate(
            "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
            " { detail: { command: c, value: v } }))",
            [cmd, value],
        )

    def _html(frame):
        return frame.evaluate("document.getElementById('editor').innerHTML")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "First item of list." in _frame_text(frame)
                  and "Second item of list." in _frame_text(frame))

            first = len("First item of list.")
            second = first + len("Second item of list.")
            _bus(frame, "insertOrderedList", None, first, first)
            _wait(lambda: "<ol>" in _html(frame).lower())
            _bus(frame, "insertOrderedList", None, second, second)
            _wait(lambda: _html(frame).lower().count("<ol") == 1)
            # indent the second item under the first -> canonical li>ol
            _bus(frame, "multilevel", None, second, second)
            _wait(lambda: _html(frame).lower().count("<ol") >= 2)

            frame.locator("#btn-save").click()
            _wait(lambda: "<li>" in _host_html(servers, seed).lower())
            host = _host_html(servers, seed).lower()
            assert "first item of list" in host and "second item of list" in host
        finally:
            ctx.close()
            browser.close()


def test_wsb_object_roundtrip(servers):
    """insertObject (textart) -> object dialog -> save: the object div
    survives the docx round-trip (generic object: descr contract)."""
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "obj.docx", text="Plain body text here.")

    _SET_RANGE = """([at, end]) => {
      const ed = document.getElementById('editor');
      ed.focus();
      let pos = 0, sn = null, so = 0, en = null, eo = 0;
      let last = null, lastLen = 0;
      const w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        last = n; lastLen = n.data.length;
        const len = n.data.length;
        if (!sn && pos + len >= at) { sn = n; so = at - pos; }
        if (sn && pos + len >= end) { en = n; eo = end - pos; break; }
        pos += len;
      }
      if (!sn) { sn = last; so = lastLen; }
      if (!en) { en = sn; eo = Math.min(so, sn.data.length); }
      const r = document.createRange();
      r.setStart(sn, Math.min(so, sn.data.length));
      r.setEnd(en, Math.min(eo, en.data.length));
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }"""

    def _bus(frame, cmd, value=None, at=None, end=None):
        if at is not None:
            frame.evaluate(_SET_RANGE, [at, end if end is not None else at])
        frame.evaluate(
            "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
            " { detail: { command: c, value: v } }))",
            [cmd, value],
        )

    def _html(frame):
        return frame.evaluate("document.getElementById('editor').innerHTML")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Plain body text here." in _frame_text(frame))

            _bus(frame, "insertObject", "textart", 999999, 999999)
            frame.locator("#object-dialog").wait_for(state="visible", timeout=10000)
            assert frame.locator("#object-type").input_value() == "textart"
            frame.locator("#object-content").fill("Hello art")
            frame.locator("#btn-object-ok").click()
            _wait(lambda: 'data-type="textart"' in _html(frame).lower())
            frame.locator("#btn-save").click()
            _wait(lambda: 'data-type="textart"' in _host_html(servers, seed).lower())
        finally:
            ctx.close()
            browser.close()


def test_wsb_compare_versions_tracked_diff(servers):
    """F-103 compare flow (server mode): an older snapshot diffs onto the
    live document as tracked-change spans (ins.track-insert). Version
    history is host-managed in client mode, so this runs on a store-backed
    document where saves create real snapshots."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            # store-backed doc (no WOPI host) -> versions record on /save
            made = json.loads(
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{servers['doc_port']}/api/documents/new",
                        data=b"",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=10,
                ).read()
            )
            doc_id = made["doc_id"]
            page.goto(f"http://127.0.0.1:{servers['doc_port']}/editor/{doc_id}")
            page.locator("#editor").wait_for(state="visible", timeout=45000)
            # empty doc: ensure the editor finished rendering (blank <p>)
            _wait(lambda: page.locator("#editor > p").count() >= 1)

            def _bus(cmd, value=None):
                page.evaluate(
                    "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
                    " { detail: { command: c, value: v } }))",
                    [cmd, value],
                )

            # two snapshots: edit -> save, edit -> save
            page.evaluate("document.getElementById('editor').focus()")
            page.evaluate(
                "(() => { const el = document.getElementById('editor');"
                " el.lastChild.textContent = 'Version two text here.'; })()")
            page.locator("#btn-save").click()
            _wait(lambda: "Version two text here." in page.locator("#editor").inner_text())
            page.evaluate(
                "(() => { const el = document.getElementById('editor');"
                " el.lastChild.textContent += ' More later.'; })()")
            page.locator("#btn-save").click()
            _wait(lambda: "More later." in page.locator("#editor").inner_text())

            _bus("compareVersion")
            page.locator("#version-history-dialog").wait_for(state="visible", timeout=10000)
            _wait(lambda: page.locator(".version-compare").count() >= 1)
            page.locator(".version-compare").first.click()
            _wait(lambda: (page.locator("ins.track-insert").count()
                           + page.locator("del.track-delete").count()) >= 1)
        finally:
            ctx.close()
            browser.close()




def test_r4_view_hyperlink_ai_congruence(servers):
    """R4 congruence batch: the Insert-tab Hyperlink stub was a duplicate of
    the real link dialog; view.mode cycles the display modes; Gridlines is a
    view-only overlay; Navigation lists the document outline and jumps;
    ai.rewrite/ai.summarize open the propose dialog with preset instructions.
    All view-state/AI-side effects — no document content, so no save needed."""
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "r4.docx", text="Alpha beta gamma delta end.")

    def _bus(frame, cmd, value=None):
        frame.evaluate(
            "([c, v]) => dispatchEvent(new CustomEvent('wo-command',"
            " { detail: { command: c, value: v } }))",
            [cmd, value],
        )

    def _html(frame):
        return frame.evaluate("document.getElementById('editor').innerHTML")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context()
            parent = ctx.new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)
            _wait(lambda: "Alpha beta gamma delta end." in _frame_text(frame))

            # Seed an outline for navigation + a target paragraph.
            frame.evaluate(
                "(() => { const ed = document.getElementById('editor');"
                " ed.innerHTML = '<h1>Alpha</h1><p><br></p><h2>Beta</h2>"
                "<p><br></p><h3>Gamma</h3><p><br></p>'; })()")

            # 1. hyperlink -> the existing link dialog
            _bus(frame, "link")
            _wait(lambda: frame.evaluate(
                "!!document.getElementById('link-dialog')?.classList.contains('open')"))
            frame.evaluate("(() => {"
                " const d = document.getElementById('link-dialog');"
                " if (d) d.classList.remove('open'); })()")

            # 2. view.mode -> display mode cycle (data-view-mode on #editor,
            #    default "markup" -> "original" -> "final")
            _bus(frame, "displayMode")
            _wait(lambda: frame.evaluate(
                "document.getElementById('editor').dataset.viewMode") == "original")
            _bus(frame, "displayMode")
            _wait(lambda: frame.evaluate(
                "document.getElementById('editor').dataset.viewMode") == "final")

            # 3. gridlines: view-only overlay, toggled from the View tab
            _open_ribbon_tab(frame, "view")
            frame.evaluate("document.getElementById('btn-gridlines').click()")
            _wait(lambda: frame.evaluate(
                "document.getElementById('editor').classList.contains('show-gridlines')"))
            assert frame.evaluate(
                "document.getElementById('btn-gridlines').getAttribute('aria-pressed')") == "true"
            # state-mirror loop must not clobber a view toggle
            _bus(frame, "bold")
            _wait(lambda: frame.evaluate(
                "document.getElementById('btn-gridlines').getAttribute('aria-pressed')") == "true")
            frame.evaluate("document.getElementById('btn-gridlines').click()")
            _wait(lambda: not frame.evaluate(
                "document.getElementById('editor').classList.contains('show-gridlines')"))

            # 4. navigation sidebar lists the outline; clicking jumps + flashes
            _bus(frame, "toggleNavigation")
            _wait(lambda: frame.evaluate(
                "!document.getElementById('nav-panel').hidden"))
            outline = frame.evaluate(
                "[...document.querySelectorAll('#nav-panel .nav-panel-list a')]"
                ".map(a => a.textContent)")
            assert outline == ["Alpha", "Beta", "Gamma"], outline
            frame.evaluate(
                "document.querySelector('#nav-panel .nav-panel-list a').click()")
            _wait(lambda: frame.evaluate(
                "!!document.querySelector('h1.nav-flash')"))
            _bus(frame, "toggleNavigation")
            _wait(lambda: frame.evaluate(
                "document.getElementById('nav-panel').hidden"))

            # 5. ai.rewrite / ai.summarize -> propose dialog with presets
            _bus(frame, "aiRewrite")
            _wait(lambda: frame.evaluate(
                "!!document.getElementById('ai-propose-dialog')?.classList.contains('open')"))
            task = frame.evaluate(
                "document.getElementById('ai-propose-instruction').value")
            assert task.startswith("Rewrite the document"), task
            frame.evaluate("(() => { const d = document.getElementById('ai-propose-dialog');"
                           " if (d) d.classList.remove('open'); })()")
            _bus(frame, "aiSummarize")
            task = frame.evaluate(
                "document.getElementById('ai-propose-instruction').value")
            assert task.startswith("Summarize the document"), task
        finally:
            ctx.close()
            browser.close()
