"""报表口径分类：按整页原文判“合并/母公司”层级与“正文/报表/附注”来源。

口径是页级属性，检索 chunk 碎片可能不含表头，故按整页判。规则高精度、
宁漏勿误：
- 母公司报表：行首独占的“母公司XX表”表头（排除审计报告里“合并及母公司
  XX表”的嵌入式提及）。
- 附注：编号≥20 的 note 表头（正文经营情况子节编号小，如“5、现金流”，
  不会误判）；早期附注 note（<20）漏判默认正文，是安全方向。
- 其余默认合并正文（年报正文默认合并口径）。
"""
from __future__ import annotations

import re
from functools import lru_cache

from src.config import PROJECT

_TABLES_DIR = PROJECT / "data" / "extracted_tables_plus"

_STATEMENTS = "资产负债表|利润表|现金流量表|所有者权益变动表|股东权益变动表"
_PARENT_RE = re.compile(rf"^\s*母公司({_STATEMENTS})", re.MULTILINE)
_CONSOL_RE = re.compile(rf"^\s*合并({_STATEMENTS})", re.MULTILINE)
# note 表头：编号≥20、后接非数字非空白标题（排除“20、21号”这类表格单元）
_NOTE_RE = re.compile(r"^\s*(?:[2-9][0-9]|[1-9][0-9]{2})、\s*[^\d\s]",
                      re.MULTILINE)


def classify_page(text: str) -> dict:
    """整页原文 -> {"level": 合并|母公司, "section": 正文|报表|附注}。"""
    text = text or ""
    if _PARENT_RE.search(text):
        return {"level": "母公司", "section": "报表"}
    if _CONSOL_RE.search(text):
        return {"level": "合并", "section": "报表"}
    if _NOTE_RE.search(text):
        return {"level": "合并", "section": "附注"}
    return {"level": "合并", "section": "正文"}


def scope_tag(scope: dict) -> str:
    """渲染进出处标签的口径字样；正文默认不打标签。"""
    if scope.get("level") == "母公司":
        return "母公司报表"
    section = scope.get("section")
    if section == "报表":
        return "合并报表"
    if section == "附注":
        return "报表附注"
    return ""


@lru_cache(maxsize=512)
def page_scope(year: int, page: int) -> dict:
    """读整页原文判口径；页文件缺失时默认合并正文。"""
    path = _TABLES_DIR / str(year) / f"page_{int(page):03d}.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"level": "合并", "section": "正文"}
    return classify_page(text)


def filter_default_scope(hits: list[dict], question: str) -> list[dict]:
    """默认合并口径的硬约束：问题未明确问母公司时，剔除母公司报表证据块。

    软提示压不住模型对“母公司现金流量表更权威”的偏好（实测 id169 放着
    首条合并数不用、偏挑末条母公司数），故直接在证据层剔除，让模型没得选。
    合成指标片段（显式 source）口径已 curated，保留；页码直达走 page_fn，
    不经此过滤，用户显式意图不受影响。
    """
    if "母公司" in (question or ""):
        return hits
    out = []
    for h in hits:
        if h.get("source"):
            out.append(h)
            continue
        if page_scope(h["year"], h["page"]).get("level") == "母公司":
            continue
        out.append(h)
    return out