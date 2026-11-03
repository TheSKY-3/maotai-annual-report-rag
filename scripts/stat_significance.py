"""答案层对比的统计显著性：配对 McNemar 精确检验 + bootstrap 置信区间。

回答"两个评测结果的差异是不是运气"：50 题的小样本上 +15.6pp 这类
差异必须给出区间与显著性，否则只是两个点数。两份结果 JSON 按题目 id
配对，只取有答案题（无答案题的拒答正确率两边都是 100%，不参与）。

用法:
  .venv/Scripts/python.exe scripts/stat_significance.py \
      --baseline results/final_e8_cloud_answers.json \
      --treatment results/agentic_cloud_answers.json
输出: 控制台摘要（含 McNemar p 值与差值的 95% CI）。
"""
import argparse
import json
import random
from math import comb
from pathlib import Path


def correct_map(path: Path) -> dict[int, bool]:
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    return {r["id"]: bool(r.get("answer_correct"))
            for r in rows if r["type"] != "no_answer"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--treatment", required=True)
    parser.add_argument("--repeats", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    base = correct_map(Path(args.baseline))
    treat = correct_map(Path(args.treatment))
    ids = sorted(set(base) & set(treat))
    both = sum(1 for i in ids if base[i] and treat[i])
    only_b = sum(1 for i in ids if base[i] and not treat[i])
    only_t = sum(1 for i in ids if not base[i] and treat[i])
    neither = sum(1 for i in ids if not base[i] and not treat[i])
    n = only_b + only_t
    print(f"配对样本 {len(ids)} 题（有答案题）")
    print(f"  双对 {both} | 仅基线对 {only_b} | 仅新管线对 {only_t} | 双错 {neither}")
    if n == 0:
        print("两次结果完全一致，无差异可检验。")
        return
    # McNemar 精确检验（二项，双侧）：不一致对里"谁赢"是否符合 50/50
    tail = sum(comb(n, k) for k in range(0, min(only_b, only_t) + 1)) / 2**n
    p = min(1.0, 2 * tail)
    print(f"  McNemar 精确检验 p = {p:.4f}"
          f"（不一致对 {only_b}:{only_t}，<0.05 即差异显著）")

    diff0 = (sum(treat[i] for i in ids) - sum(base[i] for i in ids)) / len(ids)
    rng = random.Random(args.seed)
    diffs = []
    for _ in range(args.repeats):
        s = [ids[rng.randrange(len(ids))] for _ in range(len(ids))]
        diffs.append((sum(treat[i] for i in s)
                      - sum(base[i] for i in s)) / len(s))
    diffs.sort()
    lo, hi = diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]
    rate_b = sum(base[i] for i in ids) / len(ids)
    rate_t = sum(treat[i] for i in ids) / len(ids)
    print(f"  完整答案率 {rate_b:.3f} → {rate_t:.3f}（{diff0:+.3f}），"
          f"bootstrap 95% CI [{lo:+.3f}, {hi:+.3f}]"
          f"（区间不含 0 即差异稳健）")


if __name__ == "__main__":
    main()
