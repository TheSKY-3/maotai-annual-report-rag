"""进程内 token 用量累计（评测/成本核算用）。

单线程顺序跑评测时用它统计一次运行的总消耗；多线程场景请自行加锁使用
（record 本身有锁，但 reset/snapshot 的语义是全局的）。
"""
from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager

_LOCK = threading.Lock()
_TOTAL = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
# 单次请求的局部累计：API 并发时全局计数会混入别的请求，用 contextvar
# 让 record() 同时写入当前请求的桶。
_LOCAL = contextvars.ContextVar("usage_local", default=None)


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
    local = _LOCAL.get()
    if local is not None:
        local["prompt_tokens"] += p
        local["completion_tokens"] += c
        local["calls"] += 1


@contextmanager
def collect():
    """收集 with 块内的 token 用量（同时仍计入全局累计）。

    不用 ContextVar.reset(token)：SSE 生成器可能在另一个 Context 里
    恢复执行，token 会因“创建于不同 Context”而 reset 失败；直接恢复
    旧值即可，当前 Context 结束后自然丢弃。
    """
    bucket = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    previous = _LOCAL.get()
    _LOCAL.set(bucket)
    try:
        yield bucket
    finally:
        _LOCAL.set(previous)


def copy(bucket: dict | None) -> dict:
    """返回桶的快照；None 视为空。"""
    empty = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    return dict(bucket) if bucket is not None else empty


def snapshot() -> dict:
    with _LOCK:
        return dict(_TOTAL)


def reset() -> None:
    with _LOCK:
        _TOTAL.update({"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
