"""数字抽取与接地校验。

Phase A 要点：
1. 按 token 比较而不是子串包含：12 与 1234 不会互相满足；
2. 单位归一：万元/亿元/元 归到元，% 归一成比例；
3. 容差：默认相对 0.2%，覆盖“四舍五入到两位小数”的正常写法。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# 金额单位（元为基准）
_MONEY = {
    "万亿元": Decimal("1e12"),
    "亿元": Decimal("1e8"),
    "百万元": Decimal("1e6"),
    "千万元": Decimal("1e7"),
    "千元": Decimal("1e3"),
    "万元": Decimal("1e4"),
    "亿": Decimal("1e8"),
    "万": Decimal("1e4"),
    "千": Decimal("1e3"),
    "元": Decimal("1"),
}
_UNITS = "|".join(sorted(_MONEY, key=len, reverse=True))

TOKEN_RE = re.compile(
    r"(?<![\d.])"
    r"(?P<sign>-)?"
    r"(?P<num>\d{1,3}(?:[,，]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>" + _UNITS + r"|%|％)?"
)

REL_TOL = Decimal("0.002")   # 相对容差 0.2%
ABS_TOL = Decimal("1e-9")
# 百分数用 0.1% 的相对容差（50% 附近约 0.05 个百分点）；金额的
# 0.2% 相对容差放到 50% 附近会放宽到 0.1 个百分点，把 51.9% 和
# 52.0% 误当同值。
PERCENT_TOL = Decimal("0.001")


@dataclass(frozen=True)
class Num:
    raw: str
    value: Decimal
    kind: str          # money | percent | plain
    unit: str
    start: int
    end: int


def _to_decimal(num: str) -> Decimal:
    return Decimal(num.replace(",", "").replace("，", ""))


def iter_numbers(text: str) -> list[Num]:
    out: list[Num] = []
    if not text:
        return out
    for m in TOKEN_RE.finditer(text):
        try:
            v = _to_decimal(m.group("num"))
        except InvalidOperation:
            continue
        if m.group("sign") == "-":
            v = -v
        unit = m.group("unit") or ""
        if unit in ("%", "％"):
            kind, value = "percent", v / Decimal(100)
        elif unit:
            kind, value = "money", v * _MONEY[unit]
        else:
            kind, value = "plain", v
        out.append(Num(m.group(0).strip(), value, kind, unit,
                       m.start(), m.end()))
    return out


def _close(a: Decimal, b: Decimal, rel_tol: Decimal = REL_TOL) -> bool:
    diff = abs(a - b)
    scale = max(abs(a), abs(b))
    return diff <= max(ABS_TOL, rel_tol * scale)


def same_value(a: Num, b: Num, rel_tol: Decimal = REL_TOL) -> bool:
    if a.kind == b.kind:
        if a.kind == "percent":
            rel_tol = min(rel_tol, PERCENT_TOL)
        return _close(a.value, b.value, rel_tol)
    if {a.kind, b.kind} == {"money", "plain"}:
        return _close(a.value, b.value, rel_tol)
    # 表格里的百分比列经常不带“%”（15.71），正文写“15.71%”，
    # 两者是同一个数；percent 已归一成 0.1571，和 plain 比要除以 100。
    if {a.kind, b.kind} == {"percent", "plain"}:
        p, q = (a, b) if a.kind == "percent" else (b, a)
        return _close(p.value, q.value / Decimal(100),
                      min(rel_tol, PERCENT_TOL))
    return False


def parse_token(raw: str) -> Num | None:
    s = raw.strip()
    if not s or not TOKEN_RE.fullmatch(s):
        return None
    found = iter_numbers(s)
    return found[0] if found else None


_DECL_UNIT_RE = re.compile(
    r"单位\s*[:：]\s*(万亿元|亿元|百万元|千万元|千元|万元|亿|万|千|元)")


def _declared_unit(text: str) -> str | None:
    """页面上唯一的“单位：X”声明；同页出现多种单位时不猜。"""
    units = {m.group(1) for m in _DECL_UNIT_RE.finditer(text)}
    return next(iter(units)) if len(units) == 1 else None


def find_number(raw: str, texts: list[str],
                rel_tol: Decimal = REL_TOL) -> bool:
    target = parse_token(raw)
    if target is None:
        return any(raw in t for t in texts)
    for t in texts:
        # 年报表格常在表头写“单位：万元”，单元格里只写 3,626,162.73。
        # 回答写“3,626,162.73 万元”时，裸数字要按表头单位还原后再比，
        # 否则每个表格金额都会被误报成“证据中找不到”。
        declared = _declared_unit(t) if target.kind == "money" else None
        for n in iter_numbers(t):
            if same_value(target, n, rel_tol):
                return True
            if declared and n.kind == "plain" and not n.unit:
                if _close(target.value, n.value * _MONEY[declared], rel_tol):
                    return True
    return False


def numbers_in_answer(text: str) -> list[str]:
    seen: list[str] = []
    for n in iter_numbers(text):
        if n.raw not in seen:
            seen.append(n.raw)
    return seen


def ungrounded(answer: str, cited_texts: list[str],
               skip: set[str] | None = None) -> list[str]:
    bad: list[str] = []
    for raw in numbers_in_answer(answer):
        if skip and raw in skip:
            continue
        if not find_number(raw, cited_texts):
            bad.append(raw)
    return bad
