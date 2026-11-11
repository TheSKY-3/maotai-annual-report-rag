"""相邻页续接测试（轴2轻半 Part 2）。

跨页表格只检索到上半页时，补上下一页（P+1）整页原文，让模型读全。
只增强答案层证据，不改检索命中；下一页已在命中里或不存在则跳过；不连锁。
用真实整页映射驱动（2023 P34 含分红关键数字 308.76）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import claims


def _hit(year, page, hid=None):
    return {"id": hid or f"{year}-p{page:03d}-c00", "year": year,
            "page": page, "text": "片段", "score": 0.9}


def test_adjacent_adds_next_page_with_full_text():
    adj = claims.adjacent_page_hits([_hit(2023, 33)])
    p34 = [h for h in adj if h["page"] == 34]
    assert len(p34) == 1
    assert "308.76" in p34[0]["text"]  # 下一页整页原文含分红关键数字


def test_adjacent_skips_page_already_in_hits():
    adj = claims.adjacent_page_hits([_hit(2023, 33), _hit(2023, 34)])
    assert all(h["page"] != 34 for h in adj)  # P34 已命中，不重复补


def test_adjacent_skips_missing_page():
    assert claims.adjacent_page_hits([_hit(2023, 9999)]) == []


def test_adjacent_no_cascade():
    adj = claims.adjacent_page_hits([_hit(2023, 33)])
    assert [h["page"] for h in adj] == [34]  # 只补 P+1，不连锁补 P+2


def test_adjacent_does_not_mutate_input():
    hits = [_hit(2023, 33)]
    claims.adjacent_page_hits(hits)
    assert len(hits) == 1


def test_adjacent_empty_hits():
    assert claims.adjacent_page_hits([]) == []


def test_adjacent_dedups_same_next_page():
    # 同页多个片段命中，下一页只补一次
    adj = claims.adjacent_page_hits([_hit(2023, 33, "2023-p033-c01"),
                                     _hit(2023, 33, "2023-p033-c02")])
    assert [h["page"] for h in adj] == [34]