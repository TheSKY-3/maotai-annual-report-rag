"""按题型分配召回（跨年题按年分名额 + 可选页级去重）的判分测试。

只用假检索器，不加载模型、不发网络请求。核心 invariant：
返回块数不得超过 top_k——否则 hit@k 是靠多返回结果刷出来的，不是真提升。
"""
from __future__ import annotations

import pytest

from scripts.per_year_recall import PerYearRecall


class FakeBase:
    """按年份返回预设块；记录每次调用的 (query, top_k, year)。"""

    def __init__(self, by_year: dict[int, list[tuple[int, float]]],
                 direct: bool = False):
        self.by_year = by_year
        self.direct = direct
        self.calls: list[tuple[str, int, object]] = []

    def search(self, query, top_k=5, year=None):
        self.calls.append((query, top_k, year))
        years = [year] if isinstance(year, int) else sorted(self.by_year)
        out = []
        for y in years:
            for i, (page, score) in enumerate(self.by_year.get(y, [])):
                out.append({"id": f"{y}-p{page:03d}-c{i:02d}",
                            "text": f"y{y}p{page}",
                            "year": y, "page": page, "score": score,
                            **({"direct": True} if self.direct else {})})
        out.sort(key=lambda c: -c["score"])
        return out[:top_k]


def pages(hits):
    return [(h["year"], h["page"]) for h in hits]


# ---------- 单年 / 无年份：行为必须与生产一致 ----------

def test_单年题原样委托():
    base = FakeBase({2024: [(63, 0.9), (17, 0.8)]})
    r = PerYearRecall(base)
    got = r.search("2024年营业总收入是多少？", 5)
    assert base.calls == [("2024年营业总收入是多少？", 5, None)]
    assert pages(got) == [(2024, 63), (2024, 17)]


def test_无年份题原样委托():
    base = FakeBase({2024: [(63, 0.9)]})
    r = PerYearRecall(base)
    r.search("茅台的分红方案是什么？", 5)
    assert base.calls == [("茅台的分红方案是什么？", 5, None)]


def test_未知年份不算跨年():
    base = FakeBase({2024: [(63, 0.9)]})
    r = PerYearRecall(base)
    r.search("2024年和1999年的收入", 5)
    assert base.calls == [("2024年和1999年的收入", 5, None)]


def test_显式传year时不拆分():
    base = FakeBase({2024: [(63, 0.9)], 2022: [(110, 0.9)]})
    r = PerYearRecall(base)
    r.search("2024年比2022年增长多少", 5, year=2024)
    assert base.calls == [("2024年比2022年增长多少", 5, 2024)]


def test_页码直达题不拆分():
    base = FakeBase({2024: [(63, 1.0)]}, direct=True)
    r = PerYearRecall(base)
    got = r.search("2024年报P63和2022年报P110写了什么", 5)
    assert len(base.calls) == 1
    assert base.calls[0][2] is None
    assert all(h.get("direct") for h in got)


# ---------- 跨年题：核心目的 ----------

def test_跨年题两年都有代表():
    # 生产行为：一次融合排序，2024 的高分块把 2022 全挤掉
    base = FakeBase({2024: [(63, 0.95), (17, 0.9), (9, 0.85)],
                     2022: [(110, 0.6), (17, 0.5)]})
    r = PerYearRecall(base)
    got = r.search("2024年比2022年营业总收入增长多少？", 5)
    ys = {h["year"] for h in got}
    assert ys == {2024, 2022}, "跨年题必须两年都拿到名额"


def test_跨年题返回块数不超过top_k():
    base = FakeBase({2024: [(63, 0.9), (17, 0.8), (9, 0.7), (5, 0.6)],
                     2022: [(110, 0.5), (17, 0.4), (8, 0.3)]})
    r = PerYearRecall(base)
    for k in (1, 2, 3, 5, 8):
        got = r.search("2024年比2022年增长多少？", k)
        assert len(got) <= k, f"k={k} 时返回 {len(got)} 块，靠多返回刷分"


def test_跨年题每年各调用一次且限定到该年():
    base = FakeBase({2024: [(63, 0.9)], 2022: [(110, 0.5)]})
    r = PerYearRecall(base)
    r.search("2024年比2022年增长多少？", 5)
    assert sorted(c[2] for c in base.calls) == [2022, 2024]


def test_某年召回不足时另一年补满():
    base = FakeBase({2024: [(63, 0.9)],
                     2022: [(110, 0.5), (17, 0.4), (8, 0.3), (5, 0.2)]})
    r = PerYearRecall(base)
    got = r.search("2024年比2022年增长多少？", 5)
    assert len(got) == 5, "名额不该浪费"


def test_三年题名额总和仍为top_k():
    base = FakeBase({2024: [(63, 0.9)], 2023: [(58, 0.8)], 2022: [(110, 0.7)]})
    r = PerYearRecall(base)
    got = r.search("2022年2023年2024年的收入分别是多少？", 5)
    assert len(got) <= 5
    assert {h["year"] for h in got} == {2022, 2023, 2024}


def test_跨年题不重复返回同一块():
    base = FakeBase({2024: [(63, 0.9), (63, 0.8)], 2022: [(110, 0.5)]})
    r = PerYearRecall(base)
    got = r.search("2024年比2022年增长多少？", 5)
    assert len({h["id"] for h in got}) == len(got)


# ---------- 页级去重（可选） ----------

def test_页级去重_同页只留最高分块():
    base = FakeBase({2024: [(63, 0.9), (63, 0.8), (17, 0.7)]})
    r = PerYearRecall(base, dedup_pages=True)
    got = r.search("2024年收入", 5)
    assert pages(got) == [(2024, 63), (2024, 17)]
    assert got[0]["score"] == 0.9


def test_页级去重默认关闭_保持生产行为():
    base = FakeBase({2024: [(63, 0.9), (63, 0.8), (17, 0.7)]})
    r = PerYearRecall(base)
    got = r.search("2024年收入", 5)
    assert pages(got) == [(2024, 63), (2024, 63), (2024, 17)]


def test_页级去重对跨年题也生效():
    base = FakeBase({2024: [(63, 0.9), (63, 0.8), (17, 0.7)],
                     2022: [(110, 0.5), (110, 0.4)]})
    r = PerYearRecall(base, dedup_pages=True)
    got = r.search("2024年比2022年增长多少？", 5)
    assert len(pages(got)) == len(set(pages(got)))
    assert {h["year"] for h in got} == {2024, 2022}


def test_空召回不报错():
    base = FakeBase({})
    r = PerYearRecall(base)
    assert r.search("2024年比2022年增长多少？", 5) == []


def test_关掉按年分配时只做页级去重():
    base = FakeBase({2024: [(63, 0.9), (63, 0.8)], 2022: [(110, 0.5)]})
    r = PerYearRecall(base, dedup_pages=True, per_year=False)
    got = r.search("2024年比2022年增长多少？", 5)
    assert len(base.calls) == 1 and base.calls[0][2] is None
    assert pages(got) == [(2024, 63), (2022, 110)]