"""切块页脚过滤测试（轴2重半病根修复）。

病根：chunk_fixed 为去页脚把含"/"的行整行删掉，而年报表格空单元格用"/"
表示，合计行"合计 / / / … 680.93 /"被误删，数据从索引丢失。修复后只删
"本页页码 / 总页数"那一行页脚，含"/"的表格行必须保留。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunking import chunk_fixed

PAGE = (
    "贵州茅台酒股份有限公司 2024 年年度报告\n"
    "26 / 143\n"
    "姓名 职务 报告期内从公司获得的税前报酬总额（万元）\n"
    "张三 董事 100.00\n"
    "李四 监事 91.00\n"
    "合计 / / / / /    / 680.93 /\n"
    "说明：上述董事、监事、高级管理人员报酬为报告期内从公司获得的所有税前报酬总额。\n"
)


def _text(pages):
    return "\n".join(c.text for c in chunk_fixed(pages, 2024))


def test_table_row_with_slashes_preserved():
    # 合计行空单元格用"/"，不得当页脚误删（病根复现）
    assert "680.93" in _text([(26, PAGE)])


def test_decimal_ratio_row_preserved():
    page = ("项目 本期 上期 增减\n"
            "营业收入 16.84 / 73.00 元\n"
            "营业成本 10.00 / 20.00 元\n"
            "说明：以上为分部数据对比，含本期与上期金额。\n")
    out = _text([(26, page)])
    assert "16.84" in out and "73.00" in out


def test_page_footer_removed():
    assert "26 / 143" not in _text([(26, PAGE)])


def test_page_header_removed():
    assert "年度报告" not in _text([(26, PAGE)])


def test_other_page_number_row_not_treated_as_footer():
    # 表格行恰好是"整数 / 整数"但不等于本页页码，应保留
    page = ("项目 比例\n"
            "某指标 16 / 73 见附注\n"
            "说明：以上为示例比例数据，用于验证页脚过滤不误删。\n")
    assert "16 / 73" in _text([(26, page)])