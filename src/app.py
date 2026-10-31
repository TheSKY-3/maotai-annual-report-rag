"""茅台年报问答助手 · Streamlit 界面。

运行（项目根目录）:
  .venv/Scripts/python.exe -m streamlit run src/app.py

功能:
  1. 对话问答，每条答案挂出处（年份+页码），可展开原文片段
  2. 预设问题：空界面时一键提问
  3. 追问推荐：每次回答后由模型生成 3 个后续问题，点击即问
  4. 年份筛选 + 对比模式：同一问题在两份年报内并排回答
"""
from __future__ import annotations

import os
import re

import streamlit as st
from openai import OpenAI

from src.ask import build_context
from src.config import GEN_MODEL, OLLAMA_BASE_URL, YEARS
from src.pipeline import get_search

PRESET_QUESTIONS = [
    "2024年茅台营业总收入是多少？",
    "茅台2024年度分红方案是什么？",
    "2024年末国内经销商有多少家？",
    "i茅台渠道2024年收入是多少？",
    "2025年营业总收入比2022年增长了多少？",
    "五粮液2024年的营业收入是多少？",
]

FOLLOWUP_PROMPT = """根据刚才的问题和回答，提出3个用户接下来最可能想问的后续问题。
要求：每个不超过20个字，一行一个，不要编号，不要解释，只输出3行。

刚才的问题：{q}
刚才的回答：{a}

/no_think"""

PROMPT = """你是贵州茅台年报问答助手。请仅根据下面的年报片段回答问题。

要求：
1. 回答末尾标注出处，格式如：[来源: 2024年报 P8]。可以引用多个片段。
2. 如果片段中没有足够信息，只回答"根据年报内容无法回答该问题"，不要编造。
3. 数字必须与片段中完全一致，不要自行计算或换算。

--- 年报片段 ---
{context}
--- 片段结束 ---

问题：{question}

回答：/no_think"""

st.set_page_config(page_title="茅台年报问答助手", page_icon="🍷", layout="wide")
st.title("🍷 茅台年报问答助手")
st.caption("数据：贵州茅台 2022–2025 年年度报告（巨潮资讯网公开披露）· 答案必带原文出处")


@st.cache_resource(show_spinner="加载检索模型...")
def get_retriever():
    """返回生产检索函数（混合检索+年份过滤+精排）。"""
    return get_search()


def _client() -> OpenAI:
    return OpenAI(base_url=os.environ.get("RAG_BASE_URL", OLLAMA_BASE_URL),
                  api_key="ollama")


def generate(question: str, hits: list[dict]) -> str:
    resp = _client().chat.completions.create(
        model=os.environ.get("RAG_MODEL", GEN_MODEL),
        messages=[{"role": "user",
                   "content": PROMPT.format(context=build_context(hits),
                                            question=question)}],
        temperature=0.1,
    )
    return resp.choices[0].message.content or ""


def gen_followups(question: str, answer: str) -> list[str]:
    """生成 3 个追问建议；失败则返回空（不阻塞主回答）。"""
    try:
        resp = _client().chat.completions.create(
            model=os.environ.get("RAG_MODEL", GEN_MODEL),
            messages=[{"role": "user",
                       "content": FOLLOWUP_PROMPT.format(
                           q=question, a=answer[:400])}],
            temperature=0.3,
            max_tokens=300,
        )
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
    except Exception:  # noqa: BLE001
        return []


def render_sources(hits: list[dict]) -> None:
    for i, h in enumerate(hits):
        with st.expander(f"出处 {i + 1}：{h['year']}年报 第{h['page']}页 "
                         f"(相似度 {h['score']:.3f})"):
            st.text(h["text"][:600])


def ask_followup(q: str) -> None:
    st.session_state.pending = q


if "msgs" not in st.session_state:
    st.session_state.msgs = []

mode = st.radio("模式", ["对话问答", "对比模式"], horizontal=True)

if mode == "对话问答":
    year = st.selectbox("检索范围", ["全部年份"] + YEARS)

    # ---- 渲染历史对话 ----
    for mi, m in enumerate(st.session_state.msgs):
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("hits"):
                render_sources(m["hits"])
            if m["role"] == "assistant" and m.get("followups"):
                st.caption("💡 接着问")
                for j, fu in enumerate(m["followups"]):
                    st.button(fu, key=f"fu_{mi}_{j}", on_click=ask_followup,
                              args=(fu,))

    # ---- 空界面：预设问题 ----
    if not st.session_state.msgs:
        st.markdown("**🎯 试试这些问题（点击即问）**")
        for row in range(0, len(PRESET_QUESTIONS), 2):
            cols = st.columns(2)
            for ci, q in enumerate(PRESET_QUESTIONS[row:row + 2]):
                cols[ci].button(q, key=f"preset_{row + ci}",
                                on_click=ask_followup, args=(q,),
                                width="stretch")

    # ---- 处理新问题（输入框 或 预设/追问点击） ----
    user_q = st.chat_input("例如：2024年茅台营业总收入是多少？") \
        or st.session_state.pop("pending", None)
    if user_q:
        st.session_state.msgs.append({"role": "user", "content": user_q})
        st.session_state.processing = True
        st.rerun()

    if st.session_state.pop("processing", False):
        question = st.session_state.msgs[-1]["content"]
        filt = None if year == "全部年份" else int(year)
        with st.chat_message("assistant"):
            hits = get_retriever()(question, top_k=5, year=filt)
            with st.spinner("检索年报并生成答案..."):
                text = generate(question, hits)
            st.markdown(text)
            render_sources(hits)
            with st.spinner("生成追问建议..."):
                fus = gen_followups(question, text)
            if fus:
                st.caption("💡 接着问")
                for j, fu in enumerate(fus):
                    st.button(fu, key=f"fu_now_{j}", on_click=ask_followup,
                              args=(fu,))
        st.session_state.msgs.append(
            {"role": "assistant", "content": text, "hits": hits,
             "followups": fus})

else:  # 对比模式
    c1, c2 = st.columns(2)
    with c1:
        year_a = st.selectbox("年报 A", YEARS, index=len(YEARS) - 2)
    with c2:
        year_b = st.selectbox("年报 B", YEARS, index=len(YEARS) - 1)
    q = st.text_input("问题", "营业总收入是多少？主要经营情况如何？")
    if st.button("开始对比") and q:
        ret = get_retriever()
        a, b = st.columns(2)
        for col, year in ((a, year_a), (b, year_b)):
            with col:
                st.subheader(f"{year} 年报")
                hits = ret(q, top_k=5, year=year)
                with st.spinner("生成中..."):
                    st.markdown(generate(q, hits))
                render_sources(hits)
