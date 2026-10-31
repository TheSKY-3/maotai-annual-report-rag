"""评测核心：检索层确定性指标（被基线与实验脚本共用）。

指标定义（k 为每次召回的块数）：
  page_hit@k   top-k 中至少一块来自标准出处页
  MRR          第一块命中出处页的排名倒数
  page_prec@k  top-k 中命中出处页的比例
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

RESULTS_DIR = Path("results")


def parse_sources(source: str) -> list[tuple[int, int]]:
    """'2024年报 P8、P16' / '2022年报 P20；2024年报 P21' -> [(年,页)...]"""
    return [(int(y), int(p))
            for y, p in re.findall(r"(\d{4})年报\s*P(\d+)", source)]


def run_eval(search_fn, questions: list[dict], k: int = 5) -> tuple[list, dict]:
    """search_fn(query, top_k) -> [{year, page, ...}]；返回逐题行与汇总。"""
    rows = []
    for q in questions:
        gold = set(parse_sources(q["source"]))
        got = [(h["year"], h["page"]) for h in search_fn(q["question"], k)]
        mrr = 0.0
        for rank, g in enumerate(got, start=1):
            if g in gold:
                mrr = 1.0 / rank
                break
        rows.append({
            "id": q["id"], "type": q["type"], "question": q["question"],
            "page_hit": any(g in gold for g in got),
            "mrr": mrr,
            "page_prec": len(set(got) & gold) / k,
            "gold_pages": sorted(gold),
            "got_pages": [f"{y}P{p}" for y, p in got],
        })

    def agg(rs):
        n = len(rs)
        return {"n": n,
                "page_hit@k": round(sum(r["page_hit"] for r in rs) / n, 4),
                "mrr": round(sum(r["mrr"] for r in rs) / n, 4),
                "page_prec@k": round(sum(r["page_prec"] for r in rs) / n, 4)}

    by_type = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(r)
    summary = {"k": k, "overall": agg(rows),
               **{t: agg(rs) for t, rs in sorted(by_type.items())}}
    return rows, summary


def save_and_print(name: str, rows: list, summary: dict,
                   results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(exist_ok=True)
    out = results_dir / f"{name}_retrieval.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{name}] k={summary['k']} -> {out}")
    for t in ["overall", "fact", "cross_page", "cross_year", "no_answer"]:
        if t in summary:
            s = summary[t]
            print(f"  {t:11s} n={s['n']:3d} hit@k={s['page_hit@k']:.2%} "
                  f"MRR={s['mrr']:.3f} prec@k={s['page_prec@k']:.2%}")
    return out
