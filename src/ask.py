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

# 拒答判定不限于标准句式：模型常换说法（"未披露 X""无法据此列出"），
# 这些同样是拒答而非编造。仅匹配短语族，不会把给出数字的正常作答误判。
REFUSE_RE = re.compile(
    r"无法回答|无法据此|未能找到"
    r"|无法(从|根据|凭).{0,12}(找到|获取|确定|回答)"
    r"|未披露|没有披露|未提供|无法提供|无法确定|无法给出|无从得知")


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


def answer_question(client: OpenAI, search_fn, question: str, model: str,
                    search_kwargs: dict | None = None
                    ) -> tuple[str, list[dict]]:
    """检索+生成一问；返回 (答案文本, 实际使用的检索片段)。

    search_kwargs 传给检索函数的附加参数（如 {"year": 2024}），重试时保持一致。
    """
    skw = search_kwargs or {}
    hits = search_fn(question, top_k=TOP_K, **skw)
    text = _complete(client, model,
                     PROMPT_TEMPLATE.format(context=build_context(hits),
                                            question=question))
    if retry_enabled() and REFUSE_RE.search(text):
        more = search_fn(question, top_k=RETRY_TOP_K, **skw)
        retry_text = _complete(client, model,
                               RETRY_PROMPT_TEMPLATE.format(
                                   context=build_context(more),
                                   question=question))
        if retry_text:
            # 仍拒答则保留首轮片段（拒答本就不该带出处），答上了则换用扩充片段
            return (retry_text, hits if REFUSE_RE.search(retry_text) else more)
    return text, hits


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
