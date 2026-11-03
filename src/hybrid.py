"""E2/E3 检索策略：混合检索（BM25+向量 RRF 融合）与查询年份/页码识别。

设计动机来自基线 badcase：
  1. 纯向量对"精确词"（i茅台、酱香型、专表数字）不敏感 -> BM25 关键词路；
  2. 嵌入对"数字年份"不敏感 -> 从问题中识别年份，直接限定检索范围；
  3. 页码编号（P6）没有语义可匹配 -> 识别页码后跳过检索，直接取该页原文。
"""
from __future__ import annotations

import re

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from src.config import YEARS
from src.store import VectorStore

YEAR_RE = re.compile(r"(20\d{2})\s*年")
PAGE_RE = re.compile(r"[PpＰ]\s*(\d{1,3})|第\s*(\d{1,3})\s*页")
RRF_K = 60


def detect_years(query: str) -> list[int]:
    """提取问题中的年份（去重保序）。"""
    return [int(y) for y in dict.fromkeys(YEAR_RE.findall(query))]


def detect_page(query: str) -> int | None:
    """提取问题中的页码（如 P6、第6页）；无则 None。"""
    m = PAGE_RE.search(query)
    return int(m.group(1) or m.group(2)) if m else None


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

    def get_page(self, year: int, page: int) -> list[dict]:
        """取某年某页的全部块（Agentic RAG 的 page_direct 工具用）。

        语义检索受 top_k 截断，密集页（最多 26 块）只能进 5 块——
        通读整页必须走这里，返回格式与 search() 一致。
        """
        found = [c for c in self.chunks
                 if c["year"] == year and c["page"] == page]
        found.sort(key=lambda c: c["id"])  # id 含零填充块序号，即原文顺序
        return [{"id": c["id"], "text": c["text"],
                 "year": c["year"], "page": c["page"],
                 "score": 1.0, "direct": True}
                for c in found]

    def search(self, query: str, top_k: int = 5,
               year: int | set[int] | None = None) -> list[dict]:
        years = detect_years(query)
        if self.use_year_filter and year is None:
            known = [y for y in dict.fromkeys(years) if y in YEARS]
            if len(known) in (1, 2):
                year = set(known)  # 单年题或跨年题：限定到出现的年份（集合）

        # 页码直达：问题明确写了 P6/第6页 时跳过语义检索，直接取该页原文。
        # 页码编号没有语义可嵌入，纯向量/BM25 都匹配不上（badcase：P6 内容题）。
        if self.use_year_filter:
            page = detect_page(query)
            if page is not None:
                want = {y for y in years if y in YEARS}
                if isinstance(year, (set, frozenset)):
                    want |= set(year)
                elif year is not None:
                    want.add(year)
                allow = want or None
                direct = [c for c in self.chunks
                          if c["page"] == page
                          and (allow is None or c["year"] in allow)]
                if direct:
                    direct.sort(key=lambda c: c["year"])
                    return [{"id": c["id"], "text": c["text"],
                             "year": c["year"], "page": c["page"],
                             "score": 1.0, "direct": True}
                            for c in direct[:top_k]]

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
        # BM25 路（仅在该年份子集上；year 可能是集合）
        allow = year if isinstance(year, (set, frozenset)) else (
            None if year is None else {year})
        pos = [i for i, c in enumerate(self.chunks)
               if allow is None or c["year"] in allow]
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


class RerankedRetriever:
    """在基础检索之上加交叉编码器精排（E3）：粗召回 top20 -> 重排 -> top_k。"""

    def __init__(self, base, reranker):
        self.base = base
        self.reranker = reranker

    def search(self, query: str, top_k: int = 5,
               year: int | None = None) -> list[dict]:
        cands = self.base.search(query, top_k=20, year=year)
        if not cands:
            return []
        if all(c.get("direct") for c in cands):  # 页码直达：无需精排
            return cands[:top_k]
        scores = self.reranker.predict([(query, c["text"]) for c in cands])
        for c, s in zip(cands, scores):
            c["score"] = float(s)
        return sorted(cands, key=lambda c: -c["score"])[:top_k]
