"""E2/E3 检索策略：混合检索（BM25+向量 RRF 融合）与查询年份识别。

设计动机来自基线 badcase：
  1. 纯向量对"精确词"（i茅台、酱香型、专表数字）不敏感 -> BM25 关键词路；
  2. 嵌入对"数字年份"不敏感 -> 从问题中识别年份，直接限定检索范围。
"""
from __future__ import annotations

import re

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from src.store import VectorStore

YEAR_RE = re.compile(r"(20\d{2})\s*年")
RRF_K = 60


def detect_years(query: str) -> list[int]:
    """提取问题中的年份（去重保序）。"""
    return [int(y) for y in dict.fromkeys(YEAR_RE.findall(query))]


class HybridRetriever:
    """BM25 + 向量双路召回，RRF 融合；可按问题中的年份过滤。"""

    def __init__(self, chunks_file, index_file, embed_model,
                 use_bm25: bool = True, use_year_filter: bool = True):
        self.store = VectorStore(chunks_file, index_file)
        self.embed_model = embed_model
        self.use_bm25 = use_bm25
        self.use_year_filter = use_year_filter
        self.chunks = self.store.chunks
        if use_bm25:
            tokenized = [list(jieba.cut_for_search(c["text"]))
                         for c in self.chunks]
            self.bm25 = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 5,
               year: int | None = None) -> list[dict]:
        years = detect_years(query)
        if self.use_year_filter and year is None and len(set(years)) == 1:
            year = years[0]  # 单一明确年份：限定范围；跨年题不过滤

        q_emb = np.asarray(self.embed_model.encode(
            [query], normalize_embeddings=True))
        idx = self.store.search(q_emb, top_k=50, year=year)  # 先粗召回
        if not idx:
            return []

        if not self.use_bm25:
            return [{"id": h.id, "text": h.text, "year": h.year,
                     "page": h.page, "score": h.score} for h in idx[:top_k]]

        # 向量路排名
        vec_rank = {h.id: r for r, h in enumerate(idx, start=1)}
        # BM25 路（仅在该年份子集上）
        pos = [i for i, c in enumerate(self.chunks)
               if year is None or c["year"] == year]
        tokens = list(jieba.cut_for_search(query))
        bm_scores = self.bm25.get_scores(tokens)
        bm_sorted = sorted(pos, key=lambda i: -bm_scores[i])
        bm_rank = {self.chunks[i]["id"]: r
                   for r, i in enumerate(bm_sorted[:50], start=1)}

        # RRF 融合
        all_ids = list(dict.fromkeys(list(vec_rank) + list(bm_rank)))
        fused = sorted(
            all_ids,
            key=lambda cid: -(1.0 / (RRF_K + vec_rank.get(cid, 10 ** 9))
                              + 1.0 / (RRF_K + bm_rank.get(cid, 10 ** 9))),
        )
        by_id = {h.id: h for h in idx}
        chunk_by_id = {c["id"]: c for c in self.chunks}
        out = []
        for cid in fused[:top_k]:
            h = by_id.get(cid)
            c = chunk_by_id[cid]
            out.append({
                "id": cid,
                "text": (h.text if h else c["text"]),
                "year": c["year"], "page": c["page"],
                "score": (1.0 / (RRF_K + vec_rank.get(cid, 10 ** 9))
                          + 1.0 / (RRF_K + bm_rank.get(cid, 10 ** 9))),
            })
        return out
