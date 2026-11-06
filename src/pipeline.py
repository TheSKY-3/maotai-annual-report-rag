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

import json
from functools import lru_cache
from pathlib import Path

from src.config import (CHUNKS_FILE, CONTEXTUAL_CHUNKS_FILE,
                        CONTEXTUAL_INDEX_FILE, EMBED_MODEL, EMBED_PATH,
                        INDEX_FILE, YEARS)

_search = None
_retriever = None
_index_kind: str | None = None


def _get_retriever():
    """构建 HybridRetriever（进程内单例），供检索与整页读取共用。"""
    global _retriever, _search, _index_kind
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
        _index_kind = "contextual"
    else:
        print("[pipeline] 上下文增强索引缺失，退回基线索引"
              "（先运行 scripts/build_contextual_chunks.py 生成）")
        _retriever = HybridRetriever(CHUNKS_FILE, INDEX_FILE, emb,
                                     use_bm25=True, use_year_filter=True)
        _index_kind = "baseline"
    _search = _retriever.search
    return _retriever


@lru_cache(maxsize=4)
def _chunk_summary(path_str: str, _mtime_ns: int, _size: int
                   ) -> tuple[int, tuple[int, ...], int]:
    """统计 JSONL 块数与年份覆盖；按 mtime/size 失效，避免每次探测都读文件。"""
    count = 0
    years: set[int] = set()
    malformed = 0
    with open(path_str, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                obj = json.loads(line)
                years.add(int(obj["year"]))
            except (ValueError, KeyError, TypeError):
                malformed += 1
    return count, tuple(sorted(years)), malformed


def _file_info(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    st = path.stat()
    return {"exists": True, "bytes": st.st_size,
            "mtime": int(st.st_mtime)}


def index_status() -> dict:
    """报告检索索引与模型的可用性；不加载嵌入模型，供 /ready 探测。

    status:
      ready      上下文增强索引可用（生产配置）
      degraded   退了基线索引，仍可服务但 hit@5 约从 0.66 降到 0.62
      not_ready  索引/块文件缺失或为空，无法检索
    """
    contextual = (CONTEXTUAL_CHUNKS_FILE.exists()
                  and CONTEXTUAL_INDEX_FILE.exists())
    kind = "contextual" if contextual else "baseline"
    chunks_file = CONTEXTUAL_CHUNKS_FILE if contextual else CHUNKS_FILE
    index_file = CONTEXTUAL_INDEX_FILE if contextual else INDEX_FILE

    loaded = _retriever is not None
    if loaded:
        kind = _index_kind or kind
        chunks_file = (CONTEXTUAL_CHUNKS_FILE if kind == "contextual"
                       else CHUNKS_FILE)
        index_file = (CONTEXTUAL_INDEX_FILE if kind == "contextual"
                      else INDEX_FILE)
        chunks = len(_retriever.chunks)
        years = tuple(sorted({int(c["year"]) for c in _retriever.chunks}))
        malformed = 0
    else:
        chunks = 0
        years = ()
        malformed = 0
        if chunks_file.exists():
            st = chunks_file.stat()
            chunks, years, malformed = _chunk_summary(
                str(chunks_file), st.st_mtime_ns, st.st_size)

    reasons: list[str] = []
    chunks_info = _file_info(chunks_file)
    index_info = _file_info(index_file)
    if not chunks_info["exists"]:
        reasons.append(f"块文件不存在：{chunks_file}")
    if not index_info["exists"]:
        reasons.append(f"向量索引不存在：{index_file}")
    if chunks == 0:
        reasons.append("块文件为空或没有可解析的块")
    if years and set(years) != set(YEARS):
        reasons.append(f"年份覆盖不全：{list(years)}，期望 {YEARS}")
    if kind == "baseline":
        reasons.append("上下文增强索引缺失，已退回基线索引")
    if malformed:
        reasons.append(f"{malformed} 行块无法解析")

    if not chunks_info["exists"] or not index_info["exists"] or chunks == 0:
        status = "not_ready"
    elif kind == "baseline" or (years and set(years) != set(YEARS)):
        status = "degraded"
    else:
        status = "ready"

    return {
        "status": status,
        "loaded": loaded,
        "index": {
            "kind": kind,
            "chunks_file": str(chunks_file),
            "index_file": str(index_file),
            "chunks": chunks,
            "years": list(years),
            "missing_years": [y for y in YEARS if y not in years],
            "chunks_file_info": chunks_info,
            "index_file_info": index_info,
        },
        "embedding": {
            "model": EMBED_MODEL,
            "path": EMBED_PATH,
            "local_files": Path(EMBED_PATH).exists(),
        },
        "reasons": reasons,
    }


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
