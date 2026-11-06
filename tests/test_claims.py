import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import claims


def make_hits():
    return [
        {"id": "a", "year": 2024, "page": 8, "context": "语境句",
         "text": "语境句\n2024年营业总收入为174,144,069,958.25元"},
        {"id": "b", "year": 2024, "page": 56,
         "text": "营业收入 17,089,915.23 万元"},
    ]


def test_build_evidence_strips_context():
    ev = claims.build_evidence(make_hits())
    assert ev[0].eid == "E1" and ev[0].text.startswith("2024年营业总收入")
    assert "语境句" not in ev[0].text


def test_validate_ok():
    ev = claims.build_evidence(make_hits())
    obj = {"refused": False, "claims": [
        {"text": "营业总收入 1741.44 亿元", "evidence": ["E1"],
         "numbers": ["1741.44亿元"]}]}
    c, v, cites = claims.validate(obj, ev)
    assert len(c) == 1 and v == [] and cites == ["2024年报 P8"]


def test_validate_unknown_id():
    ev = claims.build_evidence(make_hits())
    obj = {"claims": [{"text": "x", "evidence": ["E9"]}]}
    _, v, _ = claims.validate(obj, ev)
    assert any("不存在的证据" in x for x in v)


def test_validate_number_on_other_page_fails():
    ev = claims.build_evidence(make_hits())
    obj = {"claims": [{"text": "营收 17,089,915.23 万元", "evidence": ["E1"],
                       "numbers": ["17,089,915.23"]}]}
    _, v, _ = claims.validate(obj, ev)
    assert any("找不到" in x for x in v)


def test_render_program_sources():
    ev = claims.build_evidence(make_hits())
    text, cites = claims.render(
        [{"text": "营收 1741.44 亿元 [来源: 2024年报 P999]", "evidence": ["E1"]}],
        ev)
    assert "2024年报 P8" in text and "P999" not in text
    assert cites == ["2024年报 P8"]


def test_parse_citation_pages():
    assert claims.parse_citation_pages("[来源: 2024年报 P9、P16；2025年报 P6]") == \
        [(2024, 9), (2024, 16), (2025, 6)]


def test_reconcile_drops_invalid():
    hits = make_hits()
    text = "答 1 [来源: 2024年报 P8] 答 2 [来源: 2024年报 P999]"
    tidied, bad, good = claims.reconcile_text(text, hits)
    assert "P999" not in tidied
    assert bad == ["[来源: 2024年报 P999]"]
    assert good == ["[来源: 2024年报 P8]"]


class _Msg:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Usage:
    prompt_tokens = 100
    completion_tokens = 50


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class _Completions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        return _Resp(self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0])


class FakeClient:
    def __init__(self, payloads):
        self.chat = type("C", (), {"completions": _Completions(payloads)})()


def test_generate_ok():
    ev = claims.build_evidence(make_hits())
    good = json.dumps({"refused": False, "claims": [
        {"text": "营业总收入 1741.44 亿元", "evidence": ["E1"],
         "numbers": ["1741.44亿元"]}], "calculation": None}, ensure_ascii=False)
    c = FakeClient([good])
    ans = claims.generate(c, "m", "营收多少", ev)
    assert ans.refused is False and ans.violations == []
    assert "2024年报 P8" in ans.text


def test_generate_retries_on_bad_json():
    ev = claims.build_evidence(make_hits())
    bad = "不是 JSON"
    good = json.dumps({"refused": True, "claims": [], "calculation": None},
                      ensure_ascii=False)
    c = FakeClient([bad, good])
    ans = claims.generate(c, "m", "营收多少", ev)
    assert ans.refused is True and ans.calls == 2


def test_generate_calc_uses_program_value():
    ev = claims.build_evidence([
        {"id": "a", "year": 2024, "page": 8,
         "text": "营收 200 元，成本 150 元"}])
    payload = json.dumps({
        "refused": False,
        "claims": [{"text": "毛利 50 元", "evidence": ["E1"],
                    "numbers": ["50"]}],
        "calculation": {"expr": "200-150", "value": "99"}}, ensure_ascii=False)
    ans = claims.generate(FakeClient([payload]), "m", "毛利多少", ev)
    assert "200-150 = 50" in ans.calc_note
    assert "模型原写 99" in ans.calc_note

def test_attribute_splits_pages_per_sentence():
    hits = [
        {"id": "a", "year": 2024, "page": 8, "text": "营业总收入 174,144,069,958.25 元"},
        {"id": "b", "year": 2024, "page": 56, "text": "直销渠道收入 74,843,327.10 万元"},
    ]
    text = "营业总收入 1741.44 亿元。直销渠道收入 74,843,327.10 万元。"
    out, cites, missing = claims.attribute_text(text, hits)
    assert cites == ["2024年报 P8", "2024年报 P56"]
    assert missing == []
    assert out.count("[来源: 2024年报 P8]") == 1


def test_attribute_ignores_calculated_number():
    hits = [{"id": "a", "year": 2024, "page": 15,
             "text": "茅台酒销量 46,412.95 吨；系列酒销量 36,919.81 吨"}]
    text = "46,412.95 + 36,919.81 = 83,332.76 吨（计算得出）。"
    out, cites, missing = claims.attribute_text(text, hits)
    assert missing == [] and cites == ["2024年报 P15"]


def test_attribute_flags_hallucinated_number():
    hits = [{"id": "a", "year": 2024, "page": 8,
             "text": "营业总收入 174,144,069,958.25 元"}]
    out, cites, missing = claims.attribute_text("毛利率 99.99%。", hits)
    assert missing == ["99.99%"] and cites == []
    assert "[来源" not in out

def test_attribute_merges_chunks_of_same_page():
    hits = [
        {"id": "a1", "year": 2024, "page": 9, "text": "营业收入 100 元"},
        {"id": "a2", "year": 2024, "page": 9, "text": "毛利率 50%"},
    ]
    out, cites, missing = claims.attribute_text("营业收入 100 元，毛利率 50%。", hits)
    assert cites == ["2024年报 P9"]
    assert missing == []

def test_attribute_multi_page_in_one_sentence():
    hits = [
        {"id": "a", "year": 2024, "page": 8, "text": "2024年营业总收入 100 元"},
        {"id": "b", "year": 2023, "page": 9, "text": "2023年营业总收入 200 元"},
    ]
    out, cites, missing = claims.attribute_text("2024年为 100 元，2023年为 200 元。", hits)
    assert set(cites) == {"2024年报 P8", "2023年报 P9"}
    assert missing == []
    assert "；" in out

def test_validate_uses_full_page_map():
    # 片段里没有 16.53%，但同一页的其它片段有 → 整页校验应通过
    hits = [{"id": "a", "year": 2022, "page": 7,
             "text": "2022年营业总收入 1,275.54 亿元"}]
    ev = claims.build_evidence(hits)
    obj = {"claims": [{"text": "同比增长 16.53%", "evidence": ["E1"],
                       "numbers": ["16.53%"]}]}
    pm = {"2022年报 P7": "2022年营业总收入 1,275.54 亿元，同比增长 16.53%"}
    _, bad_without, _ = claims.validate(obj, ev)
    _, bad_with, _ = claims.validate(obj, ev, pages=pm)
    assert bad_without and not bad_with


def test_attribute_uses_full_page_map():
    hits = [{"id": "a", "year": 2022, "page": 7,
             "text": "2022年营业总收入 1,275.54 亿元"}]
    pm = {"2022年报 P7": "2022年营业总收入 1,275.54 亿元，同比增长 16.53%"}
    out, cites, missing = claims.attribute_text(
        "同比增长 16.53%。", hits, pages=pm)
    assert cites == ["2022年报 P7"] and missing == []
