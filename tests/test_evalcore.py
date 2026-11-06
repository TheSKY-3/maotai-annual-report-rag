"""src/evalcore.py 金标解析与命中口径的回归测试（纯本地，无网络）。"""
import sys
from pathlib import Path

# 直接跑 pytest 时把项目根加入 sys.path，不依赖 conftest.py
PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from src.evalcore import (  # noqa: E402
    all_gold_hit,
    cite_precision,
    parse_sources,
    run_eval,
)


# ---------------------------------------------------------------- 四种写法
def test_parse_single_page():
    """写法一：单个年份单页。"""
    assert parse_sources("2022年报 P7") == [(2022, 7)]


def test_parse_two_pages_one_year():
    """写法二：同一年份段内两个页码（第二个页码没有年份，必须继承）。
    这是修复前的漏洞：旧正则只返回 [(2024, 9)]，漏掉 P16。"""
    assert parse_sources("2024年报 P9、P16") == [(2024, 9), (2024, 16)]


def test_parse_two_year_groups():
    """写法三：两个年份段，各自带页码，必须出两对。"""
    assert parse_sources("2022年报 P20；2024年报 P21") == [(2022, 20), (2024, 21)]


def test_parse_three_pages_one_year():
    """写法四：同一年份段内三个页码。"""
    assert parse_sources("2025年报 P10、P17、P23") == [(2025, 10), (2025, 17), (2025, 23)]


def test_parse_old_regex_would_lose_page():
    """回归护栏：修复前的旧正则确实会丢掉第二个页码。"""
    import re

    old = [(int(y), int(p))
           for y, p in re.findall(r"(\d{4})年报\s*P(\d+)", "2024年报 P9、P16")]
    assert old == [(2024, 9)]                                    # 旧口径丢页
    assert parse_sources("2024年报 P9、P16") == [(2024, 9), (2024, 16)]


def test_parse_mixed_separators_and_dedup():
    """、与；混用、同一页重复出现时要保住年份归属并去重保序。"""
    src = "2024年报 P9、2024年报 P16；2023年报 P8、P16、P9"
    assert parse_sources(src) == [(2024, 9), (2024, 16),
                                 (2023, 8), (2023, 16), (2023, 9)]


def test_parse_no_year_or_empty():
    """没有年份标记（页码无年份可继承）或空串时返回空列表。"""
    assert parse_sources("") == []
    assert parse_sources("P9、P16") == []


def test_parse_tolerates_spacing():
    """年份与页码之间的空格/全角写法不影响解析。"""
    assert parse_sources("2024年报P9") == [(2024, 9)]
    assert parse_sources("2024年报  P9 、 P16") == [(2024, 9), (2024, 16)]


# ------------------------------------------------------------- all_gold_hit
def test_all_gold_hit_true():
    """gold 两页都在 got 里 -> 严格命中为真。"""
    assert all_gold_hit({(2024, 9), (2024, 16), (2024, 8)},
                        {(2024, 9), (2024, 16)}) is True


def test_all_gold_hit_false_when_partial():
    """只命中一页（any 口径会算命中，all 口径必须算未命中）。"""
    got = {(2024, 9), (2024, 8)}
    gold = {(2024, 9), (2024, 16)}
    assert all_gold_hit(got, gold) is False
    assert any(g in gold for g in got) is True   # 旧 any 口径确实会放行


def test_all_gold_hit_false_when_wrong_year():
    """页码对但年份不对不算命中。"""
    assert all_gold_hit({(2023, 9)}, {(2024, 9)}) is False


def test_all_gold_hit_empty_gold():
    """无 gold 页（如 no_answer 题）时不算命中。"""
    assert all_gold_hit({(2024, 9)}, set()) is False


# ------------------------------------------------------------ cite_precision
def test_cite_precision_half():
    """引用两页只有一页落在 gold -> 0.5。"""
    assert cite_precision({(2024, 9), (2024, 50)}, {(2024, 9), (2024, 16)}) == 0.5


def test_cite_precision_one():
    """引用全部落在 gold -> 1.0（多引也压不满精确率）。"""
    assert cite_precision({(2024, 9), (2024, 16)}, {(2024, 9), (2024, 16)}) == 1.0


def test_cite_precision_none_when_no_citation():
    """没有引用返回 None，不能当成 0 或 1。"""
    assert cite_precision(set(), {(2024, 9)}) is None


def test_cite_precision_zero_when_all_wrong():
    """引用存在但全错 -> 0.0，与 None 区分。"""
    assert cite_precision({(2024, 50)}, {(2024, 9)}) == 0.0


# ------------------------------------------------------- run_eval 的汇总口径
def test_run_eval_reports_any_and_all_separately():
    """同一份召回：any 算命中、all 算未命中，汇总必须两个数都给出。"""
    def fake_search(query, top_k):
        return [{"year": 2024, "page": 9}, {"year": 2024, "page": 8}]

    questions = [{"id": 31, "type": "cross_page", "question": "q",
                  "source": "2024年报 P9、P16"}]
    rows, summary = run_eval(fake_search, questions, k=5)
    row = rows[0]
    assert row["page_hit"] is True              # 命中 P9 -> any 成立
    assert row["all_gold_hit"] is False         # 缺 P16 -> all 不成立
    assert row["gold_pages"] == [(2024, 9), (2024, 16)]
    assert row["mrr"] == 1.0
    assert abs(row["page_prec"] - 0.2) < 1e-9   # 5 块里命中 1 块
    assert summary["overall"]["page_hit@k"] == 1.0
    assert summary["overall"]["all_gold_hit@k"] == 0.0
    assert summary["cross_page"]["n"] == 1

# 答案正文常写成「2024 年报 P9」（年份与“年报”之间有空格），
# 解析器必须容忍空格，否则这类引用会被静默丢弃、cite_any 偏低。
def test_parse_tolerates_space_before_nianbao():
    assert parse_sources("2025 年报片段（P57、P59）") == [(2025, 57), (2025, 59)]
    assert parse_sources("2024 年报 P9、P16") == [(2024, 9), (2024, 16)]
