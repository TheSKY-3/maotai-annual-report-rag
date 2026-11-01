"""E5 上下文增强索引（Anthropic Contextual Retrieval 的适配实现）。

思路: 基线切块脱离了上下文——"营业总收入 174,144,069,958.25 元"这种块
不知道自己来自哪个章节。让大模型给每块写一句"语境说明"（它在年报的哪
个部分、讨论什么主题），拼接在原文前面，再嵌入 + 进 BM25。

参考: Anthropic "Contextual Retrieval"（2024-09），语境嵌入单用降低
35% 检索失败率，配合 BM25 与精排可达 67%——后两者本项目 E2/E3 已有。

用法（项目根目录）:
  DEEPSEEK_API_KEY=sk-... HF_HUB_OFFLINE=1 \\
      .venv/Scripts/python.exe scripts/build_contextual_chunks.py [--limit N]

产物:
  data/chunks/contextual.jsonl   text = 语境句 + 原文（其余字段同基线）
  data/index/contextual.npz      对增强文本重建的向量索引

成本: 约 1761 块 × (1.1K 输入 + 60 输出) ≈ 5 元（deepseek-flash，峰时价）
支持断点续跑: 进度保存在 data/chunks/contextual_progress.jsonl
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import EMBED_PATH  # noqa: E402

BASELINE = PROJECT / "data" / "chunks" / "baseline.jsonl"
OUT_CHUNKS = PROJECT / "data" / "chunks" / "contextual.jsonl"
OUT_INDEX = PROJECT / "data" / "index" / "contextual.npz"
PROGRESS = PROJECT / "data" / "chunks" / "contextual_progress.jsonl"

API_BASE = "https://api.deepseek.com/v1"
API_MODEL = "deepseek-flash"

PROMPT = """你是一名年报索引员。以下是贵州茅台{year}年年度报告第{page}页的一个片段，以及它前后的相邻片段。请写一句话（不超过50个字）说明这个片段在年报中讨论的主题和所处的位置，供检索系统理解它的上下文。

要求：只输出这一句话，不要任何前缀、编号或解释。

--- 前一片段结尾 ---
{prev}
--- 本片段 ---
{chunk}
--- 后一片段开头 ---
{next}"""


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    env_file = PROJECT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("未找到 DEEPSEEK_API_KEY（环境变量或 .env）")


def gen_context(client, chunk: dict, by_id: dict, attempt: int = 0) -> str:
    prev_id, next_id = chunk["prev"], chunk["next"]
    prev = by_id[prev_id]["text"][-200:] if prev_id else "（无，本页开头）"
    nxt = by_id[next_id]["text"][:200] if next_id else "（无，本页结尾）"
    # V4.1-Flash 默认带思考模式：max_tokens 必须 > 思考长度(~300)，
    # 否则思考占满配额、正文为空（与 Qwen3 不加 /no_think 同源的问题）。
    # 个别块思考超长：重试时翻倍配额
    resp = client.chat.completions.create(
        model=API_MODEL,
        messages=[{"role": "user", "content": PROMPT.format(
            year=chunk["year"], page=chunk["page"], prev=prev,
            chunk=chunk["text"], next=nxt)}],
        temperature=0, max_tokens=800 * (2 ** attempt), timeout=90,
    )
    ctx = (resp.choices[0].message.content or "").strip()
    if not ctx:
        raise RuntimeError(f"正文为空（思考占用 completion_tokens="
                           f"{resp.usage.completion_tokens}）")
    ctx = ctx.splitlines()[0].strip("　\"'` ") if ctx else ""
    return ctx[:120]


def worker(client, chunk, by_id, retries=4):
    for i in range(retries):
        try:
            ctx = gen_context(client, chunk, by_id, attempt=i)
            if ctx:
                return chunk["id"], ctx
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                print(f"[失败] {chunk['id']}: {e}", flush=True)
            else:
                time.sleep(2 * (i + 1))
    return chunk["id"], None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 块（调试）")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    chunks = [json.loads(l) for l in
              open(BASELINE, encoding="utf-8") if l.strip()]
    if args.limit:
        chunks = chunks[: args.limit]
    by_id = {c["id"]: c for c in chunks}

    # 相邻块 id（同文件顺序即阅读顺序）
    for i, c in enumerate(chunks):
        c["prev"] = chunks[i - 1]["id"] if i > 0 else None
        c["next"] = chunks[i + 1]["id"] if i < len(chunks) - 1 else None

    # 断点续跑
    done: dict[str, str] = {}
    if PROGRESS.exists():
        for l in PROGRESS.open(encoding="utf-8"):
            if l.strip():
                rid, ctx = json.loads(l)
                done[rid] = ctx
    todo = [c for c in chunks if c["id"] not in done]
    print(f"总块数 {len(chunks)}，已完成 {len(done)}，待处理 {len(todo)}")

    from openai import OpenAI
    client = OpenAI(base_url=API_BASE, api_key=load_key())
    t0 = time.time()
    n_new = 0
    with PROGRESS.open("a", encoding="utf-8") as log, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, client, c, by_id) for c in todo]
        for fut in as_completed(futures):
            rid, ctx = fut.result()
            if ctx:
                done[rid] = ctx
                log.write(json.dumps([rid, ctx], ensure_ascii=False) + "\n")
                log.flush()
            n_new += 1
            if n_new % 100 == 0:
                rate = n_new / (time.time() - t0)
                print(f"进度 {n_new}/{len(todo)}  {rate:.1f} 块/秒"
                      f"  预计还需 {(len(todo) - n_new) / max(rate, 0.1) / 60:.0f} 分钟",
                      flush=True)

    missing = [c["id"] for c in chunks if c["id"] not in done]
    if missing:
        raise SystemExit(f"{len(missing)} 块生成失败，修复后重跑（进度已保存）: "
                         f"{missing[:5]}...")

    # 写增强切块文件（text = 语境句 + 原文）
    OUT_CHUNKS.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CHUNKS.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(
                {"id": c["id"], "text": f"{done[c['id']]}\n{c['text']}",
                 "year": c["year"], "page": c["page"],
                 "context": done[c["id"]]},
                ensure_ascii=False) + "\n")
    print(f"增强切块 -> {OUT_CHUNKS}")

    # 重建向量索引
    from sentence_transformers import SentenceTransformer
    print(f"加载 embedding 模型 {EMBED_PATH} ...")
    model = SentenceTransformer(EMBED_PATH, device="cpu")
    texts = [f"{done[c['id']]}\n{c['text']}" for c in chunks]
    print("向量化中...")
    emb = model.encode(texts, batch_size=64, show_progress_bar=True,
                       normalize_embeddings=True)
    OUT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT_INDEX,
        ids=np.array([c["id"] for c in chunks]),
        embeddings=emb.astype(np.float32),
    )
    print(f"索引 -> {OUT_INDEX}  完成，共 {len(chunks)} 块")


if __name__ == "__main__":
    main()
