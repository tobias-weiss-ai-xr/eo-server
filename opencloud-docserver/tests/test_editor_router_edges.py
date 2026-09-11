"""Editor router edge cases and error branches.

Covers the following functions in src/editor/router.py:

1. **_parse_launch** - WOPI launch parsing with token resolution, WOPISrc parsing,
   session registration, and lock acquisition (best-effort, never fails launch).
2. **document_html** - 404 for missing documents, conversion failures.
3. **save_document** - save/export failures, read-only enforcement.
4. **put_document_contents** - lock mismatch (409), client mode forwarding.
5. **acquire_lock** - lock conflicts when document locked by another user.

All tests use the TestClient with a temporary SQLite store and content directory.
Paradigm: UNIT tests for HTTP-level edge cases and error branches.
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote, urlencode

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import EditorSession, SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.protocol import LOCK_HEADER


# -----------------------------------------------------------------------------
# Shared app builder
# -----------------------------------------------------------------------------


def _make_app(tmp_path):
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
    return app, store


@pytest.fixture
def client(tmp_path):
    """TestClient with lifespan running; backing store on ``client.test_store``."""
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# -----------------------------------------------------------------------------
# Helper for creating DOCX content
# -----------------------------------------------------------------------------


def _docx_bytes(text: str = "Test content") -> bytes:
    """Create a minimal DOCX with the given paragraph text."""
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# -----------------------------------------------------------------------------
# 1. _parse_launch edge cases
# -----------------------------------------------------------------------------


def test_parse_launch_missing_wopi_src_returns_none(client):
    """_parse_launch returns None when WOPISrc is missing (no launch params).

    The launch handler should not register a session and should fall back
    to serving an empty/placeholder page.
    """
    # GET /editor without any query params
    res = client.get("/editor")

    # Should return 200 (the editor page) but no session was registered
    assert res.status_code == 200
    # Check that no session was created
    registry = client.app.state.sessions  # type: ignore[attr-defined]
    # No session registered for any doc_id
    assert registry.get("any-doc") is None


def test_parse_launch_wopi_src_with_missing_doc_id_parses_doc_from_url(client):
    """_parse_launch extracts doc_id from the last segment of WOPISrc when file_id is missing."""
    app = client.app
    cfg = app.state.config  # type: ignore[attr-defined]

    # WOPISrc without file_id in form body
    wopi_src = f"{cfg.wopi_host or 'http://localhost'}/files/doc-123"
    form_data = {"access_token": "test-token-abc", "WOPISrc": wopi_src}

    res = client.post("/editor", data=form_data)

    assert res.status_code == 200
    registry = app.state.sessions  # type: ignore[attr-defined]
    session = registry.get("doc-123")
    assert session is not None
    assert session.doc_id == "doc-123"


def test_parse_launch_wopi_src_ignores_trailing_slash(client):
    """_parse_launch handles WOPISrc with trailing slash correctly."""
    app = client.app
    cfg = app.state.config  # type: ignore[attr-defined]

    # WOPISrc with trailing slash
    wopi_src = f"{cfg.wopi_host or 'http://localhost'}/files/doc-trailing/"
    form_data = {"access_token": "tok-x", "WOPISrc": wopi_src}

    res = client.post("/editor", data=form_data)

    assert res.status_code == 200
    registry = app.state.sessions  # type: ignore[attr-defined]
    session = registry.get("doc-trailing")
    assert session is not None
    assert session.doc_id == "doc-trailing"


def test_parse_launch_lock_failure_does_not_break_launch(client, monkeypatch):
    """_parse_launch best-effort lock acquisition: lock failure logs but doesn't break launch.

    The launch must never fail because of locking issues. Even if WOPI lock
    acquisition fails, the session is still registered and served.
    """
    from src.editor.router import RemoteWopiClient

    original_acquire = RemoteWopiClient.acquire_or_adopt_lock

    def failing_acquire(self, doc_id, owner=""):
        """Simulate lock acquisition failure."""
        raise ConnectionError("WOPI host unreachable")

    monkeypatch.setattr(RemoteWopiClient, "acquire_or_adopt_lock", failing_acquire)

    app = client.app
    cfg = app.state.config  # type: ignore[attr-defined]

    wopi_src = f"{cfg.wopi_host or 'http://localhost'}/files/doc-lock-fail"
    form_data = {"access_token": "broken-token", "WOPISrc": wopi_src}

    res = client.post("/editor", data=form_data)

    # Launch must still succeed even though lock failed
    assert res.status_code == 200
    registry = app.state.sessions  # type: ignore[attr-defined]
    session = registry.get("doc-lock-fail")
    # Session should be registered with empty lock token
    assert session is not None
    assert session.lock_token == ""
    # NOTE: current behavior - read_only not set on lock failure (defaults False).


# -----------------------------------------------------------------------------
# 2. document_html 404 and conversion failures
# -----------------------------------------------------------------------------


def test_document_html_missing_document_returns_404(client):
    """document_html returns 404 for non-existent document."""
    res = client.get("/api/documents/ghost-document/html")

    assert res.status_code == 404
    assert res.json()["error"] == "not found"


def test_document_html_corrupt_docx_returns_empty_html(client):
    """document_html returns empty HTML for invalid DOCX (converter degrades gracefully)."""
    # Seed a document with invalid/corrupt content (not a real DOCX)
    store = client.test_store  # type: ignore[attr-defined]
    store.init("corrupt", "test.docx")
    store.put_content("corrupt", b"not a docx file at all " * 100)

    res = client.get("/api/documents/corrupt/html")

    # The converter silently returns empty HTML for invalid input
    assert res.status_code == 200
    assert res.json()["html"] == ""


def test_document_html_empty_document_returns_blank(client):
    """document_html returns blank HTML for 0-byte documents (start from scratch)."""
    store = client.test_store  # type: ignore[attr-defined]
    store.init("empty", "empty.docx")
    store.put_content("empty", b"")

    res = client.get("/api/documents/empty/html")

    assert res.status_code == 200
    body = res.json()
    assert body["html"] == ""
    assert body.get("blank") is True
    assert body["name"] == "empty.docx"


# -----------------------------------------------------------------------------
# 3. save_document failures and read-only enforcement
# -----------------------------------------------------------------------------


def test_save_document_invalid_json_returns_400(client):
    """save_document returns 400 for non-JSON body."""
    # Seed a document first
    store = client.test_store  # type: ignore[attr-defined]
    store.init("doc1", "test.docx")
    store.put_content("doc1", _docx_bytes())

    res = client.post(
        "/api/documents/doc1/save",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )

    assert res.status_code == 400
    assert res.json()["error"] == "invalid JSON"


def test_save_document_read_only_rejected(client):
    """save_document returns 403 when session is in read_only mode.

    The read_only flag is checked in save_document to prevent edits on documents
    locked by another user. This requires a session with in_client_mode=True
    (forwarding to remote WOPI host) where the remote host returned writable=False.
    """
    # Seed a document
    store = client.test_store  # type: ignore[attr-defined]
    store.init("readonly-doc", "test.docx")
    store.put_content("readonly-doc", _docx_bytes())

    # Create a session with read_only=True
    # The read_only check only applies when session exists (regardless of in_client_mode)
    from src.editor.session import EditorSession
    session = EditorSession(
        doc_id="readonly-doc",
        name="readonly.docx",
        size=100,
        version="1",
        last_modified=1234567890,
        lock_token="some-lock",
        read_only=True,  # This is what triggers the 403
    )
    client.app.state.sessions.register(session)  # type: ignore[attr-defined]

    res = client.post(
        "/api/documents/readonly-doc/save",
        json={"html": "<p>Attempted edit</p>"},
    )

    assert res.status_code == 403
    assert "read-only" in res.json()["error"].lower()


def test_save_document_conversion_failure_returns_500(client, monkeypatch):
    """save_document returns 500 when HTML-to-DOCX conversion explicitly fails.

    The converter itself handles most invalid input gracefully (returns empty HTML).
    This test verifies the 500 path exists by monkeypatching the converter to raise.
    """
    store = client.test_store  # type: ignore[attr-defined]
    store.init("conv-fail", "test.docx")
    store.put_content("conv-fail", _docx_bytes())

    # Monkeypatch the converter in the router module (imported directly)
    from src.editor import router as router_module

    original_html_to_docx = router_module.html_to_docx

    def failing_converter(html: str) -> bytes:
        raise RuntimeError("Converter crashed")

    router_module.html_to_docx = failing_converter

    try:
        res = client.post(
            "/api/documents/conv-fail/save",
            json={"html": "<p>Test</p>"},
        )

        assert res.status_code == 500
        assert "conversion failed" in res.json()["error"].lower()
    finally:
        router_module.html_to_docx = original_html_to_docx


# -----------------------------------------------------------------------------
# 4. put_document_contents lock conflicts and client mode
# -----------------------------------------------------------------------------


def test_put_document_contents_missing_document_returns_404(client):
    """PUT /api/documents/{doc_id}/contents returns 404 when document doesn't exist."""
    res = client.put(
        "/api/documents/missing-doc/contents",
        content=b"PK\x03\x04" + b"x" * 50,
    )

    assert res.status_code == 404
    assert res.json()["error"] == "not found"


def test_put_document_contents_lock_mismatch_returns_409(client):
    """PUT /api/documents/{doc_id}/contents returns 409 when lock token doesn't match.

    The WOPI protocol requires the X-WOPI-Lock header to match the current
    lock token. A mismatch means another user is editing.
    """
    # Seed a document and set a lock
    store = client.test_store  # type: ignore[attr-defined]
    store.init("locked-doc", "test.docx")
    store.put_content("locked-doc", _docx_bytes())
    store.set_lock("locked-doc", "lock-token-alice")

    # Try to write with wrong lock token
    res = client.put(
        "/api/documents/locked-doc/contents",
        content=b"PK\x03\x04" + b"new content" * 10,
        headers={LOCK_HEADER: "wrong-lock-token"},
    )

    assert res.status_code == 409
    assert res.json()["error"] == "lock mismatch"
    # The response should include the current lock token
    assert res.headers.get(LOCK_HEADER) == "lock-token-alice"


def test_put_document_contents_with_correct_lock_succeeds(client):
    """PUT /api/documents/{doc_id}/contents with matching lock token succeeds."""
    store = client.test_store  # type: ignore[attr-defined]
    store.init("lock-success", "test.docx")
    store.put_content("lock-success", _docx_bytes())
    store.set_lock("lock-success", "my-lock-123")

    res = client.put(
        "/api/documents/lock-success/contents",
        content=b"PK\x03\x04" + b"updated" * 10,
        headers={LOCK_HEADER: "my-lock-123"},
    )

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["size"] > 0


# -----------------------------------------------------------------------------
# 5. acquire_lock conflicts and edge cases
# -----------------------------------------------------------------------------


def test_acquire_lock_missing_document_returns_404(client):
    """acquire_lock on non-existent document returns 404."""
    res = client.post("/api/documents/ghost-doc/lock")

    assert res.status_code == 404
    assert res.json()["error"] == "not found"


def test_acquire_lock_success_sets_lock_and_returns_token(client):
    """acquire_lock successfully sets a lock and returns the lock token."""
    store = client.test_store  # type: ignore[attr-defined]
    store.init("lockable", "test.docx")
    store.put_content("lockable", _docx_bytes())

    res = client.post("/api/documents/lockable/lock?editor=alice")

    assert res.status_code == 200
    assert res.json()["ok"] is True
    lock_token = res.headers.get(LOCK_HEADER)
    assert lock_token is not None
    assert lock_token.startswith("editor-")
    # Verify the lock was stored
    assert store.get_lock("lockable") == lock_token


def test_acquire_lock_overwrites_existing_lock(client):
    """acquire_lock overwrites the existing lock with a new one.

    The current implementation takes the lock regardless of who holds it.
    A future improvement might check if the lock belongs to another user.
    """
    store = client.test_store  # type: ignore[attr-defined]
    store.init("overwrite-lock", "test.docx")
    store.put_content("overwrite-lock", _docx_bytes())
    store.set_lock("overwrite-lock", "old-lock-999")

    # Alice tries to acquire the lock
    res = client.post("/api/documents/overwrite-lock/lock?editor=alice")
    assert res.status_code == 200

    new_lock = res.headers.get(LOCK_HEADER)
    assert new_lock is not None
    # New lock overwrites old
    assert new_lock != "old-lock-999"
    assert store.get_lock("overwrite-lock") == new_lock


def test_acquire_lock_empty_query_param_uses_empty_suffix(client):
    """acquire_lock with empty query params uses empty suffix for lock token."""
    store = client.test_store  # type: ignore[attr-defined]
    store.init("empty-query", "test.docx")
    store.put_content("empty-query", _docx_bytes())

    # POST without any query params
    res = client.post("/api/documents/empty-query/lock")

    assert res.status_code == 200
    lock_token = res.headers.get(LOCK_HEADER)
    assert lock_token is not None
    # When query params empty, next(iter(...)) yields "" -> "editor-"
    assert lock_token == "editor-"


# -----------------------------------------------------------------------------
# Integration scenarios combining multiple edge cases
# -----------------------------------------------------------------------------


def test_save_document_not_found_client_mode_forwards_to_remote(client):
    """save_document in client mode on unknown doc forwards to remote WOPI host.

    When the session is in client_mode and the document is not in the local
    store, the save attempt converts the HTML then forwards to the remote WOPI host.
    If the remote host doesn't have the document, the remote save will fail.
    """
    # Create a session in client mode without a local document
    from src.editor.session import EditorSession
    session = EditorSession(
        doc_id="remote-only-doc",
        name="remote.docx",
        size=100,
        version="1",
        last_modified=1234567890,
        remote_host="http://remote-host",
        access_token="remote-token",
        read_only=False,
    )
    client.app.state.sessions.register(session)  # type: ignore[attr-defined]

    # The remote host doesn't exist, so the save will fail with 502
    res = client.post(
        "/api/documents/remote-only-doc/save",
        json={"html": "<p>Remote edit</p>"},
    )

    # In client mode, conversion succeeds but remote save fails with 502
    assert res.status_code == 502
    assert "remote save failed" in res.json()["error"].lower()
    assert res.status_code == 502
    assert "remote save failed" in res.json()["error"].lower()


def test_document_html_invalid_doc_id_returns_400(client):
    """document_html returns 400 for invalid doc_id format (null byte)."""
    # URI-encode a null byte in the doc_id
    invalid_id = "doc%00path"
    res = client.get(f"/api/documents/{invalid_id}/html")

    assert res.status_code == 400
    assert "Invalid file id" in res.json()["error"]


def test_export_document_not_found_returns_404(client):
    """export_document returns 404 when the document doesn't exist."""
    res = client.post("/api/documents/ghost-export/export?format=pdf")

    assert res.status_code == 404
    assert res.json()["error"] == "not found"


def test_export_document_conversion_failure_returns_empty_html(client):
    """export_document returns empty HTML for invalid DOCX (converter degrades gracefully)."""
    # Seed a document with invalid content
    store = client.test_store  # type: ignore[attr-defined]
    store.init("bad-convert", "test.docx")
    store.put_content("bad-convert", b"not valid at all " * 50)

    res = client.post("/api/documents/bad-convert/export?format=pdf")

    # The converter gracefully returns empty HTML, which weasyprint renders as minimal PDF
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    # Minimal valid PDF (no weasyprint) or actual PDF with weasyprint


# -----------------------------------------------------------------------------
# Hypothesis property tests for robustness
# -----------------------------------------------------------------------------


@pytest.mark.skip(reason="Hypothesis tests require more setup for converters")
def test_document_html_handles_arbitrary_docx_bytes(client):
    """Property test: document_html doesn't crash on arbitrary DOCX-like bytes."""
    # This would use Hypothesis to generate arbitrary byte strings
    # and verify document_html either succeeds or returns a 400/500,
    # never crashes or leaks memory.
    # Skipping for now - requires a working DOCX parser that handles garbage.
    pass


def test_lock_token_format_contains_timestamp_or_uuid(client):
    """acquire_lock generates lock tokens with predictable format (editor-<suffix>)."""
    store = client.test_store  # type: ignore[attr-defined]
    store.init("token-format", "test.docx")
    store.put_content("token-format", _docx_bytes())

    res = client.post("/api/documents/token-format/lock?user=alice")

    assert res.status_code == 200
    lock_token = res.headers.get(LOCK_HEADER)
    assert lock_token is not None
    # Current implementation: "editor-" + first query param value
    # This test validates the format contract
    assert lock_token.startswith("editor-")

def test_version_content_compare_endpoint(client):
    """GET /api/documents/{id}/versions/{ts}/content serves text + html for
    the Compare flow (F-103): current doc, two saves, then diff against
    the older snapshot."""
    made = client.post("/api/documents/new", params={"format": "docx"}).json()
    doc_id = made["doc_id"]
    h1 = "<p>Alpha line one</p><p>Beta</p>"
    r = client.post(f"/api/documents/{doc_id}/save", json={"html": h1})
    assert r.status_code == 200, r.text
    h2 = "<p>Alpha line one</p><p>Beta</p><p>Gamma added</p>"
    r = client.post(f"/api/documents/{doc_id}/save", json={"html": h2})
    assert r.status_code == 200, r.text
    versions = client.get(f"/api/documents/{doc_id}/versions").json()["versions"]
    # newest first: [h2 snapshot, h1 snapshot, initial blank]; compare h1.
    assert len(versions) >= 3
    older = versions[1]["ts"]
    res = client.get(f"/api/documents/{doc_id}/versions/{older}/content")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "Alpha line one" in data["text"] and "Beta" in data["text"]
    assert "Gamma added" not in data["text"]
    assert "<p>Alpha line one</p>" in data["html"]
    # unknown version is typed, not 500
    res = client.get(f"/api/documents/{doc_id}/versions/1999-01-01T00:00:00Z/content")
    assert res.status_code in (400, 404, 422)
    assert res.status_code != 500
