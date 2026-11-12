"""压测汇总函数：百分位用向上取整秩，错误率含非 2xx 与异常。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import load_test as lt


def test_summarize_percentiles_and_rps():
    out = lt.summarize([10, 20, 30, 40], [200, 200, 200, 200], wall_s=2.0)
    assert out["n"] == 4
    assert out["p50_ms"] == 20
    assert out["p95_ms"] == 40
    assert out["rps"] == 2.0
    assert out["errors"] == 0


def test_summarize_counts_errors():
    out = lt.summarize([5, 5], [200, 429], wall_s=1.0)
    assert out["errors"] == 1
    assert out["error_rate"] == 0.5


def test_summarize_empty_is_safe():
    out = lt.summarize([], [], wall_s=1.0)
    assert out["n"] == 0
    assert out["p95_ms"] is None