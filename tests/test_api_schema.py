import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import AskResponse


def test_response_accepts_structured_meta():
    meta = {
        "question": "2024年营业总收入？",
        "standalone_question": "2024年营业总收入？",
        "answer": "1. 营业总收入 1741.44 亿元 [来源: 2024年报 P8]",
        "refused": False,
        "sources": [{"year": 2024, "page": 8, "score": 0.9, "snippet": "..."}],
        "model": "deepseek-flash",
        "route": "direct",
        "steps": [],
        "hits": [{"id": "2024-p008-c00"}],   # 额外字段应被忽略
        "structured": True,
        "citations": ["2024年报 P8"],
        "violations": [],
        "degraded": False,
        "claims": [{"text": "x", "evidence": ["E1"]}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "calls": 1,
        "calc_note": "",
    }
    resp = AskResponse(**meta)
    assert resp.structured is True
    assert resp.citations == ["2024年报 P8"]
    assert resp.violations == []
