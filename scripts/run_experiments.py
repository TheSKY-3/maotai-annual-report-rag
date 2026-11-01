"""E1/E2/E3 检索层实验矩阵：同一黄金集，多个检索管线，统一出分。

用法（项目根目录）:
  HF_HUB_OFFLINE=1 .venv/Scripts/python.exe -u scripts/run_experiments.py [--only E1]

实验设计（每行一个"管线"，共享同一评测循环）:
  E1 切块策略:  E1_baseline(500字窗口) / E1_structured(标题结构) / E1_small(220小块)
  E2 检索方式:  E2_pure(纯向量=基线) / E2_hybrid(+BM25 RRF) /
               E2_year(纯向量+年份过滤) / E2_hybrid_year(混合+年份过滤)
  E3 精排:      E3_rerank(最优配置 + bge-reranker-base 重排 top20->top5)

结果: results/E{n}_{variant}_retrieval.json
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import (CHUNKS_FILE, EMBED_PATH, GOLD_FILE,  # noqa: E402
                        INDEX_FILE)
from src.evalcore import run_eval, save_and_print  # noqa: E402
from src.hybrid import HybridRetriever  # noqa: E402
from src.retriever import Retriever  # noqa: E402

DATA = PROJECT / "data"
MODELS = DATA / "models"


def build_pipelines() -> dict:
    pipes: dict[str, object] = {}

    # ---- E1 切块策略（纯向量，仅换切块） ----
    chunks_dir, index_dir = DATA / "chunks", DATA / "index"
    for name, stem in [("E1_baseline", "baseline"),
                       ("E1_structured", "structured"),
                       ("E1_small", "small")]:
        cf, nf = chunks_dir / f"{stem}.jsonl", index_dir / f"{stem}.npz"
        if cf.exists() and nf.exists():
            pipes[name] = Retriever(chunks_file=cf, index_file=nf).search

    # ---- E2 检索方式（基线切块上叠加策略） ----
    from sentence_transformers import SentenceTransformer
    emb = SentenceTransformer(EMBED_PATH, device="cpu")

    pipes["E2_pure"] = Retriever().search
    for name, bm25, yf in [("E2_year", False, True),
                           ("E2_hybrid", True, False),
                           ("E2_hybrid_year", True, True)]:
        pipes[name] = HybridRetriever(
            CHUNKS_FILE, INDEX_FILE, emb,
            use_bm25=bm25, use_year_filter=yf).search

    # ---- E3 精排（在最优检索配置上重排） ----
    rr_dir = MODELS / "bge-reranker-base"
    try:
        from sentence_transformers import CrossEncoder
        reranker = CrossEncoder(str(rr_dir), max_length=512, device="cpu")
    except Exception as e:  # noqa: BLE001
        print(f"[跳过 E3] 精排模型加载失败: {e}")
        return pipes

    from src.hybrid import RerankedRetriever

    # 精排叠加在"混合检索+年份过滤"之上（E2 中最优者）
    pipes["E3_rerank"] = RerankedRetriever(HybridRetriever(
        CHUNKS_FILE, INDEX_FILE, emb,
        use_bm25=True, use_year_filter=True), reranker).search

    # ---- E5 上下文增强索引（Anthropic Contextual Retrieval 适配） ----
    # 每块前置大模型生成的语境句；嵌入与 BM25 均使用增强文本
    ctx_chunks = DATA / "chunks" / "contextual.jsonl"
    ctx_index = DATA / "index" / "contextual.npz"
    if ctx_chunks.exists() and ctx_index.exists():
        pipes["E5_hybrid_year"] = HybridRetriever(
            ctx_chunks, ctx_index, emb,
            use_bm25=True, use_year_filter=True).search
        pipes["E5_rerank"] = RerankedRetriever(HybridRetriever(
            ctx_chunks, ctx_index, emb,
            use_bm25=True, use_year_filter=True), reranker).search
    return pipes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None,
                        help="只跑名称含该子串的实验，如 E1")
    args = parser.parse_args()

    questions = [json.loads(l) for l in
                 open(GOLD_FILE, encoding="utf-8") if l.strip()]
    pipes = build_pipelines()
    if args.only:
        pipes = {n: f for n, f in pipes.items() if args.only in n}
    print(f"待跑管线: {sorted(pipes)}\n")
    for name in sorted(pipes):
        rows, summary = run_eval(pipes[name], questions, k=5)
        save_and_print(name, rows, summary)


if __name__ == "__main__":
    main()
