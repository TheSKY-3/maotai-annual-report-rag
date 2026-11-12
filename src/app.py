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

from src import cite_locate, claims, followups
from src.ask import answer_auto, answer_auto_stream
from src.config import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GEN_MODEL,
                        OLLAMA_BASE_URL, PROJECT, YEARS)
from src.pipeline import get_search

CITE_CROP_DIR = PROJECT / "docs" / "citations" / "ui"

PRESET_QUESTIONS = [
    "2024年茅台营业总收入是多少？",
    "茅台2024年度分红方案是什么？",
    "2024年末国内经销商有多少家？",
    "i茅台渠道2024年收入是多少？",
    "2025年营业总收入比2022年增长了多少？",
    "五粮液2024年的营业收入是多少？",
]
CIRCLED = "①②③④⑤⑥⑦⑧⑨"

st.set_page_config(page_title="茅台年报问答助手", page_icon="📑", layout="wide")

# ---------------------------------------------------------------------------
# 视觉主题：「审计卷宗」—— 酱墨侧栏 + 暖纸正文 + 朱砂印章/酒液金强调。
# 配色取自标的物：酱色酒液、年报纸面、审计朱砂章、墨蓝批注；数字用等宽数位。
# （刻意去品牌化：不使用茅台商标与商标元素）
# ---------------------------------------------------------------------------
CSS = """
:root {
  --ink: #1E150E; --ink-2: #2B1D12; --ink-line: #46331F;
  --paper: #F4EEE1; --card: #FFFCF4; --wash: #EDE5D2;
  --line: #DFD4BC; --text: #241B12; --muted: #857761; --muted-dark: #B9A98F;
  --verm: #C03A1B; --verm-deep: #96290F; --verm-soft: #F8E5DC;
  --amber: #B9862A; --amber-soft: #F4E7CB;
  --blue: #2C5E86; --blue-soft: #E4EDF4;
  --green: #2E7D4C; --green-soft: #E2EFE4;
  --danger: #B3261E;
  --serif: "Source Han Serif SC", "Noto Serif SC", "STZhongsong", "SimSun", serif;
  --sans: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
}
.stApp, [data-testid="stApp"] {
  background:
    radial-gradient(1100px 420px at 85% -80px, rgba(185, 134, 42, .10), transparent 70%),
    radial-gradient(900px 380px at -120px 30%, rgba(192, 58, 27, .05), transparent 70%),
    var(--paper);
  font-family: var(--sans);
  color: var(--text);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stAppDeployButton"], [data-testid="stToolbarActions"],
[data-testid="stMainMenu"], [data-testid="stStatusWidget"] { display: none; }
::selection { background: var(--verm-soft); color: var(--verm-deep); }
* { scrollbar-width: thin; scrollbar-color: var(--amber) var(--wash); }
*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-track { background: var(--wash); }
*::-webkit-scrollbar-thumb {
  background: linear-gradient(var(--amber), #9A6E1F);
  border-radius: 8px; border: 2px solid var(--wash);
}
*::-webkit-scrollbar-thumb:hover { background: var(--verm); }

/* ---- 阅读宽度 ---- */
[data-testid="stMainBlockContainer"],
[data-testid="stBottomBlockContainer"] {
  max-width: 920px; margin-left: auto; margin-right: auto;
}

/* ---- 侧边栏：酱墨卷宗脊 ---- */
[data-testid="stSidebar"] {
  background: linear-gradient(178deg, var(--ink-2), var(--ink) 240px);
  border-right: 1px solid var(--ink-line);
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: 22px; }
[data-testid="stSidebar"] * { color: #EFE6D4; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] label {
  font-size: 12px; letter-spacing: .12em; color: var(--muted-dark);
}
.mt-side-brand { display: flex; align-items: center; gap: 11px; margin: 2px 0 6px; }
.mt-side-brand .seal {
  flex: 0 0 auto; width: 36px; height: 36px; border-radius: 9px;
  background: linear-gradient(160deg, #D24A24, var(--verm-deep));
  color: #FFF6E8; display: flex; align-items: center; justify-content: center;
  font-family: var(--serif); font-size: 20px; font-weight: 700;
  box-shadow: inset 0 0 0 2px rgba(255, 246, 232, .32), 0 3px 10px rgba(0, 0, 0, .35);
}
.mt-side-brand .name {
  font-family: var(--serif); font-size: 18px; font-weight: 700;
  letter-spacing: .04em; color: #F6EDDC;
}
.mt-side-sub { font-size: 11px; color: var(--muted-dark); letter-spacing: .18em;
  margin: 0 0 14px; }
.mt-pill {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: 11.5px; padding: 3px 11px; border-radius: 999px;
  border: 1px solid var(--ink-line); background: rgba(255, 246, 232, .06);
  color: #E9DFC9; margin: 0 0 12px;
}
.mt-pill .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green);
  box-shadow: 0 0 0 3px rgba(46, 125, 76, .25); }
.mt-pill.off .dot { background: var(--amber); box-shadow: 0 0 0 3px rgba(185, 134, 42, .25); }
[data-testid="stSidebar"] [data-testid="stSegmentedControl"] {
  background: rgba(255, 246, 232, .07); border: 1px solid var(--ink-line);
  border-radius: 11px; padding: 3px;
}
[data-testid="stSidebar"] button,
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] [data-baseweb="select"] > div {
  background-color: rgba(255, 246, 232, .07) !important;
  color: #F2E9D7 !important;
}
[data-testid="stSidebar"] [data-testid="stSegmentedControl"] button {
  background: transparent !important; color: var(--muted-dark) !important;
  border-radius: 8px; font-size: 12.5px;
}
[data-testid="stSidebar"] [data-testid="stSegmentedControl"] button[aria-checked="true"],
[data-testid="stSidebar"] [data-testid="stSegmentedControl"] button[data-selected="true"],
[data-testid="stSidebar"] [data-testid="stSegmentedControl"] button[kind="segmented_controlActive"] {
  background: linear-gradient(160deg, #D24A24, var(--verm-deep)) !important;
  color: #FFF6E8 !important;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input,
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] [data-testid="stTextInput"] > div,
[data-testid="stSidebar"] [data-testid="stTextInput"] > div > div {
  background: rgba(255, 246, 232, .07) !important;
  border: 1px solid var(--ink-line) !important;
  color: #F2E9D7 !important; border-radius: 10px !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div {
  background: rgba(255, 246, 232, .07) !important;
  border: 1px solid var(--ink-line) !important; border-radius: 10px !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] div {
  background: transparent !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] * {
  color: #F2E9D7 !important;
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div,
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div,
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div > div {
  background: rgba(255, 246, 232, .07) !important;
  border: 1px solid var(--ink-line) !important;
  color: #F2E9D7 !important; border-radius: 10px !important;
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] * {
  color: #F2E9D7 !important;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input::placeholder {
  color: #8D7C60 !important;
}
[data-testid="stSidebar"] [data-testid="stCheckbox"] label p { font-size: 12.5px; }
[data-testid="stSidebar"] [data-testid="baseButton-secondary"] {
  border-radius: 10px; border: 1px dashed var(--ink-line) !important;
  background: transparent !important; color: var(--muted-dark) !important;
  font-size: 12.5px;
}
[data-testid="stSidebar"] [data-testid="baseButton-secondary"]:hover {
  border-color: var(--amber) !important; color: var(--amber) !important;
}
[data-testid="stSidebar"] hr { margin: 14px 0; border-color: var(--ink-line); }
[data-testid="stSidebar"] *:focus-visible {
  outline-color: var(--amber) !important;
}
.mt-side-caption { font-size: 11px; color: var(--muted-dark); line-height: 1.75; }
.mt-eyebrow-dark {
  font-size: 11px; font-weight: 700; letter-spacing: .26em;
  color: var(--amber); margin: 4px 0 8px;
}

/* ---- 页首：朱砂印 + 宋体标题 + 金线 ---- */
.mt-header {
  display: flex; align-items: center; gap: 15px;
  padding: 8px 2px 16px; margin-bottom: 14px;
  border-bottom: 1px solid var(--line); position: relative;
}
.mt-header:after {
  content: ""; position: absolute; left: 2px; bottom: -1px;
  width: 96px; height: 3px;
  background: linear-gradient(90deg, var(--verm), var(--amber) 60%, transparent);
}
.mt-seal {
  flex: 0 0 auto; width: 50px; height: 50px; border-radius: 11px;
  background: linear-gradient(160deg, #D24A24, var(--verm-deep));
  color: #FFF6E8; display: flex; align-items: center; justify-content: center;
  font-family: var(--serif); font-size: 29px; font-weight: 700;
  box-shadow: inset 0 0 0 2px rgba(255, 246, 232, .32), 0 4px 12px rgba(150, 41, 15, .28);
  animation: mt-stamp .5s cubic-bezier(.2, 1.4, .4, 1) both;
}
@keyframes mt-stamp {
  from { transform: scale(1.35) rotate(-6deg); opacity: 0; }
  to { transform: scale(1) rotate(0); opacity: 1; }
}
.mt-title {
  font-family: var(--serif); font-size: 28px; font-weight: 700;
  letter-spacing: .03em; line-height: 1.15; color: var(--text);
}
.mt-tagline { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.mt-chip {
  font-size: 11.5px; letter-spacing: .06em; padding: 2px 10px;
  border-radius: 999px; border: 1px solid var(--line);
  background: var(--card); color: var(--muted);
}
.mt-chip.hot { border-color: #E4B9A6; background: var(--verm-soft); color: var(--verm-deep); }
.mt-chip.gold { border-color: #DCC28C; background: var(--amber-soft); color: #7A5714; }

/* ---- 卷宗封面带（空界面） ---- */
.mt-cover {
  display: flex; align-items: stretch; gap: 0; margin: 2px 0 18px;
  border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
  background: var(--card); box-shadow: 0 2px 14px rgba(30, 21, 14, .06);
}
.mt-cover .band {
  flex: 0 0 8px; background: linear-gradient(var(--verm), var(--amber));
}
.mt-cover .body { padding: 14px 18px; }
.mt-cover .k { font-family: var(--serif); font-size: 16px; font-weight: 700; }
.mt-cover .v { font-size: 12px; color: var(--muted); margin-top: 5px; line-height: 1.8; }
.mt-cover .v b { color: var(--verm-deep); font-weight: 700; }

/* ---- 小节眉标 ---- */
.mt-eyebrow {
  font-size: 12px; font-weight: 700; letter-spacing: .24em;
  color: var(--verm-deep); margin: 10px 0 10px;
}

/* ---- 聊天气泡 ---- */
[data-testid="stChatMessage"] { background: transparent; }
[data-testid="stChatMessageContent"] {
  background: var(--card); border: 1px solid var(--line);
  border-radius: 16px 16px 16px 4px; padding: 14px 20px;
  box-shadow: 0 2px 14px rgba(30, 21, 14, .06);
  font-size: 14.5px; line-height: 1.8;
  animation: mt-rise .35s ease both;
}
@keyframes mt-rise {
  from { transform: translateY(8px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  flex-direction: row-reverse;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {
  background: linear-gradient(135deg, var(--ink-2), var(--ink));
  border: 1px solid var(--ink-line); color: #F2E9D7;
  border-radius: 16px 16px 4px 16px;
  box-shadow: 0 3px 12px rgba(30, 21, 14, .25);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] p { color: #F2E9D7 !important; }
[data-testid="stChatMessageAvatarAssistant"] {
  background: linear-gradient(160deg, #D24A24, var(--verm-deep)); border-radius: 10px;
  box-shadow: inset 0 0 0 2px rgba(255, 246, 232, .32);
}
[data-testid="stChatMessageAvatarAssistant"] span,
[data-testid="stChatMessageAvatarAssistant"] svg {
  color: #FFF6E8 !important; fill: #FFF6E8;
}
[data-testid="stChatMessageAvatarUser"] {
  background: var(--wash); border: 1px solid var(--line); border-radius: 10px;
}

/* ---- 答案元信息条 / 拒答章 ---- */
.mt-meta { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 8px; }
.mt-stamp {
  display: inline-block; margin: 2px 0 8px; padding: 4px 14px;
  border: 2px solid var(--verm); border-radius: 6px; color: var(--verm-deep);
  font-family: var(--serif); font-weight: 700; font-size: 14px;
  letter-spacing: .18em; transform: rotate(-2deg);
  background: rgba(192, 58, 27, .06);
}

/* ---- 答案里的出处标注 tag ---- */
.mt-cite {
  display: inline-block; background: var(--blue-soft); color: var(--blue);
  border: 1px solid #BFD3E2; border-radius: 999px; padding: 0 10px;
  font-size: 12.5px; font-weight: 600; margin: 1px 4px 1px 2px;
  white-space: nowrap;
}

/* ---- 表格：财务数字右对齐 + 等宽数位 ---- */
[data-testid="stMarkdownContainer"] table, [data-testid="stTable"] table {
  border-collapse: collapse; width: 100%; font-size: 13px;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMarkdownContainer"] th, [data-testid="stTable"] th {
  background: var(--ink-2); color: #F2E9D7; font-weight: 600;
  padding: 7px 12px; text-align: left; font-size: 12.5px; letter-spacing: .04em;
}
[data-testid="stMarkdownContainer"] td, [data-testid="stTable"] td {
  border-bottom: 1px solid var(--line); padding: 6px 12px;
}
[data-testid="stMarkdownContainer"] td:not(:first-child),
[data-testid="stTable"] td:not(:first-child) { text-align: right; }
[data-testid="stMarkdownContainer"] tr:nth-child(even) td,
[data-testid="stTable"] tr:nth-child(even) td { background: var(--wash); }

/* ---- 证据档案卡 ---- */
[data-testid="stExpander"] {
  background: var(--card); border: 1px solid var(--line);
  border-left: 4px solid var(--amber); border-radius: 12px;
  box-shadow: 0 1px 4px rgba(30, 21, 14, .05);
  margin-bottom: 6px; overflow: hidden;
  transition: box-shadow .18s, border-color .18s;
}
[data-testid="stExpander"]:hover {
  box-shadow: 0 4px 14px rgba(150, 41, 15, .10); border-left-color: var(--verm);
}
[data-testid="stExpander"] summary {
  font-size: 13.5px; font-weight: 600; color: var(--text);
}
[data-testid="stExpander"] summary:hover { color: var(--verm-deep); }
[data-testid="stExpanderDetails"] { font-size: 13px; }
.mt-source-text {
  white-space: pre-wrap; color: #4A4034; line-height: 1.85; font-size: 12.5px;
  background: var(--paper); border: 1px dashed var(--line);
  border-radius: 8px; padding: 10px 12px; margin-top: 4px;
}
.mt-exhibit-tag {
  font-size: 11px; letter-spacing: .18em; font-weight: 700;
  color: var(--verm-deep); margin: 10px 0 4px;
}
[data-testid="stImage"] img {
  border: 1px solid var(--line); border-radius: 8px;
  box-shadow: 0 2px 10px rgba(30, 21, 14, .10); background: #fff;
}
[data-testid="stCaption"] { color: var(--muted); font-size: 11.5px; }

/* ---- Agent 轨迹 ---- */
.mt-tool {
  display: inline-block; font-family: Consolas, "Courier New", monospace;
  font-size: 12px; background: var(--amber-soft); color: #7A5714;
  border: 1px solid #DCC28C; border-radius: 6px; padding: 0 7px; margin-right: 6px;
}

/* ---- 候选问题卡（预设/追问统一） ---- */
[data-testid="stButton"] button {
  width: 100%; text-align: left; justify-content: flex-start;
  background: var(--card);
  color: var(--text); border: 1px solid var(--line);
  border-left: 3px solid var(--amber); border-radius: 12px;
  padding: 10px 15px; font-size: 13.5px; font-weight: 400;
  transition: border-color .16s, color .16s, background .16s,
              transform .16s, box-shadow .16s;
}
[data-testid="stButton"] button:hover {
  border-color: #E4B9A6; border-left-color: var(--verm);
  color: var(--verm-deep); background: var(--verm-soft);
  transform: translateY(-2px); box-shadow: 0 5px 14px rgba(150, 41, 15, .12);
}
[data-testid="stButton"] button:focus-visible {
  border-color: var(--verm); box-shadow: 0 0 0 3px rgba(192, 58, 27, .18);
}
[data-testid="stButton"] button p {
  font-size: 13.5px; text-align: left !important; margin: 0 !important;
  width: 100%;
}
[data-testid="stButton"] button > div,
[data-testid="stButton"] button > span {
  justify-content: flex-start !important; text-align: left !important;
  width: 100%;
}

/* ---- 输入框 ---- */
[data-testid="stBottom"] {
  background: linear-gradient(transparent, var(--paper) 32%);
}
[data-testid="stChatInput"] {
  border-radius: 14px; border: 1px solid var(--line);
  background: var(--card); box-shadow: 0 2px 16px rgba(30, 21, 14, .08);
}
[data-testid="stChatInput"]:focus-within {
  border-color: var(--verm); box-shadow: 0 0 0 3px rgba(192, 58, 27, .13);
}
[data-testid="stChatInputSubmitButton"] button {
  background: linear-gradient(160deg, #D24A24, var(--verm-deep));
  border-color: var(--verm-deep); border-radius: 10px;
}
[data-testid="stChatInputSubmitButton"] button:hover {
  background: var(--verm-deep); border-color: var(--verm-deep);
}

/* ---- 状态/异常/控件 ---- */
[data-testid="stStatus"] {
  background: var(--card); border: 1px solid var(--line);
  border-left: 4px solid var(--amber); border-radius: 12px;
}
[data-testid="stException"] {
  background: var(--verm-soft); border: 1px solid #E4B9A6;
  border-left: 4px solid var(--danger); border-radius: 12px;
}
[data-testid="stWidgetLabel"] p { color: var(--muted); font-size: 13px; }
h2, h3, [data-testid="stHeading"] { font-family: var(--serif); }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
"""
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

st.markdown(
    """
    <div class="mt-header">
      <div class="mt-seal">档</div>
      <div>
        <div class="mt-title">茅台年报问答</div>
        <div class="mt-tagline">
          <span class="mt-chip gold">贵州茅台 2022–2025 年报</span>
          <span class="mt-chip">答案必附原文页码出处</span>
          <span class="mt-chip hot">查不到就明说</span>
        </div>
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


_CITE_TAG_RE = re.compile(r"\[来源[:：]\s*([^\]]+)\]")


def fmt_answer(text: str) -> str:
    """答案里的 [来源: ...] 渲染成出处 tag，其余内容转义防注入。"""
    escaped = html.escape(text)
    return _CITE_TAG_RE.sub(r'<span class="mt-cite">📄 \1</span>', escaped)


def gen_followups(question: str, answer: str) -> list[str]:
    """生成 3 个追问建议；实现与 API 共用 src/followups.py。"""
    client, model = _resolve_backend()
    return followups.gen_followups(client, model, question, answer)


def render_sources(hits: list[dict], answer: str | None = None) -> None:
    spec = claims.citation_spec(answer) if answer else {"numbers": [], "pages": []}
    for i, h in enumerate(hits):
        badge = "页码直达" if h.get("direct") else (
            "最佳匹配" if i == 0 else "")
        label = f"证据 {i + 1} · {h['year']}年报 P{h['page']}"
        if badge:
            label += f" · {badge}"
        # 首条默认展开：坐标高亮是本产品的核验入口，不该藏在点击后面
        with st.expander(label, expanded=(i == 0)):
            body = html.escape(h["text"][:600])
            st.markdown(f'<div class="mt-source-text">{body}</div>',
                        unsafe_allow_html=True)
            crop = cite_locate.crop_for_source(h, spec, CITE_CROP_DIR)
            if crop:
                st.markdown('<p class="mt-exhibit-tag">坐标核验 · 红框 = 答案引用的数字</p>',
                            unsafe_allow_html=True)
                st.image(str(crop),
                         caption=f"{h['year']}年报 P{h['page']} 原文横带")


def render_steps(steps: list[dict]) -> None:
    """agent 路径的工具调用轨迹（Function Calling 过程可视化）。"""
    if not steps:
        return
    with st.expander(f"Agent 工具调用轨迹（{len(steps)} 步）"):
        lines = []
        for i, s in enumerate(steps, start=1):
            arg_str = "、".join(f"{k}: {v}" for k, v in s["args"].items())
            lines.append(f'{i}. <span class="mt-tool">{html.escape(s["tool"])}</span>'
                         f'{html.escape(arg_str)} → 取回 {s["n_hits"]} 个片段')
        st.markdown("<br>".join(lines), unsafe_allow_html=True)


def render_meta(route: str | None, refused: bool, n_hits: int) -> None:
    """答案脚注：路径徽章 + 证据数；拒答盖朱砂章。"""
    chips = []
    if route:
        label = "Agent 工具链" if route == "agent" else "直通检索"
        chips.append(f'<span class="mt-chip hot">{label}</span>')
    chips.append(f'<span class="mt-chip">{n_hits} 处证据</span>')
    st.markdown('<div class="mt-meta">' + "".join(chips) + "</div>",
                unsafe_allow_html=True)
    if refused:
        st.markdown('<div class="mt-stamp">查无披露 · 已拒答</div>',
                    unsafe_allow_html=True)


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
        '<div class="name">茅台年报问答</div></div>'
        '<p class="mt-side-sub">审计卷宗 · 2022–2025</p>',
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

    st.markdown('<p class="mt-eyebrow-dark">生成模型</p>', unsafe_allow_html=True)
    backend = st.segmented_control(
        "生成模型", ["云端 DeepSeek", "本地 Ollama"], key="backend",
        default="云端 DeepSeek"
        if (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("RAG_API_KEY"))
        else "本地 Ollama", label_visibility="collapsed")
    backend = backend or "云端 DeepSeek"
    if backend == "云端 DeepSeek":
        has_key = bool((st.session_state.get("byo_key") or "").strip()
                       or os.environ.get("DEEPSEEK_API_KEY")
                       or os.environ.get("RAG_API_KEY"))
        st.markdown(
            f'<span class="mt-pill{"" if has_key else " off"}"><span class="dot"></span>'
            f'{"DeepSeek 已连接" if has_key else "待填 Key · 当前不可用"}</span>',
            unsafe_allow_html=True)
        st.text_input("DeepSeek API Key", type="password", key="byo_key",
                      placeholder="sk-…（留空则用环境变量）",
                      help="粘贴你自己的 DeepSeek API Key。Key 只保存在当前"
                           "浏览器会话里，不会上传到任何第三方。")
        st.markdown('<p class="mt-side-caption">没有 Key？到 '
                    'platform.deepseek.com 申请，一次问答约 ¥0.001。</p>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<span class="mt-pill"><span class="dot"></span>'
                    '本地 Ollama · 离线零成本</span>', unsafe_allow_html=True)
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
                render_meta(m.get("route"), m.get("refused", False),
                            len(m.get("hits") or []))
            else:
                st.markdown(m["content"])
            if m.get("hits"):
                render_sources(m["hits"], m["content"])
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

    # ---- 空界面：卷宗封面 + 预设问题 ----
    if not st.session_state.msgs:
        st.markdown(
            '<div class="mt-cover"><div class="band"></div><div class="body">'
            '<div class="k">卷宗说明</div>'
            '<div class="v">语料：贵州茅台 2022–2025 四份年报 · <b>1,778</b> 个证据块<br>'
            '核验：答案数字定位到原文页框并高亮 · 留出集拒答 <b>15/15</b> 零编造</div>'
            '</div></div>',
            unsafe_allow_html=True)
        st.markdown('<p class="mt-eyebrow">试试这些问题</p>',
                    unsafe_allow_html=True)
        for row in range(0, len(PRESET_QUESTIONS), 2):
            cols = st.columns(2)
            for ci, q in enumerate(PRESET_QUESTIONS[row:row + 2]):
                cols[ci].button(f"{CIRCLED[row + ci]} {q}",
                                key=f"preset_{row + ci}",
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
            # write_stream 只渲染原始 markdown：流结束后重渲染以套用出处 pill
            answer_slot.markdown(fmt_answer(text), unsafe_allow_html=True)
            meta = box["meta"]
            hits = meta["hits"]
            render_meta(meta.get("route"), meta.get("refused", False), len(hits))
            render_sources(hits, text)
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
             "route": meta.get("route"), "refused": meta.get("refused", False),
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
                render_sources(meta["hits"], meta["answer"])
