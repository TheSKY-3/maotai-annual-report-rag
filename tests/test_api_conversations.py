"""P1 会话 API：匿名 session cookie、会话 CRUD、/ask 落库、session 归属。

生成端按 test_api_followups 同款 mock；存储用内存 SQLite，每测试隔离。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import api, usage
from src.conversations import SqliteConversationStore

ANSWER = "1741.44 亿元 [来源: 2024年报 P5]"
QUESTION = "2024年营业总收入？"


def _meta():
    return {
        "question": QUESTION,
        "standalone_question": QUESTION,
        "answer": ANSWER,
        "refused": False,
        "sources": [{"year": 2024, "page": 5, "score": 0.9, "snippet": "..."}],
        "route": "direct", "steps": [], "structured": True,
        "citations": ["2024年报 P5"], "violations": [], "degraded": False,
    }


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    monkeypatch.setattr(api, "_store", SqliteConversationStore(":memory:"))

    def fake_answer(*a, **k):
        usage.record(prompt_tokens=7, completion_tokens=3)
        return _meta()

    def fake_stream(*a, **k):
        usage.record(prompt_tokens=7, completion_tokens=3)
        yield {"type": "status", "label": "检索中"}
        yield {"type": "delta", "text": ANSWER}
        yield {"type": "done", "meta": _meta()}

    monkeypatch.setattr(api, "_get_search", lambda: None)
    monkeypatch.setattr(api, "_resolve",
                        lambda key, model, backend=None: (object(), "deepseek-flash"))
    monkeypatch.setattr(api, "answer_auto", fake_answer)
    monkeypatch.setattr(api, "answer_auto_stream", fake_stream)
    monkeypatch.setattr(api, "gen_followups", lambda *a, **k: [])
    monkeypatch.setattr(api._LOGGER, "info", lambda line: None)
    yield


def test_list_issues_cookie_and_empty():
    c = TestClient(api.app)
    r = c.get("/conversations")
    assert r.status_code == 200
    assert r.json()["conversations"] == []
    assert "maotai_session" in r.cookies


def test_create_list_get_delete():
    c = TestClient(api.app)
    cid = c.post("/conversations", json={"title": "t"}).json()["id"]
    items = c.get("/conversations").json()["conversations"]
    assert [i["id"] for i in items] == [cid]
    assert items[0]["title"] == "t"
    assert c.get(f"/conversations/{cid}").json()["messages"] == []
    c.delete(f"/conversations/{cid}")
    assert c.get("/conversations").json()["conversations"] == []


def test_session_isolation():
    c1 = TestClient(api.app)
    c2 = TestClient(api.app)
    cid = c1.post("/conversations", json={"title": "a"}).json()["id"]
    assert c2.get("/conversations").json()["conversations"] == []
    assert c2.get(f"/conversations/{cid}").status_code == 404
    assert c2.delete(f"/conversations/{cid}").status_code == 404


def test_ask_persists_exchange():
    c = TestClient(api.app)
    cid = c.post("/conversations", json={}).json()["id"]
    r = c.post("/ask", json={"question": QUESTION, "conversation_id": cid})
    assert r.status_code == 200
    msgs = c.get(f"/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == QUESTION
    assert msgs[1]["content"] == ANSWER
    assert msgs[1]["meta"]["citations"] == ["2024年报 P5"]


def test_ask_without_conversation_id_does_not_persist():
    c = TestClient(api.app)
    c.post("/ask", json={"question": QUESTION})
    assert c.get("/conversations").json()["conversations"] == []


def test_ask_stream_persists_exchange():
    c = TestClient(api.app)
    cid = c.post("/conversations", json={}).json()["id"]
    r = c.post("/ask/stream", json={"question": QUESTION, "conversation_id": cid})
    assert r.status_code == 200
    msgs = c.get(f"/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == ANSWER


def test_ask_rejects_foreign_conversation():
    c1 = TestClient(api.app)
    c2 = TestClient(api.app)
    cid = c1.post("/conversations", json={}).json()["id"]
    r = c2.post("/ask", json={"question": QUESTION, "conversation_id": cid})
    assert r.status_code == 404


def test_rename():
    c = TestClient(api.app)
    cid = c.post("/conversations", json={"title": "old"}).json()["id"]
    c.patch(f"/conversations/{cid}", json={"title": "new"})
    assert c.get("/conversations").json()["conversations"][0]["title"] == "new"