import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ask


HITS = [
    {"id": "2024-p008-c00", "year": 2024, "page": 8, "score": 0.9,
     "context": "语境",
     "text": "语境\n2024年营业总收入为174,144,069,958.25元"},
    {"id": "2024-p056-c00", "year": 2024, "page": 56, "score": 0.8,
     "context": "语境", "text": "语境\n营业收入 17,089,915.23 万元"},
]


class Msg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class Choice:
    def __init__(self, msg):
        self.message = msg


class Usage:
    prompt_tokens = 100
    completion_tokens = 20


class Resp:
    def __init__(self, msg):
        self.choices = [Choice(msg)]
        self.usage = Usage()


class Completions:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def create(self, **kw):
        item = self.payloads[0] if len(self.payloads) == 1 else self.payloads.pop(0)
        return item if isinstance(item, Resp) else Resp(item)


class Client:
    def __init__(self, payloads):
        self.chat = type("C", (), {"completions": Completions(payloads)})()


def structured_payload(text, eid="E1", numbers=None):
    return json.dumps({
        "refused": False,
        "claims": [{"text": text, "evidence": [eid],
                    "numbers": numbers or [], "kind": "extract"}],
        "calculation": None}, ensure_ascii=False)


def test_direct_structured_renders_program_page():
    search = lambda q, top_k=5, **kw: HITS[:top_k]
    client = Client([Msg(structured_payload("营业总收入 1741.44 亿元",
                                            numbers=["1741.44亿元"]))])
    meta = ask.answer_auto(client, search, "2024年营业总收入是多少？", "m")
    assert meta["structured"] is True
    assert "[来源: 2024年报 P8]" in meta["answer"]
    assert meta["citations"] == ["2024年报 P8"]
    assert meta["violations"] == []


def test_direct_rejects_number_not_in_evidence(monkeypatch):
    monkeypatch.setattr(ask.structured_claims, "page_map", lambda: {})
    search = lambda q, top_k=5, **kw: HITS[:top_k]
    # 数字在别的页（P56），却引用 P8 → 校验必须报违规
    client = Client([Msg(structured_payload("营收 17,089,915.23 万元",
                                            numbers=["17,089,915.23"])),
                     Msg(structured_payload("营收 17,089,915.23 万元",
                                            numbers=["17,089,915.23"]))])
    meta = ask.answer_auto(client, search, "2024年营业收入是多少？", "m")
    assert meta["violations"], meta
    assert meta["degraded"] is True


def test_refusal_retry_uses_expanded_hits():
    calls = {"n": 0}

    def search(q, top_k=5, **kw):
        calls["n"] += 1
        return HITS[:top_k]

    refuse = json.dumps({"refused": True, "claims": [], "calculation": None})
    good = structured_payload("营业总收入 1741.44 亿元", numbers=["1741.44亿元"])
    client = Client([Msg(refuse), Msg(good)])
    meta = ask.answer_auto(client, search, "2024年营业总收入是多少？", "m")
    assert meta["refused"] is False
    assert calls["n"] == 2  # 首检索 + 扩检索
    assert meta["sources"]


def test_agent_path_attributes_pages(monkeypatch):
    monkeypatch.setattr(ask, "get_page_reader", lambda: (lambda y, p: HITS))
    monkeypatch.setattr(ask.structured_claims, "page_map", lambda: {})

    class ToolCall:
        def __init__(self):
            self.id = "t1"
            self.type = "function"
            self.function = type("F", (), {
                "name": "search_reports",
                "arguments": json.dumps({"query": "2024年营业总收入"})})()

    first = Resp(Msg("", [ToolCall()]))
    final = Resp(Msg("营业总收入 1741.44 亿元 [来源: 2024年报 P999]"))
    client = Client([first, final])

    def search(q, top_k=5, **kw):
        return HITS[:top_k]

    meta = ask.answer_auto(client, search, "2024年和2023年营业总收入分别是多少？", "m")
    assert meta["route"] == "agent"
    assert "P999" not in meta["answer"]
    assert meta["citations"] == ["2024年报 P8"]
    assert meta["violations"] == []


def test_agent_path_flags_unlocatable_number(monkeypatch):
    monkeypatch.setattr(ask, "get_page_reader", lambda: (lambda y, p: HITS))
    monkeypatch.setattr(ask.structured_claims, "page_map", lambda: {})

    class ToolCall:
        def __init__(self):
            self.id = "t1"
            self.type = "function"
            self.function = type("F", (), {
                "name": "search_reports",
                "arguments": json.dumps({"query": "2024年营业总收入"})})()

    first = Resp(Msg("", [ToolCall()]))
    final = Resp(Msg("营业总收入 1741.44 亿元。毛利率 99.99%。"))
    client = Client([first, final])

    def search(q, top_k=5, **kw):
        return HITS[:top_k]

    meta = ask.answer_auto(client, search, "2024年和2023年营业总收入分别是多少？", "m")
    assert meta["answer"].count("[来源: 2024年报 P8]") == 1
    assert meta["citations"] == ["2024年报 P8"]
    assert any("99.99%" in v for v in meta["violations"])


