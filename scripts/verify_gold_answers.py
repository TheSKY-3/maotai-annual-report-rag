# -*- coding: utf-8 -*-
"""Verify every question in data/gold/questions.jsonl:
- parse source (e.g. '2024年报 P9、P16' / '2025年报 P10；2023年报 P17')
- read the referenced page files
- check that every number token in gold_answer (after stripping citations)
  appears in the normalized text of at least one referenced page
- no_answer questions: gold must be exactly '年报中无此信息'
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(r"D:\eng_project\rag项目")
QFILE = ROOT / "data" / "gold" / "questions.jsonl"
EXTRACTED = ROOT / "data" / "extracted"


def norm(s: str) -> str:
    # remove all whitespace so numbers split by table cells/spaces still match
    return re.sub(r"\s+", "", s)


def parse_sources(src: str):
    """Return list of (year, page). Handles '2024年报 P9、P16' and '2025年报 P10；2023年报 P17'."""
    out = []
    # split on Chinese semicolon between year groups
    for part in re.split(r"[；;]", src):
        m = re.search(r"(20\d\d)年报(.*)", part)
        if not m:
            continue
        year = m.group(1)
        for p in re.findall(r"P(\d+)", m.group(2)):
            out.append((year, int(p)))
    return out


def page_text(year: str, page: int) -> str:
    f = EXTRACTED / year / f"page_{page:03d}.txt"
    if not f.exists():
        return None
    return norm(f.read_text(encoding="utf-8"))


def clean_answer(ans: str) -> str:
    # remove citation fragments like （2025年报 P6）, （2022年报P7：\"...\"）
    ans = re.sub(r"[（(][^（）()]*年报[^（）()]*[）)]", "", ans)
    ans = re.sub(r"\d{4}年报", "", ans)
    ans = re.sub(r"P\d+", "", ans)
    return ans


def number_tokens(text: str):
    # numbers possibly with thousand separators / decimals; ignore bare years like 2022
    toks = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{2,}", text)
    out = []
    for t in toks:
        # skip likely years/ids 4-digit without separators that appear as years
        if re.fullmatch(r"20\d\d", t):
            continue
        out.append(t)
    return out


def main():
    total = ok = 0
    problems = []
    with QFILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            q = json.loads(line)
            qid, qtype = q["id"], q["type"]
            if qtype == "no_answer":
                if q["gold_answer"] != "年报中无此信息":
                    problems.append((qid, "no_answer gold mismatch"))
                continue
            srcs = parse_sources(q["source"])
            if not srcs:
                problems.append((qid, f"cannot parse source: {q['source']}"))
                continue
            texts = []
            for year, page in srcs:
                t = page_text(year, page)
                if t is None:
                    problems.append((qid, f"missing file {year} page {page}"))
                else:
                    texts.append(t)
            hay = "".join(texts)
            body = norm(clean_answer(q["gold_answer"]))
            missing = [t for t in number_tokens(body) if t not in hay]
            if missing:
                problems.append((qid, f"numbers not found in {q['source']}: {missing}"))
            else:
                ok += 1
    print(f"total={total} numeric-verified={ok} problems={len(problems)}")
    for p in problems:
        print("  PROBLEM:", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
