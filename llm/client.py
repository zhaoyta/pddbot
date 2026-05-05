"""DeepSeek 聊天模型客户端

通过 langchain-openai 的 ChatOpenAI,使用 DeepSeek 提供的 OpenAI 兼容 API。

**所有 LLM 配置(api_key/base_url/model/temperature/max_tokens)
都从 core.settings 读取**,优先级:settings 表 > .env > 默认值。
GUI 模型页改完保存即时生效。
"""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from core import settings


# 按 (api_key, base_url, model, temperature, max_tokens) 5 元组缓存,
# 改任何一个都会自动新建实例;同一组合下复用同一连接池。
@lru_cache(maxsize=4)
def _build_model(
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=30,
        max_retries=2,
    )


def get_chat_model(
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """获取 DeepSeek 模型实例。

    参数 temperature / max_tokens 可显式覆盖,默认从 settings 读。
    """
    api_key = settings.get("llm.api_key", "")
    base_url = settings.get("llm.base_url", "https://api.deepseek.com")
    model = settings.get("llm.model", "deepseek-chat")
    if temperature is None:
        temperature = settings.get_float("llm.temperature", 0.3)
    if max_tokens is None:
        max_tokens = settings.get_int("llm.max_tokens", 800)

    if not api_key:
        raise RuntimeError(
            "DeepSeek API Key 未配置,请打开 GUI → 模型页填一下,或在 .env 里塞 "
            "DEEPSEEK_API_KEY 作为首次启动的种子值。"
        )

    return _build_model(api_key, base_url, model, float(temperature), int(max_tokens))


def reset_cache() -> None:
    """改完 settings 后,如果想立即清缓存(让下一次调用强制新建实例),可以调这里。

    一般情况下不需要主动调,因为缓存 key 包含所有参数,改任何一个就会命中新实例。
    """
    _build_model.cache_clear()
