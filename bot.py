"""pddbot 主循环

当前能力:
    - 起浏览器 ✓
    - 新消息唤醒：**仅** HTTP（``plateau/sync/message``、``chat/list`` 等；见 ``runtime/network.py``）
    - 用 conv_state.last_msg_id 去重 ✓
    - 单条消息处理流水线(有 page 时顺序固定):
        左侧选中会话 → 右侧「最新订单」→「个人订单」→ 拦截 userAllOrder 取订单
        → stage 决策 → 聊天记录+订单 喂 LLM → action_log;未勾选 dry_run 再 messaging 发送

入口:
    asyncio.run(run(stop_event))   # stop_event 用 asyncio.Event 控制中断

GUI BotWorker 在自己的线程里建 event loop 跑这个函数。
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import time
from typing import Any

from loguru import logger

from core import settings, stage as stage_mod, store as store_mod
from core.llm_log_sink import configure_llm_log_sink
from llm import runner as llm_runner
from runtime.browser import BrowserSession
from runtime.network import NetworkRouter

# 单 uid 最近订单缓存(uid -> 最新订单 dict):NetworkRouter 被动拦截 + 处理消息前 UI 主动刷新
_LATEST_ORDERS: dict[str, dict[str, Any]] = {}

# sync/message 触发的左栏扫描互斥,避免并发 evaluate/点击打架
_SYNC_LEFT_SCAN_LOCK = asyncio.Lock()


async def _process_chat_msg(
    ev: dict[str, Any],
    *,
    dry_run: bool,
    page: Any = None,
) -> None:
    """处理一条客户消息。

    落库后若 ``from_uid != uid``（非买家侧发起）则直接返回，不跑后续（避免客服消息回显二次触发）。

    在落库、去重(含 **action_log 防重启重放**) 之后，**有 page** 时顺序为:

    1. ``session_dom.activate_session``：左侧点开该 uid 会话（若 ``skip_activate_session`` 则跳过,
       因 ``http_sync_dom_scan`` 左栏补扫已点过）。
    2. ``orders_fetch.refresh_latest_order_via_ui``：右侧「最新订单」→「个人订单」，
       监听 ``/latitude/order/userAllOrder``，写入 ``_LATEST_ORDERS``。
    3. ``stage.decide``：用订单、会话状态、最新消息决策 stage。
    4. ``llm_runner.arun_stage``：context 中带最近聊天记录与订单等。
    5. 写 action_log（含 ``incoming_msg_id``）；非 dry_run 时 ``messaging.send_chat_message``。

    无 page 时跳过 1～2，订单仅依赖此前 NetworkRouter 被动拦截的缓存。

    另: ``/plateau/sync/message`` 若响应体内嵌解析出 ``chat_msg``，则**不再**入队 ``sync_message``，
    仅在内嵌为空时才左栏补扫，避免同一条新消息被处理两次。
    """
    s = store_mod.get()
    uid = ev["uid"]
    msg_id = ev["msg_id"]
    content = ev.get("content") or ""
    from_list = bool(ev.get("from_latest_convs"))
    event_src = ev.get("event_source") or ""

    wl_raw = (settings.get("bot.whitelist_uids") or "").strip()
    if wl_raw:
        allowed = {
            c.strip()
            for c in wl_raw.replace("\n", ",").split(",")
            if c.strip()
        }
        if uid not in allowed:
            if from_list:
                logger.info(
                    "[bot] 启动会话列表同步: uid={} 不在 bot.whitelist_uids,跳过",
                    uid,
                )
            else:
                logger.info(
                    "[bot] uid={} 不在 bot.whitelist_uids 中,跳过(白名单留空=不限制)",
                    uid,
                )
            return

    # 1) 落聊天历史(无论是否回复都记)
    try:
        s.upsert_chat_message(
            msg_id=msg_id,
            uid=uid,
            role="user" if ev.get("from_uid") == uid else "mall_cs",
            content=content,
            msg_type=ev.get("msg_type") or 0,
            ts=int(ev.get("ts") or time.time()),
            raw=ev.get("raw") or {},
        )
    except Exception as e:
        logger.debug("[bot] 落 chat_message 失败(可忽略): {}", e)

    # 1b) 只接待买家发来的消息。IM 会回推「客服/主账号刚发出」的条目(新 msg_id)；
    # last_msg_id 记的是**已处理过的客户消息** id,对不上客服回显 id,去重挡不住,会再跑 LLM → 重复发图/文。
    if str(ev.get("from_uid") or "") != str(uid):
        logger.info(
            "[bot] uid={} msg_id={} 非买家发起 from_uid={!r},不触发自动回复(客服侧回显/系统 echo)",
            uid,
            msg_id,
            ev.get("from_uid"),
        )
        return

    # 2) 去重:已处理过的 msg_id 直接跳
    conv = s.get_conv_state(uid)
    if conv and str(conv.get("last_msg_id") or "") == str(msg_id):
        if from_list:
            logger.info(
                "[bot] 启动会话列表同步: uid={} msg_id={} 与库中 last_msg_id 相同,"
                "视为已处理过,不重复跑 LLM(若实际未回复需改会话状态或清库后再试)",
                uid, msg_id,
            )
        else:
            logger.debug("[bot] uid={} msg_id={} 已处理,跳", uid, msg_id)
        return

    # 2a) action_log 已成功接待过该 msg_id（重启后 conv 游标可能未写上）
    if s.has_replied_to_incoming_msg_id(uid, str(msg_id)):
        logger.info(
            "[bot] uid={} msg_id={} action_log 已有成功接待,跳过(常见于冷启动/列表重放)",
            uid,
            msg_id,
        )
        try:
            s.upsert_conv_state(
                uid=uid,
                last_msg_id=str(msg_id),
                last_active_ts=int(time.time()),
            )
        except Exception:
            pass
        return

    # 3) 左侧会话 → 右侧订单 tab → 拦截 userAllOrder（与 dry_run 无关）
    ui_order_refreshed: bool | None = None
    if page is not None:
        ui_order_refreshed = False
        # 5a 左侧：选中当前客户会话（左栏补扫路径已在 _after_sync_message 里点过,勿二次点击）
        skip_activate = bool(ev.get("skip_activate_session"))
        if not skip_activate:
            try:
                from tools import session_dom as sd_mod

                prefer_unread = event_src in (
                    "http_chat_list",
                    "http_latest_convs",
                    "http_sync_message",
                    "http_sync_dom_scan",
                )
                ok = await sd_mod.activate_session(
                    page,
                    uid,
                    content_preview=(content or "")[:220],
                    prefer_unread_badge=prefer_unread,
                )
                if not ok:
                    logger.warning(
                        "[bot] session_dom 未选中左侧会话 uid={} source={} (可手动点中)",
                        uid,
                        event_src or "?",
                    )
            except Exception as e:
                logger.warning("[bot] session_dom.activate_session 异常 uid={}: {}", uid, e)
        else:
            logger.debug(
                "[bot] skip_activate_session uid={} src={},左栏补扫已激活会话",
                uid,
                event_src or "?",
            )

        # 5b 右侧：最新订单 → 个人订单 → 等 userAllOrder
        try:
            from tools import orders_fetch as of_mod

            fresh = await of_mod.refresh_latest_order_via_ui(page, uid)
            if fresh:
                _LATEST_ORDERS[uid] = fresh
                ui_order_refreshed = True
                logger.info(
                    "[bot] UI 刷新订单 uid={} order_sn={} status={!r}",
                    uid,
                    fresh.get("orderSn"),
                    (fresh.get("orderStatusStr") or "")[:60],
                )
            else:
                logger.debug(
                    "[bot] UI 刷新订单 uid={} refresh_latest_order_via_ui 未返回订单 dict",
                    uid,
                )
        except Exception as e:
            logger.warning("[bot] 订单 UI 刷新异常 uid={}: {}", uid, e)

    # 6) 决策阶段：订单 + 会话状态 + 最新消息（聊天记录在 context.history）
    order = _LATEST_ORDERS.get(uid)
    hist_limit = settings.get_int("bot.chat_history_limit", 20)
    history = _build_history(s, uid, limit=hist_limit)
    context = {
        "uid": uid,
        "latest_message": content,
        "order": order,
        "conv_state": conv,
        "history": history,
    }
    o_sn = (order or {}).get("orderSn") if order else None
    o_st = (order or {}).get("orderStatusStr") if order else ""
    logger.info(
        "[bot] stage.decide 输入 uid={} src={} ui_refresh={} "
        "order_sn={} order_status={!r} conv_last_msg_id={} history_n={} preview={!r}",
        uid,
        event_src or "-",
        ui_order_refreshed,
        o_sn or "-",
        (o_st or "")[:60] if o_st else "-",
        (conv or {}).get("last_msg_id") or "-",
        len(history),
        (content or "")[:80],
    )
    decided_stage, reason = stage_mod.decide(context)
    src = "会话列表" if from_list else (event_src or "WS/HTTP")
    logger.info(
        "[bot] uid={} stage={} reason={} (来源:{})",
        uid, decided_stage, reason, src,
    )

    # 7) S_HUMAN:转人工 = 飞书通知
    if decided_stage == "S_HUMAN":
        s.upsert_conv_state(
            uid=uid,
            last_msg_id=str(msg_id),
            last_active_ts=int(time.time()),
            silenced_until=0,
        )
        from tools import notify
        notify.send_feishu(
            "escalate",
            title="转人工告警",
            text=f"uid={uid}\n触发原因:{reason}\n最新消息:{content[:120]}",
        )
        s.log_action(uid=uid, stage=decided_stage, tool="escalate",
                     payload={
                         "reason": reason,
                         "msg_in": content,
                         "incoming_msg_id": str(msg_id),
                     },
                     success=True)
        return

    # 8) 其它 stage 走 LLM(或模板)
    deps = {
        "store": s,
        "page": page,
        "uid": uid,
        "stage": decided_stage,
        "dry_run": dry_run,
        "_chat_sent_via_tool": False,
        "_redeem_success": False,
        "order_sn": str((order or {}).get("orderSn") or ""),
    }
    try:
        reply_text = await llm_runner.arun_stage(decided_stage, context, deps)
    except Exception as e:
        logger.exception("[bot] LLM 调用失败 uid={} stage={}: {}", uid, decided_stage, e)
        reply_text = ""

    # S3 核销成功后订单接口往往仍为「已发货,待签收」,仅靠 ``stage.is_redeemed`` 进不了 S4。
    # ``submit_card_code`` 成功时已写入 ``order_state.redeemed_at``；此处同一轮再跑 S4 发网盘资料。
    if decided_stage == "S3_REDEEM" and deps.get("_redeem_success"):
        deps["stage"] = "S4_DELIVER"
        try:
            reply_s4 = await llm_runner.arun_stage("S4_DELIVER", context, deps)
        except Exception as e:
            logger.exception(
                "[bot] S4 发资料失败(紧跟 S3 核销) uid={}: {}", uid, e,
            )
            reply_s4 = ""
        parts = [p for p in (reply_text.strip(), (reply_s4 or "").strip()) if p]
        reply_text = "\n".join(parts)
        if parts:
            logger.info(
                "[bot] uid={} S3 核销成功,已链接执行 S4 发资料 (合并回复 {} 段)",
                uid,
                len(parts),
            )

    # 9) 写 action_log + (DRY_RUN 不真发) + 更新 conv_state
    s.log_action(
        uid=uid, stage=decided_stage, tool="llm_reply",
        payload={
            "reason": reason,
            "reply": reply_text,
            "msg_in": content,
            "dry_run": dry_run,
            "incoming_msg_id": str(msg_id),
        },
        success=bool(reply_text.strip()) or bool(deps.get("_chat_sent_via_tool")),
    )
    done_ts = time.time()
    s.upsert_conv_state(
        uid=uid,
        last_msg_id=str(msg_id),
        last_active_ts=int(done_ts),
        silenced_until=0,
    )

    if dry_run:
        logger.warning("[bot][DRY_RUN] uid={} 不发送(已勾选干跑),回复内容已落 action_log:\n{}",
                       uid, reply_text[:300])
    elif page is None:
        logger.warning(
            "[bot] uid={} 无 Playwright page,跳过页面发送(仅 action_log):\n{}",
            uid, reply_text[:300],
        )
    elif not reply_text.strip():
        logger.info("[bot] uid={} 回复为空,跳过发送", uid)
    elif deps.get("_chat_sent_via_tool"):
        logger.info(
            "[bot] uid={} LLM 工具已向会话发过消息,跳过对最终 reply 的重复 DOM 发送 "
            "(尾部模型文案 len={}, 仍已写入 action_log)",
            uid,
            len(reply_text.strip()),
        )
    else:
        try:
            from tools import messaging as msg_mod

            ok, err = await msg_mod.send_chat_message(page, reply_text)
            if ok:
                logger.info("[bot] uid={} 已通过 DOM 发送 len={}", uid, len(reply_text))
            else:
                logger.error("[bot] uid={} DOM 发送失败 err={} 文案预览:\n{}",
                             uid, err, reply_text[:200])
        except Exception as e:
            logger.exception("[bot] uid={} DOM 发送异常: {}", uid, e)


async def _after_sync_message(
    _ev: dict[str, Any],
    *,
    dry_run: bool,
    page: Any,
) -> None:
    """响应 ``plateau/sync/message`` 左栏补扫（仅在内嵌未派发 chat_msg 时由 network 入队）。

    优先用库里最新一条用户消息;若无入库则用左栏 preview 构造。
    已与 ``conv_state.last_msg_id`` 对齐的 uid 不再合成伪 msg_id 二次跑 LLM，避免双通道重复。

    例外：左栏仍有未读/红点但游标已对齐 —— 常为 **新消息尚未经 HTTP 回写进 chat_message**。
    此时若左栏摘要与库内最新用户正文明显不一致，则用合成 msg_id 补跑一次。
    若摘要仍含库文：若库内 msg_id 已在 action_log 成功接待，则合成补跑。
    （旧版曾在「订单已 mark_order_delivered」时直接跳过以防红点残留；
    但复购咨询常见「库/HTTP 滞后、左栏摘要仍像旧对话」，跳过会漏接待，
    故不再跳过；下游 ``_process_chat_msg`` 对已处理的 ``sync_unread_*``
    ``msg_id`` 会因 ``conv_state.last_msg_id`` 去重，一般不会重复跑 LLM。）
    """

    def _preview_matches_db_latest(preview: str, db_content: str) -> bool:
        """左栏 innerText 含昵称/时间等，只做「库正文是否仍出现在摘要里」的宽松判断。"""
        d = (db_content or "").strip()
        p = preview.strip()
        if not d:
            return False
        if not p:
            return True
        d2 = "".join(d.split())
        p2 = "".join(p.split())
        return d2 in p2 or (len(d2) <= 12 and d in p)

    if page is None:
        return
    async with _SYNC_LEFT_SCAN_LOCK:
        await asyncio.sleep(0.35)
        from tools import left_panel_scan as lp_scan
        from tools import session_dom as sd_mod

        rows = await lp_scan.collect_rows_needing_action(page)
        s = store_mod.get()
        batch_seen: set[str] = set()
        for row in rows:
            uid = (row.get("uid") or "").strip()
            if not uid:
                logger.debug(
                    "[bot][sync] 跳过无 uid 行 preview={!r}",
                    (row.get("preview") or "")[:80],
                )
                continue
            if uid in batch_seen:
                logger.debug("[bot][sync] 本批已处理 uid={}, 跳过重复点击", uid)
                continue
            batch_seen.add(uid)
            try:
                ok = await sd_mod.activate_session(
                    page,
                    uid,
                    content_preview=(row.get("preview") or "")[:220],
                    prefer_unread_badge=True,
                )
                if not ok:
                    logger.warning("[bot][sync] 左侧未点中 uid={}", uid)
                await asyncio.sleep(0.45)
            except Exception as e:
                logger.warning("[bot][sync] activate uid={}: {}", uid, e)
                continue

            preview = (row.get("preview") or "").strip()
            row_signal = bool(
                row.get("red_dot") or row.get("wait_hint") or row.get("un_watch"),
            )

            def _fp(tie: str = "") -> str:
                blob = f"{uid}|{preview[:500]}|{tie}".encode("utf-8", errors="ignore")
                return hashlib.sha256(blob).hexdigest()[:20]

            urow = s.get_latest_chat_message(uid, role="user")
            conv = s.get_conv_state(uid)
            msg_id: str
            content: str
            msg_type: int
            ts: int

            if urow is None:
                if not (row_signal and preview):
                    logger.info(
                        "[bot][sync] 库中无该 uid 的用户消息,且左栏无可用 preview,无法补跑 uid={}",
                        uid,
                    )
                    continue
                msg_id = f"sync_dom_{uid}_{_fp()}"
                content = preview
                msg_type = 0
                ts = int(time.time())
                logger.info(
                    "[bot][sync] 库中无用户消息,按左栏待跟进构造补跑 uid={} msg_id={}",
                    uid,
                    msg_id,
                )
            else:
                msg_id = str(urow["msg_id"])
                content = str(urow["content"] or "")
                msg_type = int(urow["msg_type"] or 0)
                ts = int(urow["ts"] or int(time.time()))
                if conv and str(conv.get("last_msg_id") or "") == msg_id:
                    if not row_signal:
                        logger.debug(
                            "[bot][sync] uid={} msg_id={} 已处理且无未读标记,跳过",
                            uid,
                            msg_id,
                        )
                        continue
                    # 有未读但游标已对齐
                    if not _preview_matches_db_latest(preview, content):
                        msg_id = f"sync_unread_{uid}_{_fp('unread_mismatch')}"
                        content = preview
                        msg_type = 0
                        ts = int(time.time())
                        logger.info(
                            "[bot][sync] uid={} 左栏未读且摘要与库最新不一致,"
                            "推断有新消息未入库,补跑 msg_id={} preview={!r}",
                            uid,
                            msg_id,
                            preview[:120],
                        )
                    elif s.has_replied_to_incoming_msg_id(uid, msg_id):
                        rows_ord = s.list_orders_of_uid(uid)
                        o_sn = str(rows_ord[0]["order_sn"]) if rows_ord else ""
                        delivered = bool(o_sn and s.is_order_delivered(o_sn))
                        orig_mid = msg_id
                        msg_id = f"sync_unread_{uid}_{_fp('reply_but_no_deliver')}"
                        content = preview
                        msg_type = 0
                        ts = int(time.time())
                        if delivered:
                            logger.info(
                                "[bot][sync] uid={} msg_id={} 游标对齐、摘要仍含库文且已接待；"
                                "订单已 mark_order_delivered，仍左栏补跑（防复购/HTTP 滞后漏接待）"
                                " synthetic_msg_id={} order_sn={!r}",
                                uid,
                                orig_mid,
                                msg_id,
                                o_sn or "-",
                            )
                        else:
                            logger.info(
                                "[bot][sync] uid={} msg_id={} 虽已接待但订单仍未发资料或无本地单,"
                                "左栏补跑防漏链 synthetic_msg_id={} order_sn={!r}",
                                uid,
                                orig_mid,
                                msg_id,
                                o_sn or "-",
                            )
                    else:
                        msg_id = f"sync_unread_{uid}_{_fp('preview_match_unread')}"
                        content = preview
                        msg_type = 0
                        ts = int(time.time())
                        logger.info(
                            "[bot][sync] uid={} msg_id={} 游标对齐、摘要含库文但尚未成功接待,"
                            "仅靠 HTTP 左栏补跑 msg_id={} preview={!r}",
                            uid,
                            str(urow["msg_id"]),
                            msg_id,
                            preview[:120],
                        )

            chat_ev: dict[str, Any] = {
                "kind": "chat_msg",
                "uid": uid,
                "from_uid": uid,
                "to_uid": "",
                "msg_id": msg_id,
                "msg_type": msg_type,
                "content": content,
                "ts": ts,
                "skip_activate_session": True,
                "raw": {"from_sync_dom_scan": True},
                "event_source": "http_sync_dom_scan",
            }
            await _process_chat_msg(chat_ev, dry_run=dry_run, page=page)


def _build_history(store, uid: str, limit: int = 20) -> list[dict[str, str]]:
    rows = store.list_chat_messages_recent(uid, limit=limit)
    return [
        {"role": r["role"], "content": r["content"] or ""}
        for r in rows
    ]


async def run(stop_event: asyncio.Event,
              *,
              headless: bool | None = None,
              session_holder: dict | None = None,
              force_relogin: bool = False,
              on_login_required: Any = None,
              on_login_completed: Any = None) -> None:
    """主循环。GUI BotWorker 在自己的事件循环里调用此函数。

    stop_event:        由外层 set() 触发优雅退出。
    session_holder:    可选 dict,主循环会把当前活跃的 BrowserSession 放到
                        session_holder['session'] 里供外层(如 GUI 保存按钮)取用。
    force_relogin:     True 时忽略已保存的 storage_state.json,启动后直接走扫码登录。
    on_login_required: 可选回调,当浏览器卡在 login 页等扫码时会被调用一次,
                        用来让 GUI 切到「等待扫码」状态。
    on_login_completed: 可选回调,扫码完成并已保存登录态后调用,让 GUI 切回「运行中」。
    """
    configure_llm_log_sink()
    if not settings.get_bool("bot.enabled", True):
        logger.warning("[bot] settings.bot.enabled=false,启动后立即退出")
        return

    # 干跑开关与 GUI「DRY_RUN」勾选一致;未接入 messaging 时无论真假都不会在页面点发送
    dry_run = settings.get_bool("bot.dry_run", False)
    if dry_run:
        logger.info("[bot] bot.dry_run=true：LLM 结果仅写库/日志，不尝试页面发送")
    else:
        logger.info(
            "[bot] bot.dry_run=false：回复将经 tools/messaging 尝试 DOM 发送",
        )

    if headless is None:
        # 强制有头方便观察
        headless = False

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)

    async with BrowserSession(
        headless=headless,
        force_relogin=force_relogin,
        on_login_required=on_login_required,
        on_login_completed=on_login_completed,
    ) as sess:
        if session_holder is not None:
            session_holder["session"] = sess
        router = NetworkRouter(sess.page, queue)
        router.install()
        logger.info("[bot] 进入主循环,等消息...")

        # 启动后主动给后端发个心跳:刷新一下页面 / 切到「今日接待」让 latest_conversations
        # 触发,并尝试把"启动前就到的未读消息"也带回来。
        await _trigger_initial_refresh(sess.page)

        while not stop_event.is_set():
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            kind = ev.get("kind")
            try:
                if kind == "chat_msg":
                    await _process_chat_msg(ev, dry_run=dry_run, page=sess.page)
                elif kind == "sync_message":
                    await _after_sync_message(ev, dry_run=dry_run, page=sess.page)
                elif kind == "order_list":
                    orders = ev.get("orders") or []
                    if orders:
                        # D1:取最新一笔(按 orderTime 倒序)
                        orders_sorted = sorted(
                            orders,
                            key=lambda o: o.get("orderTime") or 0,
                            reverse=True,
                        )
                        _LATEST_ORDERS[ev["uid"]] = orders_sorted[0]
                        o0 = orders_sorted[0]
                        logger.info(
                            "[bot] 缓存订单(HTTP 拦截) uid={} order_sn={} status={!r}",
                            ev["uid"],
                            o0.get("orderSn"),
                            (o0.get("orderStatusStr") or "")[:60],
                        )
                else:
                    logger.debug("[bot] 未知事件 {}", kind)
            except Exception as e:
                logger.exception("[bot] 处理事件异常 ev={}, e={}", ev.get("kind"), e)

        logger.info("[bot] 收到停止信号,退出主循环")
        try:
            router.close()
        except Exception:
            pass
        if session_holder is not None:
            session_holder.pop("session", None)


async def _trigger_initial_refresh(page) -> None:
    """启动后给页面 reload 一次,**关键作用**:

    - NetworkRouter 是在 BrowserSession 完成首屏 navigate 之后才安装的,
      首屏的 HTTP 请求(latest_conversations 等)都漏掉了。
    - 这里 reload 一次,让所有接口在 install 之后重新触发,
      bot 才能监听到「启动前就来的未读消息」对应的会话列表。

    失败不影响主流程。
    """
    try:
        logger.info("[bot] 开始启动后会话同步(reload + 点 tab,触发 latest_conversations 等)")
        # 温身:鼠标随机移动一下,再 reload(降低脚本痕迹)
        try:
            await page.mouse.move(
                random.uniform(200, 900),
                random.uniform(120, 400),
                steps=random.randint(6, 12),
            )
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.4, 1.0))

        logger.info("[bot] reload 聊天页以触发完整请求(让 NetworkRouter 命中首屏接口)")
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        # 顺便点一下「今日接待」让 latest_conversations 在用户切 tab 场景下再刷一次
        for sel in ["text=今日接待", "text=全部会话"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=2000)
                    logger.info("[bot] 已点击 {},触发会话列表刷新", sel)
                    break
            except Exception:
                continue
        await asyncio.sleep(1.0)
        # HTTP 回调里用 create_task 解析响应,给一点时间让帧入队后再进主循环
        await asyncio.sleep(2.0)
        logger.info("[bot] 启动后会话同步流程结束(若仍无处理日志请看是否命中 [net][HTTP] latest_conversations)")
    except Exception as e:
        logger.debug("[bot] 启动软触发异常(可忽略): {}", e)
