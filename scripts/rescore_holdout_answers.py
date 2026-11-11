"""答案层留出集离线重打分：修正 gold_numbers 把出处标注当答案数字的口径偏差。

零网络、零 API：只读已落盘的 answers JSON 与 gold 文件。

问题：evaluate_answers.gold_numbers 直接对 gold_answer 全文抽数字，于是
  - 出处引文里的表格其他数字（如毛利率题引文带了营收/成本）
  - 出处页码 P12/P13 里的 "12"/"13"
都被当成"必须出现在答案里的数字"，答案明明答对却判错。

修正：先用 build_holdout.clean_answer 剥掉出处标注（与检索校验同一口径），
再抽数字。对调参集与留出集**同样**重打，双口径并列，不单独美化任何一边。

用法: .venv/Scripts/python.exe scripts/rescore_holdout_answers.py
输出: results/holdout_answer_rescore.json
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
for p in (PROJECT, PROJECT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from build_holdout import clean_answer  # noqa: E402
from evaluate_answers import gold_numbers  # noqa: E402

PAIRS = [
    ("调参50题", "results/agentic_cloud_answers.json", "data/gold/questions.jsonl"),
    ("留出150题", "results/holdout_cloud_answers.json", "data/gold/holdout.jsonl"),
]


def clean_numbers(gold_answer: str) -> list[str]:
    return gold_numbers(clean_answer(gold_answer))


def rescore(rows, gold):
    old = defaultdict(list)
    new = defaultdict(list)
    flipped = []
    for r in rows:
        if r["type"] == "no_answer":
            continue
        q = gold[r["id"]]
        flat = r["answer"].replace(",", "").replace("，", "")
        gn_old = gold_numbers(q["gold_answer"])
        gn_new = clean_numbers(q["gold_answer"])
        ac_old = bool(gn_old) and all(n in flat for n in gn_old)
        ac_new = bool(gn_new) and all(n in flat for n in gn_new)
        for bucket in (r["type"], "有答案题"):
            old[bucket].append(ac_old)
            new[bucket].append(ac_new)
        if ac_old != ac_new:
            flipped.append({"id": r["id"], "old": ac_old, "new": ac_new,
                            "gold_numbers_old": gn_old,
                            "gold_numbers_new": gn_new})
    return old, new, flipped


def rate(vals):
    return round(sum(vals) / len(vals), 4) if vals else None


def main():
    out = {}
    for label, ans_path, gold_path in PAIRS:
        rows = json.loads((PROJECT / ans_path).read_text(encoding="utf-8"))["rows"]
        gold = {json.loads(l)["id"]: json.loads(l)
                for l in (PROJECT / gold_path).read_text(encoding="utf-8").splitlines()
                if l.strip()}
        old, new, flipped = rescore(rows, gold)
        out[label] = {
            "by_type": {t: {"n": len(old[t]),
                            "old": rate(old[t]), "new": rate(new[t])}
                        for t in ["有答案题", "fact", "cross_page", "cross_year"]},
            "flipped_count": len(flipped),
            "flipped": flipped,
        }
        print(f"\n=== {label} answer_correct（旧口径 → 修正口径）===")
        for t in ["有答案题", "fact", "cross_page", "cross_year"]:
            b = out[label]["by_type"][t]
            print(f"  {t:10s} n={b['n']:3d}  {b['old']:.2%} → {b['new']:.2%}")
        print(f"  判定翻转题数: {len(flipped)}")

    (PROJECT / "results/holdout_answer_rescore.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n-> results/holdout_answer_rescore.json")


if __name__ == "__main__":
    main()