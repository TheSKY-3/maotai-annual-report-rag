"""Phase A 评分：把结构化答案 run 与历史 E9 run 用同一套指标对比。

指标：
  hit@5 any / all      由落盘 hits 的 (年,页) 计算
  answer_correct       沿用旧定义（gold_answer 的全部 >=2 位数字都出现）
  cite_any / cite_precision / all_gold_cited   引用页 vs gold 页
  false_refusal / refusal_correct
  校验违规数           引用越界 / 数字不在所引页（仅 Phase A 有）
用法：.venv/Scripts/python.exe -X utf8 scripts/score_phase_a.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from scripts.evaluate_answers import (cited_pages, gold_numbers,  # noqa: E402
                                      gold_pages_of)
from src.evalcore import all_gold_hit  # noqa: E402

RESULTS = PROJECT / "results"


def pages_of_ids(ids: list[str]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for cid in ids or []:
        m = re.match(r"(\d{4})-p(\d+)", str(cid))
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def score_row(row: dict) -> dict:
    gold = gold_pages_of(row.get("source", ""))
    if row.get("type") == "no_answer":
        return {"refusal_correct": bool(row.get("refused"))}
    text = row.get("answer") or ""
    gnums = gold_numbers(row.get("gold_answer", ""))
    flat = text.replace(",", "").replace("，", "")
    hit_nums = [n for n in gnums if n in flat]
    cited = cited_pages(text)
    got = ({(h["year"], h["page"]) for h in row["hits"]}
           if row.get("hits") else pages_of_ids(row.get("hit_ids") or []))
    return {
        "num_hit": len(hit_nums), "num_total": len(gnums),
        "answer_correct": bool(gnums) and len(hit_nums) == len(gnums),
        "cite_any": bool(cited & gold),
        "cite_precision": (len(cited & gold) / len(cited)) if cited else None,
        "all_gold_cited": bool(gold) and gold.issubset(cited),
        "hit_any": bool(got & gold),
        "all_gold_hit": all_gold_hit(got, gold),
        "false_refusal": bool(row.get("refused")),
    }


def agg(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    out = {"n": n}
    if rows[0].get("type") == "no_answer":
        out["refusal_correct"] = round(
            sum(r["refusal_correct"] for r in rows) / n, 4)
        return out
    out["answer_correct"] = round(sum(r["answer_correct"] for r in rows) / n, 4)
    out["cite_any"] = round(sum(r["cite_any"] for r in rows) / n, 4)
    sc = [r for r in rows if r["cite_precision"] is not None]
    out["cite_precision"] = (round(sum(r["cite_precision"] for r in sc) / len(sc), 4)
                             if sc else None)
    out["cite_nonempty"] = len(sc)
    out["all_gold_cited"] = round(sum(r["all_gold_cited"] for r in rows) / n, 4)
    out["hit_any"] = round(sum(r["hit_any"] for r in rows) / n, 4)
    out["all_gold_hit"] = round(sum(r["all_gold_hit"] for r in rows) / n, 4)
    out["false_refusal"] = round(sum(r["false_refusal"] for r in rows) / n, 4)
    return out


def summarize(rows: list[dict]) -> dict:
    has = [r for r in rows if r["type"] != "no_answer"]
    na = [r for r in rows if r["type"] == "no_answer"]
    by_type = defaultdict(list)
    for r in has:
        by_type[r["type"]].append(r)
    return {"有答案题": agg(has), "无答案题": agg(na),
            **{t: agg(rs) for t, rs in sorted(by_type.items())}}


def load(name: str) -> list[dict]:
    path = RESULTS / name
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"] if isinstance(data, dict) else data
    gold = {str(q["id"]): q for q in map(
        json.loads,
        (PROJECT / "data/gold/questions.jsonl").read_text(
            encoding="utf-8").splitlines()) if str(q).strip()}
    for r in rows:  # 历史答案文件没存 gold 字段，按 id 回填
        q = gold.get(str(r.get("id")))
        if q:
            r.setdefault("gold_answer", q["gold_answer"])
            r["gold_answer"] = r.get("gold_answer") or q["gold_answer"]
            r["source"] = r.get("source") or q["source"]
    return rows


def main() -> None:
    args = sys.argv[1:]
    phase_name = (args[args.index("--in") + 1]
                  if "--in" in args else "phase_a_structured.json")
    out_name = (args[args.index("--out") + 1]
                if "--out" in args else "phase_a_score.json")
    phase = load(phase_name)
    old = load("agentic_cloud_answers.json")

    scored = []
    for r in phase:
        s = score_row(r)
        s.update({"id": r["id"], "type": r["type"], "route": r["route"],
                  "refused": r.get("refused"),
                  "structured": r.get("structured"),
                  "violations": r.get("violations") or [],
                  "citations": r.get("citations") or [],
                  "cost_cny": r.get("cost_cny", 0)})
        scored.append(s)
    old_scored = []
    for r in old:
        s = score_row(r)
        s["id"] = r["id"]
        s["type"] = r["type"]
        old_scored.append(s)

    violation_rows = [r for r in scored if r["violations"]]
    cite_bad = [r for r in violation_rows
                if any("引用" in v for v in r["violations"])]
    num_bad = [r for r in violation_rows
               if any("数字" in v for v in r["violations"])]

    out = {
        "phase_a": summarize(scored),
        "e9_baseline": summarize(old_scored),
        "phase_a_extra": {
            "n": len(scored),
            "with_violations": len(violation_rows),
            "cite_violations": len(cite_bad),
            "num_violations": len(num_bad),
            "structured_rows": sum(1 for r in scored if r["structured"]),
            "agent_rows": sum(1 for r in scored if r["route"] == "agent"),
            "cost_cny": round(sum(r.get("cost_cny", 0) for r in scored), 4),
        },
        "rows": scored,
    }
    (RESULTS / out_name).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("phase_a", "e9_baseline",
                                          "phase_a_extra")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
