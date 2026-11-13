"""/ask 与 /ask/stream 的 followups：拒答跳过、失败不阻塞、用量计入同一请求。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from src import api, usage

FU = ["2024年净利润是多少？", "2024年分红方案是什么？"]
ANSWER = "1741.44 亿元 [来源: 2024年报 P5]"


def _meta(refused=False):
    return {
        "question": "2024年营业总收入？",
        "standalone_question": "2024年营业总收入？",
        "answer": ANSWER,
        "refused": refused,
        "sources": [{"year": 2024, "page": 5, "score": 0.9, "snippet": "..."}],
        "route": "direct",
        "steps": [],
        "structured": True,
        "citations": ["2024年报 P5"],
        "violations": [],
        "degraded": False,
    }


def _wire(monkeypatch, *, refused=False, fus=FU):
    """接管检索/客户端/生成/追问，返回追问调用记录。"""
    seen = []

    def fake_followups(client, model, question, answer):
        seen.append((question, answer))
        usage.record(prompt_tokens=5, completion_tokens=2)
        return fus

    def fake_answer(*args, **kwargs):
        usage.record(prompt_tokens=7, completion_tokens=3)
        return _meta(refused)

    def fake_stream(*args, **kwargs):
        usage.record(prompt_tokens=7, completion_tokens=3)
        yield {"type": "status", "label": "检索中"}
        yield {"type": "delta", "text": ANSWER}
        yield {"type": "done", "meta": _meta(refused)}

    monkeypatch.setattr(api, "_get_search", lambda: None)
    monkeypatch.setattr(api, "_resolve",
                        lambda key, model, backend=None: (object(), "deepseek-flash"))
    monkeypatch.setattr(api, "answer_auto", fake_answer)
    monkeypatch.setattr(api, "answer_auto_stream", fake_stream)
    monkeypatch.setattr(api, "gen_followups", fake_followups)
    monkeypatch.setattr(api._LOGGER, "info", lambda line: None)
    return seen


def _post(path="/ask"):
    return TestClient(api.app).post(path, json={"question": "2024年营业总收入？"})


def _done_meta(resp):
    data = [json.loads(line[6:]) for line in resp.text.splitlines()
            if line.startswith("data: ")]
    return next(x for x in data if "meta" in x)["meta"]


def test_ask_returns_followups_and_counts_their_tokens(monkeypatch):
    seen = _wire(monkeypatch)
    resp = _post()
    assert resp.status_code == 200
    body = resp.json()
    assert body["followups"] == FU
    assert seen == [("2024年营业总收入？", ANSWER)]
    assert body["usage"] == {"prompt_tokens": 12, "completion_tokens": 5,
                             "calls": 2}


def test_ask_refused_skips_followups(monkeypatch):
    seen = _wire(monkeypatch, refused=True)
    body = _post().json()
    assert body["refused"] is True
    assert body["followups"] == []
    assert seen == []


def test_ask_empty_followups_keeps_answer(monkeypatch):
    _wire(monkeypatch, fus=[])
    resp = _post()
    assert resp.status_code == 200
    assert resp.json()["answer"] == ANSWER
    assert resp.json()["followups"] == []


def test_stream_done_meta_has_followups(monkeypatch):
    seen = _wire(monkeypatch)
    resp = _post("/ask/stream")
    assert resp.status_code == 200
    meta = _done_meta(resp)
    assert meta["followups"] == FU
    assert seen == [("2024年营业总收入？", ANSWER)]
    assert meta["usage"] == {"prompt_tokens": 12, "completion_tokens": 5,
                             "calls": 2}
    assert resp.text.index("event: delta") < resp.text.index("event: done")


def test_stream_refused_skips_followups(monkeypatch):
    seen = _wire(monkeypatch, refused=True)
    meta = _done_meta(_post("/ask/stream"))
    assert meta["followups"] == []
    assert seen == []