"""对已保存的答案评测结果重新打分（改进数字匹配：支持 亿元↔元 换算）。

用法: .venv/Scripts/python.exe scripts/rescore_answers.py results/E4_local_answers.json
说明: 初版匹配器要求 gold 数字逐字出现在回答中，但年报正文用"元"、
      经营讨论用"亿元"，同一数值两种写法会被误判为错。本脚本以
      "精确匹配 或 亿元换算成元后匹配" 重算 answer_correct。
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def gold_numbers(text: str) -> list[str]:
    out = []
    for m in re.findall(r"[0-9][0-9,，]*(?:\.[0-9]+)?", text):
        out.append(m.replace(",", "").replace("，", ""))
    return [n for n in out if len(n) >= 2]


def gold_unit(text: str) -> str:
    """粗略判断 gold_answer 的计量单位：亿元 / 万元 / 元。"""
    if "亿元" in text:
        return "亿"
    if "万元" in text:
        return "万"
    return ""


def num_variants(text: str) -> list[set[str]]:
    """gold_answer 中每个数字的可接受形态列表（原文 + 单位换算）。"""
    unit = gold_unit(text)
    per_num = []
    for n in gold_numbers(text):
        forms = {n}
        try:
            v = float(n)
        except ValueError:
            per_num.append(forms)
            continue
        if unit == "亿":
            forms.add(f"{v * 1e8:.2f}".rstrip("0").rstrip("."))
        elif unit == "万":
            forms.add(f"{v * 1e4:.2f}".rstrip("0").rstrip("."))
        per_num.append(forms)
    return per_num


def rescore(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data["rows"]
    # gold answers live in the gold file; match by id
    gold_file = Path("data/gold/questions.jsonl")
    gold = {json.loads(l)["id"]: json.loads(l)
            for l in gold_file.open(encoding="utf-8") if l.strip()}
    for r in rows:
        if r["type"] == "no_answer":
            continue
        g = gold[r["id"]]
        ans = r["answer"].replace(",", "").replace("，", "")
        groups = num_variants(g["gold_answer"])
        hits = sum(1 for forms in groups if any(v in ans for v in forms))
        r["num_hit"] = hits
        r["num_total"] = len(groups)
        r["answer_correct"] = bool(groups) and hits == len(groups)

    def agg(rs):
        n = len(rs)
        if n == 0:
            return {"n": 0}
        return {"n": n,
                "answer_correct率": round(sum(r["answer_correct"] for r in rs) / n, 4),
                "cite_correct率": round(sum(r["cite_correct"] for r in rs) / n, 4),
                "false_refusal率": round(sum(r["false_refusal"] for r in rs) / n, 4)}

    has = [r for r in rows if r["type"] != "no_answer"]
    by_type = defaultdict(list)
    for r in has:
        by_type[r["type"]].append(r)
    summary = {"有答案题(重打分)": agg(has),
               **{t: agg(rs) for t, rs in sorted(by_type.items())}}
    out = Path(path)
    data["summary_rescored"] = summary
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    rescore(sys.argv[1] if len(sys.argv) > 1 else "results/E4_local_answers.json")
