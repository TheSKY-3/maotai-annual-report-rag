import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from src import api, usage


def test_usage_collect_counts_only_current_bucket():
    before = usage.snapshot()
    with usage.collect() as bucket:
        usage.record(prompt_tokens=7, completion_tokens=3)
        usage.record(prompt_tokens=2, completion_tokens=1)
    assert bucket == {"prompt_tokens": 9, "completion_tokens": 4, "calls": 2}
    after = usage.snapshot()
    assert after["prompt_tokens"] - before["prompt_tokens"] == 9
    assert after["completion_tokens"] - before["completion_tokens"] == 4
    assert after["calls"] - before["calls"] == 2


def test_ready_reports_index_and_model(monkeypatch):
    monkeypatch.setattr(api, "_default_backend",
                        lambda: ("cloud", "deepseek-flash", True))
    resp = TestClient(api.app).get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ready", "degraded")
    assert body["index"]["kind"] in ("contextual", "baseline")
    assert body["index"]["chunks"] > 0
    assert body["model"] == "deepseek-flash"
    assert body["api_key_configured"] is True
    assert "sk-" not in resp.text


def test_ready_returns_503_with_reason(monkeypatch):
    monkeypatch.setattr(api, "index_status", lambda: {
        "status": "not_ready", "loaded": False,
        "index": {"kind": "baseline", "chunks": 0, "years": [],
                  "chunks_file": "missing", "index_file": "missing",
                  "missing_years": [2022, 2023, 2024, 2025]},
        "embedding": {}, "reasons": ["块文件不存在：missing"],
    })
    monkeypatch.setattr(api, "_default_backend",
                        lambda: ("ollama", "qwen3:4b", False))
    resp = TestClient(api.app).get("/ready")
    assert resp.status_code == 503
    assert "块文件不存在" in resp.json()["reasons"][0]


def test_ask_returns_usage_cost_and_one_json_log(monkeypatch):
    def fake_answer_auto(*args, **kwargs):
        usage.record(prompt_tokens=7, completion_tokens=3)
        return {
            "question": "2024年营业总收入？",
            "standalone_question": "2024年营业总收入？",
            "answer": "1741.44 亿元 [来源: 2024年报 P5]",
            "refused": False,
            "sources": [{"year": 2024, "page": 5, "score": 0.9,
                         "snippet": "..."}],
            "route": "direct",
            "steps": [],
            "structured": True,
            "citations": ["2024年报 P5"],
            "violations": [],
            "degraded": False,
        }

    lines = []
    monkeypatch.setattr(api, "_get_search", lambda: None)
    monkeypatch.setattr(api, "_resolve",
                        lambda key, model, backend=None: (object(), "deepseek-flash"))
    monkeypatch.setattr(api, "answer_auto", fake_answer_auto)
    monkeypatch.setattr(api._LOGGER, "info", lines.append)

    resp = TestClient(api.app).post("/ask", json={
        "question": "2024年营业总收入？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"] == {"prompt_tokens": 7, "completion_tokens": 3,
                             "calls": 1}
    assert body["cost_cny"] > 0
    assert body["request_id"]
    assert body["latency_ms"] >= 0

    assert len(lines) == 1
    log = json.loads(lines[0])
    assert log["event"] == "ask"
    assert log["status"] == 200
    assert log["request_id"] == body["request_id"]
    assert log["prompt_tokens"] == 7
    assert log["completion_tokens"] == 3
    assert log["cost_cny"] == body["cost_cny"]
    assert log["route"] == "direct"
    assert log["citations"] == 1
