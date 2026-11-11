"""留出集校验逻辑的回归测试（纯本地，无网络、无 API 调用）。

被测对象：scripts/build_holdout.py 的纯函数——数字须在所标页原文出现、
no_answer 格式、与调参集查重、页级隔离、冻结清单哈希。
"""
import hashlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
for p in (PROJECT, PROJECT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_holdout as bh  # noqa: E402


# ------------------------------------------------------------- 数字校验
def test_numbers_present_passes():
    q = {"gold_answer": "营业收入1,275.54亿元，同比增长16.53%",
         "source": "2022年报 P7"}
    pages = {(2022, 7): "实现营业总收入 1,275.54 亿元，同比增长 16.53%"}
    assert bh.missing_numbers(q, pages) == []


def test_number_absent_fails():
    q = {"gold_answer": "营业收入9,999.99亿元", "source": "2022年报 P7"}
    pages = {(2022, 7): "实现营业总收入 1,275.54 亿元"}
    assert bh.missing_numbers(q, pages) == ["9,999.99"]


def test_whitespace_split_numbers_still_match():
    """表格把数字拆到不同行/列，去空白后必须能对上（沿用旧校验口径）。"""
    q = {"gold_answer": "设计产能42,742.50吨", "source": "2022年报 P14"}
    pages = {(2022, 14): "茅台酒制酒车间 42,742.\n50 56,810.44"}
    assert bh.missing_numbers(q, pages) == []


def test_citation_fragments_do_not_count_as_numbers():
    """答案里的出处页码（P888）不是答案数字，不能拿去校验。"""
    q = {"gold_answer": "净利润627.16亿元（2022年报P7：\"净利润 627.16 亿元\"）",
         "source": "2022年报 P7"}
    pages = {(2022, 7): "净利润 627.16 亿元"}
    assert bh.missing_numbers(q, pages) == []


def test_bare_years_are_not_answer_numbers():
    q = {"gold_answer": "2022年经销商2143家", "source": "2022年报 P20"}
    pages = {(2022, 20): "国内经销商 2143 家"}
    assert bh.missing_numbers(q, pages) == []


def test_cross_page_numbers_may_live_on_either_page():
    q = {"gold_answer": "营收1500亿、净利700亿", "source": "2024年报 P9、P16"}
    pages = {(2024, 9): "营收 1500 亿", (2024, 16): "净利 700 亿"}
    assert bh.missing_numbers(q, pages) == []


def test_missing_page_file_is_reported():
    q = {"gold_answer": "净利润627.16亿元", "source": "2022年报 P7"}
    assert bh.missing_numbers(q, {}) == ["627.16"]


# ------------------------------------------------------------- no_answer
def test_no_answer_format():
    ok = {"type": "no_answer", "gold_answer": "年报中无此信息"}
    bad = {"type": "no_answer", "gold_answer": "五粮液营收1000亿"}
    assert bh.no_answer_format_ok(ok) is True
    assert bh.no_answer_format_ok(bad) is False


# ------------------------------------------------------------- 查重
def _q(qid, text, qtype="fact", source="2022年报 P7"):
    return {"id": qid, "type": qtype, "question": text,
            "gold_answer": "x", "source": source}


def test_exact_duplicate_after_normalization():
    tuning = [_q(1, "茅台2022年营业总收入是多少？")]
    holdout = [_q(101, "茅台 2022 年营业总收入是多少?")]  # 空白/全角差异
    dups = bh.find_duplicates(holdout, tuning)
    assert dups == [(101, 1)]


def test_near_duplicate_caught():
    tuning = [_q(1, "茅台2022年实现营业总收入多少亿元")]
    holdout = [_q(101, "茅台2022年实现营业总收入多少亿元呢")]
    assert bh.find_duplicates(holdout, tuning) == [(101, 1)]


def test_different_metric_not_flagged():
    tuning = [_q(1, "茅台2022年营业收入是多少")]
    holdout = [_q(101, "茅台2022年净利润是多少")]
    assert bh.find_duplicates(holdout, tuning) == []


# ------------------------------------------------------------- 页级隔离
def test_page_isolation():
    tuning_pages = {(2022, 7), (2024, 16)}
    holdout = [_q(101, "a", source="2022年报 P7"),      # 用了调参页
               _q(102, "b", source="2023年报 P12")]     # 干净
    assert bh.isolation_violations(holdout, tuning_pages) == [(101, 2022, 7)]


# ------------------------------------------------------------- 冻结清单
def test_freeze_manifest_hash_and_counts(tmp_path):
    holdout = tmp_path / "holdout.jsonl"
    rows = [_q(101, "a"), _q(102, "b", qtype="no_answer")]
    holdout.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                               for r in rows), encoding="utf-8")
    manifest = tmp_path / "holdout_manifest.json"
    bh.freeze(holdout, manifest, generator="test")
    m = json.loads(manifest.read_text(encoding="utf-8"))
    expect = hashlib.sha256(holdout.read_bytes()).hexdigest()
    assert m["sha256"] == expect
    assert m["n"] == 2
    assert m["types"] == {"fact": 1, "no_answer": 1}
    assert m["generator"] == "test"
    assert m["frozen_at"]  # 非空日期