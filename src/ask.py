"""命令行问答入口。

用法（项目根目录）:
  .venv/Scripts/python.exe -m src.ask "2024年茅台营业总收入是多少？"
  .venv/Scripts/python.exe -m src.ask            # 不带参数进入连续问答

生成模型走 Ollama 本地服务（需先 ollama serve 或确认托盘已启动）。

E6 拒答自动重试（CRAG-lite 式）：首轮明确拒答时，扩充检索到 RETRY_TOP_K
块、用允许拒答的宽松提示再问一次。大模型"惜答"（误拒答）是答案层最大
失分点（E4 云端误拒答 33.3%），重试给它一次反悔机会；无答案题因提示
仍允许拒答，安全底线不应被破坏。环境变量 RAG_REFUSAL_RETRY=0 可关闭。
"""
from __future__ import annotations

import os
import re
import sys

from openai import OpenAI

from src.config import GEN_MODEL, OLLAMA_BASE_URL, RETRY_TOP_K, TOP_K
from src.pipeline import get_search

PROMPT_TEMPLATE = """你是贵州茅台年报问答助手。请仅根据下面的年报片段回答问题。

要求：
1. 回答末尾标注出处，格式如：[来源: 2024年报 P8]。可以引用多个片段。
2. 如果片段中没有足够信息，只回答"根据年报内容无法回答该问题"，不要编造，也不要标注来源。
3. 数字必须与片段中完全一致，不要自行计算或换算。

--- 年报片段 ---
{context}
--- 片段结束 ---

问题：{question}

回答：/no_think"""

RETRY_PROMPT_TEMPLATE = """你是贵州茅台年报问答助手。请仅根据下面的年报片段回答问题。这次的片段比上一次更多，请逐块仔细核对。

要求：
1. 只要任一片段包含能回答问题的信息，就请作答；答案常出现在看似间接的表述中。
2. 回答末尾标注出处，格式如：[来源: 2024年报 P8]。可以引用多个片段。
3. 如果所有片段确实都与问题无关，请仍回答"根据年报内容无法回答该问题"，不要编造。
4. 数字必须与片段中完全一致，不要自行计算或换算。

--- 年报片段 ---
{context}
--- 片段结束 ---

问题：{question}

回答：/no_think"""

GROUNDING_PROMPT_TEMPLATE = """你是贵州茅台年报问答助手。你刚才的回答里，下面这些数字在所引用的年报片段中找不到——可能抄错了数字或计量单位（元/万元/亿元相差一万倍），也可能是由片段数字计算得出的：

{bad_nums}

请对照下面的年报片段核对，输出修正后的完整回答：
1. 若是抄写或单位错误，请改正——数字必须与片段中完全一致；
2. 若确由片段中的数字计算得出，请保留该数字并在其后注明"（计算得出）"；
3. 保持原有出处标注格式 [来源: 2024年报 P8]；其余内容尽量不要改动。

--- 年报片段 ---
{context}
--- 片段结束 ---

原回答：
{answer}

修正后的回答：/no_think"""

# 拒答判定不限于标准句式：模型常换说法（"未披露 X""无法据此列出"），
# 这些同样是拒答而非编造。仅匹配短语族，不会把给出数字的正常作答误判。
REFUSE_RE = re.compile(
    r"无法回答|无法据此|未能找到"
    r"|无法(从|根据|凭).{0,12}(找到|获取|确定|回答)"
    r"|未披露|没有披露|未提供|无法提供|无法确定|无法给出|无从得知")

# 多轮记忆：追问改写提示词。省调用启发式——已含年份且足够长的问题视为
# 独立完整（如追问按钮生成的"2023年末经销商有多少家"），直接检索。
FOLLOWUP_REWRITE_PROMPT = """你在财报问答系统里负责把用户的追问改写成独立完整的问题（补全省略的年份、主体、指标）。

对话历史（从早到近）：
{history}

用户当前问题：{question}

规则：
1. 若当前问题本身已完整，原样输出；
2. 若是追问（如"那净利润呢""2023年呢"），结合上文补全成完整问题；
3. 只输出改写后的问题本身，一行，不要任何解释。

/no_think"""

_YEAR_RE = re.compile(r"20\d{2}")


def rewrite_followup(client: OpenAI, model: str, history: list[dict],
                     question: str) -> str:
    """把依赖上文的追问改写成独立问题；无历史/已完整/失败时原样返回。

    history 为 [{"role": "user"|"assistant", "content": str}, ...]。
    """
    if not history:
        return question
    if _YEAR_RE.search(question) and len(question) >= 12:
        return question
    lines = []
    for h in history[-3:]:
        who = "用户" if h["role"] == "user" else "助手"
        lines.append(f"{who}: {h['content'][:160]}")
    messages = [{"role": "user",
                 "content": FOLLOWUP_REWRITE_PROMPT.format(
                     history="\n".join(lines), question=question)}]
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages,
            temperature=0.0, max_tokens=900)
        out = (resp.choices[0].message.content or "").strip()
        if not out:
            # 思考模式偶发未被 /no_think 关闭：配额被思考吃尽导致正文为空，
            # 翻倍配额重试一次（E5 语境句生成时已验证该现象）
            resp = client.chat.completions.create(
                model=model, messages=messages,
                temperature=0.0, max_tokens=1800)
            out = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        return question
    out = out.splitlines()[0].strip().strip("`\"'“” ") if out else ""
    if 4 <= len(out) <= 60 and not out.startswith("/"):
        return out
    return question


def build_context(hits: list[dict]) -> str:
    return "\n\n".join(
        f"[片段{i + 1} | 来源: {h['year']}年报 P{h['page']}]\n{h['text']}"
        for i, h in enumerate(hits)
    )


def _complete(client: OpenAI, model: str, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return resp.choices[0].message.content or ""


def retry_enabled() -> bool:
    return os.environ.get("RAG_REFUSAL_RETRY", "1") != "0"


def grounding_enabled() -> bool:
    return os.environ.get("RAG_GROUNDING", "1") != "0"


_NUM_RE = re.compile(r"[0-9][0-9,，]*(?:\.[0-9]+)?")
_CITE_RE = re.compile(r"\[来源[^\]]*\]")


def _flat_digits(s: str) -> str:
    return re.sub(r"\s+", "", s.replace(",", "").replace("，", ""))


def ungrounded_numbers(answer: str, context: str) -> list[str]:
    """回答中在语境（含单位换算双向）里找不到的数字，按出现顺序去重。

    单位换算：answer 的 n 亿元 ↔ context 的 n×1e8 元，反之亦然——
    E4 实测小模型最常见的粗心正是"元/万元"抄错一万倍。
    """
    ans = _CITE_RE.sub("", answer)  # 出处标注里的页码/年份不算数
    hay = _flat_digits(context)
    bad: list[str] = []
    seen: set[str] = set()
    for m in _NUM_RE.findall(ans):
        n = m.replace(",", "").replace("，", "")
        if len(n) < 2 or n in seen:
            continue
        seen.add(n)
        try:
            v = float(n)
        except ValueError:
            continue
        forms = {n}
        for f in (v * 1e4, v * 1e8, v / 1e4, v / 1e8):
            # 小于 1 的换算形态会四舍五入成 "0"（永在原文中）→ 假接地，跳过
            if f >= 1:
                forms.add(f"{f:.2f}".rstrip("0").rstrip("."))
        if not any(x in hay for x in forms):
            bad.append(n)
    return bad


def _answer_core(client: OpenAI, search_fn, question: str, model: str,
                 search_kwargs: dict | None = None,
                 history: list[dict] | None = None
                 ) -> tuple[str, list[dict], str]:
    """检索+生成一问；返回 (答案, 实际使用的片段, 改写后的独立问题)。

    search_kwargs 传给检索函数的附加参数（如 {"year": 2024}），重试时保持一致。
    多轮：history 非空时先把追问改写成独立问题再检索（多轮记忆）。
    E6：首轮命中拒答语义时，扩检索宽松重问一次（RAG_REFUSAL_RETRY=0 关）。
    E8：回答里有语境中找不到的数字（含单位换算双向）时，让模型对照原文
    修正一次——专治"元/万元"抄错一万倍（RAG_GROUNDING=0 关）。
    """
    def finish(text: str, hits: list[dict]) -> str:
        if grounding_enabled() and text:
            bad = ungrounded_numbers(text, build_context(hits))
            if bad:
                fixed = _complete(
                    client, model,
                    GROUNDING_PROMPT_TEMPLATE.format(
                        bad_nums="\n".join(f"- {n}" for n in bad),
                        context=build_context(hits), answer=text))
                if fixed:
                    return fixed
        return text

    skw = search_kwargs or {}
    standalone = rewrite_followup(client, model, history or [], question)
    hits = search_fn(standalone, top_k=TOP_K, **skw)
    text = _complete(client, model,
                     PROMPT_TEMPLATE.format(context=build_context(hits),
                                            question=standalone))
    if retry_enabled() and REFUSE_RE.search(text):
        more = search_fn(standalone, top_k=RETRY_TOP_K, **skw)
        retry_text = _complete(client, model,
                               RETRY_PROMPT_TEMPLATE.format(
                                   context=build_context(more),
                                   question=standalone))
        if retry_text:
            # 仍拒答则保留首轮片段（拒答本就不该带出处），答上了则换用扩充片段
            return (finish(retry_text,
                           hits if REFUSE_RE.search(retry_text) else more),
                    more if not REFUSE_RE.search(retry_text) else hits,
                    standalone)
    return finish(text, hits), hits, standalone


def answer_question(client: OpenAI, search_fn, question: str, model: str,
                    search_kwargs: dict | None = None,
                    history: list[dict] | None = None
                    ) -> tuple[str, list[dict]]:
    """检索+生成一问；返回 (答案文本, 实际使用的检索片段)。

    history 传 [{"role", "content"}, ...] 即启用多轮记忆：追问会被改写成
    独立问题（如"那净利润呢"→"2024年茅台归母净利润是多少"）再检索。
    """
    text, hits, _ = _answer_core(client, search_fn, question, model,
                                 search_kwargs, history)
    return text, hits


def answer_with_meta(client: OpenAI, search_fn, question: str, model: str,
                     search_kwargs: dict | None = None,
                     history: list[dict] | None = None) -> dict:
    """同 answer_question，但返回 API 友好的完整元信息（含拒答判定与出处）。"""
    text, hits, standalone = _answer_core(client, search_fn, question, model,
                                          search_kwargs, history)
    return {
        "question": question,
        "standalone_question": standalone,
        "answer": text,
        "refused": bool(REFUSE_RE.search(text)),
        "sources": [
            {"year": h["year"], "page": h["page"],
             "score": round(float(h["score"]), 4),
             "snippet": h["text"][:200]}
            for h in hits
        ],
    }


def main() -> None:
    model = os.environ.get("RAG_MODEL", GEN_MODEL)
    base_url = os.environ.get("RAG_BASE_URL", OLLAMA_BASE_URL)
    client = OpenAI(base_url=base_url, api_key="ollama")
    search_fn = get_search()

    questions = sys.argv[1:] or None
    if questions:
        for q in questions:
            print(f"\n问题: {q}")
            text, hits = answer_question(client, search_fn, q, model)
            print(f"回答: {text}")
            print("检索依据:", ", ".join(f"{h['year']}P{h['page']}({h['score']:.2f})" for h in hits))
        return

    print(f"进入连续问答模式（模型: {model}，输入 q 退出）")
    while True:
        try:
            q = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() == "q":
            break
        text, hits = answer_question(client, search_fn, q, model)
        print(f"回答: {text}")
        print("检索依据:", ", ".join(f"{h['year']}P{h['page']}({h['score']:.2f})" for h in hits))


if __name__ == "__main__":
    main()
