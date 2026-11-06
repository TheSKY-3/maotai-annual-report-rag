import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import numeric


def test_rounding_yi_to_yuan():
    # 1741.44 亿元 ≈ 174,144,069,958.25 元（四舍五入）
    assert numeric.find_number("1741.44亿元", ["174,144,069,958.25元"])


def test_substring_no_false_positive():
    assert not numeric.find_number("12", ["1234"])
    assert not numeric.find_number("12.5%", ["312.5%"])


def test_unit_conversion_wan():
    assert numeric.find_number("1275.54万元", ["12,755,400.00元"])


def test_plain_vs_money():
    assert numeric.find_number("174144069958.25", ["1741.44亿元"])


def test_percent_strict():
    # 51.9% 与裸数字 51.9 不互相满足（单位不同，宁可报错让人看原文）
    assert not numeric.find_number("51.9%", ["51.90"])


def test_negative_number():
    assert numeric.find_number("-3.2%", ["同比 -3.2%"])
    assert not numeric.find_number("-3.2%", ["3.2%"])


def test_numbers_in_answer_dedup():
    got = numeric.numbers_in_answer("营收 1,234 亿，其中 1,234 亿来自酒类")
    assert sum(1 for x in got if x.startswith("1,234")) == 1


def test_ungrounded_skip():
    bad = numeric.ungrounded("2024年营收 1,234 亿", ["营收 1,234 亿元"],
                             skip={"2024"})
    assert bad == []


def test_ungrounded_other_page_number_not_found():
    # 数字在“别的片段”里有，但不在给定证据里 → 必须报
    assert numeric.ungrounded("毛利率 91.5%", ["营收 1,234 亿"]) == ["91.5%"]
