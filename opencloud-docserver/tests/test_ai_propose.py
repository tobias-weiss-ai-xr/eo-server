"""AI propose endpoint: instruction -> applied ops (flagship v3-1).

A scripted model (the test-side seam for "any vendor") is registered in
``ai.propose.MODEL_REGISTRY``; the endpoint runs it through the agent tool
surface. Assertions: ops JSON lands attributed and reviewable, unknown
models are a typed 503, missing instruction is a 400, and the ops response
carries text coordinates for editor-side tracked-span projection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ai import propose as propose_mod
from src.config import Config
from src.editor.collab import reset_hub
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router


class ScriptedModel:
    """ModelFn: return the queued calls one per turn (each turn is a list),
    then stop."""

    def __init__(self, *calls: dict) -> None:
        self.calls = list(calls)

    def __call__(self, messages):
        return [self.calls.pop(0)] if self.calls else []


@pytest.fixture
def client(tmp_path):
    reset_hub()
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
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        store.init("doc1", "review.txt")
        store.put_content("doc1", b"Hello agent world")
        c.post(
            "/api/documents/doc1/collab/sync",
            json={"client_id": "human-1", "text": "Hello agent world"},
        )
        yield c
    reset_hub()
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")
    propose_mod.MODEL_REGISTRY.pop("scripted", None)


def test_propose_applies_scripted_model_ops_and_returns_ops_json(client):
    propose_mod.register_model(
        "scripted",
        ScriptedModel(
            {"name": "apply_ops", "arguments": {"doc_id": "doc1",
             "client_id": "agent=ai-propose:scripted",
             "ops": [{"t": "ins", "at": 17, "text": "!"}]}},
        ),
    )
    res = client.post(
        "/api/documents/doc1/ai/propose",
        json={"instruction": "exclaim the ending", "model": "scripted"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["report"]["ops_applied"] == 1
    assert body["report"]["text"] == "Hello agent world!"
    # ops JSON: attributable, reviewable, text coordinates for span projection
    assert body["ops"], "propose must return the new agent ops"
    op = body["ops"][0]
    assert op["agent"].startswith("agent=ai-propose:scripted")
    assert op["type"] == "insert" and op["text"] == "!"
    assert isinstance(op["rev"], int)
    # the proposal is visible to the review endpoint (same op stream)
    review = client.get("/api/documents/doc1/ai/review").json()
    assert any(o["text"] == "!" for o in review["ops"])


def test_propose_unknown_model_is_typed_503(client):
    res = client.post(
        "/api/documents/doc1/ai/propose",
        json={"instruction": "hi", "model": "nope"},
    )
    assert res.status_code == 503
    body = res.json()
    assert body["error"] == "model-not-registered"
    assert body["model"] == "nope"
    assert "scripted" in body["registered"] or body["registered"] == []


def test_propose_requires_instruction(client):
    res = client.post("/api/documents/doc1/ai/propose", json={"model": "x"})
    assert res.status_code == 400
    assert res.json()["error"] == "instruction required"


def test_propose_unknown_document_is_404(client):
    propose_mod.register_model("scripted", ScriptedModel())
    res = client.post(
        "/api/documents/missing/ai/propose",
        json={"instruction": "hi", "model": "scripted"},
    )
    assert res.status_code == 404


def test_propose_delete_op_round_trips_through_reject(client):
    """A scripted deletion proposal is reviewable: rejecting it restores the
    exact pre-proposal text (the review machinery is the accept/reject)."""
    propose_mod.register_model(
        "scripted",
        ScriptedModel(
            {"name": "apply_ops", "arguments": {"doc_id": "doc1",
             "client_id": "agent=ai-propose:scripted",
             "ops": [{"t": "del", "at": 6, "end": 12}]}},  # delete "agent "
        ),
    )
    res = client.post(
        "/api/documents/doc1/ai/propose",
        json={"instruction": "drop the word agent", "model": "scripted"},
    ).json()
    assert res["report"]["text"] == "Hello world"
    rev = res["ops"][0]["rev"]
    rej = client.post("/api/documents/doc1/ai/review/reject",
                      json={"revs": [rev]})
    assert rej.status_code == 200
    final = client.post("/api/documents/doc1/collab/sync",
                        json={"client_id": "human-1", "text": "Hello agent world"})
    assert final.json()["text"] == "Hello agent world"
