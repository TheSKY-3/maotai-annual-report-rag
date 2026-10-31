"""检索层评测：在黄金问答集上度量"是否检索到正确出处页"。

指标（不需要大模型，全部确定性可复现）：
  page_hit@k   top-k 中至少有一块来自出处页的比例
  MRR          第一块命中出处页的排名倒数均值
  page_prec@k  top-k 中命中出处页的比例

用法: .venv/Scripts/python.exe scripts/evaluate_retrieval.py [--k 5] [--name baseline]
输出: results/{name}_retrieval.json
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import GOLD_FILE, RESULTS_DIR  # noqa: E402
from src.retriever import Retriever  # noqa: E402


def parse_sources(source: str) -> list[tuple[int, int]]:
    """'2024年报 P8、P16' / '2022年报 P20；2024年报 P21' -> [(2024,8),(2024,16)...]"""
    pairs = []
    for year, page in re.findall(r"(\d{4})年报\s*P(\d+)", source):
        pairs.append((int(year), int(page)))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--name", default="baseline")
    args = parser.parse_args()

    questions = [json.loads(l) for l in
                 open(GOLD_FILE, encoding="utf-8") if l.strip()]
    retriever = Retriever(top_k=args.k)

    rows = []
    for q in questions:
        gold_pages = set(parse_sources(q["source"]))
        hits = retriever.search(q["question"], top_k=args.k)
        got = [(h["year"], h["page"]) for h in hits]
        mrr = 0.0
        for rank, g in enumerate(got, start=1):
            if g in gold_pages:
                mrr = 1.0 / rank
                break
        rows.append({
            "id": q["id"], "type": q["type"], "question": q["question"],
            "page_hit": any(g in gold_pages for g in got),
            "mrr": mrr,
            "page_prec": len(set(got) & gold_pages) / args.k,
            "gold_pages": sorted(gold_pages),
            "got_pages": [f"{y}P{p}" for y, p in got],
        })

    def agg(rs):
        n = len(rs)
        return {
            "n": n,
            "page_hit@k": round(sum(r["page_hit"] for r in rs) / n, 4),
            "mrr": round(sum(r["mrr"] for r in rs) / n, 4),
            "page_prec@k": round(sum(r["page_prec"] for r in rs) / n, 4),
        }

    by_type = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(r)
    summary = {"k": args.k, "overall": agg(rows),
               **{t: agg(rs) for t, rs in sorted(by_type.items())}}

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{args.name}_retrieval.json"
    out.write_text(json.dumps(
        {"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"检索评测（k={args.k}）-> {out}")
    for name, s in [("总览", summary["overall"])] + [
            (t, summary[t]) for t in sorted(by_type)]:
        print(f"  {name:12s} n={s['n']:3d}  hit@k={s['page_hit@k']:.2%}  "
              f"MRR={s['mrr']:.3f}  prec@k={s['page_prec@k']:.2%}")
    misses = [r for r in rows if not r["page_hit"]]
    print(f"未命中题数: {len(misses)}，示例:")
    for r in misses[:5]:
        print(f"   [{r['type']}] {r['question'][:30]}  期望{r['gold_pages']} 实得{r['got_pages']}")


if __name__ == "__main__":
    main()
