"""检索模块：embedding 查询 + numpy 向量检索（后续实验扩展混合检索与精排）。"""
from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import CHUNKS_FILE, EMBED_PATH, INDEX_FILE, QUERY_INSTRUCTION
from src.store import VectorStore


class Retriever:
    """加载一次模型与向量库，可反复查询。"""

    def __init__(self, top_k: int = 5, chunks_file=CHUNKS_FILE,
                 index_file=INDEX_FILE):
        self.model = SentenceTransformer(EMBED_PATH, device="cpu")
        self.store = VectorStore(chunks_file, index_file)
        self.top_k = top_k

    def search(self, query: str, top_k: int | None = None,
               year: int | None = None) -> list[dict]:
        """返回 [{id, text, year, page, score}]，score 为余弦相似度。"""
        k = top_k or self.top_k
        q = self.model.encode(
            [QUERY_INSTRUCTION + query], normalize_embeddings=True,
        )
        hits = self.store.search(np.asarray(q), top_k=k, year=year)
        return [
            {"id": h.id, "text": h.text, "year": h.year,
             "page": h.page, "score": h.score}
            for h in hits
        ]
