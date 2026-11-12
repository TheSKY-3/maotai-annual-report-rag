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