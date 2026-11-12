"""追问建议生成：解析、过滤、空正文重试、失败兜底、用量记账。"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import usage
from src.followups import gen_followups

Q = "2024年茅台营业总收入是多少？"
A = "1741.44 亿元 [来源: 2024年报 P5]"
FU = ["2024年净利润是多少？", "2024年分红方案是什么？", "2024年末经销商有多少家？"]


def fake_client(*bodies):
    """按调用顺序返回预置正文（Exception 则抛出）；记录每次 create 的 kwargs。"""
    calls = []

    def create(**kw):
        calls.append(kw)
        body = bodies[min(len(calls) - 1, len(bodies) - 1)]
        if isinstance(body, Exception):
            raise body
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3))

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, calls


def test_three_lines_become_three_suggestions():
    client, _ = fake_client("\n".join(FU))
    assert gen_followups(client, "deepseek-flash", Q, A) == FU


def test_numbered_single_line_is_split():
    raw = "".join(f"{i}. {q}" for i, q in enumerate(FU, 1))
    client, _ = fake_client(raw)
    assert gen_followups(client, "deepseek-flash", Q, A) == FU


def test_drops_short_long_and_non_questions():
    raw = "\n".join(["太短？", "长" * 41 + "？", "公司去年业绩表现良好",
                     "/no_think", FU[0]])
    client, _ = fake_client(raw)
    assert gen_followups(client, "deepseek-flash", Q, A) == [FU[0]]


def test_keeps_at_most_three():
    client, _ = fake_client("\n".join(FU + ["2024年毛利率是多少？"]))
    assert gen_followups(client, "deepseek-flash", Q, A) == FU


def test_empty_body_retries_with_double_quota():
    client, calls = fake_client("", FU[0])
    assert gen_followups(client, "deepseek-flash", Q, A) == [FU[0]]
    assert [c["max_tokens"] for c in calls] == [900, 1800]


def test_prompt_carries_question_and_truncated_answer():
    client, calls = fake_client("\n".join(FU))
    gen_followups(client, "deepseek-flash", Q, A + "补" * 500)
    content = calls[0]["messages"][0]["content"]
    assert Q in content
    assert A in content
    assert "补" * 500 not in content


def test_client_failure_returns_empty():
    client, _ = fake_client(RuntimeError("boom"))
    assert gen_followups(client, "deepseek-flash", Q, A) == []


def test_records_usage_into_current_bucket():
    client, _ = fake_client("\n".join(FU))
    with usage.collect() as bucket:
        gen_followups(client, "deepseek-flash", Q, A)
    assert bucket == {"prompt_tokens": 7, "completion_tokens": 3, "calls": 1}