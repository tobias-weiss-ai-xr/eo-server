"""Document protection: protect.password + protect.restrict (engine wo-feature).

Covers both halves of the feature:

1. **UI wiring** (executable smoke test, same pattern as test_file_menu.py) —
   the two Protection-tab buttons are real `data-cmd` commands wired in
   editor.js, the protect dialog exists, and the i18n/CSS surfaces ship.

2. **Server enforcement** (host-mode TestClient against a local store) — the
   real gate lives server-side:
   - protection state is stored in the OFFICE FILE itself (w:documentProtection
     in settings.xml), changed only through POST /api/documents/{id}/protect;
   - the password is PBKDF2-hashed per-document (per-document salt, never a
     plaintext, never client-side) and required before any change that removes
     it or lifts the restriction it enforces (403 otherwise);
   - POST /save and PUT /contents both refuse content writes while the stored
     document is restricted — enforcement, not a CSS veil.

The HTML editor round-trip never carries protection state (content saves are
refused while restricted, so it never needs a converter body marker), which is
why no converter change exists for this feature.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.converter import html_to_docx
from src.editor.odt_converter import html_to_odt
from src.editor.router import _doc_protection_detail
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.auth import verify_protection_password
from src.wopi.router import router as wopi_router

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# UI wiring
# ---------------------------------------------------------------------------

def test_protection_buttons_are_real_commands():
    """The two Protection-tab stubs became real commands + a dialog."""
    assert 'data-cmd="protectDialog"' in HTML, "protect.password must be data-cmd=protectDialog"
    assert 'data-cmd="restrictEditing"' in HTML, "protect.restrict must be data-cmd=restrictEditing"
    assert 'data-stub="protect.password"' not in HTML, "protect.password must not stay a stub"
    assert 'data-stub="protect.restrict"' not in HTML, "protect.restrict must not stay a stub"


def test_protect_dialog_present_in_html():
    """The dialog exposes password + restriction controls."""
    for el in ["protect-dialog", "protect-state", "protect-check-restrict",
               "protect-new-password", "protect-current-password",
               "protect-check-clear", "btn-protect-apply", "btn-protect-cancel"]:
        assert f'id="{el}"' in HTML, f"missing #{el} in index.html"


def test_editor_js_wires_protection():
    """Every protection action is a named function wired into runCommand."""
    for fn in ["protectDialog", "confirmProtectDialog", "closeProtectDialog",
               "toggleRestrictEditing", "refreshProtectionState", "applyProtectionView"]:
        assert fn in JS, f"editor.js missing {fn}"
    assert 'cmd === "protectDialog"' in JS, "runCommand must dispatch protectDialog"
    assert 'cmd === "restrictEditing"' in JS, "runCommand must dispatch restrictEditing"
    assert 'api("protect")' in JS, "editor.js must call the protect API"
    assert '"protect-dialog"' in JS, "protect-dialog must be in the modal focus trap"
    assert "refreshProtectionState" in JS, "editor.js must reflect protection after load"


def test_protection_i18n_and_css():
    """Catalog + styling ship with the feature (en + de)."""
    for key in ["Prot.Title", "Prot.StateNone", "Prot.StateRestricted",
                "Prot.StatePassword", "Prot.RestrictLabel", "Prot.NewPassword",
                "Prot.CurrentPassword", "Prot.RemovePassword", "Prot.Apply",
                "Prot.Cancel", "Prot.Hint", "Prot.StatusRestricted",
                "Prot.StatusUnrestricted", "Prot.ErrorWrongPassword"]:
        assert f'"{key}"' in I18N, f"i18n.js missing {key}"
    for key in ["Prot.Password", "Prot.Restrict"]:
        assert I18N.count(f'"{key}"') >= 2, f"{key} must exist in en + de"
    assert "#protect-dialog" in CSS, "style.css must style the protect dialog"


# ---------------------------------------------------------------------------
# Server enforcement
# ---------------------------------------------------------------------------

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
    app.include_router(wopi_router)
    app.include_router(editor_router)
    return app, store


@pytest.fixture
def client(tmp_path):
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.protection_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _seed_doc(client, doc_id="prot.docx", docx: bytes | None = None):
    client.protection_store.init(doc_id, doc_id)
    client.protection_store.put_content(
        doc_id, docx if docx is not None else html_to_docx("<p>hello</p>"))
    return f"/api/documents/{doc_id}"


def test_unprotected_document_defaults(client):
    base = _seed_doc(client)
    res = client.get(base + "/protect")
    assert res.status_code == 200
    assert res.json() == {"restrict_editing": False, "password_set": False}
    # ordinary saves are not affected when nothing is protected
    save = client.post(base + "/save", json={"html": "<p>hi</p>"})
    assert save.status_code == 200 and save.json().get("ok") is True


def test_protect_with_password_is_hashed_and_stored_in_file(client):
    base = _seed_doc(client)
    body = {"restrict_editing": True, "password": "s3cret!", "clear_password": False,
            "current_password": None}
    res = client.post(base + "/protect", json=body)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "restrict_editing": True, "password_set": True}
    # the stored DOCX carries w:documentProtection with a hash, not plaintext
    stored = client.protection_store.get_content("prot.docx")
    detail = _doc_protection_detail(stored)
    assert detail["restricted"] is True and detail["password_set"] is True
    assert detail["hash"] and detail["hash"] != "s3cret!"
    assert "'s3cret!'" not in (stored or b"").decode("latin1", "ignore")
    # the hash is per-document verifiable with the supplied password
    assert verify_protection_password("s3cret!", detail["salt"] or "", detail["hash"] or "")
    # GET /protect reports booleans only (never the hash/salt)
    state = client.get(base + "/protect").json()
    assert state == {"restrict_editing": True, "password_set": True}
    assert "hash" not in state and "salt" not in state


def test_same_password_hashes_differ_per_document(client):
    """Per-document random salt: identical passwords must not collide."""
    hashes = []
    for i in range(2):
        base = _seed_doc(client, f"prot{i}.docx")
        res = client.post(base + "/protect", json={
            "restrict_editing": True, "password": "same-pw", "clear_password": False,
            "current_password": None,
        })
        assert res.status_code == 200
        hashes.append(_doc_protection_detail(
            client.protection_store.get_content(f"prot{i}.docx"))["hash"])
    assert hashes[0] != hashes[1]


def test_save_and_put_blocked_while_restricted(client):
    base = _seed_doc(client)
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": None, "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 200
    # content writes are refused server-side while restricted
    save = client.post(base + "/save", json={"html": "<p>smuggled</p>"})
    assert save.status_code == 403
    assert "protected" in save.json()["error"]
    raw = client.put(base + "/contents", content=b"<not-docx>", headers={"X-WOPI-Override": "PUT"})
    assert raw.status_code == 403
    # the stored document is untouched
    assert _doc_protection_detail(client.protection_store.get_content("prot.docx"))["restricted"]


def test_restrict_without_password_is_freely_unrestrictable(client):
    base = _seed_doc(client)
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": None, "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 200 and r.json()["password_set"] is False
    r = client.post(base + "/protect", json={
        "restrict_editing": False, "password": None, "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 200
    state = r.json()
    assert state["restrict_editing"] is False and state["password_set"] is False
    save = client.post(base + "/save", json={"html": "<p>back</p>"})
    assert save.status_code == 200


def test_unrestrict_requires_current_password(client):
    base = _seed_doc(client)
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": "pw-one", "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 200
    # no current password / wrong password -> 403, protection survives
    for wrong in (None, "nope"):
        r = client.post(base + "/protect", json={
            "restrict_editing": False, "password": None, "clear_password": False,
            "current_password": wrong,
        })
        assert r.status_code == 403
        assert _doc_protection_detail(
            client.protection_store.get_content("prot.docx"))["password_set"]
    # correct current password lifts the restriction
    r = client.post(base + "/protect", json={
        "restrict_editing": False, "password": None, "clear_password": False,
        "current_password": "pw-one",
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restrict_editing": False, "password_set": False}
    save = client.post(base + "/save", json={"html": "<p>edited</p>"})
    assert save.status_code == 200
    detail = _doc_protection_detail(client.protection_store.get_content("prot.docx"))
    assert detail["restricted"] is False and detail["password_set"] is False


def test_remove_password_keeps_restriction_but_needs_current(client):
    base = _seed_doc(client)
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": "pw-a", "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 200
    # clearing without the current password is refused
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": None, "clear_password": True,
        "current_password": None,
    })
    assert r.status_code == 403
    # clearing WITH it drops the password but keeps read-only restriction
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": None, "clear_password": True,
        "current_password": "pw-a",
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restrict_editing": True, "password_set": False}
    assert _doc_protection_detail(client.protection_store.get_content("prot.docx"))["restricted"]


def test_change_password_requires_current_password(client):
    base = _seed_doc(client)
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": "old-secret", "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 200
    # re-setting a new password without the old one -> 403
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": "new-secret", "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 403
    # with the old password the new hash replaces the old one
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": "new-secret", "clear_password": False,
        "current_password": "old-secret",
    })
    assert r.status_code == 200
    detail = _doc_protection_detail(client.protection_store.get_content("prot.docx"))
    assert verify_protection_password("new-secret", detail["salt"] or "", detail["hash"] or "")
    assert not verify_protection_password("old-secret", detail["salt"] or "", detail["hash"] or "")


def test_protect_requires_bool_restrict_and_rejects_bad_json(client):
    base = _seed_doc(client)
    r = client.post(base + "/protect", json={"password": "x"})
    assert r.status_code == 400
    r = client.post(base + "/protect", content="not-json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": "x", "clear_password": True,
        "current_password": None,
    })
    assert r.status_code == 400  # set + clear at once


def test_odt_protection_is_refused_loudly(client):
    """ODT has no documentProtection surface → loud 400, never a silent no-op."""
    odt_bytes = html_to_odt("<p>odt</p>")
    base = _seed_doc(client, "odt.odt", docx=odt_bytes)
    r = client.get(base + "/protect")
    assert r.status_code == 400 and "ODT" in r.json()["error"]
    r = client.post(base + "/protect", json={
        "restrict_editing": True, "password": "x", "clear_password": False,
        "current_password": None,
    })
    assert r.status_code == 400 and "ODT" in r.json()["error"]


def test_protection_survives_format_error_paths():
    """Blank/absent content reads as unprotected (no crash on write gate)."""
    assert _doc_protection_detail(None)["restricted"] is False
    assert _doc_protection_detail(b"")["restricted"] is False
    assert _doc_protection_detail(b"not-a-docx")["restricted"] is False
