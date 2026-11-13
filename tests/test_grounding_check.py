"""接地校验（src/ask.py ungrounded_numbers）的判分测试。

病根：原实现用子串包含判"原文里有没有这个数"，于是 10.51 会被 110.51
满足——模型自己算的百分比被当成原文引用放过，不触发修正重试，也不标
「计算得出」。项目里早有正确实现 src/numeric.py（按 token 比 + 单位归一），
这里锁定复用它之后的行为。
"""
from __future__ import annotations

from src.ask import ungrounded_numbers


# ---- 子串假接地：必须抓到 ----

def test_子串假接地_百分数被更长数字满足():
    bad = ungrounded_numbers("同比减少10.51%", "本期毛利率110.51%，上年210.51%")
    assert bad, "10.51 只是 110.51 的子串，不该判为已接地"


def test_子串假接地_占比被金额尾数满足():
    bad = ungrounded_numbers("占比34.92%", "营业收入 1,234.92 万元")
    assert bad


def test_子串假接地_整数被更长整数满足():
    bad = ungrounded_numbers("经销商 2084 家", "编号 12084 的科目")
    assert bad


# ---- 真接地：不许误报 ----

def test_原文数字完全一致时不报():
    assert ungrounded_numbers(
        "营业总收入 174,144,069,958.25 元",
        "一、营业总收入  174,144,069,958.25 150,560,330,316.45") == []


def test_亿元与元换算算接地():
    assert ungrounded_numbers(
        "营业总收入约 1,741.44 亿元",
        "一、营业总收入  174,144,069,958.25") == []


def test_万元与元换算算接地():
    assert ungrounded_numbers(
        "直销收入 8,454,303.19 万元",
        "直销渠道 84,543,031,854.63 元") == []


def test_表格单位声明下的裸数字算接地():
    assert ungrounded_numbers(
        "报酬合计 680.93 万元",
        "单位：万元\n税前报酬总额 680.93") == []


def test_百分数与表格裸数视为同值():
    assert ungrounded_numbers("毛利率 94.19%", "毛利率 94.19") == []


# ---- 出处标注 / 列举序号 / 年份不算答案数字 ----

def test_出处标注里的页码年份不算():
    assert ungrounded_numbers(
        "报酬合计 680.93。 [来源: 2024年报 P26]",
        "税前报酬总额 680.93") == []


def test_列举序号不算数字():
    assert ungrounded_numbers(
        "1. 收入 680.93 元\n2. 毛利率 94.19%",
        "收入 680.93 元，毛利率 94.19%") == []


def test_年份本身跳过():
    assert ungrounded_numbers(
        "2024年收入 680.93 元", "收入 680.93 元") == []


# ---- 返回写法要带单位，模型才知道核对哪个量 ----

def test_返回值保留单位():
    bad = ungrounded_numbers("同比减少10.51%", "毛利率110.51%")
    assert "10.51%" in bad


def test_空答案不报():
    assert ungrounded_numbers("", "任意原文") == []


def test_去重保序():
    bad = ungrounded_numbers("增长10.51%，又说10.51%，另有34.92%",
                             "毛利率110.51%")
    assert bad == ["10.51%", "34.92%"]