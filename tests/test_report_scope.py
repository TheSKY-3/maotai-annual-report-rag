"""报表口径分类器测试（轴1）。

用真实年报页派生的文本驱动：母公司报表靠行首表头判，附注靠 N≥20 的
note 表头判，正文经营情况的小编号（"5、现金流"）不得误判成附注，
审计报告里嵌入的"合并及母公司资产负债表"不得误判成母公司报表。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ask, report_scope


# ---- classify_page：层级（合并/母公司）+ 来源（正文/报表/附注）----

def test_parent_company_statement_header():
    text = ("母公司现金流量表 \n2024 年1—12 月 \n单位：元  币种：人民币 \n"
            "项目 附注 2024年度 2023年度 \n一、经营活动产生的现金流量：")
    assert report_scope.classify_page(text) == {"level": "母公司", "section": "报表"}


def test_consolidated_statement_header():
    text = ("合并现金流量表 \n2024 年1—12 月 \n单位：元  币种：人民币 \n"
            "一、经营活动产生的现金流量：")
    assert report_scope.classify_page(text) == {"level": "合并", "section": "报表"}


def test_main_text_small_number_is_not_appendix():
    # P12 正文现金流分析："5、 现金流" 是经营情况子节，不是附注 note
    text = ("5、 现金流 \n√适用 □不适用  \n单位：元  币种：人民币 \n"
            "客户存款和同业存放款项净增加额 11,060,205,782.10")
    assert report_scope.classify_page(text) == {"level": "合并", "section": "正文"}


def test_main_text_income_analysis_is_not_appendix():
    # P9/P17 正文："2、 收入和成本分析" + 按销售渠道
    text = ("2、 收入和成本分析 \n√适用 □不适用 \n按销售渠道 \n"
            "直销 74,843,327,030.79 11.32")
    assert report_scope.classify_page(text) == {"level": "合并", "section": "正文"}


def test_large_note_number_is_appendix():
    # P108 附注：note 39/40
    text = ("39、 未分配利润 \n√适用 □不适用  \n单位：元  币种：人民币 \n"
            "40、 营业收入和营业成本 \n直销 74,911,586,867.82")
    assert report_scope.classify_page(text) == {"level": "合并", "section": "附注"}


def test_audit_report_embedded_mention_is_not_parent_statement():
    # P55 审计报告："合并及母公司资产负债表" 嵌在行中，不是行首表头
    text = ("月31 日的合并及母公司资产负债表，2024 年度的合并及母公司利润表、"
            "合并及母公司现金流量表、合并及母公司所有者权益变动表，"
            "以及相关财务报表附注。")
    assert report_scope.classify_page(text) == {"level": "合并", "section": "正文"}


def test_note_below_20_defaults_to_main_text():
    # 早期附注 note（<20）宁可漏判默认正文，也不误判（保守方向）
    text = "1、 公司基本情况 \n√适用 □不适用 \n本公司属酒、饮料和精制茶制造业。"
    assert report_scope.classify_page(text) == {"level": "合并", "section": "正文"}


def test_table_cell_with_digit_after_marker_is_not_note():
    # P118 表格单元 "20、21 号"：、后是数字，不是 note 标题
    text = "20、21 号 328,389.91 \n单位：元  币种：人民币"
    assert report_scope.classify_page(text) == {"level": "合并", "section": "正文"}


def test_empty_text_defaults():
    assert report_scope.classify_page("") == {"level": "合并", "section": "正文"}


# ---- scope_tag：渲染标签（正文默认不打标签）----

def test_scope_tag_rendering():
    assert report_scope.scope_tag({"level": "母公司", "section": "报表"}) == "母公司报表"
    assert report_scope.scope_tag({"level": "合并", "section": "报表"}) == "合并报表"
    assert report_scope.scope_tag({"level": "合并", "section": "附注"}) == "报表附注"
    assert report_scope.scope_tag({"level": "合并", "section": "正文"}) == ""


# ---- page_scope：读整页真实文件（集成）----

def test_page_scope_reads_real_pages():
    assert report_scope.page_scope(2024, 68)["level"] == "母公司"
    assert report_scope.page_scope(2024, 66)["section"] == "报表"
    assert report_scope.page_scope(2024, 108)["section"] == "附注"
    assert report_scope.page_scope(2024, 12)["section"] == "正文"
    assert report_scope.page_scope(2024, 17)["section"] == "正文"


def test_page_scope_missing_page_defaults():
    assert report_scope.page_scope(2024, 9999) == {"level": "合并", "section": "正文"}


# ---- build_context：把口径标签渲染进出处 ----

def test_build_context_tags_parent_company_page():
    hits = [{"year": 2024, "page": 68, "text": "母公司现金流量表\n一、经营活动"}]
    ctx = ask.build_context(hits)
    assert "母公司报表" in ctx
    assert "2024年报 P68" in ctx


def test_build_context_tags_appendix_page():
    hits = [{"year": 2024, "page": 108, "text": "40、 营业收入和营业成本\n直销"}]
    assert "报表附注" in ask.build_context(hits)


def test_build_context_main_text_has_no_scope_tag():
    hits = [{"year": 2024, "page": 12, "text": "5、 现金流\n客户存款"}]
    ctx = ask.build_context(hits)
    assert "母公司报表" not in ctx
    assert "报表附注" not in ctx
    assert "合并报表" not in ctx


def test_build_context_skips_synthetic_metric_source():
    # 指标汇总合成片段有显式 source、跨多页，不打单页口径标签
    hits = [{"year": 2024, "page": 68, "source": "2022年报 P5；2024年报 P68",
             "text": "2022年到2024年营业收入：…（计算得出）"}]
    ctx = ask.build_context(hits)
    assert "母公司报表" not in ctx


# ---- filter_default_scope：默认合并口径，硬剔母公司报表证据 ----

def test_filter_drops_parent_company_when_not_asked():
    hits = [{"year": 2024, "page": 68, "text": "母公司现金流量表"},
            {"year": 2024, "page": 12, "text": "5、 现金流"}]
    out = report_scope.filter_default_scope(hits, "2024年购建固定资产支付的现金")
    assert [h["page"] for h in out] == [12]


def test_filter_keeps_parent_company_when_explicitly_asked():
    hits = [{"year": 2024, "page": 68, "text": "母公司现金流量表"},
            {"year": 2024, "page": 12, "text": "5、 现金流"}]
    out = report_scope.filter_default_scope(hits, "2024年母公司购建固定资产支付的现金")
    assert [h["page"] for h in out] == [68, 12]


def test_filter_keeps_synthetic_metric_hit():
    hits = [{"year": 2024, "page": 68, "source": "2024年报 P68", "text": "指标汇总"}]
    out = report_scope.filter_default_scope(hits, "2024年营业收入")
    assert len(out) == 1


def test_filter_no_parent_company_unchanged():
    hits = [{"year": 2024, "page": 12, "text": "现金流"},
            {"year": 2024, "page": 108, "text": "40、 营业收入"}]
    out = report_scope.filter_default_scope(hits, "2024年直销收入")
    assert [h["page"] for h in out] == [12, 108]