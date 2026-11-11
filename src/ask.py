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

import json
import os
import re
import sys

from openai import BadRequestError, OpenAI

from src import claims as structured_claims
from src import metric_tools, numeric, report_scope, usage
from src.config import GEN_MODEL, OLLAMA_BASE_URL, RETRY_TOP_K, TOP_K, YEARS
from src.hybrid import detect_years
from src.pipeline import get_page_reader, get_search

PROMPT_TEMPLATE = """你是贵州茅台年报问答助手。请仅根据下面的年报片段回答问题。

要求：
1. 回答末尾标注出处，格式如：[来源: 2024年报 P8]。可以引用多个片段。
2. 如果片段中没有足够信息，只回答"根据年报内容无法回答该问题"，不要编造，也不要标注来源。
3. 数字必须与片段中完全一致，不要自行计算或换算。
4. 财务数据默认合并报表口径、优先正文经营情况披露：标注“母公司报表”的片段，除非问题明确问母公司，否则不用其数字；标注“报表附注”的片段与正文数字不一致时，优先正文。
5. 表格的“合计”“小计”“总计”行通常在表格末行，是各分项的汇总值；问到合计/总额/共计时，取该行数字作答，不要因分项已列出就拒答合计。
6. 折旧/摊销类问题须核对表格的资产类别表头：固定资产、使用权资产、投资性房地产、无形资产是不同资产，各自的“累计折旧/本期增加/计提”数字不可混用；问固定资产折旧取“固定资产”附注表的计提数，不得用“使用权资产”等其他资产表的折旧数。

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
5. 财务数据默认合并报表口径、优先正文经营情况披露：标注“母公司报表”的片段，除非问题明确问母公司，否则不用其数字；标注“报表附注”的片段与正文数字不一致时，优先正文。
6. 表格的“合计”“小计”“总计”行通常在表格末行，是各分项的汇总值；问到合计/总额/共计时，取该行数字作答，不要因分项已列出就拒答合计。
7. 折旧/摊销类问题须核对表格的资产类别表头：固定资产、使用权资产、投资性房地产、无形资产是不同资产，各自的“累计折旧/本期增加/计提”数字不可混用；问固定资产折旧取“固定资产”附注表的计提数，不得用“使用权资产”等其他资产表的折旧数。

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
        usage.record(resp)
        out = (resp.choices[0].message.content or "").strip()
        if not out:
            # 思考模式偶发未被 /no_think 关闭：配额被思考吃尽导致正文为空，
            # 翻倍配额重试一次（E5 语境句生成时已验证该现象）
            resp = client.chat.completions.create(
                model=model, messages=messages,
                temperature=0.0, max_tokens=1800)
            usage.record(resp)
            out = (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        return question
    out = out.splitlines()[0].strip().strip("`\"'“” ") if out else ""
    if 4 <= len(out) <= 60 and not out.startswith("/"):
        return out
    return question


def build_context(hits: list[dict]) -> str:
    def source(h: dict) -> str:
        # 指标汇总这类合成片段可能由多页构成，用显式 source 覆盖单页标签。
        return str(h.get("source") or f"{h['year']}年报 P{h['page']}")
    parts = []
    for i, h in enumerate(hits):
        head = f"[片段{i + 1} | 来源: {source(h)}"
        # 单页检索片段才打口径标签；合成片段（显式 source、跨多页）跳过。
        if not h.get("source"):
            tag = report_scope.scope_tag(
                report_scope.page_scope(h["year"], h["page"]))
            if tag:
                head += f" | {tag}"
        parts.append(f"{head}]\n{h['text']}")
    return "\n\n".join(parts)


def _scoped_search(search_fn, question: str):
    """包装检索函数：默认合并口径，硬剔母公司报表证据块。

    在证据进入任何构建器（build_context / claims.build_evidence）之前过滤，
    故对直答、结构化、agent 各路径一致生效。问题明确问母公司时不过滤；
    页码直达走 page_fn，不经此包装，用户显式意图不受影响。
    """
    def wrapped(query, **kw):
        return report_scope.filter_default_scope(search_fn(query, **kw), question)
    return wrapped


def _complete(client: OpenAI, model: str, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    usage.record(resp)
    return resp.choices[0].message.content or ""


def retry_enabled() -> bool:
    return os.environ.get("RAG_REFUSAL_RETRY", "1") != "0"


def grounding_enabled() -> bool:
    return os.environ.get("RAG_GROUNDING", "1") != "0"


def structured_enabled() -> bool:
    return os.environ.get("RAG_STRUCTURED", "1") != "0"


def _structured_answer(client: OpenAI, search_fn, standalone: str,
                       model: str, skw: dict
                       ) -> tuple[structured_claims.StructuredAnswer, list[dict]]:
    """检索 + 结构化生成；拒答时扩检索重试一次（与 E6 语义对齐）。"""
    hits = search_fn(standalone, top_k=TOP_K, **skw)
    hits = hits + structured_claims.adjacent_page_hits(hits)
    ev = structured_claims.build_evidence(hits)
    pages = structured_claims.page_map()
    ans = structured_claims.generate(client, model, standalone, ev, pages=pages)
    if ans.refused and retry_enabled():
        more = search_fn(standalone, top_k=RETRY_TOP_K, **skw)
        more = more + structured_claims.adjacent_page_hits(more)
        ans2 = structured_claims.generate(
            client, model, standalone, structured_claims.build_evidence(more),
            pages=pages)
        if not ans2.refused:
            return ans2, more
        return ans, hits
    return ans, hits


def _structured_meta(question: str, standalone: str,
                     ans: structured_claims.StructuredAnswer,
                     hits: list[dict], route: str = "direct") -> dict:
    meta = _meta(question, standalone, ans.text, hits, route)
    meta.update({
        "refused": ans.refused,
        "structured": True,
        "claims": ans.claims,
        "citations": ans.citations,
        "violations": ans.violations,
        "degraded": ans.degraded,
        "calc_note": ans.calc_note,
        "calls": ans.calls,
        "usage": ans.usage,
    })
    return meta


def _agent_meta(question: str, standalone: str, text: str,
                hits: list[dict], steps: list[dict]) -> dict:
    """agent 自由文本逐句归属证据：出处由程序渲染，找不到的数字记违规。"""
    attributed, cites, missing = structured_claims.attribute_text(
        text, hits, skip={str(y) for y in YEARS},
        pages=structured_claims.page_map())
    seen: list[str] = []
    for n in missing:
        if n not in seen:
            seen.append(n)
    meta = _meta(question, standalone, attributed, hits, "agent", steps)
    meta.update({
        "structured": False,
        "citations": cites,
        "violations": [f"数字在检索证据中找不到：{n}" for n in seen],
        "degraded": False,
    })
    return meta



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


def _grounding_fix(client: OpenAI, model: str, text: str,
                   hits: list[dict]) -> str:
    """E8 数字接地校验：答案里有语境中找不到的数字时，对照原文修正一次。

    直通管线与 agent 路径共用（RAG_GROUNDING=0 关）。
    """
    if grounding_enabled() and text:
        bad = ungrounded_numbers(text, build_context(hits))
        if bad:
            fixed = _complete(
                client, model,
                GROUNDING_PROMPT_TEMPLATE.format(
                    bad_nums="\n".join(f"- {n}" for n in bad),
                    context=build_context(hits), answer=text))
            if fixed:
                # 模型有时把提示词尾部的"修正后的回答："标签原样带回，剥掉
                return re.sub(r"^\s*修正后的回答[:：]\s*", "", fixed)
    return text


def _answer_core(client: OpenAI, search_fn, question: str, model: str,
                 search_kwargs: dict | None = None,
                 history: list[dict] | None = None,
                 standalone: str | None = None
                 ) -> tuple[str, list[dict], str]:
    """检索+生成一问；返回 (答案, 实际使用的片段, 改写后的独立问题)。

    search_kwargs 传给检索函数的附加参数（如 {"year": 2024}），重试时保持一致。
    多轮：history 非空时先把追问改写成独立问题再检索（多轮记忆）；
    传入 standalone（answer_auto 已改写）则跳过，避免重复改写调用。
    E6：首轮命中拒答语义时，扩检索宽松重问一次（RAG_REFUSAL_RETRY=0 关）。
    E8：见 _grounding_fix（RAG_GROUNDING=0 关）。
    """
    skw = search_kwargs or {}
    standalone = standalone or rewrite_followup(
        client, model, history or [], question)
    search_fn = _scoped_search(search_fn, standalone)
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
            return (_grounding_fix(
                        client, model, retry_text,
                        hits if REFUSE_RE.search(retry_text) else more),
                    more if not REFUSE_RE.search(retry_text) else hits,
                    standalone)
    return _grounding_fix(client, model, text, hits), hits, standalone


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


def _meta(question: str, standalone: str, text: str, hits: list[dict],
          route: str = "direct", steps: list[dict] | None = None) -> dict:
    """统一的答案元信息（API/界面/评测共用）。

    hits 为全文片段（界面渲染原文用）；sources 是截断版（API 响应用）。
    """
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
        "hits": hits,
        "route": route,
        "steps": steps or [],
    }


def answer_with_meta(client: OpenAI, search_fn, question: str, model: str,
                     search_kwargs: dict | None = None,
                     history: list[dict] | None = None) -> dict:
    """同 answer_question，但返回 API 友好的完整元信息（含拒答判定与出处）。"""
    text, hits, standalone = _answer_core(client, search_fn, question, model,
                                          search_kwargs, history)
    return _meta(question, standalone, text, hits)


# ---------------------------------------------------------------------------
# Agentic RAG：跨年对比 / 页码直达题路由到 Function Calling 工具循环。
# agent 循环成本 3-10 倍、延迟 2-5 倍（NVIDIA《Traditional RAG vs Agentic
# RAG》、arXiv 2601.07711），故只在路由命中时启用，简单题保留直通管线。
# ---------------------------------------------------------------------------

AGENT_MAX_ROUNDS = 4

AGENT_SYSTEM_PROMPT = """你是贵州茅台年报问答助手，可以通过工具查询 2022–2025 年报。

工作方式：
1. 标准财务/运营指标（营收、净利润、ROE、EPS、销量、经销商、员工等）先用 lookup_metric 按年份查；跨年比较用 compare_metric，它们直接返回指标值和年报页；
2. 其它单一年份的问题用 search_reports 检索；
3. 跨年份比较非标准指标（“A年和B年分别是多少”“增长了多少”）必须用 compare_years，确保每个年份的证据都拿到；
4. 用户给出页码（如 P6、第6页）时用 page_direct 直读该页；
5. 最多 4 轮工具调用，证据足够后立即停止调用并作答。

作答要求：
1. 只依据工具返回的片段作答，回答末尾标注出处，格式如：[来源: 2024年报 P8]。可以引用多个片段。
2. 数字必须与片段中完全一致，不要换算；若由片段数字计算得出（如相减求增长额），写出算式并在结果后注明“（计算得出）”，例如“46,412.95 + 36,919.81 = 83,332.76 吨（计算得出）”。
3. 若工具返回的片段都不足以回答，只回答“根据年报内容无法回答该问题”，不要编造，也不要标注来源。
4. 财务数据默认合并报表口径、优先正文经营情况披露：标注“母公司报表”的片段，除非问题明确问母公司，否则不用其数字；标注“报表附注”的片段与正文数字不一致时，优先正文。
5. 表格的“合计”“小计”“总计”行通常在表格末行，是各分项的汇总值；问到合计/总额/共计时，取该行数字作答，不要因分项已列出就拒答合计。
6. 折旧/摊销类问题须核对表格的资产类别表头：固定资产、使用权资产、投资性房地产、无形资产是不同资产，各自的“累计折旧/本期增加/计提”数字不可混用；问固定资产折旧取“固定资产”附注表的计提数，不得用“使用权资产”等其他资产表的折旧数。"""


def _tool_schema(name: str, description: str, properties: dict,
                 required: list[str]) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object",
                                        "properties": properties,
                                        "required": required}}}

_AGENT_YEARS = {"type": "integer", "enum": YEARS, "description": "年报年份"}

AGENT_TOOLS = [
    _tool_schema(
        "search_reports",
        "在茅台 2022-2025 年报中检索最相关的原文片段。query 用中文指标关键词"
        "（如“直销渠道收入”），需要限定年份时传 year。",
        {"query": {"type": "string", "description": "检索关键词或短句"},
         "year": {**_AGENT_YEARS, "description": "限定年报年份，可选"}},
        ["query"]),
    _tool_schema(
        "page_direct",
        "直接读取某一年年报某页的完整内容。用户明确给出页码"
        "（P6、第6页）或需要通读某页时使用。",
        {"year": _AGENT_YEARS,
         "page": {"type": "integer", "description": "页码"}},
        ["year", "page"]),
    _tool_schema(
        "compare_years",
        "跨年对比专用：把同一指标在多个年份分别检索后合并返回，保证每个年份"
        "的证据都在场。非标准指标、或检索召回不稳时使用。",
        {"query": {"type": "string",
                   "description": "指标关键词，如“经营活动产生的现金流量净额”"},
         "years": {"type": "array",
                   "items": {"type": "integer", "enum": YEARS},
                   "description": "要对比的年份列表"}},
        ["query", "years"]),
    _tool_schema(
        "lookup_metric",
        "查询标准财务/运营指标在某一年的值及年报页。支持营收、净利润、扣非"
        "净利润、经营现金流、净资产、总资产、股本、EPS、ROE、茅台酒/系列酒"
        "收入与销量、直销/批发收入、经销商、员工等。year 省略时返回全部年份。",
        {"metric": {"type": "string",
                    "description": "标准指标名，如“营业总收入”“扣非净利润”"},
         "year": {**_AGENT_YEARS, "description": "限定年份，可选"}},
        ["metric"]),
    _tool_schema(
        "compare_metric",
        "跨年比较标准指标：返回各年值并给出增减额和增幅（均标注“计算得出”）。"
        "metric 同上；years 省略时比较全部年份。",
        {"metric": {"type": "string",
                    "description": "标准指标名，如“营业总收入”“归母净利润”"},
         "years": {"type": "array",
                   "items": {"type": "integer", "enum": YEARS},
                   "description": "要对比的年份列表，可选"}},
        ["metric"]),
]


def _valid_year(y) -> int | None:
    """模型给的工具参数先校验再信（官方明示 arguments 可能幻觉）。"""
    try:
        y = int(y)
    except (TypeError, ValueError):
        return None
    return y if y in YEARS else None


def _exec_tool(name: str, args: dict, search_fn, page_fn,
               locked_year: int | None) -> list[dict]:
    """执行工具调用并返回片段列表。

    locked_year 是用户/API 显式限定的年份（search_kwargs），优先于模型
    给的 year 参数——用户锁了范围，agent 不许越界。
    """
    if name == "search_reports":
        query = str(args.get("query") or "").strip()
        if not query:
            return []
        year = locked_year if locked_year is not None \
            else _valid_year(args.get("year"))
        return search_fn(query, top_k=TOP_K, year=year)
    if name == "page_direct":
        year = locked_year if locked_year is not None \
            else _valid_year(args.get("year"))
        try:
            page = int(args.get("page"))
        except (TypeError, ValueError):
            return []
        if year is None or not 1 <= page <= 999:
            return []
        return page_fn(year, page)
    if name == "compare_years":
        query = str(args.get("query") or "").strip()
        if not query:
            return []
        years = [locked_year] if locked_year is not None else []
        if not years:
            for y in args.get("years") or []:
                y = _valid_year(y)
                if y is not None and y not in years:
                    years.append(y)
        if not years:  # 模型没给年份：从问题里兜底识别
            years = [y for y in detect_years(query) if y in YEARS]
        merged: list[dict] = []
        for y in years:
            merged.extend(search_fn(query, top_k=TOP_K, year=y))
        return merged
    if name == "lookup_metric":
        metric = str(args.get("metric") or "").strip()
        if not metric:
            return []
        year = locked_year if locked_year is not None \
            else _valid_year(args.get("year"))
        return metric_tools.lookup_metric(metric, year)
    if name == "compare_metric":
        metric = str(args.get("metric") or "").strip()
        if not metric:
            return []
        years = [locked_year] if locked_year is not None else []
        if not years:
            for y in args.get("years") or []:
                y = _valid_year(y)
                if y is not None and y not in years:
                    years.append(y)
        return metric_tools.compare_metric(metric, years or None)
    return []


# 对比意图词：配合年份出现才算跨年题（“增长了多少家经销商”是单年事实题）
_COMPARE_RE = re.compile(
    r"增长|下降|增加|减少|变化|相比|对比|比较|涨幅|跌幅|增幅|降幅|同比|环比"
    r"|多多少|少多少|高多少|低多少")
# 无需显式两个年份的跨年指代（“同比增长”隐含与上年比）
_CROSS_YEAR_RE = re.compile(r"同比|环比|比去年|比上年|上一年度?|去年同期")
_PAGE_REF_RE = re.compile(r"[PpＰ]\s*\d{1,3}|第\s*\d{1,3}\s*页")
# 多指标组合题（“A是多少？B是多少？”“分别是多少”）：一次检索的 top-5
# 装不下两个指标的片段（E8 基线 cross_page 型 answer_correct 仅 0.2 的根因），
# 需要分指标多轮检索
_MULTI_METRIC_RE = re.compile(r"分别是|各是|各占|又是多少")


def needs_agent(question: str) -> bool:
    """零成本路由：跨年对比 / 页码直达 / 多指标组合题走 agent，其余直通。"""
    years = {y for y in detect_years(question) if y in YEARS}
    if len(years) >= 2:
        return True
    if _COMPARE_RE.search(question) and (years
                                         or _CROSS_YEAR_RE.search(question)):
        return True
    if _PAGE_REF_RE.search(question):
        return True
    if question.count("？") + question.count("?") >= 2:
        return True
    return bool(_MULTI_METRIC_RE.search(question))


def _agent_call(client: OpenAI, model: str, messages, **kw):
    """agent 单轮调用。思考模式必须显式关闭：与 tools 同用时不回传
    reasoning_content 会直接 400（官方文档），也顺带避开思考吃配额
    导致正文为空的老坑；空正文翻倍配额重试一次（项目红线口径）。"""
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=0.1,
        max_tokens=900, extra_body={"thinking": {"type": "disabled"}}, **kw)
    usage.record(resp)
    msg = resp.choices[0].message
    if not (msg.content or "").strip() and not msg.tool_calls:
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.1,
            max_tokens=1800,
            extra_body={"thinking": {"type": "disabled"}}, **kw)
        usage.record(resp)
        msg = resp.choices[0].message
    return msg


def _agent_loop(client: OpenAI, search_fn, page_fn, standalone: str,
                model: str, locked_year: int | None
                ) -> tuple[str, list[dict], list[dict]]:
    """工具调用循环；返回 (答案, 证据片段池, 工具调用轨迹)。"""
    call = lambda messages, **kw: _agent_call(client, model, messages, **kw)

    messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": standalone}]
    steps: list[dict] = []
    pool: list[dict] = []  # 全部工具返回的证据（去重后），供接地校验与出处
    seen: set[str] = set()
    final_text = ""

    for _ in range(AGENT_MAX_ROUNDS):
        msg = call(messages, tools=AGENT_TOOLS)
        if not msg.tool_calls:
            final_text = msg.content or ""
            break
        messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            err = ""
            try:
                hits = _exec_tool(name, args, search_fn, page_fn, locked_year)
            except Exception as e:  # noqa: BLE001
                hits, err = [], str(e)
            steps.append({"tool": name, "args": args, "n_hits": len(hits)})
            for h in hits:
                if h["id"] not in seen:
                    seen.add(h["id"])
                    pool.append(h)
            content = build_context(hits)
            if not content:
                content = f"工具执行失败：{err}" if err else "（未检索到相关片段）"
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": content})

    if not final_text:  # 轮次用尽仍在调工具：撤掉 tools 强制收口作答
        final_text = call(messages).content or ""
    return _grounding_fix(client, model, final_text, pool), pool[:12], steps


def answer_auto(client: OpenAI, search_fn, question: str, model: str,
                search_kwargs: dict | None = None,
                history: list[dict] | None = None,
                force_agent: bool = False) -> dict:
    """路由入口：跨年对比/页码直达题走 agent 工具循环，其余走直通管线。

    answer_question / answer_with_meta 的行为不变（评测与旧调用兼容）；
    API、界面与 --router 评测走本入口。追问先改写成独立问题再路由
    （“那2023年呢”改写后才看得出年份）。
    """
    standalone = rewrite_followup(client, model, history or [], question)
    search_fn = _scoped_search(search_fn, standalone)
    skw = search_kwargs or {}
    if force_agent or needs_agent(standalone):
        locked_year = skw.get("year")
        page_fn = get_page_reader()
        text, hits, steps = _agent_loop(client, search_fn, page_fn,
                                        standalone, model, locked_year)
        return _agent_meta(question, standalone, text, hits, steps)
    if structured_enabled():
        ans, hits = _structured_answer(client, search_fn, standalone, model, skw)
        return _structured_meta(question, standalone, ans, hits)
    text, hits, standalone = _answer_core(client, search_fn, question, model,
                                          search_kwargs, history,
                                          standalone=standalone)
    return _meta(question, standalone, text, hits)


def answer_auto_stream(client: OpenAI, search_fn, question: str, model: str,
                       search_kwargs: dict | None = None,
                       history: list[dict] | None = None,
                       force_agent: bool = False):
    """answer_auto 的流式版：逐个产出事件字典，最后 yield done（含完整 meta）。

    事件类型：
      {"type": "status", "label": str}  阶段进展（检索中/工具调用/重试/校验）
      {"type": "delta",  "text": str}   答案增量（直通路径逐 token）
      {"type": "replace", "text": str}  用完整文本替换已流出内容
                                        （E6 拒答重试 / E8 接地修正时）
      {"type": "done",   "meta": dict}  结束；meta 与 answer_auto 返回值同构

    直通路径真实逐 token 流式；E6/E8 语义与 _answer_core 逐条对应。
    agent 路径按工具轮次推送 status、最终答案整段返回——工具循环行为与
    评测过的 _agent_loop 完全一致，不为流式改动它（改了就得重新评测）。
    """
    if structured_enabled():
        yield {"type": "status", "label": "检索并核对证据中…"}
        meta = answer_auto(client, search_fn, question, model, search_kwargs,
                           history, force_agent)
        yield {"type": "status",
               "label": f"已核对 {len(meta.get('citations') or [])} 处出处"}
        yield {"type": "delta", "text": meta["answer"]}
        yield {"type": "done", "meta": meta}
        return

    def emit(t, **kw):
        return dict(type=t, **kw)

    def _stream_call(prompt, max_tokens=None):
        """流式生成一次；边收边 yield delta 事件，yield from 取回全文。"""
        collected = []
        kwargs = dict(model=model,
                      messages=[{"role": "user", "content": prompt}],
                      temperature=0.1, stream=True,
                      stream_options={"include_usage": True})
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        try:
            stream = client.chat.completions.create(**kwargs)
        except BadRequestError:
            # 兼容不支持 stream_options 的 OpenAI 兼容端点：去掉后重试一次。
            kwargs.pop("stream_options", None)
            stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage.record(
                    prompt_tokens=int(getattr(u, "prompt_tokens", 0) or 0),
                    completion_tokens=int(
                        getattr(u, "completion_tokens", 0) or 0))
            if chunk.choices:
                d = chunk.choices[0].delta.content
                if d:
                    collected.append(d)
                    yield emit("delta", text=d)
        return "".join(collected)

    standalone = rewrite_followup(client, model, history or [], question)
    search_fn = _scoped_search(search_fn, standalone)

    # ---- agent 路径：轮次状态 + 整段答案 ----
    if force_agent or needs_agent(standalone):
        yield emit("status", label="Agent 已拆解问题，开始工具调用…")
        locked_year = (search_kwargs or {}).get("year")
        page_fn = get_page_reader()
        messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT},
                    {"role": "user", "content": standalone}]
        steps: list[dict] = []
        pool: list[dict] = []
        seen: set[str] = set()
        text = ""
        for _ in range(AGENT_MAX_ROUNDS):
            msg = _agent_call(client, model, messages, tools=AGENT_TOOLS)
            if not msg.tool_calls:
                text = msg.content or ""
                break
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name,
                                             "arguments": tc.function.arguments}}
                               for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                try:
                    hits = _exec_tool(name, args, search_fn, page_fn,
                                      locked_year)
                except Exception:  # noqa: BLE001
                    hits = []
                steps.append({"tool": name, "args": args,
                              "n_hits": len(hits)})
                for h in hits:
                    if h["id"] not in seen:
                        seen.add(h["id"])
                        pool.append(h)
                arg_str = "、".join(f"{k}: {v}" for k, v in args.items())
                yield emit("status", label=f"调用 {name}（{arg_str}），"
                                           f"取回 {len(hits)} 个片段")
                content = build_context(hits) or "（未检索到相关片段）"
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": content})
        if not text:
            text = _agent_call(client, model, messages).content or ""
        yield emit("status", label="数字接地校验…")
        capped = pool[:12]
        fixed = _grounding_fix(client, model, text, capped)
        if fixed != text:
            yield emit("replace", text=fixed)
            text = fixed
        yield emit("done", meta=_meta(question, standalone, text,
                                      capped, "agent", steps))
        return

    # ---- 直通路径：逐 token 流式（E6/E8 语义与 _answer_core 一致）----
    skw = search_kwargs or {}
    yield emit("status", label="检索年报…")
    hits = search_fn(standalone, top_k=TOP_K, **skw)
    yield emit("status", label=f"检索到 {len(hits)} 个片段，开始生成…")
    prompt = PROMPT_TEMPLATE.format(context=build_context(hits),
                                    question=standalone)
    text = yield from _stream_call(prompt)
    if not text:
        text = yield from _stream_call(prompt, max_tokens=1800)

    hits_used = hits
    if retry_enabled() and REFUSE_RE.search(text):
        yield emit("status", label="首轮疑似拒答，扩充检索重问一次…")
        more = search_fn(standalone, top_k=RETRY_TOP_K, **skw)
        retry_prompt = RETRY_PROMPT_TEMPLATE.format(
            context=build_context(more), question=standalone)
        retry_text = yield from _stream_call(retry_prompt)
        if retry_text and not REFUSE_RE.search(retry_text):
            hits_used = more
            text = retry_text
            yield emit("replace", text=retry_text)

    yield emit("status", label="数字接地校验…")
    fixed = _grounding_fix(client, model, text, hits_used)
    if fixed != text:
        yield emit("replace", text=fixed)
        text = fixed
    yield emit("done", meta=_meta(question, standalone, text,
                                  hits_used, "direct"))


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
