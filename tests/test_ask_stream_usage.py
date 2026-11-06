import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ask, usage


class _FakeCompletions:
    def create(self, **kwargs):
        assert kwargs.get("stream") is True
        final = SimpleNamespace(
            choices=[], usage=SimpleNamespace(prompt_tokens=7,
                                              completion_tokens=3))
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="174,144,069,958.25 "))],
                usage=None),
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="[来源: 2024年报 P5]"))],
                usage=None),
            final,
        ]
        return iter(chunks)


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_direct_stream_records_stream_usage(monkeypatch):
    monkeypatch.setattr(ask, "structured_enabled", lambda: False)
    monkeypatch.setattr(ask, "_grounding_fix",
                        lambda client, model, text, hits: text)
    hits = [{"id": "h1", "year": 2024, "page": 5, "score": 0.9,
             "text": "2024年营业总收入 174,144,069,958.25 元"}]

    events = []
    with usage.collect() as bucket:
        for ev in ask.answer_auto_stream(
                _FakeClient(), lambda q, top_k=5, **kw: hits,
                "2024年营业总收入？", "fake"):
            events.append(ev)

    assert [e["type"] for e in events].count("delta") == 2
    assert events[-1]["type"] == "done"
    assert bucket == {"prompt_tokens": 7, "completion_tokens": 3,
                      "calls": 1}
