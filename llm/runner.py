"""LLM 层主入口

`bot.py` 主循环每次收到新消息走完 core/stage.py 决策后,
调用 `run_stage(stage, context, deps)`,本模块负责:
    1. 把 context 拼装成给 LLM 的初始消息
    2. 调用 stage 对应的 agent（通过 LangChain Runnable ``config["callbacks"]`` 挂载
       ``PddChatModelLogger``,记录每一跳 Chat 模型的请求/响应）
    3. 截取最终回复文本返回

Agent 路径下详细出入日志见前缀 ``[LLM回调]``；整条链路摘要见 ``[LLM交互] mode=agent summary``。
模板模式不走模型，仍仅打 ``[LLM交互] mode=template``。

bot.py 拿到回复后:
    - 走 DOM 把消息发到聊天框（已经在 send_text 工具里发过的不再重发）
    - 更新 store（last_msg_id、订单状态、card_code 等）
"""
from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import HumanMessage
from loguru import logger

from core import store as store_mod
from tools import catalog as catalog_mod

from .agent import build_agent
from .log_format import DEFAULT_MAX_OUT, clip_llm_log
from .logging_callback import PddChatModelLogger

# 模板模式可用占位符
TEMPLATE_PLACEHOLDERS: dict[str, str] = {
    "customer_msg": "客户最新一条消息原文",
    "order_sn": "当前订单号(无订单为空)",
    "order_status": "订单状态文字(无订单为空)",
    "goods_name": "商品名称",
    "goods_id": "商品 ID",
    "sku_id": "SKU ID",
    "title": "资料标题(命中映射时有)",
    "share_url": "带 pwd 的百度网盘完整链接(命中映射时有)",
    "pwd": "百度网盘提取码(命中映射时有)",
    "material_message": "整段网盘分享文本(三行格式,命中映射时有)",
}


def _build_user_message(stage: str, context: dict) -> str:
    """把业务上下文压缩成给 LLM 的一段输入。

    格式约定（保持紧凑,省 token）:
        【店铺知识库 QA】…（启用且有答复的条目，来自 qa_item 表）
        【客户最新消息】xxx
        【订单上下文】<json>
        【最近对话】<list>
        【附加】<json>
    """
    parts = []
    try:
        qa_mem = store_mod.get().qa_context_block()
    except Exception as e:
        logger.warning("[LLM] 读取 qa_item 知识库失败: {}", e)
        qa_mem = ""
    if qa_mem:
        parts.append("【店铺知识库 QA】\n" + qa_mem)

    latest_msg = context.get("latest_message") or ""
    if latest_msg:
        parts.append(f"【客户最新消息】\n{latest_msg}")

    order = context.get("order")
    if order:
        # 只塞精炼字段,避免把整张订单 json 塞进去
        parts.append("【当前订单上下文】\n" + json.dumps(_order_summary(order),
                                                     ensure_ascii=False, indent=2))

    history = context.get("history") or []
    if history:
        lines = []
        for h in history[-6:]:
            role = "客户" if h.get("role") == "user" else "客服"
            lines.append(f"{role}: {h.get('content', '')[:120]}")
        parts.append("【最近对话】\n" + "\n".join(lines))

    extra = context.get("extra")
    if extra:
        parts.append("【附加】\n" + json.dumps(extra, ensure_ascii=False))

    return "\n\n".join(parts) if parts else "(无额外上下文)"


def _order_summary(order: dict) -> dict:
    """从拼多多订单 raw 提取 LLM 能看懂的精炼字段。"""
    g = order.get("orderGoodsList") or {}
    return {
        "订单号": order.get("orderSn"),
        "状态": order.get("orderStatusStr"),
        "商品": g.get("goodsName"),
        "goods_id": g.get("goodsId"),
        "sku_id": g.get("skuId"),
        "规格": g.get("spec"),
        "数量": g.get("goodsNumber"),
        "金额": (order.get("orderAmount") or 0) / 100,
        "下单时间": order.get("orderTime"),
        "签收时间": order.get("receiveTime"),
        "有售后": order.get("afterSalesInfo") is not None,
    }


def _build_template_vars(context: dict) -> dict[str, str]:
    """从 context 抽取模板渲染需要的字段。"""
    order = context.get("order") or {}
    g = order.get("orderGoodsList") or {}

    goods_id = g.get("goodsId") or ""
    sku_id = g.get("skuId") or ""
    goods_name = g.get("goodsName") or ""

    title = share_url = pwd = material_message = ""
    if goods_id or sku_id or goods_name:
        item = catalog_mod.lookup(
            goods_id=goods_id or None,
            sku_id=sku_id or None,
            goods_name=goods_name or None,
        )
        if item is not None:
            title = item.title
            share_url = item.share_url
            pwd = item.pwd
            material_message = item.to_message()

    return {
        "customer_msg": str(context.get("latest_message") or ""),
        "order_sn": str(order.get("orderSn") or ""),
        "order_status": str(order.get("orderStatusStr") or ""),
        "goods_name": str(goods_name),
        "goods_id": str(goods_id),
        "sku_id": str(sku_id),
        "title": title,
        "share_url": share_url,
        "pwd": pwd,
        "material_message": material_message,
    }


def render_template(template: str, context: dict) -> str:
    """用 context 把模板里的占位符替换掉。

    占位符未提供值时替换为空串(避免 KeyError)。
    """
    vars_ = _build_template_vars(context)

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    try:
        return template.format_map(_SafeDict(vars_)).strip()
    except (IndexError, ValueError) as e:
        logger.warning("[LLM] 模板渲染失败,降级原文返回: {}", e)
        return template


def _try_template_mode(stage: str, context: dict) -> str | None:
    """如果该 stage 在 stage_config 表里被配置为 template 模式,返回渲染后的文本;
    否则返回 None,让外层走 LLM。
    """
    try:
        cfg = store_mod.get().get_stage_config(stage) or {}
    except Exception as e:
        logger.warning("[LLM] 读取 stage_config 失败,降级 LLM: {}", e)
        return None

    mode = (cfg.get("mode") or "auto").lower()
    template = cfg.get("template") or ""
    if mode == "template" and template.strip():
        rendered = render_template(template, context)
        logger.debug("[LLM] stage={} 模板渲染长度={}", stage, len(rendered))
        return rendered
    return None


def run_stage(stage: str, context: dict, deps: dict, *, debug: bool = False) -> str:
    """同步入口:跑一次 stage agent,返回最终回复文本。

    内部转调 ``arun_stage``；若在已有 asyncio 事件循环的线程内调用会失败，请改用 ``arun_stage``。
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(arun_stage(stage, context, deps, debug=debug))
    raise RuntimeError(
        "run_stage() 不能在已有事件循环内调用，请使用 await arun_stage(...)",
    )


async def arun_stage(stage: str, context: dict, deps: dict, *, debug: bool = False) -> str:
    """异步版本（bot 主循环用）。"""
    rendered = _try_template_mode(stage, context)
    if rendered is not None:
        uid = deps.get("uid", "")
        lm = (context.get("latest_message") or "")[:300]
        logger.info(
            "[LLM交互] mode=template stage={} uid={} dry_run={} out_chars={} latest_in_preview={!r}",
            stage,
            uid,
            deps.get("dry_run"),
            len(rendered),
            lm,
        )
        logger.info(
            "[LLM交互] rendered_reply:\n{}",
            clip_llm_log(rendered, DEFAULT_MAX_OUT),
        )
        return rendered

    t0 = time.time()
    agent = build_agent(stage, deps, debug=debug)
    user_msg = _build_user_message(stage, context)

    cb = PddChatModelLogger(stage=stage, uid=str(deps.get("uid") or ""))

    try:
        out = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_msg)]},
            config={"callbacks": [cb]},
        )
    except Exception as e:
        logger.exception("[LLM] agent 异步调用失败: {}", e)
        logger.info(
            "[LLM交互] mode=agent stage={} uid={} （调用异常，仅记录组装输入）\n{}",
            stage,
            deps.get("uid"),
            clip_llm_log(user_msg, 12000),
        )
        return ""

    msgs = out.get("messages", [])
    final = ""
    for m in reversed(msgs):
        if m.__class__.__name__ == "AIMessage":
            content = getattr(m, "content", "")
            if isinstance(content, str) and content.strip():
                final = content.strip()
                break
            if isinstance(content, list):
                for seg in content:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        final = (seg.get("text") or "").strip()
                        if final:
                            break
                if final:
                    break

    elapsed = time.time() - t0
    logger.info(
        "[LLM交互] mode=agent summary stage={} uid={} dry_run={} packed_user_chars={} "
        "final_reply_chars={} elapsed={:.2f}s（每轮模型出入见 [LLM回调]）",
        stage,
        deps.get("uid"),
        deps.get("dry_run"),
        len(user_msg),
        len(final),
        elapsed,
    )
    return final
