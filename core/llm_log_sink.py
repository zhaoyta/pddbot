"""LLM 交互专用文件日志（仅 [LLM交互] / [LLM回调]）。

路径由 settings ``logging.llm_message_file`` 配置；空则默认
``<项目根>/logs/llm_message_{time:YYYYMMDD}.log``。

通过 loguru 独立 sink，与总日志 ``pddbot_*.log`` 分离。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from core import config
from core import settings as settings_mod

_HANDLER_ID: int | None = None


def _llm_only_filter(record: dict[str, Any]) -> bool:
    try:
        msg = record["message"]
    except Exception:
        return False
    return "[LLM交互]" in msg or "[LLM回调]" in msg


def exclude_llm_trace_filter(record: dict[str, Any]) -> bool:
    """供控制台 / GUI 日志等 sink 使用：不写 [LLM交互]、[LLM回调]（这两项仅进 llm_message 专用文件）。"""
    return not _llm_only_filter(record)


def default_llm_log_pattern() -> str:
    """与 GUI 占位符一致的默认相对路径（相对项目根）。"""
    return f"logs/llm_message_{{time:YYYYMMDD}}.log"


def resolved_llm_log_path_pattern() -> str:
    """返回传给 loguru 的路径字符串（可含 {time:YYYYMMDD}）。"""
    raw = settings_mod.get("logging.llm_message_file", "").strip()
    if not raw:
        return str(config.LOGS_DIR / "llm_message_{time:YYYYMMDD}.log")
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    return str(config.ROOT / raw)


def _ensure_parent_dir(path_pattern: str) -> None:
    base = path_pattern.split("{", 1)[0].strip()
    if not base:
        try:
            config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return
    p = Path(base)
    try:
        if base.endswith(("/", "\\")):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        try:
            config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


def configure_llm_log_sink() -> None:
    """挂载或重挂 LLM 文件 sink（修改路径后应再调一次）。"""
    global _HANDLER_ID
    if _HANDLER_ID is not None:
        try:
            logger.remove(_HANDLER_ID)
        except ValueError:
            pass
        _HANDLER_ID = None

    path_str = resolved_llm_log_path_pattern()
    _ensure_parent_dir(path_str)
    try:
        hid = logger.add(
            path_str,
            filter=_llm_only_filter,
            rotation="00:00",
            retention="14 days",
            level="INFO",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}\n",
        )
        _HANDLER_ID = hid
        logger.info("[llm_log_sink] 已写入 LLM 专用日志: {}", path_str)
    except Exception as e:
        logger.warning(
            "[llm_log_sink] 挂载失败: {} path={!r}", e, path_str,
        )
