"""对比 E9 与 Phase A：同一条规则下，答案内容是否被检索证据支撑。

规则（两套 run 完全一致）：
  1. 引用页必须出现在该题检索到的片段池里；
  2. 答案里的每个数字（去掉出处、页码、年份、"（计算得出）"派生数后）
     必须在片段池原文里找得到（单位归一、0.2% 容差）。
这比“引用与 gold 页是否一致”更基础：先保证不编造，再讨论页码对不对。
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src import numeric  # noqa: E402
from src.claims import _CALC_NUM_RE, _CITE_RE, _PAGE_NOISE_RE, raw_text  # noqa: E402
from src.config import YEARS  # noqa: E402
from src.evalcore import parse_sources  # noqa: E402

RESULTS = PROJECT / "results"
CHUNKS = PROJECT / "data/chunks/contextual.jsonl"


def load_chunks() -> dict[str, str]:
    out: dict[str, str] = {}
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            out[d["id"]] = raw_text(d)
    return out


def pool_of(row: dict) -> list[str]:
    if row.get("hits"):
        return [h["id"] for h in row["hits"] if h.get("id")]
    return list(row.get("hit_ids") or [])


def clean_numbers(text: str) -> list[str]:
    body = _CITE_RE.sub("", text or "")
    body = _PAGE_NOISE_RE.sub("", body)
    body = _CALC_NUM_RE.sub("", body)
    return [n for n in numeric.numbers_in_answer(body)
            if n not in {str(y) for y in YEARS}]


def audit(rows: list[dict], chunks: dict[str, str]) -> dict:
    by_route = defaultdict(lambda: {"n": 0, "bad_rows": 0, "bad_nums": 0,
                                    "nums": [], "cite_rows": 0, "cite_bad": 0})
    for r in rows:
        if r.get("type") == "no_answer":
            continue
        route = r.get("route", "direct")
        ids = pool_of(r)
        texts = [chunks[i] for i in ids if i in chunks]
        pool_pages = set()
        for i in ids:
            m = re.match(r"(\d{4})-p(\d+)", i)
            if m:
                pool_pages.add((int(m.group(1)), int(m.group(2))))
        nums = clean_numbers(r.get("answer") or "")
        bad = [n for n in nums if not numeric.find_number(n, texts)]
        cited = set(parse_sources(r.get("answer") or ""))
        cite_bad = cited - pool_pages if cited else set()
        s = by_route[route]
        s["n"] += 1
        s["bad_rows"] += 1 if bad else 0
        s["bad_nums"] += len(bad)
        s["nums"].append(len(nums))
        if cited:
            s["cite_rows"] += 1
            s["cite_bad"] += 1 if cite_bad else 0
    out = {}
    for route, s in by_route.items():
        n = s["n"]
        out[route] = {
            "n": n,
            "bad_row_rate": round(s["bad_rows"] / n, 4),
            "bad_nums": s["bad_nums"],
            "nums_total": sum(s["nums"]),
            "nums_per_answer_median": statistics.median(s["nums"]) if s["nums"] else 0,
            "cited_rows": s["cite_rows"],
            "cite_bad_rows": s["cite_bad"],
        }
    tot_n = sum(v["n"] for v in out.values())
    out["overall"] = {
        "n": tot_n,
        "bad_row_rate": round(sum(v["bad_row_rate"] * v["n"] for v in out.values()) / tot_n, 4),
        "bad_nums": sum(v["bad_nums"] for v in out.values()),
        "nums_total": sum(v["nums_total"] for v in out.values()),
        "cited_rows": sum(v["cited_rows"] for v in out.values()),
        "cite_bad_rows": sum(v["cite_bad_rows"] for v in out.values()),
    }
    return out


def main() -> None:
    chunks = load_chunks()
    e9 = json.loads((RESULTS / "agentic_cloud_answers.json").read_text(
        encoding="utf-8"))["rows"]
    final = json.loads((RESULTS / "phase_a_structured.json").read_text(
        encoding="utf-8"))["rows"]
    out = {"e9": audit(e9, chunks), "phase_a": audit(final, chunks)}
    (RESULTS / "grounding_compare.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
