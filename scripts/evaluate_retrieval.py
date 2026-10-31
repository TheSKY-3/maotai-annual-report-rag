"""检索层基线评测（复用 src/evalcore.py）。

用法: .venv/Scripts/python.exe scripts/evaluate_retrieval.py [--k 5] [--name baseline]
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import GOLD_FILE  # noqa: E402
from src.evalcore import run_eval, save_and_print  # noqa: E402
from src.retriever import Retriever  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--name", default="baseline")
    args = parser.parse_args()

    questions = [json.loads(l) for l in
                 open(GOLD_FILE, encoding="utf-8") if l.strip()]
    retriever = Retriever()
    rows, summary = run_eval(retriever.search, questions, args.k)
    save_and_print(args.name, rows, summary)

    misses = [r for r in rows if not r["page_hit"]]
    print(f"未命中题数: {len(misses)}，示例:")
    for r in misses[:5]:
        print(f"   [{r['type']}] {r['question'][:30]}"
              f"  期望{r['gold_pages']} 实得{r['got_pages']}")


if __name__ == "__main__":
    main()
