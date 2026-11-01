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
    """RAG 流程示意图：离线建库 + 在线问答两条流水线（E2 检索增强 + E5 语境增强）。"""
    fig, ax = plt.subplots(figsize=(11, 4.4))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # (x, y0, 标签, 颜色)；上排离线 y=0.64，下排在线 y=0.08，4 列等宽
    xs = (0.015, 0.265, 0.515, 0.765)
    w = 0.20
    boxes = [
        (xs[0], 0.64, "年报 PDF\n×4 份\n(556 页)", "#f5f5f5"),
        (xs[1], 0.64, "① 切块\n约500字/块\n(1761 块)", "#dce9f7"),
        (xs[2], 0.64, "② 语境句生成\n大模型给每块写\n一句\"说明书\" (E5)", "#fbe3c8"),
        (xs[3], 0.64, "③ 向量化+建索引\nBGE 中文模型\n文本=语境句+原文", "#dce9f7"),
        (xs[0], 0.08, "④ 用户提问\n(自然语言)", "#fdf3d7"),
        (xs[1], 0.08, "⑤ 年份识别\n正则限定该年年报\n(E2 · +16pp)", "#fbe3c8"),
        (xs[2], 0.08, "⑥ 混合检索\n向量 + BM25 关键词\nRRF 融合取前5 (E2)", "#fbe3c8"),
        (xs[3], 0.08, "⑦ 大模型生成\n问题 + 5块资料\n→ 答案 + 出处页码", "#e8f3e2"),
    ]
    for x, y0, label, color in boxes:
        ax.add_patch(plt.Rectangle((x, y0), w, 0.26, fc=color,
                                   ec="#555", lw=1.2, zorder=2))
        ax.text(x + w / 2, y0 + 0.13, label, ha="center", va="center",
                fontsize=9.8, zorder=3)
    # 上排内部箭头 + 下排内部箭头
    for x0 in xs[:3]:
        ax.annotate("", xy=(x0 + w + 0.01, 0.77), xytext=(x0 + w, 0.77),
                    arrowprops=dict(arrowstyle="->", lw=1.6, color="#333"))
        ax.annotate("", xy=(x0 + w + 0.01, 0.21), xytext=(x0 + w, 0.21),
                    arrowprops=dict(arrowstyle="->", lw=1.6, color="#333"))
    # 索引 → 混合检索（两条流水线的交汇）
    ax.annotate("", xy=(xs[2] + w / 2, 0.34), xytext=(xs[3] + w / 2, 0.64),
                arrowprops=dict(arrowstyle="->", lw=1.6, color="#333"))
    ax.text(xs[3] + w / 2 + 0.01, 0.49, "索引供检索", ha="left", va="center",
            fontsize=8.5, color="#666", zorder=3)
    ax.text(0.015, 0.95, "离线建库（做一次，语境句生成约花 3 元）", ha="left",
            va="bottom", fontsize=10, color="#666")
    ax.text(0.015, 0.40, "在线问答（每次提问）", ha="left", va="bottom",
            fontsize=10, color="#666")
    fig.suptitle("RAG 工作流程：先查资料，再回答", fontsize=13, y=0.99)
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
        ("E5_hybrid_year", "E5 语境增强\n混合+年份"),
        ("E5_rerank", "E5 语境增强\n+精排"),
        ("E7_table_hybrid_year", "E7 表格\n全替换"),
        ("E7b_tableplus_hybrid_year", "E7 表格\n追加"),
        ("E7c_ctx_table_hybrid_year", "E7 追加\n+语境句"),
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

    fig, ax = plt.subplots(figsize=(10.8, 4.2))
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
    ax.set_xticks(list(x), labels, fontsize=8)
    ax.set_ylabel("百分比 (%)")
    ax.set_title("检索策略对比：基线 30% → E5 语境增强 66%（E7 表格增强不敌语境句）")
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


def fig_e6_retry() -> None:
    """E6：拒答自动重试前后（本地/云端 × 完整答案率/误拒答率）。"""
    names = ["final_e5_local", "final_e6_local",
             "final_e5_cloud", "final_e6_cloud"]
    if not all((RESULTS / f"{n}_answers.json").exists() for n in names):
        return
    data = {n: json.loads((RESULTS / f"{n}_answers.json").read_text(
        encoding="utf-8"))["summary"] for n in names}

    def get(name: str, key: str) -> float:
        grp = "无答案题" if key == "refusal" else "有答案题"
        k = {"strict": "answer_correct率",
             "refuse": "false_refusal率",
             "refusal": "refusal_correct率"}[key]
        return data[name][grp][k] * 100

    refuse_l = get("final_e6_local", "refusal")
    refuse_c = get("final_e6_cloud", "refusal")

    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.2))
    panels = [
        (axes[0], "strict", "完整答案率（所有数字都对）", (0, 60)),
        (axes[1], "refuse", "误拒答率（有答案却说查不到）", (0, 45)),
    ]
    for ax, key, title, ylim in panels:
        vals = [[get("final_e5_local", key), get("final_e5_cloud", key)],
                [get("final_e6_local", key), get("final_e6_cloud", key)]]
        x = range(2)
        w = 0.36
        b1 = ax.bar([i - w / 2 for i in x], vals[0], w,
                    label="无重试（E5）", color="#9bbb59")
        b2 = ax.bar([i + w / 2 for i in x], vals[1], w,
                    label="E6 拒答自动重试", color="#4a7ebb")
        for bars in (b1, b2):
            for b in bars:
                ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.8,
                        f"{b.get_height():.1f}%", ha="center", fontsize=9)
        ax.set_xticks(list(x), ["本地 Qwen3-4B", "云端 DeepSeek-Flash"])
        ax.set_ylabel("百分比 (%)")
        ax.set_ylim(*ylim)
        ax.set_title(title, fontsize=11)
        ax.legend(fontsize=9)
    fig.suptitle("E6 拒答自动重试：误拒答降到原来的 1/3～1/2，完整答案率同升\n"
                 f"（无答案题拒答正确率重试前后均为 {refuse_l:.0f}% / {refuse_c:.0f}%）",
                 fontsize=12.5, y=1.02)
    fig.savefig(FIG_DIR / "fig_e6_retry.png", dpi=160, bbox_inches="tight")
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
    if (RESULTS / "final_e6_cloud_answers.json").exists():
        fig_e6_retry()
        print("fig_e6_retry.png 完成")
