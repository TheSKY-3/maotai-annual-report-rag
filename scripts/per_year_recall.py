"""按题型分配召回：跨年题每年各拿名额，而不是让一年的页挤掉另一年。

实验层策略，**不改生产接线**（src/pipeline.py 仍是 E5）。

病根（review/phase_a_metrics.md:33-37，调参 50 题修正口径）：
  单页事实题  hit@5 76.7% / all@5 76.7%
  跨页题      hit@5 90.0% / all@5 40.0%
  跨年题      hit@5 80.0% / all@5 20.0%
HybridRetriever.search 对跨年题已把年份限定成两年集合，但仍是一次融合
排序取前 5——高分那一年的块会把另一年全部挤出榜。这里改成每年各召回
top_k，再轮转合并成 top_k，两年都保证有位置。

公平性：合并后块数**不得超过 top_k**。多返回结果能把 hit@k 刷上去，
那不是检索变好，是尺子被改了。

实测结论（2026-11-13，零 API，results/recall_alloc_*.json）：**不上生产。**
  调参 50 题   prod hit@5 86.00% / all@5 68.00%
               本策略      86.00% /      70.00%   （+1 题）
  留出 150 题  prod hit@5 77.33% / all@5 67.33%
               本策略      77.33% /      66.67%   （−1 题）
调参上的增益在留出集反向，是噪声。且"跨年题抓不齐"这个诊断本身取样有误：
调参集跨年题仅 5 道（all@5 20%），留出集 15 道实为 66.67%，并不差。
留出集真正弱的是**跨页题 all@5 46.67%（30 题）**，本策略完全没碰它
（46.67% → 46.67%）。留作已否决方案的证据，别再重复试。
"""
from __future__ import annotations

from src.config import YEARS
from src.hybrid import detect_page, detect_years


class PerYearRecall:
    """包装任意 base.search(query, top_k, year) 检索器。

    dedup_pages=True 时同一页只保留最高分块，把名额让给更多不同的页
    （all_gold_hit 量的是页覆盖，同页多块并不加分）。默认关闭以保持
    与生产一致；答案层需要整页时可走 HybridRetriever.get_page。
    """

    def __init__(self, base, dedup_pages: bool = False,
                 per_year: bool = True):
        self.base = base
        self.dedup_pages = dedup_pages
        self.per_year = per_year

    def search(self, query: str, top_k: int = 5,
               year: int | set[int] | None = None) -> list[dict]:
        if year is not None:
            return self._dedup(self.base.search(query, top_k, year), top_k)

        years = [y for y in detect_years(query) if y in YEARS]
        # 页码直达由 base 自己早返回；拆年份会破坏它的 allow 集合语义
        if not self.per_year or len(years) < 2 or detect_page(query) is not None:
            return self._dedup(self.base.search(query, top_k, None), top_k)

        per_year = [self._dedup(self.base.search(query, top_k, y), top_k)
                    for y in years]
        return self._merge(per_year, top_k)

    def _dedup(self, hits: list[dict], top_k: int) -> list[dict]:
        if not self.dedup_pages:
            return hits[:top_k]
        seen: set[tuple[int, int]] = set()
        out = []
        for h in hits:  # 已按分降序，首次出现即该页最高分块
            key = (h["year"], h["page"])
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
            if len(out) >= top_k:
                break
        return out

    @staticmethod
    def _merge(per_year: list[list[dict]], top_k: int) -> list[dict]:
        """轮转取块：第 i 轮从每年各取第 i 个。

        用轮转而不是"按名额切段拼接"，是为了让两年的页都排在前面——
        切段拼接会把第二年的页全压到榜尾，MRR 掉。某年提前取空时，
        名额自动让给还有货的年份，不浪费。
        """
        out: list[dict] = []
        seen: set[str] = set()
        i = 0
        while len(out) < top_k:
            alive = False
            for hits in per_year:
                if i >= len(hits):
                    continue
                alive = True
                h = hits[i]
                if h["id"] not in seen:
                    seen.add(h["id"])
                    out.append(h)
                    if len(out) >= top_k:
                        break
            if not alive:
                break
            i += 1
        return out