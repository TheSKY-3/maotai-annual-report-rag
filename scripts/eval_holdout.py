"""留出集检索评测：冻结的 150 题 holdout 上复跑生产检索管线。

与 ci_retrieval_gate.py 同一套 run_eval + get_search，唯一区别是题目来自
data/gold/holdout.jsonl（从未参与调参，页级隔离 + 程序校验 + SHA256 冻结）。
结果落盘 results/holdout_retrieval.json，并与调参集最近一次 CI 结果对比。

本地复现: .venv/Scripts/python.exe scripts/eval_holdout.py
"""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.evalcore import run_eval, save_and_print  # noqa: E402
from src.pipeline import get_search  # noqa: E402

HOLDOUT = PROJECT / "data" / "gold" / "holdout.jsonl"
TUNING_RESULT = PROJECT / "results" / "ci_retrieval_retrieval.json"


def main() -> None:
    questions = [json.loads(l) for l in
                 open(HOLDOUT, encoding="utf-8") if l.strip()]
    rows, summary = run_eval(get_search(), questions, 5)
    save_and_print("holdout", rows, summary)

    if TUNING_RESULT.exists():
        tun = json.loads(TUNING_RESULT.read_text(encoding="utf-8"))["summary"]
        print("\n=== 留出集 vs 调参集（hit@5）===")
        print(f"{'题型':12s} {'调参50题':>10s} {'留出150题':>10s} {'差值':>8s}")
        for t in ["overall", "fact", "cross_page", "cross_year", "no_answer"]:
            if t in summary and t in tun:
                a = tun[t]["page_hit@k"]
                b = summary[t]["page_hit@k"]
                print(f"{t:12s} {a:9.2%} {b:9.2%} {b - a:+7.2%}")


if __name__ == "__main__":
    main()