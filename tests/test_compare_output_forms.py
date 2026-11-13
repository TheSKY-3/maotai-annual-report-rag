"""输出形态对照的判分口径测试。

守住两条容易出假结论的口径：
1. 数字比对必须单位归一（gold 写"亿元"、指标表写"元"），不能用字符串包含；
2. 年份与页码不是答案数字，不能白送命中；出处「对 gold 页」与「自洽定位」分开算。
"""
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from scripts import compare_output_forms as cof  # noqa: E402


def test_gold_targets_剥离出处括注与页码并跳过年份():
    gold = ("2025年直销营业收入84,543,031,854.63元（2025年报P10主营业务分销售模式情况表）；"
            "2023年直销营业收入67,232,876,883.14元（2023年报P17按销售渠道划分的主营业务构成表）")
    assert cof.gold_targets(gold) == ["84,543,031,854.63元", "67,232,876,883.14元"]


def test_gold_targets_保留亿元与百分数():
    gold = "归母净利润747.34亿元，同比增长19.16%（2023年报P8）"
    assert cof.gold_targets(gold) == ["747.34亿元", "19.16%"]


def test_单位归一_亿元对元判命中():
    """字符串包含口径会把这条判错，归一口径必须判对。"""
    hit, missed = cof.match_targets(["1,275.54亿元"],
                                    "营业总收入 127,553,959,355.97 元 [来源: 2022年报 P58]")
    assert hit == 1 and missed == []


def test_年份与页码不白送命中():
    hit, missed = cof.match_targets(["84,543,031,854.63元"],
                                    "| 直销渠道营业收入 | 2025 | 无数据 | 2025年报 P10 |")
    assert hit == 0 and missed == ["84,543,031,854.63元"]


def test_数值不同则不命中():
    hit, _ = cof.match_targets(["747.34亿元"], "归母净利润 82,320,067,101.68 元")
    assert hit == 0


def test_cite_vs_gold_页号不同不算命中():
    table = "| 总资产 | 2025 | 303,834,844,021.44 元 | 2025年报 P61 |"
    assert cof.cite_vs_gold(table, "2025年报 P6；2024年报 P5") is False
    assert cof.cite_vs_gold(table, "2025年报 P61") is True


def test_build_cells_取指标表的值与页():
    cells, missing = cof.build_cells(
        {"items": [{"metric": "总资产", "years": [2025, 2024]}]})
    assert missing == []
    assert [c["year"] for c in cells] == [2025, 2024]
    assert all(c["page"] > 0 and c["value"] for c in cells)


def test_build_cells_清单外指标不硬凑():
    cells, missing = cof.build_cells(
        {"items": [{"metric": "子公司净利润", "years": [2023]}]})
    assert cells == [] and missing == ["子公司净利润"]


def test_resolve_exact_拒绝包含匹配但接受别名():
    assert cof.resolve_exact("子公司净利润") is None
    assert cof.resolve_exact("总资产") is not None
    assert cof.resolve_exact("归母净利润") is not None


def test_render_table_出处可被解析():
    cells = [{"metric": "总资产", "year": 2025, "value": "303,834,844,021.44",
              "unit": "元", "page": 61}]
    text = cof.render_table(cells)
    assert cof.cite_vs_gold(text, "2025年报 P61") is True
    assert "303,834,844,021.44 元" in text


def test_locate_cells_只查该格声明的页(monkeypatch):
    calls = []

    def fake_locate(year, page, raw):
        calls.append((year, page, raw))
        return [object()] if page == 61 else []

    monkeypatch.setattr(cof.cite_locate, "locate_number", fake_locate)
    cells = [{"metric": "总资产", "year": 2025, "value": "303,834,844,021.44",
              "unit": "元", "page": 61},
             {"metric": "总资产", "year": 2024, "value": "298,944,579,918.70",
              "unit": "元", "page": 5}]
    out = cof.locate_cells(cells)
    assert [c["located"] for c in out] == [True, False]
    assert calls == [(2025, 61, "303,834,844,021.44 元"),
                     (2024, 5, "298,944,579,918.70 元")]


def test_locate_cells_非金额单位用裸数(monkeypatch):
    """吨/个/人/股 不是 numeric 认的单位，拼进 raw 会让 parse_token 返回 None、
    退回子串匹配带单位整串而假失败（实测 36,919.81 吨在 2024 P15 明明有）。"""
    calls = []

    def fake_locate(year, page, raw):
        calls.append(raw)
        return [object()]

    monkeypatch.setattr(cof.cite_locate, "locate_number", fake_locate)
    cells = [{"metric": "其他系列酒销量", "year": 2024, "value": "36,919.81",
              "unit": "吨", "page": 15},
             {"metric": "国内经销商数量", "year": 2022, "value": "2,084",
              "unit": "个", "page": 16}]
    out = cof.locate_cells(cells)
    assert calls == ["36,919.81", "2,084"]
    assert all(c["located"] for c in out)


def test_locate_answer_按出处页逐个试(monkeypatch):
    monkeypatch.setattr(cof.cite_locate, "locate_number",
                        lambda year, page, raw: [object()] if raw.startswith("84") else [])
    answer = ("1. 2025年直销营业收入84,543,031,854.63元 [来源: 2025年报 P10]\n"
              "2. 2023年直销营业收入67,232,876,883.14元 [来源: 2023年报 P17]")
    out = cof.locate_answer(answer)
    assert [e["located"] for e in out] == [True, False]


def test_输出文件不得覆盖留出集权威清单():
    assert cof.OUT_NAME != "citation_crops.json"


# ---- 计算值口径：已诚实标注「计算得出」的数不该按定位失败扣分 ----

def test_is_calculated_认严格括号形():
    a = "直销收入 8,454,303.19 万元（即 845.43 亿元（计算得出））；[来源: 2025年报 P17]"
    assert cof.is_calculated(a, "845.43 亿元")
    assert not cof.is_calculated(a, "8,454,303.19 万元")


def test_is_calculated_认逗号形():
    # 生产 claims._CALC_NUM_RE 只认「（计算得出）」，漏这种逗号写法
    a = "净利润 4,259,628.69 万元（约425.96亿元，计算得出） [来源: 2023年报 P19]"
    assert cof.is_calculated(a, "425.96亿元")


def test_is_calculated_不把别的数的标记算过来():
    a = "同比减少10.51%，毛利率94.50%（计算得出）"
    assert not cof.is_calculated(a, "10.51%")


def test_locate_answer_带计算值标志(monkeypatch):
    monkeypatch.setattr(cof.cite_locate, "locate_number", lambda y, p, r: [])
    out = cof.locate_answer("收入 845.43 亿元（计算得出） [来源: 2025年报 P17]")
    assert out[0]["calculated"] is True


def test_summarize_双口径并列():
    rows = [{"num_targets": 2, "num_hit": 2, "all_num_correct": True,
             "cite_gold_correct": True, "locate_numbers": 2,
             "locate_located": 1, "locate_ambiguous": 0, "locate_calc": 1}]
    s = cof.summarize(rows)
    assert s["自洽定位"] == "1/2"          # 原口径保留，可追溯
    assert s["原文引用定位"] == "1/1"       # 扣除已标计算值
    assert s["原文引用定位率"] == 1.0


def test_rescore_从已存明细重算不重新提问(tmp_path):
    import json
    p = tmp_path / "form_compare.json"
    p.write_text(json.dumps({
        "A": [{"id": 1, "type": "cross_year", "question": "q",
               "output": "收入 845.43 亿元（计算得出） [来源: 2025年报 P17]",
               "num_targets": 1, "num_hit": 1, "num_missed": [],
               "all_num_correct": True, "cite_gold_correct": True,
               "locate_numbers": 1, "locate_located": 0,
               "locate_ambiguous": 0,
               "locate_detail": [{"number": "845.43 亿元", "located": False,
                                  "ambiguous": False, "year": None,
                                  "page": None}]}],
        "B": [], "summary": {}}, ensure_ascii=False), encoding="utf-8")
    cof.rescore_file(p)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["A"][0]["locate_calc"] == 1
    assert d["summary"]["A_自然语言"]["原文引用定位"] == "0/0"