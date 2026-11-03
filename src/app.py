"""茅台年报问答助手 · Streamlit 界面。

运行（项目根目录）:
  .venv/Scripts/python.exe -m streamlit run src/app.py

功能:
  1. 对话问答，每条答案挂出处（年份+页码），可展开原文片段
  2. 多轮记忆：追问（"那净利润呢"）自动继承上一问的年份与主体
  3. 模型可在侧边栏切换：本地 Ollama（离线）或云端 DeepSeek（可粘贴
     自己的 API Key，仅存于当前浏览器会话）
  4. 年份筛选 + 对比模式：同一问题在两份年报内并排回答
"""
from __future__ import annotations

import html
import os
import re

import streamlit as st
from openai import OpenAI

from src.ask import answer_auto, answer_auto_stream
from src.config import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GEN_MODEL,
                        OLLAMA_BASE_URL, YEARS)
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

st.set_page_config(page_title="茅台年报问答助手", page_icon="📑", layout="wide")

# ---------------------------------------------------------------------------
# 视觉主题：「档案纸面」—— 暖白纸底、宋体标题、墨青蓝强调、出处做成档案卡片
# （刻意去品牌化：不使用茅台商标红与商标元素，蓝色取自金融档案文件的墨青色）
# ---------------------------------------------------------------------------
CSS = """
:root {
  --paper: #F7F5F0; --card: #FFFFFF; --wash: #F3EFE6;
  --ink: #23201B; --muted: #8A8375; --line: #E8E2D6;
  --accent: #2F5D8A; --accent-dark: #244B72; --accent-soft: #EDF2F8;
  --gold: #A08339;
}
.stApp, [data-testid="stApp"] {
  background: var(--paper);
  font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  color: var(--ink);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stAppDeployButton"], [data-testid="stToolbarActions"],
[data-testid="stMainMenu"], [data-testid="stStatusWidget"] { display: none; }

/* ---- 居中排版（聊天产品惯例的阅读宽度）---- */
[data-testid="stMainBlockContainer"],
[data-testid="stBottomBlockContainer"] {
  max-width: 880px; margin-left: auto; margin-right: auto;
}

/* ---- 侧边栏 ---- */
[data-testid="stSidebar"] {
  background: #F1EDE4; border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: 20px; }
.mt-side-brand { display: flex; align-items: center; gap: 10px; margin: 2px 0 16px; }
.mt-side-brand .seal {
  flex: 0 0 auto; width: 34px; height: 34px; border-radius: 8px;
  background: linear-gradient(160deg, #35618F, var(--accent-dark));
  color: #FBF6EC; display: flex; align-items: center; justify-content: center;
  font-family: "Source Han Serif SC", "Noto Serif SC", "STZhongsong", "SimSun", serif;
  font-size: 19px; font-weight: 700;
  box-shadow: inset 0 0 0 2px rgba(255, 251, 240, .30);
}
.mt-side-brand .name {
  font-family: "Source Han Serif SC", "Noto Serif SC", "STZhongsong", "SimSun", serif;
  font-size: 17px; font-weight: 700; letter-spacing: .02em;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 12.5px; color: var(--muted);
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input,
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div {
  border-radius: 10px !important;
}
[data-testid="stSidebar"] [data-testid="baseButton-secondary"] {
  border-radius: 10px; border: 1px dashed #D8D1C2;
  background: transparent; color: var(--muted); font-size: 13px;
}
[data-testid="stSidebar"] [data-testid="baseButton-secondary"]:hover {
  border-color: var(--accent); color: var(--accent);
}
[data-testid="stSidebar"] hr {
  margin: 12px 0; border-color: var(--line);
}
.mt-side-caption { font-size: 11.5px; color: var(--muted); line-height: 1.7; }

/* ---- 页首：印章 + 宋体标题 + 金线 ---- */
.mt-header {
  display: flex; align-items: center; gap: 14px;
  padding: 6px 2px 14px; margin-bottom: 10px;
  border-bottom: 1px solid var(--line); position: relative;
}
.mt-header:after {
  content: ""; position: absolute; left: 2px; bottom: -1px;
  width: 72px; height: 2px;
  background: linear-gradient(90deg, var(--gold), transparent);
}
.mt-seal {
  flex: 0 0 auto; width: 46px; height: 46px; border-radius: 10px;
  background: linear-gradient(160deg, #35618F, var(--accent-dark));
  color: #FBF6EC; display: flex; align-items: center; justify-content: center;
  font-family: "Source Han Serif SC", "Noto Serif SC", "STZhongsong", "SimSun", serif;
  font-size: 27px; font-weight: 700;
  box-shadow: inset 0 0 0 2px rgba(255, 251, 240, .30), 0 2px 5px rgba(36, 75, 114, .25);
}
.mt-title {
  font-family: "Source Han Serif SC", "Noto Serif SC", "STZhongsong", "SimSun", serif;
  font-size: 26px; font-weight: 700; letter-spacing: .02em; line-height: 1.15;
}
.mt-tagline { font-size: 12.5px; color: var(--muted); margin-top: 4px; letter-spacing: .05em; }
.mt-tagline em { font-style: normal; color: var(--gold); padding: 0 7px; }

/* ---- 小节眉标（蓝、疏排）---- */
.mt-eyebrow {
  font-size: 12px; font-weight: 700; letter-spacing: .22em;
  color: var(--accent); margin: 6px 0 10px;
}

/* ---- 聊天气泡 ---- */
[data-testid="stChatMessage"] { background: transparent; }
[data-testid="stChatMessageContent"] {
  background: var(--card); border: 1px solid var(--line);
  border-radius: 16px 16px 16px 4px; padding: 12px 18px;
  box-shadow: 0 2px 12px rgba(33, 29, 24, .05);
  font-size: 14.5px; line-height: 1.75;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  flex-direction: row-reverse;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {
  background: linear-gradient(135deg, #35618F, var(--accent));
  border: none; color: #F7F4EC;
  border-radius: 16px 16px 4px 16px;
  box-shadow: 0 3px 12px rgba(36, 75, 114, .22);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] p { color: #F7F4EC !important; }
[data-testid="stChatMessageAvatarAssistant"] {
  background: var(--accent); border-radius: 10px;
  box-shadow: inset 0 0 0 2px rgba(255, 251, 240, .30);
}
[data-testid="stChatMessageAvatarAssistant"] span,
[data-testid="stChatMessageAvatarAssistant"] svg {
  color: #FBF6EC !important; fill: #FBF6EC;
}
[data-testid="stChatMessageAvatarUser"] {
  background: #D9D0BC; border-radius: 10px;
}

/* ---- 答案里的出处标注 tag ---- */
.mt-cite {
  display: inline-block; background: var(--accent-soft); color: var(--accent);
  border: 1px solid #C7D6E7; border-radius: 999px; padding: 0 10px;
  font-size: 12.5px; font-weight: 600; margin: 1px 4px 1px 2px;
  white-space: nowrap;
}

/* ---- 出处档案卡 ---- */
[data-testid="stExpander"] {
  background: var(--card); border: 1px solid var(--line);
  border-left: 3px solid var(--accent); border-radius: 10px;
  box-shadow: 0 1px 3px rgba(33, 29, 24, .04);
  margin-bottom: 4px; overflow: hidden;
  transition: box-shadow .15s, border-color .15s;
}
[data-testid="stExpander"]:hover {
  box-shadow: 0 3px 10px rgba(36, 75, 114, .10); border-color: #CBD9E8;
}
[data-testid="stExpander"] summary {
  font-size: 13.5px; font-weight: 600; color: var(--ink);
}
[data-testid="stExpander"] summary:hover { color: var(--accent); }
[data-testid="stExpanderDetails"] { font-size: 13px; }
.mt-source-text {
  white-space: pre-wrap; color: #4A443B; line-height: 1.8; font-size: 12.5px;
  background: var(--paper); border: 1px dashed var(--line);
  border-radius: 8px; padding: 10px 12px; margin-top: 4px;
}

/* ---- 候选问题卡（预设/追问统一）---- */
[data-testid="stButton"] button {
  width: 100%; text-align: left; background: var(--card);
  color: var(--ink); border: 1px solid var(--line); border-radius: 12px;
  padding: 9px 14px; font-size: 13.5px; font-weight: 400;
  transition: border-color .15s, color .15s, background .15s,
              transform .15s, box-shadow .15s;
}
[data-testid="stButton"] button:hover {
  border-color: var(--accent); color: var(--accent);
  background: var(--accent-soft);
  transform: translateY(-1px); box-shadow: 0 3px 10px rgba(36, 75, 114, .10);
}
[data-testid="stButton"] button:focus-visible {
  border-color: var(--accent); box-shadow: 0 0 0 3px rgba(47, 93, 138, .18);
}
[data-testid="stButton"] button p { font-size: 13.5px; }

/* ---- 输入框 ---- */
[data-testid="stBottom"] {
  background: linear-gradient(transparent, var(--paper) 32%);
}
[data-testid="stChatInput"] {
  border-radius: 14px; border: 1px solid var(--line);
  background: var(--card); box-shadow: 0 2px 14px rgba(33, 29, 24, .06);
}
[data-testid="stChatInput"]:focus-within {
  border-color: var(--accent); box-shadow: 0 0 0 3px rgba(47, 93, 138, .12);
}
[data-testid="stChatInputSubmitButton"] button {
  background: var(--accent); border-color: var(--accent); border-radius: 10px;
}
[data-testid="stChatInputSubmitButton"] button:hover {
  background: var(--accent-dark); border-color: var(--accent-dark);
}

/* ---- 控件标签与标题 ---- */
[data-testid="stWidgetLabel"] p { color: var(--muted); font-size: 13px; }
h2, h3, [data-testid="stHeading"] {
  font-family: "Source Han Serif SC", "Noto Serif SC", "STZhongsong", "SimSun", serif;
}
"""
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

st.markdown(
    """
    <div class="mt-header">
      <div class="mt-seal">档</div>
      <div>
        <div class="mt-title">茅台年报问答</div>
        <div class="mt-tagline">贵州茅台 2022–2025 年度报告<em>·</em>答案必附原文页码出处<em>·</em>查不到就明说</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True)


@st.cache_resource(show_spinner="加载检索模型...")
def get_retriever():
    """返回生产检索函数（语境增强混合检索+年份过滤）。"""
    return get_search()


def _resolve_backend() -> tuple[OpenAI, str]:
    """按侧边栏选择组装客户端与模型名。"""
    if st.session_state.get("backend") == "本地 Ollama":
        return OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama"), GEN_MODEL
    key = (st.session_state.get("byo_key") or "").strip() \
        or os.environ.get("DEEPSEEK_API_KEY", "") \
        or os.environ.get("RAG_API_KEY", "")
    model = os.environ.get("RAG_MODEL", DEEPSEEK_MODEL)
    return OpenAI(base_url=DEEPSEEK_BASE_URL, api_key=key or "missing"), model


def generate(question: str, year: int | None = None,
             history: list[dict] | None = None,
             force_agent: bool = False) -> dict:
    """路由式问答：跨年对比/页码直达题自动走 agent 工具循环，其余直通。

    返回 answer_auto 的完整元信息（answer/hits/route/steps 等）。
    """
    client, model = _resolve_backend()
    return answer_auto(client, get_retriever(), question, model,
                       search_kwargs={"year": year}
                       if year is not None else None,
                       history=history, force_agent=force_agent)


_CITE_TAG_RE = re.compile(r"\[来源: ([^\]]+)\]")


def fmt_answer(text: str) -> str:
    """答案里的 [来源: ...] 渲染成出处 tag，其余内容转义防注入。"""
    escaped = html.escape(text)
    return _CITE_TAG_RE.sub(r'<span class="mt-cite">📄 \1</span>', escaped)


def gen_followups(question: str, answer: str) -> list[str]:
    """生成 3 个追问建议；失败则返回空（不阻塞主回答）。"""
    try:
        client, model = _resolve_backend()
        messages = [{"role": "user",
                     "content": FOLLOWUP_PROMPT.format(
                         q=question, a=answer[:400])}]
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=900,
        )
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


def render_sources(hits: list[dict]) -> None:
    for i, h in enumerate(hits):
        badge = "页码直达" if h.get("direct") else (
            "⭐ 最佳匹配" if i == 0 else "")
        label = f"出处 {i + 1} · {h['year']}年报 第{h['page']}页"
        if badge:
            label += f" · {badge}"
        with st.expander(label):
            body = html.escape(h["text"][:600])
            st.markdown(f'<div class="mt-source-text">{body}</div>',
                        unsafe_allow_html=True)


def render_steps(steps: list[dict]) -> None:
    """agent 路径的工具调用轨迹（Function Calling 过程可视化）。"""
    if not steps:
        return
    with st.expander(f"🔧 Agent 工具调用轨迹（{len(steps)} 步）"):
        lines = []
        for i, s in enumerate(steps, start=1):
            arg_str = "、".join(f"{k}: {v}" for k, v in s["args"].items())
            lines.append(f"**{i}. `{s['tool']}`**（{arg_str}）→ "
                         f"取回 {s['n_hits']} 个片段")
        st.markdown("\n\n".join(lines))


def ask_followup(q: str) -> None:
    st.session_state.pending = q


def _chat_history() -> list[dict]:
    """取最近几轮对话作为多轮改写的上下文（排除刚提出的问题）。"""
    out = []
    for m in st.session_state.msgs[:-1][-6:]:
        out.append({"role": m["role"], "content": m["content"][:200]})
    return out


if "msgs" not in st.session_state:
    st.session_state.msgs = []

# ---- 侧边栏：模式 / 检索范围 / 模型设置 ----
with st.sidebar:
    st.markdown(
        '<div class="mt-side-brand"><div class="seal">档</div>'
        '<div class="name">茅台年报问答</div></div>',
        unsafe_allow_html=True)
    mode = st.segmented_control("模式", ["对话问答", "对比模式"],
                                key="mode", default="对话问答")
    mode = mode or "对话问答"

    if mode == "对话问答":
        year_sel = st.selectbox("检索范围", ["全部年份"] + YEARS)
        force_agent = st.checkbox(
            "强制 Agent 模式（演示工具调用）", value=False,
            help="所有问题都走 Function Calling 工具循环。默认关闭："
                 "只有跨年对比/页码直达题会自动启用 Agent。")

    st.markdown('<p class="mt-eyebrow">生成模型</p>', unsafe_allow_html=True)
    backend = st.segmented_control(
        "生成模型", ["云端 DeepSeek", "本地 Ollama"], key="backend",
        default="云端 DeepSeek"
        if (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("RAG_API_KEY"))
        else "本地 Ollama", label_visibility="collapsed")
    backend = backend or "云端 DeepSeek"
    if backend == "云端 DeepSeek":
        st.text_input("DeepSeek API Key", type="password", key="byo_key",
                      placeholder="sk-…（留空则用环境变量）",
                      help="粘贴你自己的 DeepSeek API Key。Key 只保存在当前"
                           "浏览器会话里，不会上传到任何第三方。")
        st.markdown('<p class="mt-side-caption">没有 Key？到 '
                    'platform.deepseek.com 申请，一次问答约 ¥0.001。</p>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<p class="mt-side-caption">需要本机运行 Ollama'
                    '（qwen3:4b）。完全离线、零成本。</p>',
                    unsafe_allow_html=True)

    st.markdown("<hr/>", unsafe_allow_html=True)
    if st.button("🗑️ 清空对话", use_container_width=True):
        st.session_state.msgs = []
        st.rerun()
    st.markdown('<p class="mt-side-caption">贵州茅台 2022–2025 年报 · '
                '仅学习研究，非投资建议</p>', unsafe_allow_html=True)

if mode == "对话问答":
    # ---- 渲染历史对话 ----
    for mi, m in enumerate(st.session_state.msgs):
        with st.chat_message(m["role"]):
            if m["role"] == "assistant":
                st.markdown(fmt_answer(m["content"]), unsafe_allow_html=True)
            else:
                st.markdown(m["content"])
            if m.get("hits"):
                render_sources(m["hits"])
            if m.get("steps"):
                render_steps(m["steps"])
            if m["role"] == "assistant" and m.get("followups"):
                st.markdown('<p class="mt-eyebrow">接着问</p>',
                            unsafe_allow_html=True)
                cols = st.columns(len(m["followups"]))
                for j, fu in enumerate(m["followups"]):
                    with cols[j]:
                        st.button(fu, key=f"fu_{mi}_{j}", on_click=ask_followup,
                                  args=(fu,))

    # ---- 空界面：预设问题 ----
    if not st.session_state.msgs:
        st.markdown('<p class="mt-eyebrow">试试这些问题</p>',
                    unsafe_allow_html=True)
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
        filt = None if year_sel == "全部年份" else int(year_sel)
        history = _chat_history()
        with st.chat_message("assistant"):
            status_box = st.status("正在查年报…", expanded=True)
            answer_slot = st.empty()
            box: dict = {}
            try:
                with status_box as s:

                    def evgen():
                        client, model = _resolve_backend()
                        for ev in answer_auto_stream(
                                client, get_retriever(), question, model,
                                search_kwargs={"year": filt}
                                if filt is not None else None,
                                history=history, force_agent=force_agent):
                            if ev["type"] == "status":
                                s.update(label=ev["label"])
                            elif ev["type"] == "delta":
                                yield ev["text"]
                            elif ev["type"] == "replace":
                                box["replace"] = ev["text"]
                            elif ev["type"] == "done":
                                box["meta"] = ev["meta"]

                    text = answer_slot.write_stream(evgen())
            except Exception as e:  # noqa: BLE001
                status_box.update(state="error", expanded=False)
                st.error(f"生成失败：{e}")
                st.markdown('<p class="mt-side-caption">云端模式请检查侧边栏'
                            '的 API Key；本地模式请确认已运行 ollama serve。</p>',
                            unsafe_allow_html=True)
                st.session_state.msgs.pop()  # 撤回这条提问，便于重试
                st.stop()
            status_box.update(state="complete", expanded=False)
            if "replace" in box:  # 拒答重试/接地修正给出全文：整体替换
                text = box["replace"]
                answer_slot.markdown(fmt_answer(text), unsafe_allow_html=True)
            meta = box["meta"]
            hits = meta["hits"]
            render_sources(hits)
            render_steps(meta.get("steps"))
            with st.spinner("生成追问建议..."):
                fus = gen_followups(question, text)
            if fus:
                st.markdown('<p class="mt-eyebrow">接着问</p>',
                            unsafe_allow_html=True)
                cols = st.columns(len(fus))
                for j, fu in enumerate(fus):
                    with cols[j]:
                        st.button(fu, key=f"fu_now_{j}", on_click=ask_followup,
                                  args=(fu,))
        st.session_state.msgs.append(
            {"role": "assistant", "content": text, "hits": hits,
             "steps": meta.get("steps"), "followups": fus})

else:  # 对比模式
    st.markdown('<p class="mt-eyebrow">双年报对比</p>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        year_a = st.selectbox("年报 A", YEARS, index=len(YEARS) - 2)
    with c2:
        year_b = st.selectbox("年报 B", YEARS, index=len(YEARS) - 1)
    q = st.text_input("问题", "营业总收入是多少？主要经营情况如何？")
    if st.button("开始对比", type="primary") and q:
        a, b = st.columns(2)
        for col, year in ((a, year_a), (b, year_b)):
            with col:
                st.subheader(f"{year} 年报")
                try:
                    with st.spinner("检索并生成中..."):
                        meta = generate(q, year=year)
                except Exception as e:  # noqa: BLE001
                    st.error(f"生成失败：{e}")
                    continue
                st.markdown(fmt_answer(meta["answer"]), unsafe_allow_html=True)
                render_sources(meta["hits"])
