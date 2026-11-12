"""结构化答案：claims + evidence + 程序校验 + 程序渲染出处。

契约（模型只输出 JSON，不写页码）：
{
  "refused": false,
  "claims": [{"text": "...", "evidence": ["E1"], "numbers": ["1,741.44"], "kind": "extract"}],
  "calculation": {"expr": "(a-b)/b*100", "value": "17.90", "evidence": ["E1"]}
}
页码由本模块从 evidence 渲染，模型无法伪造；calculation.evidence 标出成分数字
所在片段，派生句的出处由这些片段确定。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from src import calc, numeric, usage
from src.config import YEARS

_TICK = chr(96)
_FENCE_RE = re.compile(_TICK + r"{3}(?:json)?\s*|\s*" + _TICK + r"{3}")

STRUCTURED_SYSTEM_PROMPT = """你是贵州茅台年报问答助手。只能依据下方证据片段回答，并输出 JSON（不要输出任何其他文字）。

JSON 结构：
{
  "refused": false,
  "claims": [
    {"text": "一句结论", "evidence": ["E1"], "numbers": ["1,741.44"], "kind": "extract"}
  ],
  "calculation": null
}

规则：
1. 每条 claim 必须引用至少一个 evidence id（如 E1）；禁止引用不存在的 id；
2. claim 里出现的每个数字都必须原样来自它所引用的片段，不要自己换算单位；
3. 需要四则运算时，把表达式放进 calculation：expr 用片段里的原始数字，value 写计算结果，evidence 列出这些原始数字所在的片段 id（派生句的出处由此确定）；
4. 片段不足以回答时输出 {"refused": true, "claims": [], "calculation": null}；
5. 不要写“[来源: …]”，出处由程序按 evidence 渲染；
6. 只输出 JSON，不要解释、不要 Markdown 代码块；
7. 问题里有多个问点时，每个问点至少给一条 claim（宁可多一条，不要漏问）。
8. 表格的“合计”“小计”“总计”行通常在表格末行，是各分项的汇总值；问到合计/总额/共计时，取该行数字作答，不要因分项已列出就拒答合计。
9. 折旧/摊销类问题须核对表格的资产类别表头：固定资产、使用权资产、投资性房地产、无形资产是不同资产，各自的“累计折旧/本期增加/计提”数字不可混用；问固定资产折旧取“固定资产”附注表的计提数，不得用“使用权资产”等其他资产表的折旧数。"""

STRUCTURED_RETRY_SUFFIX = """

上一次输出无法解析或未通过校验：{issues}
请修正后重新输出完整 JSON（只输出 JSON）。"""

_CITE_RE = re.compile(r"\[来源[^\]]*\]")
_YEAR_RE = re.compile(r"(20\d{2})\s*年")
_PAGE_RE = re.compile(r"P\s*(\d{1,3})")
_CALC_MARK_RE = re.compile(r"（计算得出）|\(计算得出\)")


@dataclass
class Evidence:
    eid: str
    year: int
    page: int
    text: str
    score: float = 0.0

    @property
    def citation(self) -> str:
        return f"{self.year}年报 P{self.page}"


@dataclass
class StructuredAnswer:
    refused: bool
    text: str
    claims: list[dict] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    degraded: bool = False
    calc_note: str = ""
    usage: dict = field(default_factory=dict)
    raw: str = ""
    calls: int = 0


def raw_text(hit: dict) -> str:
    """去掉语境句，只留原文（JSONL 里 context 字段单独存在）。"""
    text = hit.get("text") or ""
    ctx = hit.get("context") or ""
    if ctx and text.startswith(ctx):
        text = text[len(ctx):].lstrip("\n")
    return text


_PAGE_MAP: dict[str, str] | None = None


def page_map() -> dict[str, str]:
    """整页原文（年/页 -> 该页全部片段拼接），用于核对“这一页有没有这个数”。

    检索只取回 500 字片段，表格常被切到同一页的另一块；出处写的是“页”，
    所以校验也应看整页，而不是只看被检索到的那一块。首次调用读 JSONL。
    """
    global _PAGE_MAP
    if _PAGE_MAP is None:
        from src.config import CONTEXTUAL_CHUNKS_FILE
        m: dict[str, str] = {}
        try:
            with open(CONTEXTUAL_CHUNKS_FILE, encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    key = f"{r['year']}年报 P{r['page']}"
                    m[key] = m.get(key, "") + "\n" + raw_text(r)
        except OSError:  # 没有整页原文时退回片段级校验，不阻断问答
            m = {}
        _PAGE_MAP = m
    return _PAGE_MAP


def build_evidence(hits: list[dict]) -> list[Evidence]:
    out: list[Evidence] = []
    seen: set[str] = set()
    for h in hits:
        hid = str(h.get("id") or f"{h.get('year')}-p{h.get('page')}")
        if hid in seen:
            continue
        seen.add(hid)
        out.append(Evidence(eid=f"E{len(out) + 1}", year=int(h["year"]),
                            page=int(h["page"]), text=raw_text(h),
                            score=float(h.get("score") or 0.0)))
    return out


def build_evidence_text(evidence: list[Evidence]) -> str:
    return "\n\n".join(f"[{e.eid}] {e.citation}\n{e.text}" for e in evidence)


def adjacent_page_hits(hits: list[dict]) -> list[dict]:
    """为每个命中页补上下一页（P+1）整页原文，跨页表格才读得全。

    只增强答案层证据，不改检索命中（hit@5 不受影响）；下一页已在命中里
    或整页映射中不存在则跳过；只补 P+1，不连锁补 P+2。
    """
    pages = {(int(h["year"]), int(h["page"])) for h in hits}
    pm = page_map()
    out: list[dict] = []
    for year, page in sorted(pages):
        nxt = page + 1
        if (year, nxt) in pages:
            continue
        text = pm.get(f"{year}年报 P{nxt}")
        if not text:
            continue
        out.append({"id": f"{year}-p{nxt:03d}-adj", "year": year,
                    "page": nxt, "text": text.strip(), "score": 0.0})
    return out


def _parse_json(raw: str) -> dict | None:
    s = _FENCE_RE.sub("", raw or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None
        try:
            obj = json.loads(s[i:j + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def parse_citation_pages(ref: str) -> list[tuple[int, int]]:
    """解析一段 [来源: 2024年报 P9、P16；2025年报 P6] 里的 (年, 页)。"""
    pairs: list[tuple[int, int]] = []
    cur: int | None = None
    for m in re.finditer(r"(20\d{2})\s*年|P\s*(\d{1,3})", ref):
        if m.group(1):
            cur = int(m.group(1))
        elif cur is not None:
            pairs.append((cur, int(m.group(2))))
    return pairs


_LIST_MARK_RE = re.compile(r"(?m)^\s*\d{1,2}[.、)]\s*")


def citation_spec(answer: str) -> dict:
    """答案里待坐标核验的指标数字与出处页。

    剥掉出处标注（其中的年份/页码不是答案数字）与行首列举序号；
    年份本身按接地校验同一约定跳过。
    """
    pages = parse_citation_pages(answer)
    body = _LIST_MARK_RE.sub("", _CITE_RE.sub("", answer))
    skip = {str(y) for y in YEARS}
    numbers = [n for n in numeric.numbers_in_answer(body) if n not in skip]
    return {"numbers": numbers, "pages": pages}


def validate(obj: dict, evidence: list[Evidence], *,
             extra_texts: list[str] | None = None,
             check_numbers: bool = True,
             pages: dict[str, str] | None = None,
             calc_value: str = "",
             calc_evidence: list[str] | None = None,
             skip_numbers: set[str] | None = None
             ) -> tuple[list[dict], list[str], list[str]]:
    by_id = {e.eid: e for e in evidence}
    claims = obj.get("claims") or []
    violations: list[str] = []
    clean: list[dict] = []
    cites: list[str] = []
    extra = extra_texts or []
    skip = skip_numbers or set()
    calc_evs = [e for e in (calc_evidence or []) if e in by_id]
    if not isinstance(claims, list):
        return [], ["claims 不是数组"], []
    for i, c in enumerate(claims, 1):
        if not isinstance(c, dict):
            violations.append(f"claim {i} 不是对象")
            continue
        text = str(c.get("text") or "").strip()
        evs = [str(x) for x in (c.get("evidence") or []) if str(x).strip()]
        if not text:
            violations.append(f"claim {i} 为空")
            continue
        unknown = [e for e in evs if e not in by_id]
        if unknown:
            violations.append(f"claim {i} 引用了不存在的证据 {unknown}")
            continue
        # 派生句：claim 里出现计算器算出的值，就把表达式的来源一并算作出处。
        derived = bool(calc_value) and any(
            numeric.find_number(n, [calc_value])
            for n in numeric.numbers_in_answer(text))
        if derived:
            if calc_evs:
                evs = list(calc_evs) + [e for e in evs if e not in calc_evs]
            else:
                violations.append(
                    f"claim {i} 含计算结果但 calculation 未提供 evidence")
        if not evs:
            violations.append(f"claim {i} 没有证据")
            continue
        item = {"text": text, "evidence": evs,
                "numbers": [str(n) for n in (c.get("numbers") or [])],
                "kind": "calculation" if derived else str(c.get("kind") or "extract")}
        if derived:
            item["calc"] = True
        clean.append(item)
        for e in evs:
            cit = by_id[e].citation
            if cit not in cites:
                cites.append(cit)
        if check_numbers:
            cited_texts = [(pages or {}).get(by_id[e].citation)
                           or by_id[e].text for e in evs] + extra
            nums = numeric.numbers_in_answer(_PAGE_NOISE_RE.sub("", text))
            nums = [n for n in nums if n not in skip]
            bad = [n for n in nums if not numeric.find_number(n, cited_texts)]
            if bad:
                violations.append(f"claim {i} 的数字在证据中找不到：{bad}")
    return clean, violations, cites


def render(claims: list[dict], evidence: list[Evidence], *,
           refused: bool = False, calc_note: str = "",
           calc_cites: list[str] | None = None,
           degraded: bool = False) -> tuple[str, list[str]]:
    by_id = {e.eid: e for e in evidence}
    if refused:
        return "根据年报内容无法回答该问题。", []
    lines: list[str] = []
    cites: list[str] = []
    for i, c in enumerate(claims, 1):
        text = _CITE_RE.sub("", c["text"]).strip()
        pages: list[str] = []
        for e in c["evidence"]:
            cit = by_id[e].citation
            if cit not in pages:
                pages.append(cit)
            if cit not in cites:
                cites.append(cit)
        suffix = f" [来源: {'；'.join(pages)}]" if pages else ""
        lines.append(f"{i}. {text}{suffix}")
    if calc_note:
        extra = f" [来源: {'；'.join(calc_cites)}]" if calc_cites else ""
        lines.append(f"\n计算过程：{calc_note}{extra}")
        for cit in calc_cites or []:
            if cit not in cites:
                cites.append(cit)
    if degraded:
        lines.append("\n（部分内容未通过自动核对，请以原文为准。）")
    return "\n".join(lines), cites


def _usage_of(resp) -> tuple[dict, int]:
    u = getattr(resp, "usage", None)
    if not u:
        return {"prompt_tokens": 0, "completion_tokens": 0}, 1
    return ({"prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
             "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0)}, 1)


def _call(client, model: str, messages: list[dict], max_tokens: int):
    resp = client.chat.completions.create(
        model=model, messages=messages,
        temperature=0,          # 结构化生成要可复现，关掉采样随机性
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        extra_body={"thinking": {"type": "disabled"}},
    )
    usage.record(resp)
    return resp


def _resolve_calculation(obj: dict, evidence: list[Evidence],
                          pages: dict[str, str] | None
                          ) -> tuple[str, list[str], list[str], list[str]]:
    """解析 calculation：程序求值、校验成分来源。返回 (note, extra, 成分id, 错误)。"""
    note = ""
    extra: list[str] = []
    calc_evs: list[str] = []
    errors: list[str] = []
    cal = obj.get("calculation")
    if not isinstance(cal, dict) or not cal.get("expr"):
        return note, extra, calc_evs, errors
    by_id = {e.eid: e for e in evidence}
    try:
        val_s, _ = calc.calculate(str(cal["expr"]))
    except calc.CalcError as e:
        return "", [], [], [f"计算表达式无效：{e}"]
    extra = [val_s]
    note = f"{cal['expr']} = {val_s}"
    stated = str(cal.get("value") or "").strip()
    if stated and not numeric.find_number(stated, [val_s]):
        note += f"（模型原写 {stated}，已以计算器为准）"

    ids = [str(x) for x in (cal.get("evidence") or []) if str(x).strip()]
    unknown = [e for e in ids if e not in by_id]
    if unknown:
        errors.append(f"calculation 引用了不存在的证据 {unknown}")
    calc_evs = [e for e in ids if e in by_id]
    if not calc_evs:
        errors.append("calculation 没有 evidence，派生句无法给出来源页")
    else:
        cited = [(pages or {}).get(by_id[e].citation) or by_id[e].text
                 for e in calc_evs]
        lits = [x for x in re.findall(r"\d[\d,，]*(?:\.\d+)?", str(cal["expr"]))
                if x.replace(",", "").replace("，", "") not in ("", "1", "0", "100")]
        missing = [x for x in lits if not numeric.find_number(x, cited)]
        if missing:
            errors.append(f"calculation 的数字不在成分证据中 {missing}")
    return note, extra, calc_evs, errors


def generate(client, model: str, question: str, evidence: list[Evidence],
             *, max_tokens: int = 1200, repair: bool = True,
             pages: dict[str, str] | None = None) -> StructuredAnswer:
    """一次结构化生成（解析失败/校验不过各重试一次）。"""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    calls = 0
    user = f"问题：{question}\n\n证据：\n{build_evidence_text(evidence)}"
    messages = [{"role": "system", "content": STRUCTURED_SYSTEM_PROMPT},
                {"role": "user", "content": user}]
    resp = _call(client, model, messages, max_tokens)
    calls += 1
    u, n = _usage_of(resp)
    usage["prompt_tokens"] += u["prompt_tokens"]
    usage["completion_tokens"] += u["completion_tokens"]
    raw = (resp.choices[0].message.content or "").strip()
    obj = _parse_json(raw)
    issues: list[str] = []
    calc_note = ""
    extra: list[str] = []
    calc_evs: list[str] = []
    calc_errors: list[str] = []
    if obj is None:
        issues = ["输出不是合法 JSON"]
    else:
        calc_note, extra, calc_evs, calc_errors = _resolve_calculation(
            obj, evidence, pages)
        issues = calc_errors + validate(
            obj, evidence, pages=pages, extra_texts=extra,
            calc_value=extra[0] if extra else "",
            calc_evidence=calc_evs,
            skip_numbers={str(y) for y in YEARS})[1]

    if (obj is None or issues) and repair:
        messages2 = messages + [
            {"role": "assistant", "content": raw or " "},
            {"role": "user", "content": STRUCTURED_RETRY_SUFFIX.format(
                issues="；".join(issues[:5]) or "未知")},
        ]
        resp = _call(client, model, messages2, max(max_tokens, 1600))
        calls += 1
        u, _ = _usage_of(resp)
        usage["prompt_tokens"] += u["prompt_tokens"]
        usage["completion_tokens"] += u["completion_tokens"]
        raw = (resp.choices[0].message.content or "").strip()
        obj2 = _parse_json(raw)
        if obj2 is not None:
            obj = obj2

    if obj is None:
        return StructuredAnswer(refused=False, text="", evidence=evidence,
                                violations=["输出无法解析为 JSON"], degraded=True,
                                usage=usage, raw=raw, calls=calls)

    calc_note, extra, calc_evs, calc_errors = _resolve_calculation(
        obj, evidence, pages)
    claims, violations, cites = validate(
        obj, evidence, extra_texts=extra, pages=pages,
        calc_value=extra[0] if extra else "", calc_evidence=calc_evs,
        skip_numbers={str(y) for y in YEARS})
    violations = calc_errors + violations
    refused = bool(obj.get("refused"))
    if refused and claims:
        violations.append("refused 为真但同时给了 claims")
        refused = False
    if not claims and not refused:
        violations.append("既未拒答也没有 claims")

    degraded = bool(violations and claims)
    if not claims:
        refused = True
    by_id = {e.eid: e for e in evidence}
    calc_cites: list[str] = []
    for e in calc_evs:
        cit = by_id[e].citation
        if cit not in calc_cites:
            calc_cites.append(cit)
    text, cites = render(claims, evidence, refused=refused,
                         calc_note=calc_note, calc_cites=calc_cites,
                         degraded=degraded)
    return StructuredAnswer(refused=refused, text=text, claims=claims,
                            evidence=evidence, violations=violations,
                            citations=cites, degraded=degraded,
                            calc_note=calc_note, usage=usage, raw=raw,
                            calls=calls)


# ---- 自由文本答案的逐句出处归属（agent 路径） ----

_CALC_NUM_RE = re.compile(
    r"[-+]?\d[\d,，]*(?:\.\d+)?[^\d，。；\n]{0,12}?"
    r"[（(]\s*计算得出\s*[）)]")
_UNIT_SPLIT_RE = re.compile(r"(?<=[。！？；\n])")
_PAGE_NOISE_RE = re.compile(r"[PpＰ]\s*\d{1,3}|第\s*\d{1,3}\s*页")


def _close_dec(a, b, rel=None) -> bool:
    rel = rel if rel is not None else numeric.REL_TOL
    diff = abs(a - b)
    return diff <= max(numeric.ABS_TOL, rel * max(abs(a), abs(b)))


def _year_like(n: numeric.Num) -> bool:
    """形如 2022—2025 的裸年份不是计算成分，不能拿来反推出处。"""
    if n is None:
        return False
    return (n.kind == "plain" and not n.unit
            and n.value == n.value.to_integral_value()
            and 1900 <= n.value <= 2100
            and len(n.raw) == 4)


def _calc_match(a, b, d) -> bool:
    """a、b 能否通过四则运算得到 d（差额/合计/倍数/百分比）。"""
    if _close_dec(a - b, d) or _close_dec(a + b, d):
        return True
    if b and (_close_dec(a / b, d) or _close_dec((a - b) / b, d)):
        return True
    return False


def _infer_calc_sources(unit: str, body_plain: str,
                        page_text: dict[str, str], preferred: set[str],
                        max_pages: int) -> list[str]:
    """给只含“（计算得出）”数字的句子反推成分页。

    做法：从整段答案里找能通过四则运算得到该派生值的数字，再回证据页
    定位这些成分数字。找不到时返回空列表，由调用方回退到上一句出处。
    """
    derived = [n for m in _CALC_NUM_RE.finditer(unit)
               for n in numeric.iter_numbers(m.group(0))]
    if not derived:
        return []
    seen: set[str] = set()
    candidates: list[numeric.Num] = []
    for n in numeric.iter_numbers(body_plain):
        if n.raw in seen or _year_like(n):
            continue
        seen.add(n.raw)
        candidates.append(n)
    operands: list[numeric.Num] = []
    for d in derived:
        for a in candidates:
            if _close_dec(a.value, d.value):      # 单位换算，如 万元→亿元
                operands.append(a)
        for i, a in enumerate(candidates):
            for b in candidates[i + 1:]:
                # 两个操作数都得是“同一量级”的项，否则 2024+大数≈大数 会被误判
                lo, hi = sorted((abs(a.value), abs(b.value)))
                if hi and lo < hi * Decimal("0.01"):
                    continue
                if _calc_match(a.value, b.value, d.value) or \
                        _calc_match(b.value, a.value, d.value):
                    operands.extend([a, b])
    pages: list[str] = []
    scored = []
    for rank, (cit, ptext) in enumerate(page_text.items()):
        hit = [n for n in operands if numeric.find_number(n.raw, [ptext])]
        if hit:
            scored.append((len(hit), cit in preferred, -rank, cit))
    if not scored:
        return []
    top = max(s[0] for s in scored)
    for _, _, _, cit in sorted((s for s in scored if s[0] == top),
                               key=lambda s: (s[0], s[1], s[2]),
                               reverse=True):
        if len(pages) >= max_pages:
            break
        pages.append(cit)
    return pages


def attribute_text(text: str, hits: list[dict], *,
                   skip: set[str] | None = None, max_pages: int = 3,
                   pages: dict[str, str] | None = None
                   ) -> tuple[str, list[str], list[str]]:
    """把自由文本逐句对到证据页：程序加出处，找不到的数字记为违规。

    以「页」为单位匹配（同一页的多个片段合并）。一句话里的数字可能来自不同
    页（如“2024 年 A；2023 年 B”），所以按“覆盖最多数字”逐页挑选，最多
    max_pages 页，拼成 [来源: 页1；页2]。普通句子并列时按检索 rank 取页，
    模型自己写的 [来源: ...] 只用于派生句反推时的同分提示；最终出处由本函数渲染。
    返回 (新文本, 出处列表, 未能在证据里定位的数字)。
    """
    skip = skip or set()
    ev = build_evidence(hits)
    by_page: dict[str, list[Evidence]] = {}
    for e in ev:
        by_page.setdefault(e.citation, []).append(e)
    page_text = {c: (pages or {}).get(c) or "\n".join(x.text for x in xs)
                 for c, xs in by_page.items()}

    preferred: set[str] = set()
    for ref in _CITE_RE.findall(text or ""):
        for y, p in parse_citation_pages(ref):
            cit = f"{y}年报 P{p}"
            if cit in by_page:
                preferred.add(cit)

    body = _CITE_RE.sub("", text or "")
    body_plain = _CALC_NUM_RE.sub("", body)
    out: list[str] = []
    cites: list[str] = []
    missing: list[str] = []
    last_chosen: list[str] = []
    for unit in _UNIT_SPLIT_RE.split(body):
        if not unit.strip():
            continue
        plain = _PAGE_NOISE_RE.sub("", unit)
        plain = _CALC_NUM_RE.sub("", plain)
        nums = [n for n in numeric.numbers_in_answer(plain)
                if n not in skip and not _year_like(numeric.parse_token(n))]
        remaining = list(nums)
        chosen: list[str] = []
        while remaining and len(chosen) < max_pages:
            scored = []
            for rank, (cit, ptext) in enumerate(page_text.items()):
                if cit in chosen:
                    continue
                hit = [n for n in remaining if numeric.find_number(n, [ptext])]
                if hit:
                    scored.append((len(hit), -rank, cit))
            if not scored:
                break
            # 多页覆盖相同数字且覆盖数并列时，把这些候选页都标出来。
            # 年报经常在“管理层讨论”和“财务报表附注”重复同一数字，
            # 只取检索排名第一页会漏掉金标人工选的那一页；并列页都
            # 能在原文中找到这些数字，标出来不算编造。
            top = max(s[0] for s in scored)
            tied = sorted((s for s in scored if s[0] == top),
                          key=lambda s: s[1], reverse=True)
            for _, _, cit in tied:
                if len(chosen) >= max_pages:
                    break
                if cit not in chosen:
                    chosen.append(cit)
            covered = "\n".join(page_text[c] for c in chosen)
            remaining = [n for n in remaining
                         if not numeric.find_number(n, [covered])]
        # 只有派生数字的句子：用成分数字反推页；推不出就沿用上一句出处，
        # 避免出现“计算得出”却没有任何来源的句子。
        if not chosen and _CALC_MARK_RE.search(unit):
            chosen = _infer_calc_sources(unit, body_plain, page_text,
                                         preferred, max_pages)
            if not chosen:
                chosen = list(last_chosen[:max_pages])
        if chosen:
            last_chosen = chosen
            for cit in chosen:
                if cit not in cites:
                    cites.append(cit)
            out.append(unit.rstrip() + f" [来源: {'；'.join(chosen)}]")
            missing += remaining
        else:
            out.append(unit)
            missing += nums
            # 有“计算得出”却连成分数字都找不到：不能静默当无出处通过，
            # 记为待核，提醒调用方这句派生结论没有被验证。
            if _CALC_MARK_RE.search(unit):
                missing.append("（计算得出）")
    return "".join(out), cites, missing


def reconcile_text(text: str, hits: list[dict]) -> tuple[str, list[str], list[str]]:
    """对自由文本答案做出处对账：返回 (清理后文本, 违规引用, 有效引用)。"""
    valid = {(int(h["year"]), int(h["page"])) for h in hits}
    bad: list[str] = []
    good: list[str] = []
    cleaned = text or ""
    for ref in _CITE_RE.findall(cleaned):
        pairs = parse_citation_pages(ref)
        if pairs and all(p in valid for p in pairs):
            good.append(ref)
            continue
        bad.append(ref)
        cleaned = cleaned.replace(ref, "", 1)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, bad, good
