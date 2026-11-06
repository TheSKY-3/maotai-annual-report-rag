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
        return _close(a.value, b.value, rel_tol)
    if {a.kind, b.kind} == {"money", "plain"}:
        return _close(a.value, b.value, rel_tol)
    return False


def parse_token(raw: str) -> Num | None:
    s = raw.strip()
    if not s or not TOKEN_RE.fullmatch(s):
        return None
    found = iter_numbers(s)
    return found[0] if found else None


def find_number(raw: str, texts: list[str],
                rel_tol: Decimal = REL_TOL) -> bool:
    target = parse_token(raw)
    if target is None:
        return any(raw in t for t in texts)
    for t in texts:
        for n in iter_numbers(t):
            if same_value(target, n, rel_tol):
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
