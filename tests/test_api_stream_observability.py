import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from src import api, usage


def test_stream_done_meta_has_usage_and_one_log(monkeypatch):
    def fake_stream(*args, **kwargs):
        usage.record(prompt_tokens=11, completion_tokens=4)
        yield {"type": "status", "label": "检索中"}
        yield {"type": "delta", "text": "答案"}
        yield {"type": "done", "meta": {
            "question": "q", "standalone_question": "q", "answer": "答案",
            "refused": False, "sources": [], "route": "direct", "steps": [],
            "structured": False, "citations": [], "violations": [],
            "degraded": False,
        }}

    lines = []
    monkeypatch.setattr(api, "_get_search", lambda: None)
    monkeypatch.setattr(api, "_resolve",
                        lambda key, model, backend=None: (object(), "deepseek-flash"))
    monkeypatch.setattr(api, "answer_auto_stream", fake_stream)
    monkeypatch.setattr(api._LOGGER, "info", lines.append)

    resp = TestClient(api.app).post("/ask/stream", json={"question": "q"})
    assert resp.status_code == 200
    assert "event: done" in resp.text
    data = [json.loads(line[6:]) for line in resp.text.splitlines()
            if line.startswith("data: ")]
    done = next(x for x in data if "meta" in x)
    meta = done["meta"]
    assert meta["usage"] == {"prompt_tokens": 11, "completion_tokens": 4,
                             "calls": 1}
    assert meta["cost_cny"] > 0
    assert meta["request_id"]
    assert len(lines) == 1
    log = json.loads(lines[0])
    assert log["status"] == 200
    assert log["prompt_tokens"] == 11


def test_stream_disconnect_logs_aborted(monkeypatch):
    def fake_stream(*args, **kwargs):
        yield {"type": "status", "label": "检索中"}
        yield {"type": "delta", "text": "答"}
        # 客户端断开：生成器不会走到 done

    lines = []
    monkeypatch.setattr(api, "_get_search", lambda: None)
    monkeypatch.setattr(api, "_resolve",
                        lambda key, model, backend=None: (object(), "deepseek-flash"))
    monkeypatch.setattr(api, "answer_auto_stream", fake_stream)
    monkeypatch.setattr(api._LOGGER, "info", lines.append)

    resp = api.ask_stream(api.AskRequest(question="q"))

    async def consume_one():
        it = resp.body_iterator
        first = await it.__anext__()
        assert "event: status" in first
        await it.aclose()

    asyncio.run(consume_one())

    assert len(lines) == 1
    log = json.loads(lines[0])
    assert log["status"] == "aborted"
    assert log["error"] == "client disconnected"
