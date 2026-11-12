"""轴4 批量核验脚本：拒答行不产图，答对行每个指标数字一条清单记录。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import make_citation_crops as mcc


def _rows():
    rows = json.loads((ROOT / "results" / "holdout_v3_answers.json")
                      .read_text(encoding="utf-8"))["rows"]
    return [r for r in rows if r["id"] in (175, 236)]


def test_build_manifest_skips_refused_and_locates_answered(tmp_path):
    rows = _rows()
    assert sum(1 for r in rows if r["refused"]) == 1
    summary, entries = mcc.build_manifest(rows, mcc.load_chunk_texts(), tmp_path)
    assert summary["answers"] == 1
    assert summary["located"] == summary["numbers"] == 1
    assert entries[0]["id"] == 175
    assert Path(entries[0]["crop"]).exists()


def test_build_manifest_reports_unlocated_numbers(tmp_path):
    rows = [dict(_rows()[0], answer="合计为12345.67。 [来源: 2024年报 P26]")]
    summary, entries = mcc.build_manifest(rows, {}, tmp_path)
    assert summary["located"] == 0
    assert entries[0]["located"] is False