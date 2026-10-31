"""向量存储与检索（numpy 暴力余弦检索）。

为什么不用向量数据库：本语料仅 ~1800 块，numpy 矩阵点积检索
单次 <1ms，且完全确定性、可复现；语料到百万级再引入专业向量库。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Hit:
    id: str
    text: str
    year: int
    page: int
    score: float


class VectorStore:
    """加载切块与向量文件，提供余弦相似度检索。"""

    def __init__(self, chunks_file: Path, index_file: Path):
        self.chunks: list[dict] = []
        with open(chunks_file, encoding="utf-8") as f:
            self.chunks = [json.loads(line) for line in f]
        data = np.load(index_file)
        ids = data["ids"]
        self.embs = data["embeddings"]  # 已归一化
        assert len(ids) == len(self.chunks), "向量与切块数量不一致"
        self.pos = {cid: i for i, cid in enumerate(ids)}

    def search(self, query_emb: np.ndarray, top_k: int = 5,
               year: int | None = None) -> list[Hit]:
        """query_emb: 归一化后的查询向量；year 可选，仅在该年份内检索。"""
        if year is None:
            idx = np.arange(len(self.chunks))
        else:
            idx = np.array([i for i, c in enumerate(self.chunks)
                            if c["year"] == year])
            if len(idx) == 0:
                return []
        scores = self.embs[idx] @ query_emb[0]
        top_local = np.argsort(-scores)[:top_k]
        top = idx[top_local]  # 子集排名 -> 全局块编号
        return [
            Hit(
                id=self.chunks[i]["id"],
                text=self.chunks[i]["text"],
                year=self.chunks[i]["year"],
                page=self.chunks[i]["page"],
                score=float(scores[j]),
            )
            for j, i in enumerate(top)
        ]
