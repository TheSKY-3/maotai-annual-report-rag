"""茅台年报问答助手 · Streamlit 界面。

运行（项目根目录）:
  .venv/Scripts/python.exe -m streamlit run src/app.py

功能:
  1. 对话问答，每条答案挂出处（年份+页码），可展开原文片段
  2. 年份筛选：只在该年份的年报内检索
  3. 对比模式：同一问题分别在两份年报内检索并并排回答
"""
from __future__ import annotations

import os

import streamlit as st
from openai import OpenAI

from src.ask import build_context
from src.config import GEN_MODEL, OLLAMA_BASE_URL, YEARS
from src.retriever import Retriever

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
def get_retriever() -> Retriever:
    return Retriever()


def generate(question: str, hits: list[dict]) -> str:
    client = OpenAI(base_url=os.environ.get("RAG_BASE_URL", OLLAMA_BASE_URL),
                    api_key="ollama")
    resp = client.chat.completions.create(
        model=os.environ.get("RAG_MODEL", GEN_MODEL),
        messages=[{"role": "user",
                   "content": PROMPT.format(context=build_context(hits),
                                            question=question)}],
        temperature=0.1,
    )
    return resp.choices[0].message.content or ""


def render_sources(hits: list[dict]) -> None:
    for i, h in enumerate(hits):
        with st.expander(f"出处 {i + 1}：{h['year']}年报 第{h['page']}页 "
                         f"(相似度 {h['score']:.3f})"):
            st.text(h["text"][:600])


mode = st.radio("模式", ["对话问答", "对比模式"], horizontal=True)

if mode == "对话问答":
    year = st.selectbox("检索范围", ["全部年份"] + YEARS)
    if "msgs" not in st.session_state:
        st.session_state.msgs = []
    for m in st.session_state.msgs:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("hits"):
                render_sources(m["hits"])
    if q := st.chat_input("例如：2024年茅台营业总收入是多少？"):
        st.session_state.msgs.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            filt = None if year == "全部年份" else int(year)
            hits = get_retriever().search(q, top_k=5, year=filt)
            text = generate(q, hits)
            st.markdown(text)
            render_sources(hits)
        st.session_state.msgs.append(
            {"role": "assistant", "content": text, "hits": hits})

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
                hits = ret.search(q, top_k=5, year=year)
                with st.spinner("生成中..."):
                    st.markdown(generate(q, hits))
                render_sources(hits)
