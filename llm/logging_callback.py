"""LangChain 回调：记录每一次 Chat 模型调用（含 Agent 多轮 tool 循环中的每一跳）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from loguru import logger

from .log_format import DEFAULT_MAX_IN, DEFAULT_MAX_OUT, clip_llm_log


def _format_message_batch(messages: list[list[BaseMessage]]) -> str:
    lines: list[str] = []
    for batch in messages:
        for m in batch:
            name = type(m).__name__
            content: Any = getattr(m, "content", "")
            if isinstance(content, list):
                content = str(content)
            lines.append(f"{name}: {str(content)}")
    return "\n".join(lines)


def _generations_text(response: LLMResult) -> str:
    parts: list[str] = []
    for gen_list in response.generations:
        for gen in gen_list:
            msg = getattr(gen, "message", None)
            if msg is not None:
                c = getattr(msg, "content", "")
                parts.append(str(c))
            else:
                parts.append(getattr(gen, "text", "") or "")
    return "\n---\n".join(parts) if parts else ""


class PddChatModelLogger(AsyncCallbackHandler):
    """在每次 chat model start/end 写日志，便于区分 Agent 内多轮模型调用。"""

    def __init__(self, *, stage: str, uid: str | None) -> None:
        super().__init__()
        self._stage = stage
        self._uid = uid or ""
        self._round = 0

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self._round += 1
        model_id = ""
        try:
            model_id = (serialized.get("kwargs") or {}).get("model") or serialized.get("id", [])
            if isinstance(model_id, list):
                model_id = model_id[-1] if model_id else ""
        except Exception:
            model_id = ""
        logger.info(
            "[LLM回调] chat_start round={} stage={} uid={} run_id={} model={!r}",
            self._round,
            self._stage,
            self._uid,
            run_id,
            model_id,
        )
        logger.info(
            "[LLM回调] messages:\n{}",
            clip_llm_log(_format_message_batch(messages), DEFAULT_MAX_IN),
        )

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        text = _generations_text(response)
        logger.info(
            "[LLM回调] chat_end round={} stage={} uid={} run_id={} out_chars={}",
            self._round,
            self._stage,
            self._uid,
            run_id,
            len(text),
        )
        logger.info(
            "[LLM回调] generation:\n{}",
            clip_llm_log(text, DEFAULT_MAX_OUT),
        )

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        logger.warning(
            "[LLM回调] chat_error round={} stage={} uid={} run_id={} err={}",
            self._round,
            self._stage,
            self._uid,
            run_id,
            error,
        )
