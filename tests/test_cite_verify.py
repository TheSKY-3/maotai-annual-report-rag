"""轴4 核验层：答案 → 每个指标数字的坐标/高亮图/歧义标记。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import cite_locate

ID175 = ("1. 2024年董事、监事和高级管理人员从公司获得的税前报酬总额合计为680.93。"
         " [来源: 2024年报 P26]")
P26_EVIDENCE = "合计 / / / / / / 680.93 /"


def test_verify_locates_number_and_writes_crop(tmp_path):
    entries = cite_locate.verify_citations(ID175, P26_EVIDENCE, tmp_path, "id175")
    assert len(entries) == 1
    e = entries[0]
    assert e["located"] and not e["ambiguous"]
    assert (e["year"], e["page"]) == (2024, 26)
    assert e["boxes"] == [m.bbox for m in cite_locate.locate_number(2024, 26, "680.93")]
    assert Path(e["crop"]).exists()


def test_verify_marks_number_absent_from_cited_page(tmp_path):
    entries = cite_locate.verify_citations(
        "合计为12345.67。 [来源: 2024年报 P26]", "", tmp_path, "absent")
    assert entries[0]["located"] is False
    assert entries[0]["crop"] is None


def test_verify_flags_ambiguous_and_boxes_all_matches(tmp_path):
    entries = cite_locate.verify_citations(
        "净利润为89,334,728,025.90。 [来源: 2024年报 P64]",
        "与本页无关的文本", tmp_path, "amb")
    e = entries[0]
    assert e["located"] and e["ambiguous"]
    assert len(e["boxes"]) == 2
    assert Path(e["crop"]).exists()


def test_page_crop_none_when_number_not_on_page(tmp_path):
    assert cite_locate.page_crop(2024, 26, ["12345.67"], tmp_path) is None


def test_page_crop_renders_and_reuses_file(tmp_path):
    first = cite_locate.page_crop(2024, 26, ["680.93"], tmp_path)
    assert first is not None and first.exists()
    assert cite_locate.page_crop(2024, 26, ["680.93"], tmp_path) == first


def test_crop_for_source_only_on_cited_page(tmp_path):
    spec = {"numbers": ["680.93"], "pages": [(2024, 26)]}
    hit = {"year": 2024, "page": 26}
    assert cite_locate.crop_for_source(hit, spec, tmp_path) is not None
    assert cite_locate.crop_for_source({"year": 2024, "page": 25}, spec,
                                       tmp_path) is None


def test_crop_for_source_without_numbers_is_none(tmp_path):
    spec = {"numbers": [], "pages": [(2024, 26)]}
    assert cite_locate.crop_for_source({"year": 2024, "page": 26}, spec,
                                       tmp_path) is None


MANY_NUMBERS = [
    "127,553,959,355.97 元", "172,054,171,890.91 元", "44,500,212,534.94 元",
    "34.89%", "107,833,685,277.94", "146,499,906,480.49", "38,666,221,202.55",
    "15,938,647,070.77", "22,274,678,707.16", "6,336,031,636.39",
    "123,772,332,348.71", "168,774,585,187.65", "45,002,252,838.94",
    "49,378,737,661.60", "84,543,031,854.63", "35,164,294,193.03",
    "74,393,594,687.11", "84,231,553,333.02", "9,837,958,645.91",
    "386.66 亿元", "63.36 亿元", "351.64 亿元", "98.38", "0.39", "-9.76", "-1.08",
]


def test_page_crop_filename_bounded_for_long_number_list(tmp_path):
    # 跨年比较答案含几十个数字：拼接命名会超 Windows 260 字符路径上限
    out = cite_locate.page_crop(2025, 61, MANY_NUMBERS, tmp_path)
    assert out is not None and out.exists()
    assert len(str(out)) < 200
    assert cite_locate.page_crop(2025, 61, MANY_NUMBERS, tmp_path) == out


def test_page_crop_cache_key_distinguishes_number_sets(tmp_path):
    a = cite_locate.page_crop(2025, 61, MANY_NUMBERS, tmp_path)
    b = cite_locate.page_crop(2025, 61, MANY_NUMBERS[:1], tmp_path)
    assert a != b