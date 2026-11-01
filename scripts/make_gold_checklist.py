# -*- coding: utf-8 -*-
"""生成黄金问答集《人工核对清单》（docs/黄金集核对清单.md）。

背景: 黄金集由 AI 起草，verify_gold_answers.py 已做数字预检（答案中的
数字串必须出现在引用页原文里，当前 50/50 通过）。但机器只能验证"数字
在页面上"，验证不了"这个数字就是问题问的那个"——同页常有几十个数字。
本脚本把每题的引用页原文里包含标准答案数字的上下文截出来，人工只需
扫一眼"数字对不对位"，把 1-2 小时的逐题翻 PDF 压缩到约 20 分钟。

用法: .venv/Scripts/python.exe scripts/make_gold_checklist.py
输出: docs/黄金集核对清单.md（核对时直接在 ☐ 里打勾/改错）
"""
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from scripts.verify_gold_answers import (  # noqa: E402
    clean_answer, norm, number_tokens, page_text, parse_sources)

GOLD = PROJECT / "data" / "gold" / "questions.jsonl"
OUT = PROJECT / "docs" / "黄金集核对清单.md"

TYPE_LABEL = {"fact": "事实题", "cross_page": "跨页题",
              "cross_year": "跨年题", "no_answer": "无答案题"}


def excerpt(raw: str, token: str, width: int = 45) -> str:
    """原文中 token 首次出现位置的上下文（压缩空白）。"""
    flat = re.sub(r"\s+", " ", raw)
    i = flat.find(token)
    if i < 0:
        return ""
    lo, hi = max(0, i - width), min(len(flat), i + len(token) + width)
    return ("…" if lo else "") + flat[lo:hi] + ("…" if hi < len(flat) else "")


def row_for(q: dict) -> str:
    """一题的核对清单行：问题 / 标准答案 / 出处 / 页面原文摘录。"""
    src = q["source"]
    pages = parse_sources(src)
    raw = "\n".join(t for t in (page_text(y, p) for y, p in pages) if t)
    toks = number_tokens(norm(clean_answer(q["gold_answer"])))
    # 每个数字各截一段上下文，最多 3 段
    seen, clips = set(), []
    for t in toks:
        c = excerpt(raw, t)
        if c and c not in seen:
            seen.add(c)
            clips.append(c.replace("|", "／"))
        if len(clips) == 3:
            break
    if not clips:  # 无数字答案：给页面开头供人工浏览
        head = re.sub(r"\s+", " ", raw)[:90]
        clips = [head + "…" if head else "（页面文本缺失）"]
    clip = "<br>".join(clips)
    return (f"| {q['id']} | {q['question']} | {q['gold_answer']} "
            f"| {src} | {clip} | ☐ |")


def main() -> None:
    questions = [json.loads(l) for l in GOLD.open(encoding="utf-8")
                 if l.strip()]
    lines = [
        "# 黄金问答集 · 人工核对清单",
        "",
        f"> 生成时间 2026-11-02 · 共 {len(questions)} 题 · "
        "脚本数字预检 50/50 通过（答案中的数字均能在引用页原文找到，"
        "见 scripts/verify_gold_answers.py）",
        "",
        "**怎么核对**：机器确认不了\"页面上那个数字就是问题问的那个\""
        "（同页常有几十个数字）。请逐题看「页面原文摘录」一列，确认"
        "①出处页码对 ②数字对位。没问题就在行尾 ☐ 打 ✅；有错直接改"
        " `data/gold/questions.jsonl` 对应行的 gold_answer / source，"
        "改完重跑 `python scripts/verify_gold_answers.py` 复检。",
        "",
    ]
    cur_type = None
    for q in questions:
        if q["type"] != cur_type:
            cur_type = q["type"]
            lines += [f"## {TYPE_LABEL.get(cur_type, cur_type)}", "",
                      "| # | 问题 | 标准答案 | 出处 | 页面原文摘录 | 核对 |",
                      "|---|---|---|---|---|---|"]
        lines.append(row_for(q))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"核对清单 -> {OUT}  共 {len(questions)} 题")


if __name__ == "__main__":
    main()
