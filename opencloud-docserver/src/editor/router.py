"""Editor router: serves the web editor and converts content.

Endpoints:
    GET  /editor/{doc_id}            -- the editor page (HTML)
    GET  /api/documents/{doc_id}     -- document metadata
    GET  /api/documents/{doc_id}/html -- DOCX as HTML for editing
    POST /api/documents/{doc_id}/save -- save HTML back to DOCX
    POST /api/documents/{doc_id}/lock -- acquire editing lock
    POST /api/documents/{doc_id}/unlock
    GET  /api/documents              -- list
    POST /api/upload                 -- create a document from upload
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
import urllib.parse
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..ai.review import agent_ops, reject_agent_ops
from ..ai.propose import propose as ai_propose_run, DEFAULT_MAX_OPS, DEFAULT_MAX_STEPS
from ..ai.tools import ToolContext
from ..editor.collab import get_hub
from ..editor.converter import docx_to_html, html_to_docx
from ..editor.odt_converter import html_to_odt, odt_to_html
from ..editor.sanitize import sanitize_html
from ..editor.session import (
    EditorSession,
    RemoteWopiClient,
    SessionRegistry,
    session_from_token,
)
from ..lib.store import DocumentStoreError
from ..wopi.auth import hash_protection_password, verify_protection_password
from ..wopi.protocol import LOCK_HEADER, invalid_doc_id

router = APIRouter()

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
_templates = Jinja2Templates(directory=str(WEB_DIR))


def _store(request: Request):
    return request.app.state.store


def _registry(request: Request) -> SessionRegistry:
    return request.app.state.sessions


def _session_for(request: Request, doc_id: str) -> EditorSession | None:
    """Resolve the active session, preferring the per-launch session id (so
    concurrent editors of the same file never borrow each other's session)."""
    sid = request.query_params.get("session")
    if sid:
        session = _registry(request).get_by_id(sid)
        if session:
            return session
    return _registry(request).get(doc_id)


def _client(request: Request, doc_id: str) -> RemoteWopiClient | None:
    session = _session_for(request, doc_id)
    if session and session.in_client_mode:
        client = RemoteWopiClient(
            session.remote_host or "",
            session.access_token or "",
        )
        # The WOPI lock lives on the session (taken at launch); without it
        # the wopiserver refuses PutFile (409 unlocked file).
        client.lock_token = session.lock_token
        return client
    return None


# ----------------------------------------------------------------------
# Editor page
# ----------------------------------------------------------------------

WOPI_DISCOVERY_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<wopi-discovery>
  <net-zone name="external-http">
    <app name="WorldOffice" favIconUrl="https://worldoffice.org/favicon.ico">
      <action name="view" ext="docx" urlsrc="{public_url}/editor"/>
      <action name="edit" ext="docx" urlsrc="{public_url}/editor"/>
      <action name="view" ext="odt" urlsrc="{public_url}/editor"/>
      <action name="edit" ext="odt" urlsrc="{public_url}/editor"/>
    </app>
  </net-zone>
</wopi-discovery>"""


@router.get("/hosting/discovery")
async def wopi_discovery(request: Request) -> Response:
    """WOPI discovery XML consumed by OpenCloud's collaboration/app-provider.

    IMPORTANT (validated against real OpenCloud 7.3.0):
    - urlsrc must NOT contain an `access_token=` query param. OpenCloud appends
      `WOPISrc` (plus optional lang params) itself and then POSTs an
      urlencoded form to the resolved URL with the REAL access_token
      (plus file_id/embedded) in the body (see `editor_page`).
    - Do not use str.format() with the XML: it mangles braces; use replace().
    """
    xml = WOPI_DISCOVERY_XML.replace("{public_url}", request.app.state.config.public_url)
    return Response(content=xml, media_type="text/xml")


def _parse_launch(request: Request, form: dict | None):
    """Resolve (token, wopi_src, doc_id) from a WOPI launch request.

    OpenCloud POSTs a urlencoded form to the app URL: `access_token`,
    `file_id` and `embedded` live in the body; `WOPISrc`/`UI_LLCC` ride in
    the query string. GET launches (dev/local curl) put everything in the
    query string. Returns None when no usable launch params were found.
    """
    q = request.query_params
    token = (form or {}).get("access_token") or q.get("access_token")
    wopi_src = q.get("WOPISrc") or (form or {}).get("WOPISrc")
    if wopi_src and "://" not in wopi_src:
        # Tolerate scheme-less WOPISrc (dev launches, curl, rigs): urlparse
        # would otherwise see no scheme/netloc and silently fall back to the
        # configured wopi_host — CFI would hit the wrong host and 401.
        wopi_src = f"http://{wopi_src}"
    doc_id = (form or {}).get("file_id")
    if wopi_src:
        parsed = urllib.parse.urlparse(wopi_src)
        if parsed.scheme and parsed.netloc:
            wopi_host = f"{parsed.scheme}://{parsed.netloc}"
        else:
            wopi_host = request.app.state.config.wopi_host or q.get("wopi_host")
        if not doc_id:
            doc_id = wopi_src.rstrip("/").split("/")[-1]
        if not doc_id:
            return None
        session = EditorSession(
            doc_id=doc_id,
            name="document.docx",
            size=0,
            version="1",
            last_modified=int(time.time()),
            remote_host=wopi_host,
            access_token=token or "",
        )
        _registry(request).register(session)
        # Take the WOPI lock on the remote host so saves (PutFile) succeed —
        # the wopiserver refuses PutFile on unlocked files (409). The lock is
        # owner-named (wo:{user}:{uuid}); if another user already holds it,
        # the session is served read-only instead of clobbering their edits.
        # Best effort: launch must never fail because of locking.
        if token:
            try:
                host = RemoteWopiClient(wopi_host, token)
                owner = ""
                try:
                    file_info = host.check_file_info(doc_id) or {}
                    owner = file_info.get("UserId") or ""
                    # BaseFileName carries the real extension (.odt vs .docx),
                    # which the editor needs to route conversions correctly.
                    base_name = file_info.get("BaseFileName") or ""
                    if base_name:
                        session.name = base_name
                    # WOPI-canonical identity for the titlebar user chip.
                    if file_info.get("UserFriendlyName"):
                        session.user_name = file_info["UserFriendlyName"]
                except Exception as exc:
                    print(f"[launch] CFI failed for {doc_id}: {exc!r}")
                # Unknown owner still gets an owner-named token (wo:unknown:…)
                # so other users can never steal the lock out from under us.
                lock_token, writable = host.acquire_or_adopt_lock(doc_id, owner=owner or "unknown")
                print(
                    f"[launch] doc={doc_id} owner={owner[:40]!r} writable={writable} "
                    f"lock={lock_token[:32] if lock_token else ''}"
                )
                session.lock_token = lock_token
                session.read_only = not writable
                session.user_id = owner
            except Exception as exc:
                print(f"[launch] lock failed for {doc_id}: {exc!r}")
                session.lock_token = ""
        return session
    # Legacy launch: signed token + explicit wopi_host (query params).
    wopi_host = request.app.state.config.wopi_host or q.get("wopi_host")
    if token and wopi_host:
        session = session_from_token(token, request.app.state.config.jwt_secret)
        if session and session.doc_id:
            session.remote_host = wopi_host
            session.access_token = token
            _registry(request).register(session)
            return session
    return None


@router.get("/editor")
@router.post("/editor")
async def editor_page_root(request: Request) -> HTMLResponse:
    """WOPI launch entry point (no path segment). OpenCloud POSTs a form
    here with the real access_token; the file id comes from WOPISrc."""
    return await editor_page("", request)


@router.get("/editor/{doc_id}")
@router.post("/editor/{doc_id}")
async def editor_page(doc_id: str, request: Request) -> HTMLResponse:
    """Serve the editor page.

    In client mode we register a session from the OCIS-issued access_token
    (form body on POST, query string on GET) before serving, so the editor's
    API calls can read/write through the remote WOPI host.
    """
    form = None
    if request.method == "POST":
        try:
            form = await request.form()
        except Exception:
            form = {}
    session = _parse_launch(request, form)
    # The editor resolves its doc id from the launch (form file_id or the last
    # segment of WOPISrc). At the root /editor path the path param is empty,
    # so use the resolved id (the editor JS reads __DOC_ID__).
    read_only = False
    session_id = ""
    if session and session.doc_id:
        doc_id = session.doc_id
        read_only = session.read_only
        session_id = session.session_id
    # The root /editor launch path enters with an empty path id that is only
    # resolved from WOPISrc above — so validate the *resolved* id. (The
    # empty-id degenerate root page is inert: it reads no store content.)
    if doc_id and invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)

    return _templates.TemplateResponse(
        request,
        "index.html",
        {
            "doc_id": doc_id,
            "name": _doc_name(request, doc_id),
            "read_only": read_only,
            "session_id": session_id,
            "user_name": (session.user_name if session else "") or "",
        },
    )


def _doc_name(request: Request, doc_id: str) -> str:
    doc = _store(request).get(doc_id)
    if doc:
        return doc["name"]
    session = _registry(request).get(doc_id)
    return session.name if session else "document.docx"


def _document_format(request: Request, doc_id: str) -> str:
    """Resolve the document format from its file name extension.

    "docx" is the fallback for unknown/missing extensions so the existing
    DOCX path keeps working; ".odt" files route through the ODT converter.
    """
    name = (_doc_name(request, doc_id) or "").lower()
    if name.endswith(".odt"):
        return "odt"
    return "docx"


# Content types by extension (kept deliberately small and in sync with the
# WOPI host router; used by the extended contents/metadata endpoints).
_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def _content_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _CONTENT_TYPES.get(f".{ext}", "application/octet-stream")


def _export_pdf(html: str) -> tuple[bytes, str]:
    """Render HTML to PDF with WeasyPrint.

    Returns (pdf_bytes, engine). The historical behavior — a minimal no-content
    stub PDF when WeasyPrint was unavailable — is GONE on purpose: a silent
    stub defeats the export contract. Missing engine now surfaces as a 500
    with an actionable error (see export_document).
    """
    from weasyprint import HTML as WHTML

    return WHTML(string=html).write_pdf(), "weasyprint"


# ----------------------------------------------------------------------
# Document API
# ----------------------------------------------------------------------

@router.get("/api/documents/{doc_id}/html")
async def document_html(doc_id: str, request: Request) -> JSONResponse:
    """Return the editable HTML of a document.

    Reads bytes from the local store, or from the remote WOPI host when
    in client mode, then converts DOCX -> HTML.
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    data = _load_bytes(request, doc_id)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not data:
        # 0-byte file: start from a blank document so the user can just write;
        # the first save re-encodes the (now non-empty) HTML into a valid DOCX.
        return JSONResponse({"html": "", "name": _doc_name(request, doc_id), "blank": True})
    try:
        if _document_format(request, doc_id) == "odt":
            html = odt_to_html(data)
        else:
            html = docx_to_html(data)
    except Exception as exc:
        return JSONResponse({"error": f"conversion failed: {exc}"}, status_code=500)
    return JSONResponse({"html": html, "name": _doc_name(request, doc_id)})


@router.post("/api/documents/{doc_id}/save")
async def save_document(doc_id: str, request: Request) -> JSONResponse:
    """Convert submitted HTML back to DOCX and persist."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    body = await request.body()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid JSON body: expected an object"}, status_code=400)
    html = payload.get("html", "")

    # Sanitize before conversion to prevent XSS
    html = sanitize_html(html)

    # Protect enforcement: a restricted document refuses content writes
    # until protection is lifted via POST /protect (which verifies the
    # password server-side). This is the real gate — the editor's read-only
    # veil is UX, this 403 is enforcement.
    stored = _load_bytes(request, doc_id)
    if stored and _doc_protection_detail(stored)["restricted"]:
        return JSONResponse(
            {"error": "document is protected: restrict editing is on — unprotect to save"},
            status_code=403,
        )

    session = _session_for(request, doc_id)
    if session and session.read_only:
        return JSONResponse(
            {"error": "read-only: another user is editing this document"},
            status_code=403,
        )

    try:
        if _document_format(request, doc_id) == "odt":
            output_bytes = html_to_odt(html)
        else:
            output_bytes = html_to_docx(html)
    except Exception as exc:
        return JSONResponse({"error": f"conversion failed: {exc}"}, status_code=500)

    client = _client(request, doc_id)
    if client:
        try:
            client.put_contents(doc_id, output_bytes)
        except Exception as exc:
            return JSONResponse({"error": f"remote save failed: {exc}"}, status_code=502)
        _registry(request).get(doc_id)
    else:
        _store(request).put_content(doc_id, output_bytes)

    return JSONResponse({"ok": True, "size": len(output_bytes)})


@router.post("/api/documents/{doc_id}/export")
async def export_document(doc_id: str, request: Request, format: str = "pdf") -> Response:
    """Export the current document to the requested format (pdf/odt/html/docx).

    Converts the stored office bytes to editable HTML, then to the target
    format. PDF uses weasyprint when available, otherwise a minimal valid PDF.
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    data = _load_bytes(request, doc_id)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        html = (
            odt_to_html(data)
            if _document_format(request, doc_id) == "odt"
            else docx_to_html(data)
        )
    except Exception as exc:
        return JSONResponse({"error": f"conversion failed: {exc}"}, status_code=500)
    html = sanitize_html(html)
    name = _doc_name(request, doc_id) or "document"
    engine_header: dict[str, str] | None = None
    try:
        if format == "html":
            out, mime = html.encode("utf-8"), "text/html"
        elif format == "odt":
            out, mime = html_to_odt(html), "application/vnd.oasis.opendocument.text"
        elif format == "docx":
            out, mime = (
                html_to_docx(html),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        elif format == "pdf":
            pdf_bytes, engine = _export_pdf(html)
            out, mime = pdf_bytes, "application/pdf"
            engine_header = {"X-Export-Engine": engine}
        else:
            return JSONResponse({"error": f"unsupported format: {format}"}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"export failed: {exc}"}, status_code=500)
    base = name.rsplit(".", 1)[0]
    ext = {"html": ".html", "odt": ".odt", "docx": ".docx", "pdf": ".pdf"}.get(format, ".bin")
    headers = {"Content-Disposition": f'attachment; filename="{base}{ext}"'}
    if engine_header:
        headers.update(engine_header)
    return Response(
        content=out,
        media_type=mime,
        headers=headers,
    )


@router.post("/api/documents/new")
async def new_document(request: Request, format: str = "docx") -> JSONResponse:
    """Create a blank document and register a session; return an editor URL."""
    import io

    from docx import Document as DocxDocument
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    if format == "odt":
        doc = OpenDocumentText()
        doc.text.addElement(P(text=""))
        buf = io.BytesIO()
        doc.save(buf)
        data = buf.getvalue()
        name = "untitled.odt"
    else:
        blank = DocxDocument()
        blank.add_paragraph("")
        buf = io.BytesIO()
        blank.save(buf)
        data = buf.getvalue()
        name = "untitled.docx"
    store = _store(request)
    doc_id = f"new-{int(time.time() * 1000)}"
    store.init(doc_id, name)
    store.put_content(doc_id, data)
    sess = EditorSession(
        doc_id=doc_id,
        name=name,
        size=len(data),
        version="1",
        last_modified=int(time.time()),
    )
    _registry(request).register(sess)
    return JSONResponse({"doc_id": doc_id, "url": f"/editor/{doc_id}", "name": name})


# ----------------------------------------------------------------------
# Extended WOPI API: raw contents + extended metadata
# ----------------------------------------------------------------------
# The browser normally edits through the HTML conversion endpoints above,
# but WOPI-style clients (and the remote-host forwarding path) need the raw
# document bytes and richer metadata on the editor API surface. These
# endpoints mirror WOPI GetFile/PutFile/CheckFileInfo semantics and work in
# both host mode (local store) and client mode (forwarding to the OCIS host).


@router.get("/api/documents/{doc_id}/contents")
async def document_contents(doc_id: str, request: Request) -> Response:
    """WOPI GetFile on the editor API: return the raw document bytes.

    In host mode this reads the local store; in client mode it forwards to
    the remote WOPI host. The ``X-WOPI-ItemVersion`` header carries the
    document version, as the WOPI spec requires.
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    data = _load_bytes(request, doc_id)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    version = ""
    session = _session_for(request, doc_id)
    if session:
        version = str(session.last_modified)
    if not version:
        doc = _store(request).get(doc_id)
        if doc:
            version = str(doc["updated_at"])
    return Response(
        content=data,
        media_type=_content_type(_doc_name(request, doc_id)),
        headers={"X-WOPI-ItemVersion": version},
    )


@router.get("/api/agents/runs")
async def agent_runs(request: Request, client_id: str | None = None, doc_id: str | None = None,
                     limit: int = 100) -> JSONResponse:
    """Agent run audit rows (E20), newest first, optional filters.

    Every finished AgentRunner run with the store attached leaves a row:
    who ran, when, on which document, with what step/op budget usage and
    stop reason. The operator-facing answer to 'what did the agents do?'.
    """
    if limit < 1 or limit > 1000:
        return JSONResponse({"error": "limit must be 1..1000"}, status_code=400)
    return JSONResponse({
        "runs": _store(request).list_agent_runs(client_id=client_id, doc_id=doc_id, limit=limit),
    })


@router.get("/api/agents/runs/{run_id}")
async def agent_run_detail(run_id: int, request: Request) -> JSONResponse:
    """One agent run with its redacted trace (E20S1).

    The trace keeps structure (calls, arguments, op kinds, results meta)
    and bounds every text payload — debugging context, not a document
    mirror.
    """
    run = _store(request).get_agent_run(run_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    trace = _store(request).get_agent_trace(run_id)
    run["trace"] = json.loads(trace["payload"]) if trace else None
    return JSONResponse(run)


@router.get("/agents")
async def agents_dashboard(request: Request):
    """Agents dashboard (E20S3) — a read-only server-rendered page: per-agent
    aggregates on top, the most recent runs underneath. Stoic: a page, not a
    SPA."""
    store = _store(request)
    return _templates.TemplateResponse(
        request,
        "agents.html",
        {
            "summary": store.agent_summary(),
            "runs": store.list_agent_runs(limit=20),
        },
    )


@router.get("/api/agents/summary")
async def agent_summary(request: Request) -> JSONResponse:
    """Per-agent aggregates over the audit log: runs, ops, docs, last seen."""
    return JSONResponse({"agents": _store(request).agent_summary()})


@router.get("/api/documents/{doc_id}/info")
async def document_info(doc_id: str, request: Request) -> JSONResponse:
    """Document metadata: name, format, size, timestamps, version.

    The metadata surface for document info (file properties are shown by the
    OpenCloud shell in the integrated product; this is the docserver-side
    source of truth for it).
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    doc = _store(request).get(doc_id)
    if not doc:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = _load_bytes(request, doc_id)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    name = doc["name"]
    fmt = "odt" if (name or "").lower().endswith(".odt") else "docx"
    return JSONResponse(
        {
            "id": doc_id,
            "name": name,
            "format": fmt,
            "size": len(data),
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
            "version": str(doc["updated_at"]),
        }
    )


# ----------------------------------------------------------------------
# Version history (snapshots taken on every content write)
# ----------------------------------------------------------------------


@router.get("/api/documents/{doc_id}/versions")
async def document_versions(doc_id: str, request: Request) -> JSONResponse:
    """Return the document's version history, newest first.

    Snapshot metadata (ts, author, size) is served from the local store.
    Remote (client-mode) documents are managed by the WOPI host, whose
    own revision history is authoritative — return a clear error instead
    of a misleading empty list.
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    if _client(request, doc_id) is not None:
        return JSONResponse(
            {"error": "version history is managed by the remote document host"},
            status_code=400,
        )
    store = _store(request)
    if store.get(doc_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"versions": store.list_versions(doc_id)})


@router.post("/api/documents/{doc_id}/versions/{ts}/restore")
async def restore_document_version(doc_id: str, ts: int, request: Request) -> JSONResponse:
    """Restore the given snapshot as the document's current content.

    The pre-restore state is preserved as a new snapshot so the restore is
    itself undoable. Client-mode documents are refused (host owns history).
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    if _client(request, doc_id) is not None:
        return JSONResponse(
            {"error": "version history is managed by the remote document host"},
            status_code=400,
        )
    store = _store(request)
    if store.get(doc_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    session = _session_for(request, doc_id)
    if session and session.read_only:
        return JSONResponse(
            {"error": "read-only: another user is editing this document"},
            status_code=403,
        )
    try:
        head_ts = store.restore_version(doc_id, ts)
    except DocumentStoreError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse({"ok": True, "ts": head_ts})


@router.get("/api/documents/{doc_id}/versions/{ts}/content")
async def version_content(doc_id: str, ts: int, request: Request) -> JSONResponse:
    """Serve a version's content as plain text (+ html) for the Compare
    flow (F-103): the editor diffs the current document against this text
    and renders the delta as tracked changes. Restored by the store on
    demand (versions are on-disk snapshots)."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    if _client(request, doc_id) is not None:
        return JSONResponse(
            {"error": "version history is managed by the remote document host"},
            status_code=400,
        )
    store = _store(request)
    if store.get(doc_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    raw = store.get_version(doc_id, ts)
    if raw is None:
        return JSONResponse({"error": "version not found"}, status_code=404)
    html = docx_to_html(raw) or ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return JSONResponse({"html": html, "text": text, "ts": ts})


@router.put("/api/documents/{doc_id}/contents")
@router.post("/api/documents/{doc_id}/contents")
async def put_document_contents(doc_id: str, request: Request) -> JSONResponse:
    """WOPI PutFile on the editor API: replace the raw document bytes.

    ``POST`` is accepted when the WOPI ``X-WOPI-Override: PUT`` header is
    present (the convention the OCIS wopiserver itself requires); a bare
    ``PUT`` is the same call. The store lock is honoured: a locked document
    rejects writes without the matching ``X-WOPI-Lock`` token (WOPI 409).
    In client mode the bytes are forwarded to the remote host and the
    session's read-only state is enforced.
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    if request.method == "POST" and request.headers.get("X-WOPI-Override", "").upper() != "PUT":
        return JSONResponse(
            {"error": "X-WOPI-Override: PUT required on POST /contents"}, status_code=400
        )

    store = _store(request)
    session = _session_for(request, doc_id)
    if store.get(doc_id) is None:
        if session is None or not session.in_client_mode:
            return JSONResponse({"error": "not found"}, status_code=404)

    if session and session.read_only:
        return JSONResponse(
            {"error": "read-only: another user is editing this document"},
            status_code=403,
        )

    # Protect enforcement on the raw-bytes write path too (WOPI PutFile): a
    # restricted document must not be replaceable without lifting protection.
    stored = _load_bytes(request, doc_id)
    if stored and _doc_protection_detail(stored)["restricted"]:
        return JSONResponse(
            {"error": "document is protected: restrict editing is on — unprotect to write"},
            status_code=403,
        )

    lock = request.headers.get(LOCK_HEADER, "")
    current_lock = store.get_lock(doc_id)
    if current_lock and lock != current_lock:
        return JSONResponse(
            {"error": "lock mismatch"},
            status_code=409,
            headers={LOCK_HEADER: current_lock},
        )

    body = await request.body()
    client = _client(request, doc_id)
    if client:
        try:
            client.put_contents(doc_id, body)
        except Exception as exc:
            return JSONResponse({"error": f"remote save failed: {exc}"}, status_code=502)
    else:
        store.put_content(doc_id, body)

    return JSONResponse({"ok": True, "size": len(body)})


@router.get("/api/documents/{doc_id}")
async def document_meta(doc_id: str, request: Request) -> JSONResponse:
    """Return document metadata (size, name, lock state) plus the extended
    WOPI fields: base file name, format, MIME type, version, writability and
    the contents URL for the raw-bytes endpoint."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    doc = _store(request).get(doc_id)
    if doc is None:
        session = _registry(request).get(doc_id)
        if session is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(
            {
                "id": doc_id,
                "name": session.name,
                "size": session.size,
                "updated_at": session.last_modified,
                "locked": bool(session.lock_token),
                "client_mode": session.in_client_mode,
                "base_file_name": session.name,
                "format": _document_format(request, doc_id),
                "mime_type": _content_type(session.name),
                "version": session.version,
                "editable": not session.read_only,
                "writable": not session.read_only,
                "contents_url": f"/api/documents/{doc_id}/contents",
            }
        )
    name = doc["name"]
    return JSONResponse(
        {
            "id": doc_id,
            "name": name,
            "size": doc["size"],
            "updated_at": doc["updated_at"],
            "locked": bool(doc["lock_token"]),
            "base_file_name": name,
            "format": _document_format(request, doc_id),
            "mime_type": _content_type(name),
            "version": str(doc["updated_at"]),
            "editable": True,
            "writable": True,
            "contents_url": f"/api/documents/{doc_id}/contents",
        }
    )


@router.get("/api/documents")
async def document_list(request: Request) -> JSONResponse:
    """List all locally stored documents."""
    docs = _store(request).list()
    return JSONResponse(
        [{"id": d["id"], "name": d["name"], "size": d["size"]} for d in docs]
    )


@router.post("/api/upload")
async def upload_document(file: UploadFile, request: Request) -> JSONResponse:
    """Create a document record from an uploaded file."""
    data = await file.read()
    if not data:
        return JSONResponse({"error": "empty file"}, status_code=400)
    doc_id = file.filename or "doc"
    # A hostile filename is a path-traversal vector (the filename becomes
    # the doc id, i.e. the content filename) — reject it at the boundary.
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    store = _store(request)
    store.init(doc_id, file.filename or "document.docx")
    store.put_content(doc_id, data)
    return JSONResponse({"id": doc_id, "name": file.filename})


# ----------------------------------------------------------------------
# Real-time collaboration (CRDT)
# ----------------------------------------------------------------------
# Character-level CRDT editing. Clients exchange idempotent insert/delete
# operations (see src/editor/collab.py for the wire format) through the
# hub, which assigns every applied op a global revision, replays missing
# ops to late joiners and streams live updates over SSE.
#
#   GET  /api/documents/{id}/collab/state     -- snapshot: rev + text + log
#   GET  /api/documents/{id}/collab/ops       -- catch-up ops since ?since=N
#   POST /api/documents/{id}/collab/ops       -- apply client ops
#   POST /api/documents/{id}/collab/resync    -- rebase on authoritative text
#   GET  /api/documents/{id}/collab/stream    -- SSE live event stream (CO-3)
#   POST /api/documents/{id}/collab/presence  -- announce cursor / leave
#   GET  /api/documents/{id}/collab/presence  -- list active editors (CO-3)


def _collab_base_text(request: Request, doc_id: str) -> str:
    """Best-effort baseline for a document's collaboration state: the bytes
    currently in the store (or the remote WOPI host) converted to **plain
    text** (HTML markup stripped, entities decoded), so a freshly touched
    collaboration state reflects the visible document as it exists.

    Plain text (not HTML) is the correct base for character-level CRDT
    edits: cursor positions and insert indices are expressed in visible
    characters, exactly what a browser editor exposes. Seeding HTML would
    make concurrent inserts land outside the tags and break persistence.
    Returns "" when there is nothing to seed from yet.
    """
    data = _load_bytes(request, doc_id)
    if not data:
        return ""
    try:
        if _document_format(request, doc_id) == "odt":
            html = odt_to_html(data)
        else:
            html = docx_to_html(data)
    except Exception:
        return ""
    return _html_to_text(html)


def _html_to_text(html: str) -> str:
    """Strip HTML to plain text, turning block/line breaks into newlines
    and decoding entities ("<p>A</p><p>B</p>" -> "A\\nB")."""
    import re as _re
    from html import unescape as _unescape

    text = _re.sub(r"</p>\s*<p>", "\n", html)
    text = _re.sub(r"</p>|<br\s*/?>", "\n", text)
    text = _re.sub(r"<[^>]+>", "", text)
    return _unescape(text).strip()


@router.get("/api/documents/{doc_id}/collab/state")
async def collab_state(doc_id: str, request: Request) -> JSONResponse:
    """Current collaboration snapshot: revision, visible text, full op log
    and the list of active editors. A new (late-joining) client can apply
    the whole op log from scratch and converge with every other editor."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    hub = get_hub()
    hub.ensure(doc_id, _collab_base_text(request, doc_id))
    return JSONResponse(hub.state(doc_id))


@router.get("/api/documents/{doc_id}/collab/ops")
async def collab_ops(doc_id: str, request: Request) -> JSONResponse:
    """Catch-up replay: every hub op applied after revision ``since``.
    Poll this (or subscribe to the SSE stream) to stay in sync."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    hub = get_hub()
    hub.ensure(doc_id, _collab_base_text(request, doc_id))
    try:
        since = int(request.query_params.get("since", 0))
    except (TypeError, ValueError):
        since = 0
    return JSONResponse({"rev": hub.rev(doc_id), "ops": hub.ops_since(doc_id, since)})


@router.post("/api/documents/{doc_id}/collab/ops")
async def collab_apply_ops(doc_id: str, request: Request) -> JSONResponse:
    """Apply a batch of client operations (idempotent, deduplicated).
    Body: ``{"client_id": str, "base_rev": int, "ops": [...]}``. The reply
    carries the new revision, the ops that were applied, and any ops the
    client is still missing since ``base_rev`` (single-round-trip healing
    of gaps from lost/reordered delivery)."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    ops = payload.get("ops") if isinstance(payload, dict) else None
    if not isinstance(ops, list):
        return JSONResponse({"error": "ops must be a list"}, status_code=400)
    client_id = payload.get("client_id") or "anon"
    if not isinstance(client_id, str):
        client_id = "anon"
    base_rev = payload.get("base_rev")
    if not isinstance(base_rev, int):
        base_rev = None
    return JSONResponse(get_hub().apply_ops(doc_id, client_id, ops, base_rev))


@router.post("/api/documents/{doc_id}/collab/sync")
async def collab_sync(doc_id: str, request: Request) -> JSONResponse:
    """Browser-friendly collaboration sync: the client posts its full plain-
    text content and the server merges it into the CRDT (see CollabHub.sync_text).
    No client-side CRDT required — keeps the browser thin."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    text = payload.get("text", "")
    client_id = payload.get("client_id") or "anon"
    if not isinstance(client_id, str):
        client_id = "anon"
    hub = get_hub()
    hub.ensure(doc_id, _collab_base_text(request, doc_id))
    state = hub.sync_text(doc_id, client_id, str(text))
    return JSONResponse(state)


@router.post("/api/documents/{doc_id}/collab/resync")
async def collab_resync(doc_id: str, request: Request) -> JSONResponse:
    """Rebase the collaboration state onto authoritative text — used after
    a full save so the CRDT layer and the stored document stay in step."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    state = get_hub().resync(doc_id, payload.get("text", ""))
    return JSONResponse(state)


@router.get("/api/documents/{doc_id}/collab/stream")
async def collab_stream(doc_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream. Emits a ``state`` event with the current
    snapshot on connect, then ``ops``/``presence``/``resync`` events as they
    happen — the real-time push half of the collaboration protocol."""
    hub = get_hub()
    hub.ensure(doc_id, _collab_base_text(request, doc_id))
    queue = hub.subscribe(doc_id)

    async def event_stream():
        try:
            # Seed the fresh subscriber so it converges immediately.
            yield f"event: state\ndata: {json.dumps(hub.state(doc_id))}\n\n"
            # Keep the connection alive: SSE proxies/browsers close idle
            # streams, so emit a comment heartbeat if no real event arrives.
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            hub.unsubscribe(doc_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/documents/{doc_id}/collab/presence")
async def collab_presence(doc_id: str, request: Request) -> JSONResponse:
    """Announce an editor (cursor/selection sharing) or leave by sending an
    empty cursor. Body: ``{"client_id": str, "user": str, "cursor": ...}``."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    client_id = payload.get("client_id") or ""
    if not isinstance(client_id, str) or not client_id:
        return JSONResponse({"error": "client_id required"}, status_code=400)
    clients = get_hub().set_presence(
        doc_id, client_id, payload.get("user", ""), payload.get("cursor")
    )
    return JSONResponse({"ok": True, "clients": clients})


@router.get("/api/documents/{doc_id}/collab/presence")
async def collab_presence_list(doc_id: str, request: Request) -> JSONResponse:
    """List the editors currently collaborating on a document."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    return JSONResponse({"clients": get_hub().clients(doc_id)})


# ----------------------------------------------------------------------
# AI review (op-stream diff + per-op reject)
# ----------------------------------------------------------------------

@router.get("/api/documents/{doc_id}/ai/review")
async def ai_review(doc_id: str, request: Request) -> JSONResponse:
    """The reviewable agent portion of the op stream (spec:
    agent-collab-client): every agent op with its revision, attribution and
    a one-line summary — the diff between the pre-agent and post-agent
    revisions, one row per op."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    try:
        since = int(request.query_params.get("since", 0))
    except (TypeError, ValueError):
        since = 0
    hub = get_hub()
    hub.ensure(doc_id, _collab_base_text(request, doc_id))
    return JSONResponse(agent_ops(hub, doc_id, since_rev=since))


@router.post("/api/documents/{doc_id}/ai/review/reject")
async def ai_review_reject(doc_id: str, request: Request) -> JSONResponse:
    """Reject agent ops: body ``{"revs": [..]}`` or ``{"all": true}``.
    Each rejection emits the inverse op as the ``reviewer`` client, so the
    rejection itself is a normal, attributable, undoable op."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    hub = get_hub()
    hub.ensure(doc_id, _collab_base_text(request, doc_id))
    if payload.get("all"):
        listing = agent_ops(hub, doc_id)
        revs = [op["rev"] for op in listing["ops"]]
    else:
        revs = payload.get("revs")
        if not isinstance(revs, list) or not all(isinstance(r, int) for r in revs):
            return JSONResponse({"error": "revs must be a list of ints"}, status_code=400)
    return JSONResponse(reject_agent_ops(hub, doc_id, revs))


@router.post("/api/documents/{doc_id}/ai/propose")
async def ai_propose(doc_id: str, request: Request) -> JSONResponse:
    """AI proposal: ``{"instruction": str, "model": str?}`` -> applied edit
    ops. Runs a registered model (``ai.propose.MODEL_REGISTRY``; the server
    itself never calls a vendor) through the agent tool surface, so every
    edit is attributed ``agent=ai-propose:<model>`` and lands in ``/ai/review``
    for per-op accept/reject. Response carries the new ops in text
    coordinates so an editor can project them as tracked-change spans."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    instruction = str(payload.get("instruction") or "").strip()
    if not instruction:
        return JSONResponse({"error": "instruction required"}, status_code=400)
    store = _store(request)
    if store.get(doc_id) is None:
        # WOPI-mode documents live on the host until first save; materialize
        # the current bytes into the store so the agent tool surface (which
        # reads baselines from the store) sees the same document the hub has.
        data = _load_bytes(request, doc_id)
        if not data:
            return JSONResponse({"error": "Document not found"}, status_code=404)
        store.init(doc_id, _doc_name(request, doc_id))
        store.put_content(doc_id, data)
    cfg = getattr(request.app.state, "config", None)
    if cfg is not None and not getattr(cfg, "agents_enabled", True):
        return JSONResponse({"error": "agents disabled"}, status_code=403)
    hub = get_hub()
    hub.ensure(doc_id, _collab_base_text(request, doc_id))
    since_rev = len(hub.ensure(doc_id).log)
    ctx = ToolContext(
        store=store,
        hub=hub,
        agents_enabled=True if cfg is None else getattr(cfg, "agents_enabled", True),
    )
    try:
        max_steps = min(int(payload.get("max_steps", DEFAULT_MAX_STEPS)), DEFAULT_MAX_STEPS)
        max_ops = min(int(payload.get("max_ops", DEFAULT_MAX_OPS)), DEFAULT_MAX_OPS)
    except (TypeError, ValueError):
        max_steps, max_ops = DEFAULT_MAX_STEPS, DEFAULT_MAX_OPS
    out = ai_propose_run(
        ctx, doc_id, instruction,
        model_name=str(payload.get("model") or "default"),
        max_steps=max_steps, max_ops=max_ops,
    )
    if not out.get("ok"):
        return JSONResponse(out, status_code=int(out.get("status", 500)))
    listing = agent_ops(hub, doc_id, since_rev=since_rev)
    return JSONResponse({**out, "ops": listing["ops"]})


# ----------------------------------------------------------------------
# OCR plugin
# ----------------------------------------------------------------------

@router.post("/api/documents/{doc_id}/ai/ocr")
async def ocr_document(doc_id: str, request: Request) -> JSONResponse:
    """Run OCR on the document via the AI propose pipeline.

    The server routes OCR through the MODEL_REGISTRY pattern (see ai.propose):
    unregistered model → loud 503, not a silent stub. The OCR instruction
    is passed to the registered model which should extract text and return it.
    """
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)

    store = _store(request)
    if store.get(doc_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    session = _session_for(request, doc_id)
    if session and session.read_only:
        return JSONResponse(
            {"error": "read-only: another user is editing this document"},
            status_code=403,
        )

    # OCR goes through the same agent tool surface as ai.propose
    # It uses the model registry; unknown models surface as 503
    cfg = getattr(request.app.state, "config", None)
    if cfg is not None and not getattr(cfg, "agents_enabled", True):
        return JSONResponse({"error": "agents disabled"}, status_code=403)

    hub = get_hub()
    data = _load_bytes(request, doc_id)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Seed the collaboration state with the document baseline
    hub.ensure(doc_id, _collab_base_text(request, doc_id))

    # OCR instruction for the model
    instruction = "Perform OCR on the document, extract all text, and return it as plain text."

    # Run OCR through the propose pipeline (uses MODEL_REGISTRY)
    max_steps = 8
    max_ops = 30

    ctx = ToolContext(
        store=store,
        hub=hub,
        agents_enabled=True if cfg is None else getattr(cfg, "agents_enabled", True),
    )

    out = ai_propose_run(
        ctx, doc_id, instruction,
        model_name=str(request.query_params.get("model", "default")),
        max_steps=max_steps, max_ops=max_ops,
    )

    if not out.get("ok"):
        return JSONResponse(out, status_code=int(out.get("status", 500)))

    return JSONResponse({"ok": True, **out})


# ----------------------------------------------------------------------
# Plugin registry
# ----------------------------------------------------------------------

@router.get("/api/plugins")
async def list_plugins(request: Request) -> JSONResponse:
    """Return the installed plugin registry (catalog of hosted plugins).

    Browse lists this catalog; Manage toggles per-plugin enable state.
    """
    return JSONResponse({"plugins": _PLUGIN_REGISTRY})


# The plugin catalog: every plugin the host ships. OCR and Photo editor
# are real (see PLAIN web/editor.js photoEditor dialog + /ai/ocr); the
# list grows when a new plugin host is added.
_PLUGIN_REGISTRY = [
    {
        "id": "ocr",
        "name": "OCR",
        "version": "1.0.0",
        "description": "Extract text from the document via the registered vision model (MODEL_REGISTRY; loud 503 when unregistered).",
    },
    {
        "id": "photoeditor",
        "name": "Photo editor",
        "version": "1.0.0",
        "description": "Canvas 2D filters on the selected image: brightness, contrast, rotate, crop; Save writes the image back.",
    },
]


# ----------------------------------------------------------------------
# Locking (editor-level convenience over the WOPI host store)
# ----------------------------------------------------------------------

@router.post("/api/documents/{doc_id}/lock")
async def acquire_lock(doc_id: str, request: Request) -> JSONResponse:
    store = _store(request)
    if store.get(doc_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    store.set_lock(doc_id, "editor-" + next(iter(request.query_params), ""))
    return JSONResponse({"ok": True}, headers={LOCK_HEADER: store.get_lock(doc_id)})


@router.post("/api/documents/{doc_id}/unlock")
async def release_lock(doc_id: str, request: Request) -> JSONResponse:
    """Release the editing lock. In client mode this unlocks the remote WOPI
    host (called via a sendBeacon on editor unload); in host mode it clears
    the local store lock."""
    client = _client(request, doc_id)
    if client:
        client.release_lock(doc_id)
        return JSONResponse({"ok": True})
    _store(request).release_lock(doc_id)
    return JSONResponse({"ok": True})


def _load_bytes(request: Request, doc_id: str) -> bytes | None:
    client = _client(request, doc_id)
    if client:
        try:
            return client.get_contents(doc_id)
        except Exception:
            return None
    return _store(request).get_content(doc_id)


# ----------------------------------------------------------------------
# Document protection (protect.password / protect.restrict)
# ----------------------------------------------------------------------
# Protection is stored in the OFFICE FILE itself — w:documentProtection in
# settings.xml, the same place Word stores Restrict-Editing — and is changed
# ONLY through POST /api/documents/{id}/protect, which verifies the current
# password server-side before any mutation (403 on mismatch). POST /save
# refuses content writes while the stored document is restricted, so the
# restriction is enforced at the write path, not as a CSS veil. The HTML
# editor round-trip never carries protection state: because content saves
# are rejected while restricted, protection never needs to ride the
# converters' body markers (the HTML is only convertible again after an
# authenticated /protect has lifted the restriction).
#
# The password scheme is PBKDF2-SHA512 (100k iters, per-document random
# salt) with hex-encoded w:hash / w:salt; cryptAlgorithmSid 14 is SHA-512
# and cryptSpinCount mirrors the iteration count for human readers. This is
# our documented scheme (Word's own finalizer is a different, MD5-based
# construction, so Word can enforce read-only via w:edit but cannot verify
# our hash to lift it — a known, documented interop limitation).


def _doc_protection_detail(data: bytes | None) -> dict:
    """Read {edit, restricted, password_set, salt, hash} from a stored DOCX.

    Any parse failure (0-byte/blank docs, ODT bytes, corrupt files) yields
    the unprotected default — enforcement only ever engages on a valid
    stored DOCX that actually carries w:documentProtection.
    """
    detail = {"edit": "none", "restricted": False, "password_set": False,
              "salt": None, "hash": None}
    if not data:
        return detail
    try:
        doc = DocxDocument(io.BytesIO(data))
        settings = doc.settings.element
        el = settings.find(qn("w:documentProtection"))
        if el is None:
            return detail
        edit = (el.get(qn("w:edit")) or "none").lower()
        detail["edit"] = edit
        detail["restricted"] = edit != "none"
        detail["salt"] = el.get(qn("w:salt"))
        detail["hash"] = el.get(qn("w:hash"))
        detail["password_set"] = bool(detail["hash"])
    except Exception:
        pass  # not a readable DOCX / missing part -> unprotected default
    return detail


def _write_doc_protection(data: bytes, *, restrict: bool,
                          salt_hex: str | None, hash_hex: str | None) -> bytes:
    """Return new DOCX bytes with w:documentProtection (re)written.

    The element is removed entirely when nothing remains to enforce (no
    restriction, no password), so unprotecting a document restores the
    plain file."""
    doc = DocxDocument(io.BytesIO(data))
    settings = doc.settings.element
    el = settings.find(qn("w:documentProtection"))
    if el is not None:
        settings.remove(el)
    if restrict or (salt_hex and hash_hex):
        el = OxmlElement("w:documentProtection")
        if restrict:
            el.set(qn("w:edit"), "readOnly")
        el.set(qn("w:enforcement"), "1")
        if salt_hex and hash_hex:
            el.set(qn("w:hash"), hash_hex)
            el.set(qn("w:salt"), salt_hex)
            el.set(qn("w:cryptAlgorithmSid"), "14")
            el.set(qn("w:cryptSpinCount"), "100000")
        settings.append(el)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _persist_protection(request: Request, doc_id: str, data: bytes) -> None:
    """Persist rewritten document bytes (host store or remote WOPI host)."""
    client = _client(request, doc_id)
    if client:
        client.put_contents(doc_id, data)
    else:
        _store(request).put_content(doc_id, data)


@router.get("/api/documents/{doc_id}/protect")
async def document_protect_state(doc_id: str, request: Request) -> JSONResponse:
    """Current protection state — booleans only, never the hash/salt."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    data = _load_bytes(request, doc_id)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if _document_format(request, doc_id) == "odt":
        return JSONResponse(
            {"error": "document protection is a DOCX feature; not available for ODT"},
            status_code=400,
        )
    d = _doc_protection_detail(data)
    return JSONResponse({"restrict_editing": d["restricted"], "password_set": d["password_set"]})


@router.post("/api/documents/{doc_id}/protect")
async def document_protect(doc_id: str, request: Request) -> JSONResponse:
    """Change protection. Body: ``{"restrict_editing": bool,
    "password": str|null, "clear_password": bool, "current_password": str|null}``.

    Any state change that removes an established password (changing it,
    clearing it, or lifting the restriction it enforces) must present the
    current password — the server verifies it against the stored hash and
    answers 403 on mismatch. Real enforcement: a restricted document also
    refuses content saves until protection is lifted here."""
    if invalid_doc_id(doc_id):
        return JSONResponse({"error": "Invalid file id"}, status_code=400)
    data = _load_bytes(request, doc_id)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if _document_format(request, doc_id) == "odt":
        return JSONResponse(
            {"error": "document protection is a DOCX feature; not available for ODT"},
            status_code=400,
        )
    session = _session_for(request, doc_id)
    if session and session.read_only:
        return JSONResponse(
            {"error": "read-only: another user is editing this document"},
            status_code=403,
        )
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid JSON body: expected an object"}, status_code=400)
    restrict = payload.get("restrict_editing")
    if not isinstance(restrict, bool):
        return JSONResponse({"error": "restrict_editing must be a boolean"}, status_code=400)
    new_password: str | None = None
    password = payload.get("password")
    if password is not None:
        if not isinstance(password, str):
            return JSONResponse({"error": "password must be a string"}, status_code=400)
        if password:
            new_password = password
    clear_password = bool(payload.get("clear_password"))
    if new_password is not None and clear_password:
        return JSONResponse(
            {"error": "cannot both set and clear the password"}, status_code=400)
    current_password = payload.get("current_password")
    if current_password is not None and not isinstance(current_password, str):
        return JSONResponse({"error": "current_password must be a string"}, status_code=400)

    detail = _doc_protection_detail(data)
    # Removing/changing an established password, or lifting the restriction
    # it enforces, requires the current password (server-side check).
    drops_enforcement = detail["password_set"] and (
        new_password is not None or clear_password
        or (not restrict and detail["restricted"])
    )
    if drops_enforcement:
        supplied = current_password if isinstance(current_password, str) else ""
        if not verify_protection_password(supplied, detail["salt"] or "", detail["hash"] or ""):
            return JSONResponse(
                {"error": "document is password-protected: wrong current password"},
                status_code=403,
            )

    final_restrict = restrict
    if not restrict:
        final_salt = final_hash = None  # un-restricting drops enforcement entirely
    elif new_password is not None:
        final_salt, final_hash = hash_protection_password(new_password)
    elif clear_password:
        final_salt = final_hash = None
    elif detail["password_set"]:
        final_salt, final_hash = detail["salt"], detail["hash"]
    else:
        final_salt = final_hash = None

    try:
        out = _write_doc_protection(
            data, restrict=final_restrict, salt_hex=final_salt, hash_hex=final_hash)
    except Exception as exc:
        return JSONResponse({"error": f"protection write failed: {exc}"}, status_code=500)
    _persist_protection(request, doc_id, out)
    return JSONResponse({
        "ok": True,
        "restrict_editing": final_restrict,
        "password_set": final_hash is not None,
    })
