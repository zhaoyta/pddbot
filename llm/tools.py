"""把 tools/* 包装成 LangChain @tool

设计要点:
    - 用 closure 模式：make_stage_tools(stage, deps) 返回该 stage 允许的工具列表
    - deps 需含 ``page``(Playwright async Page)、``store``、``uid``、``dry_run``、``stage``
      —— ``bot._process_chat_msg`` 已传入当前会话的 ``page``（无 page 时走日志 stub）
    - 需要 DOM 的工具为 **async** ``@tool``（与 agent ``ainvoke`` 配合）；纯逻辑工具可为同步

运行时依赖 deps 期望的 keys：
    store        : core.store.Store      —— 必须
    page         : playwright Page       —— 有则真实发消息/核销；无则 stub
    uid          : 当前会话客户 uid       —— 必须
    dry_run      : bool                   —— 干跑时不操作页面
    stage        : str                    —— 当前 stage（写 action_log 用）

    内部由工具回写（勿在业务里手动设）:
    _chat_sent_via_tool : 若 send_text / send_card_code_guide 已成功向会话发过 DOM 消息,
                          bot 将不再把模型尾部的 reply_text 再发一遍,避免重复。
    _send_text_parts     : send_text 多次调用时先追加到此列表,由 bot 在 LLM 回合结束后合并为一条发出。
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from loguru import logger

from tools import catalog as catalog_mod


def _dom_stub(deps: dict) -> bool:
    """无 Playwright page 或干跑：不执行真实 DOM。"""
    return deps.get("dry_run", False) or deps.get("page") is None


def _note_chat_sent_via_tool(deps: dict) -> None:
    """本轮已有工具向当前会话发过 DOM 消息：bot 结尾不要再 send_chat_message(reply_text)。"""
    deps["_chat_sent_via_tool"] = True


def _log_action(deps: dict, tool_name: str, payload: Any,
                success: bool, err: str | None = None) -> None:
    store = deps.get("store")
    if store is None:
        return
    try:
        store.log_action(
            uid=deps.get("uid", ""),
            stage=deps.get("stage", ""),
            tool=tool_name,
            payload=payload,
            success=success,
            error_msg=err,
        )
    except Exception as e:
        logger.warning("写 action_log 失败: {}", e)


# =============================================================
# 工具工厂（按 stage 返回不同子集）
# =============================================================

def make_stage_tools(stage: str, deps: dict) -> list:
    """根据 stage 返回这一阶段允许的工具列表。"""
    common = [_make_send_text(deps), _make_escalate_to_human(deps)]

    if stage == "S0_GREET":
        return common
    if stage == "S1_CONSULT":
        return common
    if stage == "S2_GUIDE":
        return [_make_send_card_code_guide(deps)] + common
    if stage == "S3_REDEEM":
        return [_make_submit_card_code(deps)] + common
    if stage == "S4_DELIVER":
        return [_make_lookup_product_url(deps)] + common

    return common


# =============================================================
# 各工具的 closure 构造
# =============================================================

def _make_send_text(deps: dict):
    @tool
    async def send_text(text: str) -> str:
        """主动发送一段文字给【当前会话客户】。

        可在一轮对话中多次调用；正文会在该轮 LLM 结束后由 bot **合并为一条消息**发出，
        避免会话里出现多条气泡。建议仍将完整答复（含链接）写在一次调用里。

        参数:
            text: 要发送的文本（≤ 2000 字）
        返回:
            "ok" 或错误描述
        """
        uid = deps.get("uid", "")
        chunk = (text or "").strip()
        if not chunk:
            return "empty_text"
        parts = deps.setdefault("_send_text_parts", [])
        parts.append(chunk)
        logger.debug(
            "[send_text] 已加入待发队列 uid={} part_len={} total_parts={}",
            uid,
            len(chunk),
            len(parts),
        )
        _log_action(
            deps,
            "send_text",
            {"text": chunk, "queued": True, "part_index": len(parts)},
            True,
        )
        return "ok"

    return send_text


def _make_send_card_code_guide(deps: dict):
    @tool
    async def send_card_code_guide() -> str:
        """发送【如何获取核销码】教程图 + 标准文案给当前会话客户。"""
        uid = deps.get("uid", "")
        if _dom_stub(deps):
            logger.debug("[send_card_code_guide] stub uid={}", uid)
            _log_action(deps, "send_card_code_guide", {}, True)
            return "ok"

        from tools import messaging as msg_mod

        page = deps.get("page")
        ok, err = await msg_mod.send_card_code_guide(page)
        _log_action(deps, "send_card_code_guide", {"err": err}, ok, err)
        if ok:
            _note_chat_sent_via_tool(deps)
        return "ok" if ok else (err or "send_failed")

    return send_card_code_guide


def _make_submit_card_code(deps: dict):
    @tool
    async def submit_card_code(code: str) -> dict:
        """在拼多多核销页输入并提交一个卡券码,返回核销结果。

        参数:
            code: 卡券码字符串（一般为 12 位数字）
        返回:
            {"success": bool, "order_sn": "...", "error": "..."}
        """
        uid = deps.get("uid", "")
        if _dom_stub(deps):
            logger.debug("[submit_card_code] stub uid={} code={}", uid, code)
            result = {"success": True, "order_sn": "MOCK-ORDER-" + code, "error": None}
            _log_action(deps, "submit_card_code",
                        {"code": code, "result": result}, True)
            deps["_redeem_success"] = True
            return result

        from tools import redeem as redeem_mod

        page = deps.get("page")
        result = await redeem_mod.submit_card_code(page, code)
        ok = bool(result.get("success"))
        _log_action(
            deps, "submit_card_code",
            {"code": code, "result": result}, ok,
            None if ok else str(result.get("error")),
        )
        if ok:
            deps["_redeem_success"] = True
            if result.get("already_redeemed"):
                logger.info(
                    "[submit_card_code] 平台接口认定券此前已核销(非本次提交成功),继续后续 uid={} order_sn={}",
                    uid,
                    result.get("order_sn"),
                )
            sn = str(result.get("order_sn") or "").strip()
            store = deps.get("store")
            if store and sn:
                try:
                    store.upsert_order({
                        "orderSn": sn,
                        "uid": str(uid),
                        "orderGoodsList": {},
                    })
                    store.mark_order_redeemed(sn)
                except Exception as e:
                    logger.warning("[submit_card_code] 标记订单已核销失败: {}", e)
            if store:
                try:
                    store.record_code_submit(code, str(uid), sn or None)
                    store.record_code_success(code, sn or None)
                except Exception as e:
                    logger.warning("[submit_card_code] card_code 落库失败: {}", e)
        return result

    return submit_card_code


def _make_lookup_product_url(deps: dict):
    @tool
    def lookup_product_url(
        goods_id: str | None = None,
        sku_id: str | None = None,
        goods_name: str | None = None,
    ) -> dict | None:
        """查询商品对应的百度网盘资料。

        参数:
            goods_id: 拼多多 goodsId（首选）
            sku_id: 拼多多 skuId（次选）
            goods_name: 商品名（兜底,模糊匹配）
        返回:
            命中: ``CatalogItem.to_dict()`` — {{title, url, product_url, description, pwd, message}}；
            message 为发给客户的整段 share_body；url/product_url 为对外链接（显式配置优先）。
            未命中: None
        """
        item = catalog_mod.lookup(
            goods_id=goods_id, sku_id=sku_id, goods_name=goods_name
        )
        if item is None:
            _log_action(
                deps, "lookup_product_url",
                {"goods_id": goods_id, "sku_id": sku_id, "goods_name": goods_name},
                False, "miss",
            )
            return None
        result = item.to_dict()
        _log_action(
            deps, "lookup_product_url",
            {"in": {"goods_id": goods_id, "sku_id": sku_id, "goods_name": goods_name},
             "out_title": item.title},
            True,
        )
        return result

    return lookup_product_url


def _make_escalate_to_human(deps: dict):
    @tool
    def escalate_to_human(reason: str) -> str:
        """触发转人工：发送告警 webhook。

        参数:
            reason: 触发原因（写日志用）
        返回:
            "ok"
        """
        uid = deps.get("uid", "")
        logger.warning("[ESCALATE] uid={} reason={}", uid, reason)
        _log_action(deps, "escalate_to_human", {"reason": reason}, True)

        try:
            from tools import notify as notify_mod
            notify_mod.send_feishu(
                "escalate",
                title="转人工告警",
                text=f"uid={uid}\n触发原因: {reason}\n阶段: {deps.get('stage', '?')}",
            )
        except Exception as e:
            logger.warning("[ESCALATE] 发飞书失败: {}", e)
        return "ok"

    return escalate_to_human


__all__ = ["make_stage_tools"]
