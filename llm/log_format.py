"""LLM 日志：截断长文本，供 runner 与 callback 共用。"""
from __future__ import annotations

# 与 runner 中注入用户消息、模型回复的日志上限一致思路
DEFAULT_MAX_IN = 12000
DEFAULT_MAX_OUT = 8000


def clip_llm_log(text: str | None, max_len: int) -> str:
    """截断用于日志落盘的长文本。"""
    if text is None:
        return ""
    t = str(text)
    if len(t) <= max_len:
        return t
    return t[:max_len] + f"\n…(截断,总长 {len(t)} 字符)"
