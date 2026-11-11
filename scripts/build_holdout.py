"""留出集（holdout）校验与冻结。

出题方式：Qoder agent 在对话内直接读年报原文页写题（零 API 成本），
本脚本负责客观把关，不信任出题者自觉：
  --check   答案数字须在所标页原文出现 / no_answer 格式 / 与50题调参集
            查重 / 页级隔离 / 题型配比
  --freeze  校验全部通过后写 data/gold/holdout_manifest.json（SHA256+日期）

冻结后不在留出集上调任何参数（α=0.5 等保持不动）。
本地运行: .venv/Scripts/python.exe scripts/build_holdout.py --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import EXTRACTED_DIR, GOLD_FILE  # noqa: E402
from src.evalcore import parse_sources  # noqa: E402

HOLDOUT_FILE = PROJECT / "data" / "gold" / "holdout.jsonl"
MANIFEST_FILE = PROJECT / "data" / "gold" / "holdout_manifest.json"
EXPECTED_MIX = {"fact": 90, "cross_page": 30, "cross_year": 15, "no_answer": 15}
NEAR_DUP_THRESHOLD = 0.8  # 字符二元组 Jaccard；短问句共享 boilerplate 约 0.5，0.8 只抓真改写


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def clean_answer(ans: str) -> str:
    """去掉出处标注，只留答案本体（沿用 verify_gold_answers.py 口径）。"""
    ans = re.sub(r"[（(][^（）()]*年报[^（）()]*[）)]", "", ans)
    ans = re.sub(r"\d{4}年报", "", ans)
    ans = re.sub(r"P\d+", "", ans)
    return ans


def number_tokens(text: str) -> list[str]:
    toks = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{2,}", text)
    return [t for t in toks if not re.fullmatch(r"20\d\d", t)]


def missing_numbers(q: dict, pages: dict[tuple[int, int], str]) -> list[str]:
    """答案数字未在所标页原文出现的清单；跨页/跨年题允许数字落在任一标页。"""
    hay = norm("".join(pages.get(p, "") for p in parse_sources(q["source"])))
    body = norm(clean_answer(q["gold_answer"]))
    return [t for t in number_tokens(body) if t not in hay]


def no_answer_format_ok(q: dict) -> bool:
    return q["gold_answer"] == "年报中无此信息"


_PUNCT = ('，。？！、；：（）()[]【】,.?!;:-—…·'
          + '"' + "'" + '“”‘’')
_PUNCT_RE = re.compile(r'[\s' + re.escape(_PUNCT) + r']')


def normalize_question(text: str) -> str:
    return _PUNCT_RE.sub("", text).lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


def find_duplicates(holdout: list[dict], tuning: list[dict]) -> list[tuple]:
    """与调参集查重：归一化精确匹配，或字符二元组 Jaccard >= 阈值。"""
    tun = [(q["id"], normalize_question(q["question"])) for q in tuning]
    tun_bi = {qid: _bigrams(n) for qid, n in tun}
    dups = []
    for q in holdout:
        n = normalize_question(q["question"])
        bi = _bigrams(n)
        for qid, tn in tun:
            if n == tn:
                dups.append((q["id"], qid))
                break
            tb = tun_bi[qid]
            if bi and tb and len(bi & tb) / len(bi | tb) >= NEAR_DUP_THRESHOLD:
                dups.append((q["id"], qid))
                break
    return dups


def isolation_violations(holdout: list[dict],
                         tuning_pages: set[tuple[int, int]]) -> list[tuple]:
    out = []
    for q in holdout:
        for year, page in parse_sources(q["source"]):
            if (year, page) in tuning_pages:
                out.append((q["id"], year, page))
    return out


def type_counts(holdout: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for q in holdout:
        counts[q["type"]] = counts.get(q["type"], 0) + 1
    return dict(sorted(counts.items()))


def load_questions(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def freeze(holdout_path: Path, manifest_path: Path, generator: str) -> dict:
    rows = load_questions(holdout_path)
    manifest = {
        "file": holdout_path.name,
        "sha256": hashlib.sha256(holdout_path.read_bytes()).hexdigest(),
        "n": len(rows),
        "types": type_counts(rows),
        "generator": generator,
        "frozen_at": date.today().isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--freeze", action="store_true")
    args = ap.parse_args()

    holdout = load_questions(HOLDOUT_FILE)
    tuning = load_questions(GOLD_FILE)
    tuning_pages = {p for q in tuning for p in parse_sources(q["source"])}
    pages: dict[tuple[int, int], str] = {}

    def page_text(year: int, page: int) -> str:
        if (year, page) not in pages:
            f = EXTRACTED_DIR / str(year) / f"page_{page:03d}.txt"
            pages[(year, page)] = (f.read_text(encoding="utf-8")
                                   if f.exists() else "")
        return pages[(year, page)]

    problems: list[str] = []
    ids = [q["id"] for q in holdout]
    if len(ids) != len(set(ids)):
        problems.append("id 重复")
    for q in holdout:
        if q["type"] not in EXPECTED_MIX:
            problems.append(f"id{q['id']} 未知题型 {q['type']}")
        if not q["question"].strip():
            problems.append(f"id{q['id']} 问题为空")
        if q["type"] == "no_answer":
            if not no_answer_format_ok(q):
                problems.append(f"id{q['id']} no_answer 金标格式不对")
            continue
        for year, page in parse_sources(q["source"]):
            page_text(year, page)  # 预热缓存，缺文件按空文本处理
        missing = missing_numbers(q, pages)
        if missing:
            problems.append(f"id{q['id']} 数字不在所标页: {missing} ({q['source']})")
    for hid, tid in find_duplicates(holdout, tuning):
        problems.append(f"id{hid} 与调参集 id{tid} 重复/近似")
    for hid, year, page in isolation_violations(holdout, tuning_pages):
        problems.append(f"id{hid} 用了调参页 {year}P{page}")
    counts = type_counts(holdout)
    if counts != EXPECTED_MIX:
        problems.append(f"题型配比 {counts} != 期望 {EXPECTED_MIX}")

    print(f"holdout n={len(holdout)} types={counts} problems={len(problems)}")
    for p in problems:
        print("  PROBLEM:", p)
    if problems:
        return 1
    if args.freeze:
        m = freeze(HOLDOUT_FILE, MANIFEST_FILE,
                   generator="Qoder agent 对话内直接出题，程序校验（零 API 成本）")
        print(f"已冻结 -> {MANIFEST_FILE} sha256={m['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())