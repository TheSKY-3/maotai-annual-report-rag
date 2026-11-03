"""生产检索管线（E5 配置）：上下文增强索引 + 混合检索 + 年份过滤。

用法:
    from src.pipeline import get_search
    search = get_search()          # 首次调用加载模型（约几秒），之后复用
    hits = search("2024年茅台营业总收入是多少？", top_k=5)

E5 起（2026-11）：每块文字前置大模型生成的语境句
（scripts/build_contextual_chunks.py 生成，约 3 元），嵌入与 BM25 均
使用增强文本。评测 hit@5 66%，高于 E3 精排管线的 62%，且不再需要
每次查询约 2 秒的精排——精排增益已被语境句取代（E5_rerank 64%）。
"""
from __future__ import annotations

from src.config import (CHUNKS_FILE, CONTEXTUAL_CHUNKS_FILE,
                        CONTEXTUAL_INDEX_FILE, EMBED_PATH, INDEX_FILE)

_search = None
_retriever = None


def _get_retriever():
    """构建 HybridRetriever（进程内单例），供检索与整页读取共用。"""
    global _retriever, _search
    if _retriever is not None:
        return _retriever

    from sentence_transformers import SentenceTransformer

    from src.hybrid import HybridRetriever

    emb = SentenceTransformer(EMBED_PATH, device="cpu")
    if (CONTEXTUAL_CHUNKS_FILE.exists()
            and CONTEXTUAL_INDEX_FILE.exists()):
        _retriever = HybridRetriever(CONTEXTUAL_CHUNKS_FILE,
                                     CONTEXTUAL_INDEX_FILE, emb,
                                     use_bm25=True, use_year_filter=True)
    else:
        print("[pipeline] 上下文增强索引缺失，退回基线索引"
              "（先运行 scripts/build_contextual_chunks.py 生成）")
        _retriever = HybridRetriever(CHUNKS_FILE, INDEX_FILE, emb,
                                     use_bm25=True, use_year_filter=True)
    _search = _retriever.search
    return _retriever


def get_search():
    """返回 search(query, top_k=5, year=None) -> [hits]（进程内单例）。"""
    _get_retriever()
    return _search


def get_page_reader():
    """返回 read_page(year, page) -> [hits]（进程内单例，页码直达工具用）。

    与 get_search() 共用同一次模型/索引加载。
    """
    _get_retriever()
    return _retriever.get_page
