"""答案 → 待核验数字 + 出处页（轴4 的输入层）。

出处标注里的页码/年份不是答案数字；"1. " 这类列举序号也不是指标值。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import claims

ID175 = ("1. 2024年董事、监事和高级管理人员从公司获得的税前报酬总额合计为680.93。"
         " [来源: 2024年报 P26]")


def test_spec_extracts_metric_number_and_cited_page():
    assert claims.citation_spec(ID175) == {
        "numbers": ["680.93"], "pages": [(2024, 26)]}


def test_spec_handles_multiple_pages_and_percent():
    spec = claims.citation_spec("毛利率为94.19%。 [来源: 2024年报 P12、P16]")
    assert spec == {"numbers": ["94.19%"], "pages": [(2024, 12), (2024, 16)]}


def test_spec_without_citation_has_no_pages():
    assert claims.citation_spec("合计为680.93。")["pages"] == []


def test_spec_keeps_unit_scaled_amount():
    spec = claims.citation_spec("营业收入为1,708.99亿元。 [来源: 2024年报 P12]")
    assert spec["numbers"] == ["1,708.99亿元"]