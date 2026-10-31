"""命令行问答入口。

用法（项目根目录）:
  .venv/Scripts/python.exe -m src.ask "2024年茅台营业总收入是多少？"
  .venv/Scripts/python.exe -m src.ask            # 不带参数进入连续问答

生成模型走 Ollama 本地服务（需先 ollama serve 或确认托盘已启动）。
"""
from __future__ import annotations

import os
import sys

from openai import OpenAI

from src.config import GEN_MODEL, OLLAMA_BASE_URL, TOP_K
from src.pipeline import get_search

PROMPT_TEMPLATE = """你是贵州茅台年报问答助手。请仅根据下面的年报片段回答问题。

要求：
1. 回答末尾标注出处，格式如：[来源: 2024年报 P8]。可以引用多个片段。
2. 如果片段中没有足够信息，只回答"根据年报内容无法回答该问题"，不要编造。
3. 数字必须与片段中完全一致，不要自行计算或换算。

--- 年报片段 ---
{context}
--- 片段结束 ---

问题：{question}

回答："""


def build_context(hits: list[dict]) -> str:
    return "\n\n".join(
        f"[片段{i + 1} | 来源: {h['year']}年报 P{h['page']}]\n{h['text']}"
        for i, h in enumerate(hits)
    )


def answer_question(client: OpenAI, search_fn, question: str,
                    model: str) -> tuple[str, list[dict]]:
    hits = search_fn(question, top_k=TOP_K)
    prompt = PROMPT_TEMPLATE.format(context=build_context(hits), question=question)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return resp.choices[0].message.content or "", hits


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
