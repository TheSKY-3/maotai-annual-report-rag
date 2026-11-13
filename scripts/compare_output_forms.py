"""输出形态对照：自然语言回答 vs 结构化对比表（只用调参集，不碰冻结留出集）。

形态A = 现有 answer_auto 路由（跨年题进 agent 工具循环），数字由模型写进句子。
形态B = 模型只抽「指标名 + 年份」，数字由代码从 data/metrics.json 填，出处页随值走。

判分口径（本次对照专用，与历史留出集 91.9% / 坐标 90.24% 均不可比）：
  数字命中   gold 关键数字（剥出处括注与页码、跳过年份）在输出里按 numeric 单位归一比对，
             不用字符串包含——gold 写"1,275.54亿元"、指标表写"127,553,959,355.97元"是同一个数。
  对gold页   输出标注的 (年,页) 与 gold source 有交集。指标表用的是报表页、gold 常写正文页，
             故此口径会低估形态B，必须与下面的自洽定位分开看。
  自洽定位   输出声明的那一页上真能找到该数字（cite_locate.locate_number，不渲染图）。
             歧义 = 同页多处出现，未做行首标签消歧，比历史口径严。

用法（项目根目录；Key 由 shell 从 .env 代入，值不打印）:
  .venv/Scripts/python.exe -X utf8 scripts/compare_output_forms.py
输出: results/form_compare.json（不写 citation_crops.json，那是留出集权威清单）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src import cite_locate, claims, metric_tools, numeric  # noqa: E402
from src.config import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,  # noqa: E402
                        GOLD_FILE, RESULTS_DIR, YEARS)
from src.evalcore import parse_sources  # noqa: E402

OUT_NAME = "form_compare.json"
DEFAULT_TYPES = ("cross_year", "cross_page")

_SRC_BRACKET_RE = re.compile(r"\[来源:[^\]]*\]")
_CITE_PAREN_RE = re.compile(r"（[^）]*?20\d\d\s*年报[^）]*?）")
_YEAR_REPORT_RE = re.compile(r"20\d\d\s*年报\s*P?\d*")
_PAGE_RE = re.compile(r"P\s*\d{1,3}")
_YEAR_VALUES = [Decimal(y) for y in YEARS]


def gold_targets(gold_answer: str) -> list[str]:
    """gold 里真正要答的数字：剥出处标注与页码，跳过年份，去重保序。"""
    body = _SRC_BRACKET_RE.sub("", gold_answer or "")
    body = _CITE_PAREN_RE.sub("", body)
    body = _YEAR_REPORT_RE.sub("", body)
    body = _PAGE_RE.sub("", body)
    out: list[str] = []
    for n in numeric.iter_numbers(body):
        if n.raw in out:
            continue
        if n.kind == "plain" and n.value in _YEAR_VALUES:
            continue
        out.append(n.raw)
    return out


def match_targets(targets: list[str], output: str) -> tuple[int, list[str]]:
    """按 token 同值比对（单位归一 + numeric 容差），返回命中数与未命中清单。"""
    tokens = numeric.iter_numbers(output or "")
    hit, missed = 0, []
    for raw in targets:
        target = numeric.parse_token(raw)
        ok = (any(numeric.same_value(target, n) for n in tokens)
              if target is not None else raw in (output or ""))
        if ok:
            hit += 1
        else:
            missed.append(raw)
    return hit, missed


def cite_vs_gold(output: str, gold_source: str) -> bool:
    return bool(set(parse_sources(output)) & set(parse_sources(gold_source)))


def resolve_exact(name: str) -> tuple[str, dict] | None:
    """表格形态只接受精确指标名（key/label/alias 归一后相等）。

    生产的 metric_tools.resolve_metric 走"最长包含"，实测会把"子公司净利润"
    错配到"归属于上市公司股东的净利润"——主体不同、数字看着权威。表格的价值
    主张是口径确定，故这里不做包含匹配，配不上就记未覆盖。
    """
    q = metric_tools._norm(name)
    if not q:
        return None
    for key, m in metric_tools.catalog().items():
        for cand in (key, m.get("label", ""), *(m.get("aliases") or [])):
            if metric_tools._norm(cand) == q:
                return key, m
    return None


def build_cells(mapping: dict) -> tuple[list[dict], list[str]]:
    """模型抽的指标名+年份 → 指标表里的值与页；清单外的指标不硬凑。"""
    cells: list[dict] = []
    missing: list[str] = []
    for item in (mapping or {}).get("items") or []:
        name = str(item.get("metric") or "").strip()
        found = resolve_exact(name)
        if not found:
            if name:
                missing.append(name)
            continue
        key, m = found
        label = m.get("label") or key
        for y in item.get("years") or []:
            try:
                year = int(y)
            except (TypeError, ValueError):
                continue
            entry = (m.get("values") or {}).get(str(year))
            if not isinstance(entry, dict) or "value" not in entry:
                missing.append(f"{label}@{year}")
                continue
            cells.append({"metric": label, "year": year,
                          "value": str(entry["value"]),
                          "unit": m.get("unit") or "",
                          "page": int(entry["page"])})
    return cells, missing


def render_table(cells: list[dict], uncovered: str = "") -> str:
    lines = ["| 指标 | 年份 | 数值 | 出处 |", "|---|---|---|---|"]
    for c in cells:
        number = f"{c['value']} {c['unit']}".strip()
        lines.append(f"| {c['metric']} | {c['year']} | {number} "
                     f"| {c['year']}年报 P{c['page']} |")
    if not cells:
        lines.append("| （指标表未覆盖该问题） | - | - | - |")
    if uncovered:
        lines.append(f"\n未覆盖：{uncovered}")
    return "\n".join(lines)


def locate_cells(cells: list[dict]) -> list[dict]:
    """形态B：每格只查它自己声明的那一页（结构化输出知道数字来自哪页）。"""
    out = []
    for c in cells:
        raw = f"{c['value']} {c['unit']}".strip()
        # 吨/个/人/股 不在 numeric 的单位表里，拼进去会让 parse_token 返回 None、
        # locate_number 退回子串匹配带单位整串，页面上没有该串就假报未定位。
        if numeric.parse_token(raw) is None:
            raw = c["value"]
        matches = cite_locate.locate_number(c["year"], c["page"], raw)
        out.append({**c, "number": raw, "located": bool(matches),
                    "ambiguous": len(matches) > 1})
    return out


def is_calculated(answer: str, raw: str) -> bool:
    """该数字在答案里是否已被标为「计算得出」。

    认两种写法：「（计算得出）」和「（约425.96亿元，计算得出）」。生产
    claims._CALC_NUM_RE 只认前一种，判分口径必须认全——否则把模型已诚实
    标注的计算值当成"定位失败"扣分，量出来的不是产品缺陷而是尺子缺陷。
    """
    for m in re.finditer(re.escape(raw), answer):
        tail = answer[m.end():m.end() + 40]
        # 截到下一个数字或出处标注前，避免把邻近别的数的标记算过来
        tail = re.split(r"\[来源|\d", tail)[0]
        if "计算得出" in tail:
            return True
    return False


def locate_answer(answer: str) -> list[dict]:
    """形态A：散文答案不知道哪个数字来自哪页，按出处页逐个试（现有核验同思路）。"""
    spec = claims.citation_spec(answer)
    out = []
    for number in spec["numbers"]:
        entry = {"number": number, "located": False, "ambiguous": False,
                 "year": None, "page": None,
                 "calculated": is_calculated(answer, number)}
        for year, page in spec["pages"]:
            matches = cite_locate.locate_number(year, page, number)
            if not matches:
                continue
            entry.update(located=True, year=year, page=page,
                         ambiguous=len(matches) > 1)
            break
        out.append(entry)
    return out


_EXTRACT_PROMPT = """你是指标映射器。可用的标准指标清单（只能从中选，名称逐字一致）：
{labels}

用户问题：{question}

只输出 JSON，格式：
{{"items":[{{"metric":"<清单中的名称>","years":[<年份整数>]}}],"uncovered":"<清单里没有对应指标时写原因，否则空字符串>"}}

规则：
1. 问题问几个指标就输出几个 item；同一指标的多个年份写进同一个 item 的 years。
2. 年份只能是 2022/2023/2024/2025；"年末"仍按该年处理。
3. 清单里没有的指标（子公司净利润、i茅台平台收入、产量、库存量、毛利率、股东户数、
   审计费、分红、区域收入、酒类整体销量等）不要硬凑到相近指标上，写进 uncovered。
4. 不要输出 JSON 以外的任何文字。"""


def extract_mapping(client, model: str, question: str, labels: list[str]) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(
            labels="\n".join(f"- {x}" for x in labels), question=question)}],
        temperature=0, max_tokens=900,
        extra_body={"thinking": {"type": "disabled"}})
    text = (resp.choices[0].message.content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {"items": [], "uncovered": f"抽指标输出无法解析：{text[:120]}"}
    return obj if isinstance(obj, dict) else {"items": [], "uncovered": "输出不是对象"}


def _score(q: dict, output: str, located: list[dict]) -> dict:
    targets = gold_targets(q["gold_answer"])
    hit, missed = match_targets(targets, output)
    n_loc = sum(1 for e in located if e["located"])
    return {
        "id": q["id"], "type": q["type"], "question": q["question"],
        "output": output,
        "num_targets": len(targets), "num_hit": hit, "num_missed": missed,
        "all_num_correct": bool(targets) and hit == len(targets),
        "cite_gold_correct": cite_vs_gold(output, q["source"]),
        "locate_numbers": len(located), "locate_located": n_loc,
        "locate_ambiguous": sum(1 for e in located if e["ambiguous"]),
        "locate_calc": sum(1 for e in located if e.get("calculated")),
        "locate_detail": located,
    }


def run_form_a(q: dict, client, search_fn, model: str) -> dict:
    from src.ask import answer_auto

    meta = answer_auto(client, search_fn, q["question"], model)
    row = _score(q, meta["answer"], locate_answer(meta["answer"]))
    row.update(form="A_自然语言", route=meta.get("route"),
               steps=len(meta.get("steps") or []))
    return row


def run_form_b(q: dict, client, model: str, labels: list[str]) -> dict:
    mapping = extract_mapping(client, model, q["question"], labels)
    cells, missing = build_cells(mapping)
    uncovered = "；".join(x for x in [str(mapping.get("uncovered") or "").strip(),
                                     "、".join(missing)] if x)
    text = render_table(cells, uncovered)
    row = _score(q, text, locate_cells(cells))
    row.update(form="B_对比表", covered=bool(cells), cells=len(cells),
               mapping=mapping, metric_missing=missing)
    return row


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    targets = sum(r["num_targets"] for r in rows)
    hits = sum(r["num_hit"] for r in rows)
    numbers = sum(r["locate_numbers"] for r in rows)
    located = sum(r["locate_located"] for r in rows)
    calc = sum(r.get("locate_calc", 0) for r in rows)
    quoted = numbers - calc
    out = {
        "n": len(rows),
        "数字命中率": round(hits / targets, 4) if targets else None,
        "数字命中": f"{hits}/{targets}",
        "全对题数": sum(r["all_num_correct"] for r in rows),
        "对gold页命中题数": sum(r["cite_gold_correct"] for r in rows),
        "自洽定位率": round(located / numbers, 4) if numbers else None,
        "自洽定位": f"{located}/{numbers}",
        # 已标「计算得出」的数纸上本来就没有，不该按定位失败扣分
        "原文引用定位率": round(located / quoted, 4) if quoted else None,
        "原文引用定位": f"{located}/{quoted}",
        "已标计算值数": calc,
        "歧义数": sum(r["locate_ambiguous"] for r in rows),
    }
    if "covered" in rows[0]:
        out["指标表覆盖题数"] = sum(r["covered"] for r in rows)
    return out


def rescore_file(path) -> dict:
    """从已存逐题明细重算计算值口径：不重新提问、零 API 成本。"""
    path = Path(path)
    d = json.loads(path.read_text(encoding="utf-8"))
    for key in ("A", "B"):
        for r in d.get(key, []):
            if "error" in r or "locate_detail" not in r:
                continue
            for e in r["locate_detail"]:
                e["calculated"] = is_calculated(r.get("output", ""), e["number"])
            r["locate_calc"] = sum(1 for e in r["locate_detail"]
                                   if e["calculated"])
    s = d.get("summary", {})
    for key, label in (("A", "A_自然语言"), ("B", "B_对比表")):
        rows = [r for r in d.get(key, []) if "error" not in r]
        if rows or label in s:
            s[label] = summarize(rows)
    note = ("双口径并列：自洽定位含已标计算值；原文引用定位扣除已标"
            "「计算得出」的数（纸上本无此数）。")
    if note not in s.get("口径", ""):
        s["口径"] = (s.get("口径", "") + " " + note).strip()
    d["summary"] = s
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--types", default=",".join(DEFAULT_TYPES))
    ap.add_argument("--ids", default="", help="逗号分隔题号，优先于 --types")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--forms", default="a,b")
    ap.add_argument("--out", default=str(RESULTS_DIR / OUT_NAME))
    ap.add_argument("--rescore", default="",
                    help="只重算已存结果的计算值口径，不调用模型")
    args = ap.parse_args()

    if args.rescore:
        d = rescore_file(Path(args.rescore))
        print(f"重算完成 -> {args.rescore}")
        print(json.dumps(d["summary"], ensure_ascii=False, indent=1))
        return

    out_path = Path(args.out)
    if out_path.name == "citation_crops.json":
        raise SystemExit("拒绝写入 citation_crops.json（留出集权威清单），换 --out")

    from openai import OpenAI

    from src.pipeline import get_search

    base_url = os.environ.get("RAG_BASE_URL", DEEPSEEK_BASE_URL)
    model = os.environ.get("RAG_MODEL", DEEPSEEK_MODEL)
    key = os.environ.get("RAG_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("缺 Key：请在 shell 里从 .env 代入 DEEPSEEK_API_KEY（不要打印值）")
    client = OpenAI(base_url=base_url, api_key=key)

    questions = [json.loads(l) for l in
                 GOLD_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",")}
        questions = [q for q in questions if q["id"] in wanted]
    else:
        types = {t.strip() for t in args.types.split(",") if t.strip()}
        questions = [q for q in questions if q["type"] in types]
    if args.limit:
        questions = questions[: args.limit]

    labels = [m.get("label") or k for k, m in metric_tools.catalog().items()]
    search_fn = get_search() if "a" in args.forms else None
    rows_a, rows_b = [], []
    for q in questions:
        if "a" in args.forms:
            try:
                row = run_form_a(q, client, search_fn, model)
            except Exception as e:  # noqa: BLE001
                row = {"id": q["id"], "form": "A_自然语言", "error": str(e)}
            rows_a.append(row)
            print(f"[A id{q['id']:02d}] "
                  + (row.get("error") or
                     f"数字 {row['num_hit']}/{row['num_targets']} "
                     f"定位 {row['locate_located']}/{row['locate_numbers']} "
                     f"route={row.get('route')}"), flush=True)
        if "b" in args.forms:
            try:
                row = run_form_b(q, client, model, labels)
            except Exception as e:  # noqa: BLE001
                row = {"id": q["id"], "form": "B_对比表", "error": str(e)}
            rows_b.append(row)
            print(f"[B id{q['id']:02d}] "
                  + (row.get("error") or
                     f"覆盖={row['covered']} 数字 {row['num_hit']}/{row['num_targets']} "
                     f"定位 {row['locate_located']}/{row['locate_numbers']}"), flush=True)

    summary = {"model": model, "base_url": base_url,
               "题集": "调参集 data/gold/questions.jsonl（非留出集）",
               "口径": "本次对照专用：数字单位归一比对；对gold页与自洽定位分开；"
                       "歧义=同页多处未消歧。与历史 91.9%/90.24% 不可比。",
               "A_自然语言": summarize([r for r in rows_a if "error" not in r]),
               "B_对比表": summarize([r for r in rows_b if "error" not in r]),
               "A_错误数": sum("error" in r for r in rows_a),
               "B_错误数": sum("error" in r for r in rows_b)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "A": rows_a, "B": rows_b},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n对照结果 -> {out_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()