import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ask, claims
from src import metric_tools as mt


def _flat(text: str) -> str:
    return re.sub(r"[\s,，]", "", str(text or ""))


def test_all_metric_values_appear_on_cited_pages():
    data = json.loads(mt.METRICS_FILE.read_text(encoding="utf-8"))["metrics"]
    pages = claims.page_map()
    missing = []
    count = 0
    for key, m in data.items():
        for year, item in m["values"].items():
            count += 1
            page_text = pages.get(f"{year}年报 P{item['page']}", "")
            if _flat(item["value"]) not in _flat(page_text):
                missing.append((key, year, item["page"], item["value"]))
    assert count == 84
    assert not missing, missing


def test_resolve_metric_prefers_exact_and_longest():
    assert mt.resolve_metric("营业总收入")[0] == "revenue"
    assert mt.resolve_metric("营业收入")[0] == "operating_revenue"
    assert mt.resolve_metric("营收")[0] == "operating_revenue"
    assert mt.resolve_metric("净利润")[0] == "net_profit"
    assert mt.resolve_metric("扣非净利润")[0] == "deducted_net_profit"
    assert mt.resolve_metric("ROE")[0] == "roe"
    assert mt.resolve_metric("roe")[0] == "roe"
    assert mt.resolve_metric("不存在的指标") is None


def test_lookup_metric_single_year_and_all_years():
    hits = mt.lookup_metric("营业总收入", 2024)
    assert len(hits) == 1
    assert hits[0]["year"] == 2024 and hits[0]["page"] == 63
    assert "174,144,069,958.25" in hits[0]["text"]

    operating = mt.lookup_metric("营业收入", 2024)
    assert operating[0]["page"] == 5
    assert "170,899,152,276.34" in operating[0]["text"]

    all_hits = mt.lookup_metric("ROE")
    assert [h["year"] for h in all_hits] == [2022, 2023, 2024, 2025]
    assert all(h["page"] == 5 for h in all_hits[:3]) and all_hits[-1]["page"] == 6

    assert mt.lookup_metric("营业总收入", 2021) == []
    assert mt.lookup_metric("毛利率") == []


def test_compare_metric_summary_marks_calculated_values():
    hits = mt.compare_metric("营业收入", [2024, 2025])
    assert [h["year"] for h in hits] == [2024, 2025, 2025]
    summary = hits[-1]
    assert summary["id"] == "metric:operating_revenue:compare:2024-2025"
    assert "减少" in summary["text"]
    assert "（计算得出）" in summary["text"]
    assert "170,899,152,276.34" in summary["text"]
    assert "168,838,102,514.79" in summary["text"]

    total_hits = mt.compare_metric("营业总收入", [2024, 2025])
    total = total_hits[-1]["text"]
    assert "174,144,069,958.25" in total and "172,054,171,890.91" in total
    assert total_hits[-1]["source"] == "2024年报 P63；2025年报 P61"
    assert ask.build_context(total_hits).count("2024年报 P63") >= 1
    assert "2025年报 P61；2024年报 P63" not in ask.build_context(total_hits)

    roe = mt.compare_metric("ROE", [2022, 2025])[-1]["text"]
    assert "个百分点" in roe and "（计算得出）" in roe

    dealers = mt.compare_metric("经销商", [2022, 2023])[-1]["text"]
    assert "2,084" in dealers and "2,080" in dealers


def test_exec_tool_dispatches_metric_tools_and_honors_locked_year():
    def boom(*args, **kwargs):
        raise AssertionError("metric tool must not call retrieval")

    hits = ask._exec_tool("lookup_metric", {"metric": "营业总收入", "year": 2024},
                          boom, boom, None)
    assert len(hits) == 1 and hits[0]["year"] == 2024
    assert hits[0]["page"] == 63

    locked = ask._exec_tool("lookup_metric", {"metric": "营业总收入", "year": 2024},
                            boom, boom, 2023)
    assert locked[0]["year"] == 2023 and locked[0]["page"] == 63

    compared = ask._exec_tool("compare_metric",
                              {"metric": "净利润", "years": [2022, 2025]},
                              boom, boom, None)
    assert [h["year"] for h in compared] == [2022, 2025, 2025]
    assert "（计算得出）" in compared[-1]["text"]

    assert ask._exec_tool("lookup_metric", {}, boom, boom, None) == []
    assert ask._exec_tool("lookup_metric", {"metric": "毛利率"}, boom, boom, None) == []
