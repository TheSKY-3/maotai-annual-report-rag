"""E2/E3 检索策略：混合检索（BM25+向量归一化加权融合）与查询年份/页码识别。

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
RRF_K = 60  # 旧 RRF 融合参数，保留供历史实验脚本引用
CAND_DEPTH = 50  # 每路粗召回深度
FUSION_ALPHA = 0.5  # 向量路权重（1-alpha 为 BM25 路）


def _minmax(x: np.ndarray) -> np.ndarray:
    """把一路分数压到 [0,1]；全相等时返回全 0。"""
    lo, hi = float(x.min()), float(x.max())
    if hi <= lo:
        return np.zeros_like(x)
    return (x - lo) / (hi - lo)


def fuse_candidates(vec_scores: np.ndarray, bm_scores: np.ndarray,
                    depth: int = CAND_DEPTH,
                    alpha: float = FUSION_ALPHA) -> list[tuple[int, float]]:
    """对两路候选做归一化加权融合，返回 (本地下标, 融合分) 降序。

    为什么不再用 RRF：金标块常在 BM25 路排第 1-3，但排在向量路 top50
    之外，RRF 只给它单路分 1/(60+rank)；同时被两路都召回的平庸块拿
    双路分，前 5 名反而把它挤掉（q11/14/19/24 的漏页机制）。把两路
    分数先各自 min-max 归一化再加权，单路强信号就不会被双路弱信号淹
    没。这里只在两路 top-depth 的并集上排名，避免长尾噪声。
    """
    v_norm = _minmax(np.asarray(vec_scores, dtype=float))
    b_norm = _minmax(np.asarray(bm_scores, dtype=float))
    v_top = np.argsort(-vec_scores)[:depth]
    b_top = np.argsort(-bm_scores)[:depth]
    cand = {int(i) for i in v_top} | {int(i) for i in b_top}
    scored = [(i, alpha * float(v_norm[i]) + (1.0 - alpha) * float(b_norm[i]))
              for i in cand]
    return sorted(scored, key=lambda item: -item[1])


def detect_years(query: str) -> list[int]:
    """提取问题中的年份（去重保序）。"""
    return [int(y) for y in dict.fromkeys(YEAR_RE.findall(query))]


def detect_page(query: str) -> int | None:
    """提取问题中的页码（如 P6、第6页）；无则 None。"""
    m = PAGE_RE.search(query)
    return int(m.group(1) or m.group(2)) if m else None


class HybridRetriever:
    """BM25 + 向量双路召回，归一化加权融合；可按问题中的年份过滤。"""

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
        vec_idx, vec_scores = self.store.score_all(q_emb, year=year)
        if len(vec_idx) == 0:
            return []

        if not self.use_bm25:
            order = np.argsort(-vec_scores)[:top_k]
            return [{"id": self.chunks[int(vec_idx[i])]["id"],
                     "text": self.chunks[int(vec_idx[i])]["text"],
                     "year": self.chunks[int(vec_idx[i])]["year"],
                     "page": self.chunks[int(vec_idx[i])]["page"],
                     "score": float(vec_scores[i])}
                    for i in order]

        # BM25 分数先算全量，再按年份子集取对应位置（与向量路同一
        # 子集，两路归一化才有可比性）。
        tokens = list(jieba.cut_for_search(query))
        bm_scores = self.bm25.get_scores(tokens)[vec_idx]

        fused = fuse_candidates(vec_scores, bm_scores,
                                depth=CAND_DEPTH, alpha=FUSION_ALPHA)
        out = []
        for local, score in fused[:top_k]:
            c = self.chunks[int(vec_idx[local])]
            out.append({"id": c["id"], "text": c["text"],
                        "year": c["year"], "page": c["page"],
                        "score": float(score)})
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
