"""按题型分配召回的门禁对比（纯本地，零 API 成本）。

复用生产检索器（src.pipeline._get_retriever，E5 语境增强索引），只在外面
套策略包装器，**不改 src/pipeline.py**。指标口径与 CI 门禁完全一致
（src.evalcore.run_eval，k=5）。

纪律：选型只在调参集 50 题上做；留出集 150 题冻结，选定后只跑一次验证，
不在留出集上比较多个配置挑最好的。

2026-11-13 实测结论：**策略无效，不上生产**（详见 per_year_recall 模块
docstring 与 results/recall_alloc_holdout.json）。保留本脚本是为了让
"按年分配召回已试过、无增益"可复现，避免以后重复投入。

用法（项目根目录）:
  .venv/Scripts/python.exe -X utf8 scripts/run_recall_alloc.py --set tuning
  .venv/Scripts/python.exe -X utf8 scripts/run_recall_alloc.py --set holdout \
      --configs prod,peryear_dedup
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))

from src.config import GOLD_FILE, RESULTS_DIR  # noqa: E402
from src.evalcore import run_eval  # noqa: E402

HOLDOUT = PROJECT / "data" / "gold" / "holdout.jsonl"
K = 5
TYPES = ["overall", "fact", "cross_page", "cross_year", "no_answer"]
# 权威结果文件，本脚本永不覆盖
PROTECTED = {"holdout_retrieval.json", "ci_retrieval_retrieval.json",
             "citation_crops.json", "form_compare.json"}


def build_configs(retriever) -> dict:
    from per_year_recall import PerYearRecall

    return {
        "prod": retriever.search,
        "peryear": PerYearRecall(retriever).search,
        "dedup": PerYearRecall(retriever, dedup_pages=True,
                               per_year=False).search,
        "peryear_dedup": PerYearRecall(retriever, dedup_pages=True).search,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["tuning", "holdout"], default="tuning")
    ap.add_argument("--configs", default="prod,peryear,dedup,peryear_dedup")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out_name = args.out or f"recall_alloc_{args.set}.json"
    if Path(out_name).name in PROTECTED:
        raise SystemExit(f"拒绝覆盖权威结果文件: {out_name}")
    out_path = RESULTS_DIR / out_name

    src = GOLD_FILE if args.set == "tuning" else HOLDOUT
    questions = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    print(f"题集: {args.set}  n={len(questions)}  k={K}  来源={src.name}")

    from src.pipeline import _get_retriever

    t0 = time.time()
    retriever = _get_retriever()
    print(f"检索器加载 {time.time() - t0:.1f}s  块数={len(retriever.chunks)}")

    all_configs = build_configs(retriever)
    names = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [n for n in names if n not in all_configs]
    if unknown:
        raise SystemExit(f"未知配置: {unknown}；可选 {list(all_configs)}")

    results = {}
    for name in names:
        t = time.time()
        rows, summary = run_eval(all_configs[name], questions, K)
        results[name] = {"summary": summary, "rows": rows,
                         "seconds": round(time.time() - t, 1)}
        s = summary["overall"]
        print(f"  {name:15s} hit@5={s['page_hit@k']:.2%} "
              f"all@5={s['all_gold_hit@k']:.2%} MRR={s['mrr']:.3f} "
              f"({results[name]['seconds']}s)")

    print(f"\n=== {args.set} 分题型 all@5（严格口径：gold 页全抓齐）===")
    head = f"{'题型':12s}" + "".join(f"{n:>16s}" for n in names)
    print(head)
    for t in TYPES:
        line = f"{t:12s}"
        for n in names:
            s = results[n]["summary"].get(t)
            line += f"{s['all_gold_hit@k']:>15.2%} " if s else f"{'—':>16s}"
        print(line)

    print(f"\n=== {args.set} 分题型 hit@5（宽松口径：至少抓到一页）===")
    print(head)
    for t in TYPES:
        line = f"{t:12s}"
        for n in names:
            s = results[n]["summary"].get(t)
            line += f"{s['page_hit@k']:>15.2%} " if s else f"{'—':>16s}"
        print(line)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"set": args.set, "n": len(questions), "k": K,
         "口径": "与 CI 门禁同一套 src.evalcore.run_eval；零 API",
         "configs": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n-> {out_path}")


if __name__ == "__main__":
    main()