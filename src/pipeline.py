"""生产检索管线：混合检索 + 年份过滤 + 精排（E2/E3 实验出的最优配置）。

用法:
    from src.pipeline import get_search
    search = get_search()          # 首次调用加载模型（约几秒），之后复用
    hits = search("2024年茅台营业总收入是多少？", top_k=5)
"""
from __future__ import annotations

from src.config import CHUNKS_FILE, EMBED_PATH, INDEX_FILE, PROJECT

_RR_DIR = PROJECT / "data" / "models" / "bge-reranker-base"
_search = None


def get_search():
    """返回 search(query, top_k=5, year=None) -> [hits]（进程内单例）。"""
    global _search
    if _search is not None:
        return _search

    from sentence_transformers import CrossEncoder, SentenceTransformer

    from src.hybrid import HybridRetriever, RerankedRetriever

    emb = SentenceTransformer(EMBED_PATH, device="cpu")
    base = HybridRetriever(CHUNKS_FILE, INDEX_FILE, emb,
                           use_bm25=True, use_year_filter=True)
    try:
        reranker = CrossEncoder(str(_RR_DIR), max_length=512, device="cpu")
        _search = RerankedRetriever(base, reranker).search
    except Exception as e:  # noqa: BLE001
        print(f"[pipeline] 精排模型不可用，退化为混合检索: {e}")
        _search = base.search
    return _search
