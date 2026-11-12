"""答案后的追问建议：不依赖界面，Streamlit 与 API 共用同一份实现。"""
from __future__ import annotations

import re

from src import usage

FOLLOWUP_PROMPT = """根据刚才的问题和回答，提出3个用户接下来最可能想问的后续问题。
要求：每个不超过20个字，一行一个，不要编号，不要解释，只输出3行。

刚才的问题：{q}
刚才的回答：{a}

/no_think"""


def gen_followups(client, model: str, question: str, answer: str) -> list[str]:
    """生成 3 个追问建议；失败则返回空（不阻塞主回答）。"""
    try:
        messages = [{"role": "user",
                     "content": FOLLOWUP_PROMPT.format(
                         q=question, a=answer[:400])}]
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=900,
        )
        usage.record(resp)
        raw = resp.choices[0].message.content or ""
        if not raw.strip():
            # 思考模式偶发未被 /no_think 关闭：配额被思考吃尽导致正文为空，
            # 翻倍配额重试一次（与 E5 语境句生成同源现象）
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=1800,
            )
            usage.record(resp)
            raw = resp.choices[0].message.content or ""
        # 模型偶尔把 3 条挤在一行：按编号标记再拆一次
        lines = []
        for l in raw.splitlines():
            lines.extend(x for x in re.split(r"[0-9]+[.、)．]\s*", l) if x.strip())
        out = []
        for l in lines:
            l = re.sub(r"^[0-9]+[.、)．]\s*", "", l.strip().strip("`-；;，,"))
            if 8 <= len(l) <= 40 and not l.startswith("/") and (
                    l.endswith("？") or re.search(r"[多少什么怎么如何哪是否]", l)):
                out.append(l)
        return out[:3]
    except Exception as e:  # noqa: BLE001
        print("[followups] 生成失败:", repr(e))
        return []