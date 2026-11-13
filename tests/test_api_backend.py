"""backend 显式选择（云端/本地）与 sources.direct 透传。

界面侧边栏要能像 Streamlit 一样切换生成后端；证据卡要能标「页码直达」。
全部用 fake 客户端/检索，不打真 LLM。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import api, usage
from src.ask import _meta
from src.config import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GEN_MODEL,
                        OLLAMA_BASE_URL)


# ---- _resolve：显式 backend 优先于环境变量 ----

def test_resolve_ollama_overrides_env_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    client, model = api._resolve(None, None, backend="ollama")
    assert str(client.base_url).rstrip("/") == OLLAMA_BASE_URL
    assert model == GEN_MODEL


def test_resolve_ollama_ignores_byo_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    client, model = api._resolve("sk-byo", None, backend="ollama")
    assert str(client.base_url).rstrip("/") == OLLAMA_BASE_URL
    assert model == GEN_MODEL


def test_resolve_cloud_uses_byo_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client, model = api._resolve("sk-byo", None, backend="cloud")
    assert str(client.base_url).rstrip("/") == DEEPSEEK_BASE_URL
    assert client.api_key == "sk-byo"
    assert model == DEEPSEEK_MODEL


def test_resolve_cloud_falls_back_to_env_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    client, model = api._resolve(None, None, backend="cloud")
    assert str(client.base_url).rstrip("/") == DEEPSEEK_BASE_URL
    assert client.api_key == "sk-env"
    assert model == DEEPSEEK_MODEL


def test_resolve_cloud_without_key_is_explicit_missing(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("RAG_BASE_URL", raising=False)
    client, _ = api._resolve(None, None, backend="cloud")
    assert str(client.base_url).rstrip("/") == DEEPSEEK_BASE_URL
    assert client.api_key == "missing"


def test_resolve_default_behavior_unchanged(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    monkeypatch.delenv("RAG_BASE_URL", raising=False)
    client, model = api._resolve(None, None)
    assert str(client.base_url).rstrip("/") == DEEPSEEK_BASE_URL
    assert model == DEEPSEEK_MODEL
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    client, model = api._resolve(None, None)
    assert str(client.base_url).rstrip("/") == OLLAMA_BASE_URL
    assert model == GEN_MODEL


def test_resolve_model_override_still_wins(monkeypatch):
    client, model = api._resolve(None, "my-model", backend="ollama")
    assert model == "my-model"


# ---- sources.direct 透传 ----

def _hit(direct=False):
    h = {"year": 2024, "page": 5, "score": 0.9, "text": "原文片段"}
    if direct:
        h["direct"] = True
    return h


def test_meta_sources_carry_direct_flag():
    meta = _meta("q", "q", "答案", [_hit(direct=True), _hit()])
    assert meta["sources"][0]["direct"] is True
    assert meta["sources"][1]["direct"] is False


def test_source_model_defaults_direct_false():
    src = api.Source(year=2024, page=5, score=0.9, snippet="...")
    assert src.direct is False


# ---- 端点把 req.backend 交给 _resolve ----

def _wire(monkeypatch):
    seen = {}

    def fake_resolve(key, model, backend=None):
        seen["backend"] = backend
        seen["key"] = key
        return object(), "deepseek-flash"

    def fake_answer(*args, **kwargs):
        usage.record(prompt_tokens=7, completion_tokens=3)
        return {
            "question": "q", "standalone_question": "q", "answer": "答案",
            "refused": True, "sources": [], "route": "direct", "steps": [],
            "structured": False, "citations": [], "violations": [],
            "degraded": False,
        }

    def fake_stream(*args, **kwargs):
        yield {"type": "done", "meta": fake_answer()}

    monkeypatch.setattr(api, "_get_search", lambda: None)
    monkeypatch.setattr(api, "_resolve", fake_resolve)
    monkeypatch.setattr(api, "answer_auto", fake_answer)
    monkeypatch.setattr(api, "answer_auto_stream", fake_stream)
    monkeypatch.setattr(api._LOGGER, "info", lambda line: None)
    return seen


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_endpoint_passes_backend(monkeypatch, path):
    seen = _wire(monkeypatch)
    resp = TestClient(api.app).post(
        path, json={"question": "q", "backend": "ollama"})
    assert resp.status_code == 200
    assert seen["backend"] == "ollama"


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_endpoint_backend_optional(monkeypatch, path):
    seen = _wire(monkeypatch)
    resp = TestClient(api.app).post(path, json={"question": "q"})
    assert resp.status_code == 200
    assert seen["backend"] is None


def test_endpoint_rejects_unknown_backend(monkeypatch):
    _wire(monkeypatch)
    resp = TestClient(api.app).post(
        "/ask", json={"question": "q", "backend": "azure"})
    assert resp.status_code == 422