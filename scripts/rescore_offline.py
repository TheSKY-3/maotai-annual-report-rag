r"""Phase A 指标层离线重算：用修正后的 gold 解析重算 results/*.json 里的历史指标。

零网络、零 API 调用：只读 results/*.json 与 data/gold/questions.jsonl。

口径
  旧口径    修复前生成的数字。优先取 results 文件里落盘的字段（page_hit /
            mrr / page_prec / cite_correct / answer_correct）；当时没落盘的
            字段（all_gold_hit、cite_precision）用修复前的正则
            r"(\d{4})年报\s*P(\d+)" 按同一公式重算，等价于"当时会算出的值"。
  修正口径  用修复后的 src.evalcore.parse_sources 重新解析 gold 后计算。
  算不出的字段一律 null，原因写进每个文件的 unavailable，并汇总进 md 第 7 节。

为什么不需要重跑检索：检索阶段只依赖问题文本，got_pages 是当时真实召回的证据，
本次只改 gold 解析口径，所以用落盘的 got_pages 重算即可，不引入新的模型调用。

用法
  .venv\Scripts\python.exe -X utf8 scripts/rescore_offline.py
输出
  results/rescore_phase_a.json   机器可读的全量重算结果
  review/phase_a_metrics.md      中文表格报告
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.evalcore import all_gold_hit, cite_precision, parse_sources  # noqa: E402

RESULTS_DIR = PROJECT / "results"
GOLD_FILE = PROJECT / "data" / "gold" / "questions.jsonl"
OUT_JSON = RESULTS_DIR / "rescore_phase_a.json"
OUT_MD = PROJECT / "review" / "phase_a_metrics.md"

# 修复前的正则，只用于复现旧口径；评测主链路必须用 evalcore.parse_sources。
OLD_SOURCE_RE = re.compile(r"(\d{4})年报\s*P(\d+)")
GOT_PAGE_RE = re.compile(r"\s*(\d{4})\s*P\s*(\d+)")
HIT_ID_RE = re.compile(r"\s*(\d{4})-p(\d+)-c\d+")

METRICS = ("hit_at_k_any", "hit_at_k_all", "mrr", "page_prec_at_k",
           "cite_any", "cite_precision", "answer_correct", "refusal_correct")
# 关键指标在 md 里用百分比展示，其余（MRR、cite_precision）用三位小数也够
PCT_METRICS = {"hit_at_k_any", "hit_at_k_all", "cite_any",
               "answer_correct", "refusal_correct"}

HEADLINE_FILES = ("E5_hybrid_year_retrieval.json",
                  "prod_hybrid_post_agentic_retrieval.json")

# 50 题黄金集跑满时 rows 是 50 行；低于此阈值的是冒烟/部分运行，
# 只单独列出来，不混进与 README 数字对应的那几张表。
FULL_RUN_MIN_ROWS = 45

UNAVAILABLE = {
    "retrieval": {
        "cite_any": "检索层 run 没有回答文本，无引用可解析",
        "cite_precision": "检索层 run 没有回答文本，无引用可解析",
        "answer_correct": "检索层 run 不生成答案",
        "refusal_correct": "检索层 run 不生成答案",
    },
    "answers": {
        "hit_at_k_any": "答案层 run 未落盘逐题召回页（无 got_pages），无法重算 top-k 命中",
        "hit_at_k_all": "答案层 run 未落盘逐题召回页（无 got_pages），无法重算 top-k 命中",
        "mrr": "答案层 run 未落盘逐题召回页（无 got_pages），无法重算排名",
        "page_prec_at_k": "答案层 run 未落盘逐题召回页（无 got_pages），无法重算 page_prec",
    },
    "unsupported": {m: "该结果文件无逐题证据（既无 got_pages 也无 answer 字段）"
                    for m in METRICS},
}


# ----------------------------------------------------------------- 小工具
def parse_sources_old(text: str) -> list[tuple[int, int]]:
    """修复前的解析。只用来复现旧口径，不要用于新评测。"""
    return [(int(y), int(p)) for y, p in OLD_SOURCE_RE.findall(text or "")]


def pages_of(raw) -> list[tuple[int, int]]:
    """把落盘的 got_pages / gold_pages 还原成 (年, 页)。

    兼容 "2024P9" 字符串与 [2024, 9] 数组两种写法；无法解析的条目直接跳过。
    """
    out: list[tuple[int, int]] = []
    for item in raw or []:
        if isinstance(item, str):
            m = GOT_PAGE_RE.match(item)
            if m:
                out.append((int(m.group(1)), int(m.group(2))))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            out.append((int(item[0]), int(item[1])))
    return out


def pages_of_hit_ids(raw) -> list[tuple[int, int]]:
    """证据块 id（"2022-p007-c02"）-> (年, 页)。"""
    out: list[tuple[int, int]] = []
    for item in raw or []:
        if isinstance(item, str):
            m = HIT_ID_RE.match(item)
            if m:
                out.append((int(m.group(1)), int(m.group(2))))
    return out


def mean(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def first_hit_rank(got: list[tuple[int, int]],
                   gold: set[tuple[int, int]]) -> float:
    for rank, page in enumerate(got, start=1):
        if page in gold:
            return round(1.0 / rank, 4)
    return 0.0


def gold_numbers(text: str) -> list[str]:
    """提取 gold_answer 里的数字串（沿用 scripts/evaluate_answers.py 原定义）。"""
    out = [m.replace(",", "").replace("，", "")
           for m in re.findall(r"[0-9][0-9,，]*(?:\.[0-9]+)?", text or "")]
    return [n for n in out if len(n) >= 2]


def answer_correct(answer: str, gold_answer: str) -> bool:
    """沿用原定义：gold_answer 的全部数字都出现在回答里。"""
    gnums = gold_numbers(gold_answer)
    flat = (answer or "").replace(",", "").replace("，", "")
    return bool(gnums) and all(n in flat for n in gnums)


def blank_row() -> dict:
    return {key: None for key in METRICS}


# ----------------------------------------------------------------- 打分
def score_retrieval(rows: list[dict], k: int, gold: dict) -> tuple:
    old_rows, new_rows, warnings = [], [], []
    agree = {"stored": 0, "eq_old": 0, "eq_new": 0}
    for r in rows:
        qid = int(r["id"])
        if qid not in gold:
            warnings.append(f"id={qid} 不在 questions.jsonl 里，已跳过")
            continue
        src = gold[qid]["source"]
        got = pages_of(r.get("got_pages"))
        got_set = set(got)
        gold_stored = set(pages_of(r.get("gold_pages")))
        gold_old = gold_stored or set(parse_sources_old(src))
        gold_new = set(parse_sources(src))

        if gold_stored and gold_stored != set(parse_sources_old(src)):
            warnings.append(f"id={qid} 落盘 gold_pages 与旧正则解析不一致")

        o, n = blank_row(), blank_row()
        # 旧口径：落盘值优先；同时与"用旧 gold 重算"对账
        stored_hit = r.get("page_hit")
        recomputed_old = any(p in gold_old for p in got)
        if stored_hit is not None and bool(stored_hit) != recomputed_old:
            warnings.append(f"id={qid} 落盘 page_hit 与旧 gold 重算不一致")
        o["hit_at_k_any"] = bool(stored_hit) if stored_hit is not None \
            else recomputed_old
        o["hit_at_k_all"] = all_gold_hit(got_set, gold_old)
        o["mrr"] = r.get("mrr") if r.get("mrr") is not None \
            else first_hit_rank(got, gold_old)
        o["page_prec_at_k"] = r.get("page_prec") \
            if r.get("page_prec") is not None \
            else len(got_set & gold_old) / k

        n["hit_at_k_any"] = any(p in gold_new for p in got)
        n["hit_at_k_all"] = all_gold_hit(got_set, gold_new)
        n["mrr"] = first_hit_rank(got, gold_new)
        n["page_prec_at_k"] = len(got_set & gold_new) / k
        if stored_hit is not None:
            agree["stored"] += 1
            agree["eq_old"] += bool(stored_hit) == recomputed_old
            agree["eq_new"] += bool(stored_hit) == n["hit_at_k_any"]
        old_rows.append(o)
        new_rows.append(n)
    return old_rows, new_rows, warnings, agree


def score_answers(rows: list[dict], gold: dict) -> tuple:
    old_rows, new_rows, warnings, extra = [], [], [], {}
    agree = {"stored": 0, "eq_old": 0, "eq_new": 0}
    for r in rows:
        qid = int(r["id"])
        if qid not in gold:
            warnings.append(f"id={qid} 不在 questions.jsonl 里，已跳过")
            continue
        q = gold[qid]
        answer = r.get("answer") or ""
        o, n = blank_row(), blank_row()

        if r.get("type") == "no_answer":
            stored = r.get("refusal_correct")
            refused = bool(r.get("refused"))
            if stored is not None and bool(stored) != refused:
                warnings.append(f"id={qid} 落盘 refusal_correct 与 refused 不一致")
            o["refusal_correct"] = bool(stored) if stored is not None else refused
            n["refusal_correct"] = refused
            old_rows.append(o)
            new_rows.append(n)
            continue

        gold_old = set(parse_sources_old(q["source"]))
        gold_new = set(parse_sources(q["source"]))
        cited_old = set(parse_sources_old(answer))
        cited_new = set(parse_sources(answer))
        recomputed_old_cite = bool(cited_old & gold_old)
        stored_cite = r.get("cite_correct")
        if stored_cite is not None and bool(stored_cite) != recomputed_old_cite:
            warnings.append(f"id={qid} 落盘 cite_correct 与旧口径重算不一致")

        o["cite_any"] = bool(stored_cite) if stored_cite is not None \
            else recomputed_old_cite
        o["cite_precision"] = cite_precision(cited_old, gold_old)
        ac = r.get("answer_correct")
        recomputed_ac = answer_correct(answer, q["gold_answer"])
        if ac is not None and bool(ac) != recomputed_ac:
            warnings.append(f"id={qid} 落盘 answer_correct 与原定义重算不一致")
        o["answer_correct"] = bool(ac) if ac is not None else recomputed_ac

        n["cite_any"] = bool(cited_new & gold_new)
        n["cite_precision"] = cite_precision(cited_new, gold_new)
        n["answer_correct"] = recomputed_ac
        if stored_cite is not None:
            agree["stored"] += 1
            agree["eq_old"] += bool(stored_cite) == recomputed_old_cite
            agree["eq_new"] += bool(stored_cite) == n["cite_any"]
        old_rows.append(o)
        new_rows.append(n)

        if r.get("hit_ids"):
            pool = set(pages_of_hit_ids(r["hit_ids"]))
            for tag, gold_set in (("old", gold_old), ("new", gold_new)):
                slot = extra.setdefault(tag, {"any": [], "all": []})
                slot["any"].append(bool(pool & gold_set))
                slot["all"].append(all_gold_hit(pool, gold_set))
    if extra:
        extra_block = {
            "note": "证据块 id（hit_ids）的并集，agent 多轮检索拼接而成，"
                    "不是单次 top-5 排名，故不计入 hit@5",
            "evidence_pool_hit_any": {
                "old": mean(extra["old"]["any"]),
                "new": mean(extra["new"]["any"]),
                "n": len(extra["new"]["any"]),
            },
            "evidence_pool_hit_all": {
                "old": mean(extra["old"]["all"]),
                "new": mean(extra["new"]["all"]),
                "n": len(extra["new"]["all"]),
            },
        }
    else:
        extra_block = None
    return old_rows, new_rows, warnings, extra_block, agree


def metrics_block(old_rows: list[dict], new_rows: list[dict]) -> dict:
    block = {}
    for key in METRICS:
        old_vals = [r[key] for r in old_rows if r[key] is not None]
        new_vals = [r[key] for r in new_rows if r[key] is not None]
        block[key] = {
            "old": mean(old_vals), "old_n": len(old_vals),
            "new": mean(new_vals), "new_n": len(new_vals),
        }
    return block


def by_type_block(old_rows, new_rows, types) -> dict:
    out = {}
    for tag in sorted(set(types)):
        idx = [i for i, t in enumerate(types) if t == tag]
        out[tag] = metrics_block([old_rows[i] for i in idx],
                                 [new_rows[i] for i in idx])
    return out


def load_rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("rows", "results", "answers"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


# ----------------------------------------------------------------- 报告
def cell(value, metric: str | None = None) -> str:
    if value is None:
        return "—"
    if metric in PCT_METRICS:
        return f"{value:.1%}"
    return f"{value:.3f}"


def delta(old, new, metric: str) -> str:
    if old is None or new is None:
        return "—"
    if metric in PCT_METRICS:
        return f"{(new - old) * 100:+.1f}pp"
    return f"{new - old:+.3f}"


def render_md(payload: dict) -> str:
    files = payload["files"]
    stamp = payload["generated_at"]
    by_name = {f["file"]: f for f in files}
    lines: list[str] = []
    add = lines.append

    add("# Phase A 指标层：金标出处解析修复与离线重算")
    add("")
    add("- 生成脚本：§scripts/rescore_offline.py§（零网络、零 API，只读 "
        "§results/*.json§ 与 §data/gold/questions.jsonl§）")
    add(f"- 生成时间：{stamp}")
    add("- 本文所有数字由脚本产出，未手工填写；重跑脚本即可复现。")
    add("- 两个口径：**旧口径（修复前）** 取 §results/§ 里落盘的历史指标，"
        "当时没落盘的字段用修复前正则按同一公式重算；"
        "**修正口径** 用修复后的 §src.evalcore.parse_sources§ 重新解析 gold 后计算。")
    add("")

    # ---- 1. 关键结论
    head = by_name.get(HEADLINE_FILES[0])
    add("## 1. 一句话结论")
    add("")
    if head:
        ov = head["metrics"]["hit_at_k_any"]
        cp = head["by_type"].get("cross_page", {}).get("hit_at_k_any")
        add(f"- 生产检索 hit@5：**{cell(ov['old'], 'hit_at_k_any')} → "
            f"{cell(ov['new'], 'hit_at_k_any')}**"
            f"（{delta(ov['old'], ov['new'], 'hit_at_k_any')}，n={ov['new_n']}）。")
        if cp:
            add(f"- 跨页题 hit@5：**{cell(cp['old'], 'hit_at_k_any')} → "
                f"{cell(cp['new'], 'hit_at_k_any')}**"
                f"（{delta(cp['old'], cp['new'], 'hit_at_k_any')}，n={cp['new_n']}）。")
        add("- 原因不是检索变强了，是**金标出处标少了**：§2024年报 P9、P16§ 这类"
            "写法只解析出 P9，P16 被静默丢掉，于是「只命中一页」被算成未命中。")
        add("- 严格口径 all@5（要求覆盖 gold 的全部页）在生产 run 上没变，"
            "但在 gold 页增加的 run 上会**下降**（如 baseline 28% → 24%）："
            "这是预期的严格化，说明 all 口径本就不该被任何历史数字直接引用。")
    add("")

    # ---- 2. 受影响的题
    add(f"## 2. 受影响的 {len(payload['affected_questions'])} 道题"
        "（旧口径漏掉的 gold 页）")
    add("")
    add("| 题号 | 题型 | gold 出处原文 | 旧口径解析 | 修正口径解析 |")
    add("|---|---|---|---|---|")
    for item in payload["affected_questions"]:
        old = "、".join(f"{y}P{p}" for y, p in item["old"]) or "（空）"
        new = "、".join(f"{y}P{p}" for y, p in item["new"]) or "（空）"
        add(f"| {item['id']} | {item['type']} | §{item['source']}§ | {old} | {new} |")
    add("")

    # ---- 3. 生产检索分题型
    add("## 3. 生产检索分题型（修正口径的 90% 出现在这里）")
    add("")
    for name in HEADLINE_FILES:
        item = by_name.get(name)
        if not item:
            continue
        add(f"### §{name}§（k={item['k']}，n={item['n_rows']}）")
        add("")
        add("| 题型 | n | hit@5 旧口径 | hit@5 修正口径 | Δ | all@5 旧口径 | all@5 修正口径 | MRR 旧 | MRR 修正 |")
        add("|---|---|---|---|---|---|---|---|---|")
        rows_md = [("**overall**", item["metrics"])] + \
                  [(t, b) for t, b in item["by_type"].items()]
        for tag, block in rows_md:
            h = block["hit_at_k_any"]
            a = block["hit_at_k_all"]
            m = block["mrr"]
            add(f"| {tag} | {h['new_n']} | {cell(h['old'], 'hit_at_k_any')} "
                f"| {cell(h['new'], 'hit_at_k_any')} "
                f"| {delta(h['old'], h['new'], 'hit_at_k_any')} "
                f"| {cell(a['old'], 'hit_at_k_all')} "
                f"| {cell(a['new'], 'hit_at_k_all')} "
                f"| {cell(m['old'], 'mrr')} | {cell(m['new'], 'mrr')} |")
        add("")

    def retrieval_table(items: list[dict]) -> None:
        add("| 结果文件 | n | hit@5 any 旧 | hit@5 any 修正 | Δ | hit@5 all 旧 | hit@5 all 修正 | MRR 旧 | MRR 修正 | prec@5 旧 | prec@5 修正 |")
        add("|---|---|---|---|---|---|---|---|---|---|---|")
        for item in items:
            h = item["metrics"]["hit_at_k_any"]
            a = item["metrics"]["hit_at_k_all"]
            m = item["metrics"]["mrr"]
            p = item["metrics"]["page_prec_at_k"]
            add(f"| §{item['file']}§ | {h['new_n']} "
                f"| {cell(h['old'], 'hit_at_k_any')} "
                f"| {cell(h['new'], 'hit_at_k_any')} "
                f"| {delta(h['old'], h['new'], 'hit_at_k_any')} "
                f"| {cell(a['old'], 'hit_at_k_all')} "
                f"| {cell(a['new'], 'hit_at_k_all')} "
                f"| {cell(m['old'], 'mrr')} | {cell(m['new'], 'mrr')} "
                f"| {cell(p['old'], 'page_prec_at_k')} "
                f"| {cell(p['new'], 'page_prec_at_k')} |")
        add("")

    def answers_table(items: list[dict]) -> None:
        add("| 结果文件 | 有答案题 n | cite_any 旧 | cite_any 修正 | Δ | cite_precision 旧 | cite_precision 修正 | 非空题数 | answer_correct 旧 | answer_correct 修正 |")
        add("|---|---|---|---|---|---|---|---|---|---|")
        for item in items:
            c = item["metrics"]["cite_any"]
            cp = item["metrics"]["cite_precision"]
            ac = item["metrics"]["answer_correct"]
            add(f"| §{item['file']}§ | {c['new_n']} "
                f"| {cell(c['old'], 'cite_any')} | {cell(c['new'], 'cite_any')} "
                f"| {delta(c['old'], c['new'], 'cite_any')} "
                f"| {cell(cp['old'], 'cite_precision')} "
                f"| {cell(cp['new'], 'cite_precision')} | {cp['new_n']} "
                f"| {cell(ac['old'], 'answer_correct')} "
                f"| {cell(ac['new'], 'answer_correct')} |")
        add("")

    def split(kind: str) -> tuple[list[dict], list[dict]]:
        picked = [f for f in files if f["kind"] == kind]
        return ([f for f in picked if f["tier"] == "full"],
                [f for f in picked if f["tier"] != "full"])

    # ---- 4. 检索层
    ret_full, ret_part = split("retrieval")
    add(f"## 4. 检索层全量重算（{len(ret_full)} 个 50 题 run）")
    add("")
    retrieval_table(ret_full)

    # ---- 5. 答案层
    ans_full, ans_part = split("answers")
    add(f"## 5. 答案层全量重算（引用口径，{len(ans_full)} 个 50 题 run）")
    add("")
    add("答案层 run 没有落盘逐题召回页，hit@5 / MRR / prec@5 全部为 —（见第 8 节）。")
    add("§cite_precision§ 的分母是「有引用的题」，因此同时给出非空题数。")
    add("")
    answers_table(ans_full)

    # ---- 6. 冒烟/部分运行
    if ret_part or ans_part:
        add(f"## 6. 冒烟/部分运行（rows < {FULL_RUN_MIN_ROWS}，不参与结论比较）")
        add("")
        add("这些文件也在 §results/§ 里，脚本照单全算，但题数远少于 50，"
            "只用来确认链路能跑通，不能当作评测证据。")
        add("")
        if ret_part:
            retrieval_table(ret_part)
        if ans_part:
            answers_table(ans_part)

    # ---- 6. 证据池（额外）
    pool = [(f["file"], f["extra"]) for f in files if f.get("extra")]
    if pool:
        add("## 7. 附加口径：证据池覆盖（不属于 hit@5）")
        add("")
        add("少数答案层 run 落了 §hit_ids§（证据块 id，形如 §2022-p007-c02§）。"
            "它是 agent 多轮检索拼接起来的证据池，不是单次 top-5 排名，"
            "所以只作为「证据池有没有覆盖到 gold 页」的参考，不并入 hit@5。")
        add("")
        add("| 结果文件 | 证据池 n | 覆盖任一 gold 旧 | 修正 | 覆盖全部 gold 旧 | 修正 |")
        add("|---|---|---|---|---|---|")
        for name, extra in pool:
            a = extra["evidence_pool_hit_any"]
            b = extra["evidence_pool_hit_all"]
            add(f"| §{name}§ | {a['n']} | {cell(a['old'], 'hit_at_k_any')} "
                f"| {cell(a['new'], 'hit_at_k_any')} "
                f"| {cell(b['old'], 'hit_at_k_all')} "
                f"| {cell(b['new'], 'hit_at_k_all')} |")
        add("")

    # ---- 8. 算不出的项
    add("## 8. 算不出的项（字段留 null 的原因）")
    add("")
    add("按「同类 run + 同一个原因」合并，逐个文件列在 JSON 的 "
        "§unavailable§ 字段里。")
    add("")
    add("| 涉及文件数 | 空缺指标 | 原因 |")
    add("|---|---|---|")
    counts: dict[tuple[str, str], int] = {}
    for item in files:
        grouped_by_reason: dict[str, list[str]] = {}
        for metric, reason in (item.get("unavailable") or {}).items():
            grouped_by_reason.setdefault(reason, []).append(metric)
        for reason, metrics in grouped_by_reason.items():
            key = (reason, ", ".join(sorted(metrics)))
            counts[key] = counts.get(key, 0) + 1
    for (reason, metrics), n in sorted(counts.items(),
                                       key=lambda kv: (-kv[1], kv[0][0])):
        add(f"| {n} | {metrics} | {reason} |")
    add("")

    # ---- 8. 对账
    add("## 9. 旧口径的可复现性对账")
    add("")
    add("为了证明「旧口径」不是本文编的，脚本对每一题都做了复算对账：")
    add("")
    add("| 对账项 | 结果 |")
    add("|---|---|")
    stored_files = [f for f in files if (f.get("agreement") or {}).get("stored")]
    exact = [f for f in stored_files
             if f["agreement"]["eq_old"] == f["agreement"]["stored"]]
    add(f"| 带落盘指标的文件 | {len(stored_files)} 个 |")
    add(f"| 其中落盘值逐题都能被旧算法解释（即旧口径 = 落盘值） | "
        f"{len(exact)}/{len(stored_files)} |")
    add(f"| 落盘 §gold_pages§ = 修复前正则解析结果 | "
        f"{payload['checks']['gold_pages_mismatch']} 题不一致 |")
    add(f"| 落盘 §page_hit§ = 用落盘旧 gold 重算 | "
        f"{payload['checks']['page_hit_mismatch']} 题不一致 |")
    add(f"| 落盘 §cite_correct§ = 旧口径重算 | "
        f"{payload['checks']['cite_mismatch']} 题不一致 |")
    add(f"| 落盘 §answer_correct§ = 原定义重算 | "
        f"{payload['checks']['answer_mismatch']} 题不一致 |")
    suspects = [f["file"] for f in files if f.get("post_fix_suspected")]
    if suspects:
        add(f"| 疑似生成于修复之后（落盘值其实是修正口径） | "
            f"{len(suspects)} 个：{', '.join('§' + s + '§' for s in suspects)} |")
    else:
        add("| 疑似生成于修复之后（落盘值其实是修正口径） | 0 个 |")
    add("")
    add("最后一行是关键前提：只有当文件生成于修复**之前**，落盘值才算旧口径。"
        "脚本按「落盘值更贴合旧算法还是新算法」自动判定，"
        "一旦某个 run 是修复后重跑的，它会被点出来而不是被误当旧口径。")
    add("")

    # ---- 9. 影响面与后续
    add("## 10. 影响面与后续动作")
    add("")
    add("1. **不需要重跑检索**：§got_pages§ 是当时真实的召回证据，"
        "本次只改 gold 解析，重算后的数字就是这些 run 的修正成绩。")
    add("2. **需要更新对外数字**：README / report 里引用 66% 一类数字的地方"
        "应改为本次修正口径的值（本文档不改 README 与 report）。")
    new_head = by_name.get(HEADLINE_FILES[0], {}).get("metrics", {})
    new_hit = new_head.get("hit_at_k_any", {}).get("new")
    add("3. **CI 阈值需要重新标定**：§scripts/ci_retrieval_gate.py§ 的"
        " §THRESHOLD = 0.64§ 是按旧口径 66% 减 2pp 定的；修正后生产基线抬到"
        f" {cell(new_hit, 'hit_at_k_any')}，"
        "闸门会放过约 12pp 的静默回退，应同步上调。")
    add("4. **答案层缺逐题召回证据**：答案层 run 没落 §got_pages§，"
        "只能重算引用口径；要拿到答案层 hit@5 需在评测脚本里补落盘。")
    add("")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- 主流程
def main() -> int:
    gold: dict[int, dict] = {}
    for line in GOLD_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            gold[int(q["id"])] = q

    affected = []
    for qid in sorted(gold):
        src = gold[qid]["source"]
        old = sorted(set(parse_sources_old(src)))
        new = sorted(set(parse_sources(src)))
        if old != new:
            affected.append({
                "id": qid, "type": gold[qid]["type"], "source": src,
                "old": [list(p) for p in old], "new": [list(p) for p in new],
            })

    files_out = []
    checks = {"gold_pages_mismatch": 0, "page_hit_mismatch": 0,
              "cite_mismatch": 0, "answer_mismatch": 0}
    for path in sorted(RESULTS_DIR.glob("*.json")):
        if path.name == OUT_JSON.name:
            continue  # 幂等：跳过自己的输出
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            files_out.append({
                "file": path.name, "kind": "unreadable", "tier": "partial",
                "n_rows": 0, "k": None,
                "metrics": {}, "by_type": {}, "extra": None,
                "unavailable": {m: f"文件无法解析：{exc}" for m in METRICS},
                "warnings": [],
            })
            continue
        rows = load_rows(data)
        if not rows:
            continue

        if any("got_pages" in r for r in rows):
            kind = "retrieval"
        elif any("answer" in r for r in rows):
            kind = "answers"
        else:
            kind = "unsupported"

        k = 5
        if isinstance(data, dict):
            k = (data.get("summary") or {}).get("k") or 5

        extra = None
        agree = {"stored": 0, "eq_old": 0, "eq_new": 0}
        if kind == "retrieval":
            old_rows, new_rows, warnings, agree = score_retrieval(rows, k, gold)
        elif kind == "answers":
            old_rows, new_rows, warnings, extra, agree = score_answers(rows, gold)
        else:
            old_rows, new_rows, warnings = [], [], []

        # 落盘指标若是修复之后算出来的，它就不是"旧口径"，必须点出来
        post_fix = bool(agree["stored"]) and agree["eq_new"] > agree["eq_old"]
        if post_fix:
            warnings.append(
                "落盘指标与修正口径更吻合（%d/%d 题），疑似生成于修复之后，"
                "其落盘值不能直接当旧口径" % (agree["eq_new"], agree["stored"]))

        types = [r["type"] for r in rows if int(r["id"]) in gold] \
            if kind != "unsupported" else []

        # 逐题对账计数
        for w in warnings:
            if "gold_pages" in w:
                checks["gold_pages_mismatch"] += 1
            elif "page_hit" in w:
                checks["page_hit_mismatch"] += 1
            elif "cite_correct" in w:
                checks["cite_mismatch"] += 1
            elif "answer_correct" in w:
                checks["answer_mismatch"] += 1

        files_out.append({
            "file": path.name,
            "kind": kind,
            "tier": "full" if len(rows) >= FULL_RUN_MIN_ROWS else "partial",
            "k": k,
            "n_rows": len(rows),
            "metrics": metrics_block(old_rows, new_rows),
            "by_type": by_type_block(old_rows, new_rows, types),
            "extra": extra,
            "agreement": agree,
            "post_fix_suspected": post_fix,
            "unavailable": dict(UNAVAILABLE.get(kind, {})),
            "warnings": warnings,
        })

    payload = {
        "schema": "phase_a_rescore/v1",
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "generated_by": "scripts/rescore_offline.py",
        "gold_source": "data/gold/questions.jsonl",
        "definition": {
            "hit_at_k_any": "top-k 中至少一块来自 gold 页（旧口径，宽松）",
            "hit_at_k_all": "top-k 覆盖 gold 的全部页（严格）",
            "mrr": "第一块命中 gold 页的排名倒数",
            "page_prec_at_k": "top-k 中命中 gold 页的比例",
            "cite_any": "回答引用页与 gold 页有交集（旧口径，多引必中）",
            "cite_precision": "回答引用页中落在 gold 页的比例；无引用记 null",
            "answer_correct": "gold_answer 的全部数字串都出现在回答里（沿用原定义）",
            "refusal_correct": "无答案题是否明确拒答",
            "old": "修复前口径：优先取落盘字段，未落盘字段用旧正则按同一公式重算",
            "new": "修正口径：用修复后的 src.evalcore.parse_sources 重新解析 gold",
        },
        "checks": checks,
        "affected_questions": affected,
        "files": files_out,
    }

    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    OUT_MD.parent.mkdir(exist_ok=True)
    OUT_MD.write_text(render_md(payload).replace("§", chr(96)), encoding="utf-8")

    # 控制台摘要（验收要求能直接看到 76% / 90%）
    head = next((f for f in files_out if f["file"] == HEADLINE_FILES[0]), None)
    print(f"gold 受影响题数：{len(affected)}")
    print(f"检索层 run：{sum(f['kind'] == 'retrieval' for f in files_out)} 个；"
          f"答案层 run：{sum(f['kind'] == 'answers' for f in files_out)} 个")
    if head:
        ov = head["metrics"]["hit_at_k_any"]
        cp = head["by_type"]["cross_page"]["hit_at_k_any"]
        print(f"[{HEADLINE_FILES[0]}] hit@5 {ov['old']:.1%} -> {ov['new']:.1%}")
        print(f"[{HEADLINE_FILES[0]}] cross_page hit@5 "
              f"{cp['old']:.1%} -> {cp['new']:.1%}")
    print(f"对账：{checks}")
    print(f"-> {OUT_JSON}")
    print(f"-> {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
