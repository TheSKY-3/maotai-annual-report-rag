"""标准指标表：按指标名/别名+年份直达年报页，供 agent 工具使用。

指标值来自 data/metrics.json，逐项标注“哪一年年报第几页”。工具只返回
命中页的合成片段（含年份、页、值），随后由 build_context 渲染出处；
agent 的出处校验会回查整页原文，因此值必须真实存在于该页。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from src.config import PROJECT

METRICS_FILE = PROJECT / "data" / "metrics.json"

_PUNCT_RE = re.compile(r"[\s\u3000（）()【】\[\]“”\"'·、,，。；;：:%-]+")


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", str(text or "")).lower()


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        with open(METRICS_FILE, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    metrics = obj.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def catalog() -> dict[str, dict]:
    """返回全部指标定义（只读）。"""
    return _data()


def metric_keys() -> list[str]:
    return list(_data().keys())


def resolve_metric(name: str) -> tuple[str, dict] | None:
    """把用户/模型写的指标名归一到 (key, definition)。

    先精确匹配 key/label/alias；再按“最长包含”匹配，避免“净利润”抢走
    “扣非净利润”。找不到返回 None。
    """
    q = _norm(name)
    if not q:
        return None
    best: tuple[int, str, dict] | None = None
    for key, m in _data().items():
        names = [key, m.get("label", ""), *(m.get("aliases") or [])]
        for cand in names:
            c = _norm(cand)
            if not c:
                continue
            if q == c:
                return key, m
            if c in q and (best is None or len(c) > best[0]):
                best = (len(c), key, m)
    return (best[1], best[2]) if best else None


def _years(m: dict) -> list[int]:
    out = []
    for y in (m.get("values") or {}):
        try:
            out.append(int(y))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _dec(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").replace("，", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _fmt(value: Decimal, unit: str) -> str:
    if unit in ("股", "个", "人"):
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _hit(key: str, m: dict, year: int) -> dict | None:
    item = (m.get("values") or {}).get(str(year))
    if not isinstance(item, dict) or "value" not in item:
        return None
    value = str(item["value"])
    unit = m.get("unit") or ""
    return {
        "id": f"metric:{key}:{year}",
        "year": year,
        "page": int(item["page"]),
        "score": 1.0,
        "text": f"{year}年{m.get('label') or key}：{value} {unit}".strip(),
    }


def lookup_metric(metric: str, year: int | None = None) -> list[dict]:
    """按指标名查一年或全部年份；找不到指标/年份返回 []。"""
    found = resolve_metric(metric)
    if not found:
        return []
    key, m = found
    if year is not None:
        try:
            y = int(year)
        except (TypeError, ValueError):
            return []
        hit = _hit(key, m, y)
        return [hit] if hit else []
    return [h for h in (_hit(key, m, y) for y in _years(m)) if h]


def compare_metric(metric: str, years: list[int] | None = None) -> list[dict]:
    """查多个年份并追加一条增减汇总（差额/增幅均标注“计算得出”）。"""
    found = resolve_metric(metric)
    if not found:
        return []
    key, m = found
    allowed = _years(m)
    if years:
        wanted: list[int] = []
        for y in years:
            try:
                y = int(y)
            except (TypeError, ValueError):
                continue
            if y in allowed and y not in wanted:
                wanted.append(y)
        wanted.sort()
    else:
        wanted = allowed
    hits = [h for h in (_hit(key, m, y) for y in wanted) if h]
    if len(hits) < 2:
        return hits

    first, last = hits[0], hits[-1]
    v0 = _dec((m["values"][str(first["year"])])["value"])
    v1 = _dec((m["values"][str(last["year"])])["value"])
    if v0 is None or v1 is None:
        return hits
    unit = m.get("unit") or ""
    label = m.get("label") or key
    delta = v1 - v0
    pct = (delta / v0 * 100) if v0 else Decimal(0)
    direction = "增加" if delta >= 0 else "减少"
    rate_word = "增幅" if delta >= 0 else "降幅"

    if unit == "%":
        change = f"{abs(delta):,.2f} 个百分点"
        summary = (f"{first['year']}年到{last['year']}年{label}："
                   f"{v0:,.2f}% → {v1:,.2f}%，{direction} {change}（计算得出），"
                   f"{rate_word} {abs(pct):,.2f}%（计算得出）。")
    else:
        change = f"{_fmt(abs(delta), unit)} {unit}".strip()
        summary = (f"{first['year']}年到{last['year']}年{label}："
                   f"{_fmt(v0, unit)} {unit} → {_fmt(v1, unit)} {unit}，"
                   f"{direction} {change}（计算得出），"
                   f"{rate_word} {abs(pct):,.2f}%（计算得出）。")

    sources = "；".join(f"{h['year']}年报 P{h['page']}" for h in hits)
    hits.append({
        "id": f"metric:{key}:compare:{first['year']}-{last['year']}",
        "year": last["year"],
        "page": last["page"],
        "source": sources,
        "score": 1.0,
        "text": summary,
    })
    return hits
