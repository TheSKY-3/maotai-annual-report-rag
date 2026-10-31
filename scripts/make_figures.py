"""生成报告用图表（matplotlib，中文字体用微软雅黑）。

用法: .venv/Scripts/python.exe scripts/make_figures.py
输出: report/figures/*.png
图表数据一律读 results/*.json，不手填。
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = Path("report/figures")
RESULTS = Path("results")


def fig_pipeline() -> None:
    """RAG 五步流程示意图。"""
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.axis("off")
    boxes = [
        (0.01, "年报 PDF\n×4 份\n(556 页)", "#f5f5f5"),
        (0.21, "① 切块\n约500字/块\n(1761 块)", "#dce9f7"),
        (0.41, "② 向量化\nBGE 中文模型\n(每块→512个数)", "#dce9f7"),
        (0.61, "③ 建索引\n相似度检索\n(numpy)", "#dce9f7"),
        (0.81, "⑤ 大模型生成\n答案+出处\n[2024年报 P8]", "#e8f3e2"),
    ]
    for x, label, color in boxes:
        ax.add_patch(plt.Rectangle((x, 0.30), 0.17, 0.42, fc=color,
                                   ec="#555", lw=1.2, zorder=2))
        ax.text(x + 0.085, 0.51, label, ha="center", va="center",
                fontsize=10.5, zorder=3)
    for x0 in (0.18, 0.38, 0.58, 0.78):
        ax.annotate("", xy=(x0 + 0.03, 0.51), xytext=(x0, 0.51),
                    arrowprops=dict(arrowstyle="->", lw=1.6, color="#333"))

    # 用户问题从下方进入检索步骤
    ax.add_patch(plt.Rectangle((0.61, 0.02), 0.17, 0.16, fc="#fdf3d7",
                               ec="#555", lw=1.2, zorder=2))
    ax.text(0.695, 0.10, "④ 用户提问\n(先变成向量再检索)", ha="center",
            va="center", fontsize=9.5, zorder=3)
    ax.annotate("", xy=(0.72, 0.30), xytext=(0.72, 0.18),
                arrowprops=dict(arrowstyle="->", lw=1.6, color="#333"))
    fig.suptitle("RAG 工作流程：先查资料，再回答", fontsize=13, y=0.97)
    fig.savefig(FIG_DIR / "fig_pipeline.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def fig_baseline() -> None:
    """检索基线成绩柱状图。"""
    data = json.loads((RESULTS / "baseline_retrieval.json").read_text(
        encoding="utf-8"))["summary"]
    types = ["fact", "cross_page", "cross_year", "no_answer"]
    labels = ["事实题", "跨页题", "跨年题", "无答案题"]
    hit = [data[t]["page_hit@k"] * 100 for t in types]
    mrr = [data[t]["mrr"] * 100 for t in types]

    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    x = range(len(types))
    w = 0.38
    b1 = ax.bar([i - w / 2 for i in x], hit, w, label="出处页命中率 hit@5",
                color="#4a7ebb")
    b2 = ax.bar([i + w / 2 for i in x], mrr, w, label="MRR（命中越靠前越高）",
                color="#9bbb59")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                    f"{b.get_height():.0f}%", ha="center", fontsize=9)
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("百分比 (%)")
    ax.set_title("基线检索成绩：一半以上的问题查不到正确出处页（k=5）")
    ax.set_ylim(0, max(max(hit), max(mrr)) + 12)
    ax.legend()
    fig.savefig(FIG_DIR / "fig_baseline.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def fig_experiments() -> None:
    """E1/E2 各检索管线的总命中率对比图。"""
    pipelines = [
        ("E1_baseline", "基线\n500字窗口"),
        ("E1_structured", "结构化\n切块"),
        ("E1_small", "小块\n220字"),
        ("E2_year", "向量+\n年份过滤"),
        ("E2_hybrid", "混合检索\nBM25+向量"),
        ("E2_hybrid_year", "混合检索\n+年份过滤"),
        ("E3_rerank", "再叠加\n精排"),
    ]
    names = [p[0] for p in pipelines]
    labels = [p[1] for p in pipelines]
    hit, mrr = [], []
    for n in names:
        f = RESULTS / f"{n}_retrieval.json"
        if not f.exists():
            hit.append(None)
            mrr.append(None)
            continue
        s = json.loads(f.read_text(encoding="utf-8"))["summary"]["overall"]
        hit.append(s["page_hit@k"] * 100)
        mrr.append(s["mrr"] * 100)

    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    x = range(len(names))
    w = 0.38
    b1 = ax.bar([i - w / 2 for i in x], [h or 0 for h in hit], w,
                label="出处页命中率 hit@5", color="#4a7ebb")
    b2 = ax.bar([i + w / 2 for i in x], [m or 0 for m in mrr], w,
                label="MRR", color="#9bbb59")
    for bars, vals in ((b1, hit), (b2, mrr)):
        for b, v in zip(bars, vals):
            if v is not None:
                ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                        f"{v:.0f}%", ha="center", fontsize=9)
    ax.set_xticks(list(x), labels, fontsize=9)
    ax.set_ylabel("百分比 (%)")
    ax.set_title("检索策略对比：基线 30% 最终 62%")
    ax.set_ylim(0, max(v for v in hit if v) + 12)
    ax.legend()
    fig.savefig(FIG_DIR / "fig_experiments.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def fig_answers() -> None:
    """答案层最终成绩（本地 Qwen3-4B + 最优检索）。"""
    f = RESULTS / "final_local_answers.json"
    if not f.exists():
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = [r for r in d["rows"] if r["type"] != "no_answer"]
    types = ["fact", "cross_page", "cross_year"]
    labels = ["事实题", "跨页题", "跨年题"]
    strict = [d["summary"][t]["answer_correct率"] * 100 for t in types]
    per_num = []
    for t in types:
        rs = [r for r in rows if r["type"] == t]
        tt = sum(r["num_total"] for r in rs)
        hh = sum(r["num_hit"] for r in rs)
        per_num.append(hh / tt * 100)

    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    x = range(len(types))
    w = 0.38
    b1 = ax.bar([i - w / 2 for i in x], strict, w,
                label="完整答案率（所有数字都对）", color="#c0504d")
    b2 = ax.bar([i + w / 2 for i in x], per_num, w,
                label="关键数字命中率（宽松）", color="#4a7ebb")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                    f"{b.get_height():.0f}%", ha="center", fontsize=9)
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("百分比 (%)")
    ax.set_title("答案层成绩（本地 Qwen3-4B）：单数字 68.6%，完整答案 26.7%\n"
                 "无答案题拒答正确率 100%（图中不含）")
    ax.set_ylim(0, max(max(strict), max(per_num)) + 12)
    ax.legend(fontsize=9)
    fig.savefig(FIG_DIR / "fig_answers.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def _per_num_hit(d: dict, types: list[str]) -> list[float]:
    """按题型聚合关键数字命中率（宽松口径：任一形态命中即算）。"""
    out = []
    for t in types:
        rs = [r for r in d["rows"] if r["type"] == t]
        tt = sum(r["num_total"] for r in rs)
        hh = sum(r["num_hit"] for r in rs)
        out.append(hh / tt * 100 if tt else 0)
    return out


def fig_e4_compare() -> None:
    """E4：本地 4B vs 云端 DeepSeek 答案层对比（完整答案率 + 关键数字命中率）。"""
    fl, fc = RESULTS / "final_local_answers.json", RESULTS / "final_cloud_answers.json"
    if not (fl.exists() and fc.exists()):
        return
    dl = json.loads(fl.read_text(encoding="utf-8"))
    dc = json.loads(fc.read_text(encoding="utf-8"))
    types = ["fact", "cross_page", "cross_year"]
    labels = ["事实题", "跨页题", "跨年题"]

    def strict(d: dict) -> list[float]:
        s = d.get("summary_rescored", d["summary"])
        return [s[t]["answer_correct率"] * 100 for t in types]

    refuse_l = dl["summary"]["无答案题"]["refusal_correct率"] * 100
    refuse_c = dc["summary"]["无答案题"]["refusal_correct率"] * 100

    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.2))
    x = range(len(types))
    w = 0.36
    panels = [
        (axes[0], strict(dl), strict(dc), "完整答案率（重打分，所有数字都对）"),
        (axes[1], _per_num_hit(dl, types), _per_num_hit(dc, types),
         "关键数字命中率（宽松，任一数字命中即算）"),
    ]
    for ax, lv, cv, title in panels:
        b1 = ax.bar([i - w / 2 for i in x], lv, w, label="本地 Qwen3-4B",
                    color="#4a7ebb")
        b2 = ax.bar([i + w / 2 for i in x], cv, w, label="云端 DeepSeek-Flash",
                    color="#c0504d")
        for bars in (b1, b2):
            for b in bars:
                ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                        f"{b.get_height():.0f}%", ha="center", fontsize=9)
        ax.set_xticks(list(x), labels)
        ax.set_ylabel("百分比 (%)")
        ax.set_ylim(0, 105)
        ax.set_title(title, fontsize=11)
        ax.legend(fontsize=9)
    fig.suptitle("E4 生成模型对比：同一检索管线，本地 4B vs 云端 DeepSeek\n"
                 f"（无答案题拒答正确率：本地 {refuse_l:.0f}% / 云端 {refuse_c:.0f}%）",
                 fontsize=12.5, y=1.02)
    fig.savefig(FIG_DIR / "fig_e4_compare.png", dpi=160,
                bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig_pipeline()
    print("fig_pipeline.png 完成")
    if (RESULTS / "baseline_retrieval.json").exists():
        fig_baseline()
        print("fig_baseline.png 完成")
    if (RESULTS / "E3_rerank_retrieval.json").exists():
        fig_experiments()
        print("fig_experiments.png 完成")
    if (RESULTS / "final_local_answers.json").exists():
        fig_answers()
        print("fig_answers.png 完成")
    if (RESULTS / "final_cloud_answers.json").exists():
        fig_e4_compare()
        print("fig_e4_compare.png 完成")
