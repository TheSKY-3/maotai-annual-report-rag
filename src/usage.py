"""进程内 token 用量累计（评测/成本核算用）。

单线程顺序跑评测时用它统计一次运行的总消耗；多线程场景请自行加锁使用
（record 本身有锁，但 reset/snapshot 的语义是全局的）。
"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_TOTAL = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def record(resp=None, prompt_tokens: int | None = None,
           completion_tokens: int | None = None) -> None:
    p = c = 0
    if resp is not None:
        u = getattr(resp, "usage", None)
        if u is not None:
            p = int(getattr(u, "prompt_tokens", 0) or 0)
            c = int(getattr(u, "completion_tokens", 0) or 0)
    if prompt_tokens is not None:
        p = int(prompt_tokens)
    if completion_tokens is not None:
        c = int(completion_tokens)
    with _LOCK:
        _TOTAL["prompt_tokens"] += p
        _TOTAL["completion_tokens"] += c
        _TOTAL["calls"] += 1


def snapshot() -> dict:
    with _LOCK:
        return dict(_TOTAL)


def reset() -> None:
    with _LOCK:
        _TOTAL.update({"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
