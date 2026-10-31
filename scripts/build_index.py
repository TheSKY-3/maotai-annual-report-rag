"""切块 + 向量化 + 保存 numpy 索引。

用法（项目根目录）:
  HF_HUB_OFFLINE=1 .venv/Scripts/python.exe scripts/build_index.py [--reset]

输出:
  data/chunks/baseline.jsonl   切块结果（供 BM25 等实验复用）
  data/index/baseline.npz      向量索引（ids + 归一化 embeddings）

说明:
  - Embedding 用 BAAI/bge-small-zh-v1.5（CPU 即可）
  - 语料规模小，检索用 numpy 暴力余弦即可（见 src/store.py 的说明）
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.chunking import load_all_chunks  # noqa: E402
from src.config import EMBED_PATH, INDEX_FILE  # noqa: E402

EXTRACTED = PROJECT / "data" / "extracted"
CHUNKS_FILE = PROJECT / "data" / "chunks" / "baseline.jsonl"
YEARS = [2022, 2023, 2024, 2025]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="覆盖旧索引")
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    chunks = load_all_chunks(EXTRACTED, YEARS)
    print(f"切块完成: {len(chunks)} 块")
    if not chunks:
        raise SystemExit("没有可用切块，请先运行 scripts/extract_text.py")

    CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHUNKS_FILE.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(
                {"id": c.id, "text": c.text, "year": c.year, "page": c.page},
                ensure_ascii=False,
            ) + "\n")
    print(f"切块明细 -> {CHUNKS_FILE}")

    print(f"加载 embedding 模型 {EMBED_PATH} ...")
    model = SentenceTransformer(EMBED_PATH, device="cpu")

    print("向量化中...")
    embeddings = model.encode(
        [c.text for c in chunks], batch_size=64, show_progress_bar=True,
        normalize_embeddings=True,
    )

    if INDEX_FILE.exists() and not args.reset:
        print(f"索引已存在 {INDEX_FILE}，跳过（--reset 可重建）")
        return
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        INDEX_FILE,
        ids=np.array([c.id for c in chunks]),
        embeddings=embeddings.astype(np.float32),
    )
    print(f"已保存 {len(chunks)} 条向量到 {INDEX_FILE}")


if __name__ == "__main__":
    main()
