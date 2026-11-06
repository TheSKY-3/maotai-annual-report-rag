"""评测核心：检索层确定性指标（被基线与实验脚本共用）。

指标定义（k 为每次召回的块数）：
  page_hit@k      top-k 中至少一块来自标准出处页（any，宽松口径）
  all_gold_hit@k  top-k 覆盖标准出处的全部页（all，严格口径）
  MRR             第一块命中出处页的排名倒数
  page_prec@k     top-k 中命中出处页的比例

gold 出处字符串的解析见 parse_sources：一个年份段内的多个页码共用该年份
（"2024年报 P9、P16" 是两页），见 cite_precision 的引用精确率口径。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

RESULTS_DIR = Path("results")

# 年份标记（"2024年报"）用来切段；页码标记（"P12"）在段内继承该段年份。
_YEAR_TOKEN_RE = re.compile(r"(\d{4})\s*年报")
_PAGE_TOKEN_RE = re.compile(r"P(\d+)")


def parse_sources(source: str) -> list[tuple[int, int]]:
    r"""'2024年报 P8、P16' / '2024 年报 P9' -> [(年,页)...]

    年份只写在每个年份段的开头，后续页码沿用该段年份，所以
    "2024年报 P9、P16" 是两页（P9 与 P16）；年份与“年报”之间允许空格
    （答案正文常写成“2025 年报片段（P57、P59）”）。用 finditer 先按 "\d{4}年报"
    切段、再在段内扫全部页码；若直接用一次性正则
    r"(\d{4})年报\s*P(\d+)" 匹配，第一处匹配会把 "2024年报 P9" 整段吃掉，
    后面没有年份的 "、P16" 就再也匹配不上，gold 页被静默漏掉。
    返回去重保序的 (年, 页)。
    """
    if not source:
        return []
    marks = [(m.start(), int(m.group(1)))
             for m in _YEAR_TOKEN_RE.finditer(source)]
    pages: list[tuple[int, int]] = []
    for i, (pos, year) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(source)
        pages += [(year, int(m.group(1)))
                  for m in _PAGE_TOKEN_RE.finditer(source, pos, end)]
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for item in pages:
        if item not in seen:  # 去重保序（同一页可能被写两次）
            seen.add(item)
            out.append(item)
    return out


def all_gold_hit(got_pages: set, gold_pages: set) -> bool:
    """严格命中：gold 的每一页都出现在 got 里（gold 为空时返回 False）。"""
    gold = set(gold_pages or ())
    if not gold:
        return False
    return gold.issubset(set(got_pages or ()))


def cite_precision(cited: set, gold: set) -> float | None:
    """引用精确率：cited 中落在 gold 的比例。

    未给出任何引用时返回 None（"没引用" 与 "引用全错" 必须区分开，
    否则少引用反而显得精确率高，多引必中也会被掩盖）。
    """
    cited_set = set(cited or ())
    if not cited_set:
        return None
    return len(cited_set & set(gold or ())) / len(cited_set)


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
            "all_gold_hit": all_gold_hit(set(got), gold),
            "mrr": mrr,
            "page_prec": len(set(got) & gold) / k,
            "gold_pages": sorted(gold),
            "got_pages": [f"{y}P{p}" for y, p in got],
        })

    def agg(rs):
        n = len(rs)
        return {"n": n,
                # any：top-k 命中 gold 的任意一页（旧口径，保留兼容）
                "page_hit@k": round(sum(r["page_hit"] for r in rs) / n, 4),
                # all：top-k 覆盖 gold 的每一页（严格口径）
                "all_gold_hit@k": round(
                    sum(r["all_gold_hit"] for r in rs) / n, 4),
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
                  f"all@k={s['all_gold_hit@k']:.2%} "
                  f"MRR={s['mrr']:.3f} prec@k={s['page_prec@k']:.2%}")
    return out
