"""CI 检索层回归 gate：生产检索 hit@5 低于阈值则退出非零（GitHub Actions 用）。

每次 push 免费复跑检索层评测（纯本地计算，无 API 成本），防止改动悄悄
打漏检索水位。生产基线 66%（E5 语境增强索引，已随仓库分发），阈值放宽
2pp 容忍环境抖动。结果摘要写入 $GITHUB_STEP_SUMMARY，Actions 运行页
直接可见；逐题明细存 results/ci_retrieval.json 随 artifact 上传。

本地复现:
  .venv/Scripts/python.exe scripts/ci_retrieval_gate.py
"""
import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import GOLD_FILE, RESULTS_DIR  # noqa: E402
from src.evalcore import run_eval, save_and_print  # noqa: E402
from src.pipeline import get_search  # noqa: E402

THRESHOLD = 0.64  # 生产 0.66 - 2pp 容忍


def main() -> None:
    questions = [json.loads(l) for l in
                 open(GOLD_FILE, encoding="utf-8") if l.strip()]
    rows, summary = run_eval(get_search(), questions, 5)
    save_and_print("ci_retrieval", rows, summary)

    hit = summary["overall"]["page_hit@k"]
    ok = hit >= THRESHOLD
    by_type = "  ".join(
        f"{t} {s['page_hit@k']:.0%}" for t, s in summary.items()
        if isinstance(s, dict) and "page_hit@k" in s and t != "overall")
    lines = [
        "## 检索层评测 gate",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| hit@5 | **{hit:.2%}**（阈值 {THRESHOLD:.0%}） |",
        f"| MRR | {summary['overall']['mrr']:.3f} |",
        f"| 分题型 | {by_type} |",
        "",
        "结果：" + ("✅ 通过，检索水位未回退" if ok
                    else "❌ 低于阈值——检索层发生回退，禁止合并"),
    ]
    text = "\n".join(lines)
    print(text)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        Path(step_summary).write_text(text + "\n", encoding="utf-8")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
