"""DeepSeek 调用成本估算（人民币）。

价格来自官方 Models & Pricing（2026-11）：
  deepseek-flash: 输入 cache miss  $0.15 / 1M（非高峰），$0.30 / 1M（高峰）
                  输出              $0.6  / 1M（非高峰），$1.2  / 1M（高峰）
高峰时段：UTC 周一至周五 01:00-04:00、06:00-10:00；其余为非高峰。
汇率按 7.1 CNY/USD 估算（可在环境变量覆盖）。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

USD_CNY = float(os.environ.get("RAG_USD_CNY", "7.1"))
IN_MISS_PEAK = 0.30      # USD / 1M tokens
OUT_PEAK = 1.20
IN_MISS_OFF = 0.15
OUT_OFF = 0.60


def is_peak(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    h = now.hour
    return 1 <= h < 4 or 6 <= h < 10


def estimate_cny(prompt_tokens: int, completion_tokens: int,
                 peak: bool | None = None) -> float:
    if peak is None:
        peak = is_peak()
    p_in = IN_MISS_PEAK if peak else IN_MISS_OFF
    p_out = OUT_PEAK if peak else OUT_OFF
    usd = (prompt_tokens * p_in + completion_tokens * p_out) / 1_000_000
    return round(usd * USD_CNY, 6)
