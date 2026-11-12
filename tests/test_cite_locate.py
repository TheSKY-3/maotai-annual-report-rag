"""轴4 坐标级引用测试：答案数字 → 年报页面框 → 高亮裁剪图。

夹具全部来自真实年报：2024 P26 的合计 680.93 全页唯一；2024 P64 的
净利润 89,334,728,025.90 出现两次（"五、净利润"行与续行），靠被引片段
里的行首标签消歧。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import cite_locate

P64_EVIDENCE = (
    "五、净利润（净亏损以“－”号填列）  89,334,728,025.90 77,521,476,277.80 \n"
    "（一）按经营持续性分类 \n1.持续经营净利润（净亏损以\n“－”号填列）  "
    "89,334,728,025.90 77,521,476,277.80"
)


def test_locate_unique_number_returns_box_and_row_label():
    found = cite_locate.locate_number(2024, 26, "680.93")
    assert len(found) == 1
    x0, top, x1, bottom = found[0].bbox
    assert 680 < x0 < x1 < 730
    assert 240 < top < bottom < 265
    assert "合计" in found[0].row_text


def test_locate_absent_number_returns_empty():
    assert cite_locate.locate_number(2024, 26, "12345.67") == []


def test_locate_matches_without_thousands_separators():
    found = cite_locate.locate_number(2024, 64, "89334728025.90")
    assert len(found) == 2


def test_locate_duplicates_returned_in_page_order():
    found = cite_locate.locate_number(2024, 64, "89,334,728,025.90")
    assert len(found) == 2
    assert found[0].bbox[1] < found[1].bbox[1]
    assert "五、净利润" in found[0].row_text


def test_choose_single_match_needs_no_evidence():
    found = cite_locate.locate_number(2024, 26, "680.93")
    assert cite_locate.choose(found, "") is found[0]


def test_choose_prefers_row_whose_label_appears_in_evidence():
    found = cite_locate.locate_number(2024, 64, "89,334,728,025.90")
    picked = cite_locate.choose(found, P64_EVIDENCE)
    assert picked is not None
    assert "五、净利润" in picked.row_text


def test_choose_ambiguous_without_label_signal_returns_none():
    found = cite_locate.locate_number(2024, 64, "89,334,728,025.90")
    assert cite_locate.choose(found, "与本页无关的文本") is None


def test_locate_percent_embedded_in_prose_word():
    # pdfplumber 把整句中文并成一个 word，数字在词内：须按字符跨度给框
    found = cite_locate.locate_number(2022, 10, "11.44%")
    assert len(found) == 1
    x0, top, x1, bottom = found[0].bbox
    assert x1 - x0 < 40                      # 只框数字，不框整句
    assert "前五名客户销售额" in found[0].row_text


def test_locate_amount_with_unit_embedded_in_prose_word():
    found = cite_locate.locate_number(2022, 19, "71.09亿元")
    assert len(found) == 1


def test_locate_bare_cell_via_declared_page_unit():
    # P15 表头声明单位：万元，单元格裸数 290,243.05；答案带万元
    found = cite_locate.locate_number(2022, 15, "290,243.05万元")
    assert len(found) == 1
    assert "290,243.05" in found[0].row_text


def test_locate_bare_cell_still_matches_without_unit():
    assert len(cite_locate.locate_number(2022, 15, "290,243.05")) == 1