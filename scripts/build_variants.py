"""E1 切块变体：结构化切块 + 小块（父子块检索用），并构建对应向量索引。

用法（项目根目录）:
  HF_HUB_OFFLINE=1 .venv/Scripts/python.exe -u scripts/build_variants.py [--force]

输出（jsonl 与 npz 都已存在则跳过该变体，可断点续跑; --force 强制重建）:
  data/chunks/structured.jsonl + data/index/structured.npz
  data/chunks/small.jsonl      + data/index/small.npz

npz 格式与 baseline.npz 完全一致：ids（字符串数组）+ embeddings
（float32 归一化矩阵），ids 顺序与 jsonl 行顺序一致。
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.chunking import chunk_small, chunk_structured, load_pages  # noqa: E402
from src.config import EMBED_PATH, EXTRACTED_DIR, YEARS  # noqa: E402

CHUNKS_DIR = PROJECT / "data" / "chunks"
INDEX_DIR = PROJECT / "data" / "index"


def build_variant(name: str, chunk_fn, model) -> int:
    chunks_file = CHUNKS_DIR / f"{name}.jsonl"
    index_file = INDEX_DIR / f"{name}.npz"

    chunks = []
    for year in YEARS:
        pages = load_pages(EXTRACTED_DIR, year)
        year_chunks = chunk_fn(pages, year)
        print(f"[{name}] {year}: {len(year_chunks)} 块")
        chunks.extend(year_chunks)
    print(f"[{name}] 共 {len(chunks)} 块")
    if not chunks:
        print(f"[{name}] 没有可用切块，跳过")
        return 0

    chunks_file.parent.mkdir(parents=True, exist_ok=True)
    with chunks_file.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(
                {"id": c.id, "text": c.text, "year": c.year, "page": c.page},
                ensure_ascii=False,
            ) + "\n")
    print(f"[{name}] 切块明细 -> {chunks_file}")

    print(f"[{name}] 向量化中...")
    embeddings = model.encode(
        [c.text for c in chunks], batch_size=64, show_progress_bar=True,
        normalize_embeddings=True,
    )
    index_file.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        index_file,
        ids=np.array([c.id for c in chunks]),
        embeddings=embeddings.astype(np.float32),
    )
    print(f"[{name}] 已保存 {len(chunks)} 条向量到 {index_file}")
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="忽略已存在输出，强制重建")
    args = parser.parse_args()

    variants = {"structured": chunk_structured, "small": chunk_small}
    pending = {}
    for name, fn in variants.items():
        chunks_file = CHUNKS_DIR / f"{name}.jsonl"
        index_file = INDEX_DIR / f"{name}.npz"
        if not args.force and chunks_file.exists() and index_file.exists():
            print(f"[{name}] 输出已存在，跳过（--force 可重建）")
            continue
        pending[name] = fn

    if not pending:
        print("所有变体输出均已存在，无需重建。")
        return

    from sentence_transformers import SentenceTransformer
    print(f"加载 embedding 模型 {EMBED_PATH} ...")
    model = SentenceTransformer(EMBED_PATH, device="cpu")

    counts = {name: build_variant(name, fn, model) for name, fn in pending.items()}
    print("==== 汇总 ====")
    for name, n in counts.items():
        print(f"{name}: {n} 块")


if __name__ == "__main__":
    main()
