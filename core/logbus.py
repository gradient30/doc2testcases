# -*- coding: utf-8 -*-
"""进程内实时日志总线：有界环形缓冲（线程安全），供 Web 日志面板消费。

性能约束：deque(maxlen) append O(1)、内存恒定、不落盘；无订阅者时零额外开销
（没有推送线程，消费方按需增量拉取）。进程重启缓冲即清空（任务历史在库里，不受影响）。
"""
from __future__ import annotations

import threading
import time
from collections import deque

LEVELS = ("info", "warn", "error")
_MAX_ENTRIES = 1000

_lock = threading.Lock()
_seq = 0
_buf: deque = deque(maxlen=_MAX_ENTRIES)


def log(level: str, tag: str, msg: str) -> None:
    """追加一条日志。level: info/warn/error；tag: 来源（jobs/llm/render/web）。"""
    global _seq
    if level not in LEVELS:
        level = "info"
    with _lock:
        _seq += 1
        _buf.append({"seq": _seq, "ts": round(time.time(), 3), "level": level,
                     "tag": tag, "msg": msg})


def tail(after_seq: int = 0, limit: int = 200) -> list[dict]:
    """取 seq 大于 after_seq 的日志（最多 limit 条，时间升序）。"""
    with _lock:
        out = [e for e in _buf if e["seq"] > after_seq]
    return out[-limit:]


def reset() -> None:
    """清空缓冲（测试用）。"""
    global _seq
    with _lock:
        _buf.clear()
        _seq = 0
