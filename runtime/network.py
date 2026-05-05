"""网络层路由:把 HTTP 响应转成业务事件,推到 asyncio.Queue。

新消息**仅**来自 HTTP（`/plateau/sync/message`、内嵌 data→chat_msg、`chat/list`、`latest_conversations` 等）。

业务事件类型(NetworkEvent.kind):
    "chat_msg"      —— 来自 HTTP 的客户消息(已规整)
    "sync_message"  —— /plateau/sync/message 成功且内嵌未解析出消息时,触发左栏 DOM 补扫
    "order_list"    —— /latitude/order/userAllOrder 响应,带 uid

不做 selector / DOM,完全基于 URL + payload schema 适配。

事件结构 (字典):
    kind:        "chat_msg"
    uid:         "客户的 uid 字符串"
    msg_id:      消息 id (str|int) 用于去重
    content:     文本(可空,非文本消息时是预览或 type 名)
    event_source: 可选 "http_sync_message" | "http_chat_list" | "http_latest_convs" | ...
    msg_type:    数字 type(参考 protocol.md)
    ts:          unix 秒
    raw:         原 payload(便于事后回溯)

    kind:        "order_list"
    uid:         所查询的客户 uid
    orders:      list[dict] (orderList 字段)
    raw:         原 payload
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger
from playwright.async_api import Page, Response

# 监听的关键 URL(从 captures 里挑的真实路径)
# 拼多多服务端有多个聊天接口,这里都覆盖,具体字段在 _handle_* 里宽松解析
URL_LATEST_CONVS = "/plateau/chat/latest_conversations"
URL_SYNC_MESSAGE = "/plateau/sync/message"
# 子串匹配(兼容路径变体)
_SYNC_MESSAGE_MARKERS = (
    URL_SYNC_MESSAGE,
    "/plateau/sync/messages",
)
URL_USER_ALL_ORDER = "/latitude/order/userAllOrder"
URL_CONV_LIST_VARIANTS = (
    "/plateau/chat/marked_lastest_conversations",   # 拼多多原始拼写就是 lastest(typo)
    "/latitude/mall/orderCsGroupConvList",
)
# 兼容旧版仍然保留
URL_CHAT_LIST = "/plateau/chat/list"

# 系统消息 type(从 captures 看 type=30 是"账户在别处登录")
SYSTEM_MSG_TYPES = {30}


class NetworkRouter:
    def __init__(self, page: Page, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.page = page
        self.queue = queue
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self.page.on("response", self._on_response)
        self._installed = True
        logger.info(
            "[net] 已安装 HTTP 响应监听（sync/message、chat/list、latest_convs、userAllOrder）",
        )

    # ---------- HTTP ----------
    def _on_response(self, response: Response) -> None:
        url = response.url
        if URL_CHAT_LIST in url or any(k in url for k in URL_CONV_LIST_VARIANTS):
            logger.debug("[net][HTTP] 命中 chat-list 接口: {}", url)
            asyncio.create_task(self._handle_chat_list(response))
        elif URL_LATEST_CONVS in url:
            logger.info("[net][HTTP] 命中 latest_conversations: {}", url)
            asyncio.create_task(self._handle_latest_convs(response))
        elif any(m in url for m in _SYNC_MESSAGE_MARKERS):
            try:
                st = response.status
            except Exception:
                st = "?"
            logger.info("[net][HTTP] 命中 sync/message status={} {}", st, url[:120])
            asyncio.create_task(self._handle_sync_message(response))
        elif URL_USER_ALL_ORDER in url:
            logger.info("[net][HTTP] 命中 userAllOrder: {}", url)
            asyncio.create_task(self._handle_user_all_order(response))

    async def _safe_json(self, resp: Response) -> dict | None:
        try:
            return await resp.json()
        except Exception:
            try:
                txt = await resp.text()
                return json.loads(txt)
            except Exception as e:
                logger.debug("[net] 响应非 JSON {} {}", resp.url, e)
                return None

    async def _handle_chat_list(self, resp: Response) -> None:
        body = await self._safe_json(resp)
        if not body:
            return
        result = body.get("result") or body.get("data") or {}
        msgs = (result.get("messages") or result.get("list")
                or result.get("msgList") or [])
        if not isinstance(msgs, list):
            logger.debug("[net][HTTP] chat_list 顶层 keys={} result.keys={}",
                         list(body.keys()), list(result.keys()) if isinstance(result, dict) else "?")
            return
        # 从 query 里取 uid (chat/list 是按 uid 查会话历史)
        q_uid = parse_qs(urlparse(resp.url).query).get("uid", [""])[0]
        post_uid = ""
        try:
            post = resp.request.post_data_json
            if isinstance(post, dict):
                post_uid = str(post.get("uid") or post.get("user_id") or "")
        except Exception:
            pass
        uid_hint = q_uid or post_uid

        # chat/list 常返回**整段会话历史**，若对每条都入队会连续触发多次 LLM（重复发图/文）。
        # 只派发「买家侧」消息里 msg_id 最大的一条，表示当前快照下的最新客户发言。
        candidates: list[dict[str, Any]] = []
        for m in msgs:
            ev = self._normalize_chat_msg(
                m, default_uid=uid_hint, event_source="http_chat_list",
            )
            if not ev:
                continue
            if str(ev.get("from_uid") or "") != str(ev.get("uid") or ""):
                continue
            candidates.append(ev)

        emitted = 0
        if candidates:

            def _mid_num(e: dict[str, Any]) -> int:
                try:
                    return int(str(e.get("msg_id") or "0"))
                except (TypeError, ValueError):
                    return 0

            best = max(candidates, key=_mid_num)
            await self._put(best)
            emitted = 1
            if len(candidates) > 1:
                logger.info(
                    "[net][HTTP] chat_list uid_hint={} 买家消息 {} 条→仅派发最新 msg_id={} preview={!r}",
                    uid_hint or "(无)",
                    len(candidates),
                    best.get("msg_id"),
                    (best.get("content") or "")[:60],
                )

        logger.info("[net][HTTP] chat_list uid_hint={} 消息条目={} 派发={}",
                    uid_hint or "(无)", len(msgs), emitted)

    async def _handle_latest_convs(self, resp: Response) -> None:
        """会话列表:不直接产出消息事件,但顺带把每个会话最近一条标记给上层
        (便于启动时刷个全量游标)。结构未抓到样本,先宽松解析。"""
        body = await self._safe_json(resp)
        if not body:
            return
        result = body.get("result") or body.get("data") or {}
        convs = (result.get("conversations") or result.get("list")
                 or result.get("convList") or [])
        if not isinstance(convs, list):
            logger.warning(
                "[net][HTTP] latest_conversations 字段未识别 顶层 keys={} result.keys={} "
                "—— 这里需要根据实际响应补字段",
                list(body.keys()),
                list(result.keys()) if isinstance(result, dict) else "?",
            )
            return
        emitted = 0
        for c in convs:
            if not isinstance(c, dict):
                continue
            last = (c.get("last_message") or c.get("lastMessage")
                    or c.get("last_msg") or c.get("lastMsg"))
            # 拼多多常见结构: conversations[] 每一项本身就是「最后一条消息」扁平对象
            if not isinstance(last, dict) or not last.get("msg_id"):
                last = c
            peer_uid = NetworkRouter._peer_customer_uid(c, fallback_msg=last)
            if not peer_uid:
                continue
            ev = self._normalize_chat_msg(
                last, default_uid=peer_uid, event_source="http_latest_convs",
            )
            if ev:
                ev["from_latest_convs"] = True
                await self._put(ev)
                emitted += 1
        logger.info(
            "[net][HTTP] latest_conversations 共 {} 个会话,派发 {} 条消息事件",
            len(convs), emitted,
        )
        if len(convs) > 0 and emitted == 0:
            sample = convs[0]
            last0 = (sample.get("last_message") or sample.get("lastMessage")
                      or sample.get("last_msg") or sample.get("lastMsg") or {})
            if isinstance(sample, dict) and not last0.get("msg_id"):
                last0 = sample
            logger.warning(
                "[net][HTTP] latest_conversations 有 {} 条会话但未能归一化出 chat_msg,"
                "请对照 captures 补字段: 首条会话 keys={} 消息体 keys={}",
                len(convs),
                list(sample.keys()) if isinstance(sample, dict) else type(sample),
                list(last0.keys()) if isinstance(last0, dict) else type(last0),
            )

    async def _handle_user_all_order(self, resp: Response) -> None:
        body = await self._safe_json(resp)
        if not body:
            return
        result = body.get("result") or body.get("data") or {}
        orders = (result.get("orderList") or result.get("list")
                  or result.get("orders") or [])
        if not isinstance(orders, list):
            return

        # uid 来自请求 body
        uid = ""
        try:
            post = resp.request.post_data_json
            if isinstance(post, dict):
                uid = str(post.get("uid") or post.get("user_id") or "")
        except Exception:
            pass
        if not uid:
            return

        n_orders = len(orders)
        sn0 = ""
        st0 = ""
        if orders and isinstance(orders[0], dict):
            sn0 = str(orders[0].get("orderSn") or "")
            st0 = str(orders[0].get("orderStatusStr") or "")[:48]
        logger.info(
            "[net][HTTP] userAllOrder 解析 uid={} 订单条数={} 首单 order_sn={} status={!r}",
            uid, n_orders, sn0 or "-", st0 or "-",
        )

        await self._put({
            "kind": "order_list",
            "uid": uid,
            "orders": orders,
            "raw": body,
        })

    @staticmethod
    def _flatten_sync_data_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
        """解析 result.sync_data[].data 里可能是消息对象的条目."""
        out: list[dict[str, Any]] = []
        result = body.get("result") or body.get("data") or {}
        blocks = result.get("sync_data") or []
        if not isinstance(blocks, list):
            return out
        for block in blocks:
            if not isinstance(block, dict):
                continue
            chunk = block.get("data")
            if not isinstance(chunk, list):
                continue
            for item in chunk:
                if isinstance(item, dict):
                    out.append(item)
        return out

    async def _handle_sync_message(self, resp: Response) -> None:
        """新消息会触发 plateau/sync/message;尽量从内嵌 data 派 chat_msg,并通知 DOM 扫描."""
        body = await self._safe_json(resp)
        if not body:
            logger.warning("[net] sync/message 响应体非 JSON 或解析失败 url={}", resp.url[:100])
            return
        if not body.get("success"):
            logger.info(
                "[net] sync/message success=false 跳过入队 keys={} url={}",
                list(body.keys()) if isinstance(body, dict) else type(body),
                resp.url[:100],
            )
            return
        emitted = 0
        for item in self._flatten_sync_data_messages(body):
            ev = self._normalize_chat_msg(
                item, default_uid="", event_source="http_sync_message",
            )
            if ev:
                await self._put(ev)
                emitted += 1
        if emitted:
            logger.info("[net][HTTP] sync/message 内嵌派发 {} 条 chat_msg", emitted)
        else:
            # 内嵌未解析出消息时才需要左栏 DOM 补扫；否则与内嵌 chat_msg 重复处理同一轮
            await self._put({
                "kind": "sync_message",
                "raw": body,
                "event_source": "http_sync_message",
            })
            logger.info("[net] sync/message 已入队 kind=sync_message（左栏补扫）")
            return

        logger.info(
            "[net] sync/message 内嵌已入队 {} 条 chat_msg,跳过 sync_message 事件(避免左栏再跑一遍)",
            emitted,
        )

    # ---------- 通用 ----------
    @staticmethod
    def _peer_customer_uid(conv: dict, *, fallback_msg: dict | None = None) -> str:
        """从会话行或嵌套 last_message 里取出买家 uid(用于列表同步、左侧点选).

        规则: role==user 的一方为客户;否则用顶层 uid / default_uid 字段。
        """
        for key in ("uid", "user_id", "userId", "buyer_uid", "buyerUid"):
            v = conv.get(key)
            if v not in (None, "", 0):
                return str(v)
        blob = fallback_msg if isinstance(fallback_msg, dict) else conv
        fr = blob.get("from") or {}
        to = blob.get("to") or {}
        if isinstance(fr, dict) and fr.get("role") == "user" and fr.get("uid") not in (None, "", -1):
            return str(fr.get("uid"))
        if isinstance(to, dict) and to.get("role") == "user" and to.get("uid") not in (None, "", -1):
            return str(to.get("uid"))
        return ""

    @staticmethod
    def _normalize_chat_msg(
        m: dict,
        *,
        default_uid: str = "",
        event_source: str | None = None,
    ) -> dict | None:
        """把不同来源的消息字段规整成统一事件格式。

        过滤规则:
            - 没 msg_id 或 from 的丢
            - 系统消息(type in SYSTEM_MSG_TYPES) 跳
            - 商家自己发的消息(from == 当前账号自己) 不在这里区分,
              交给 bot 主循环用 conv_state.last_msg_id 去重
        """
        if not isinstance(m, dict):
            return None
        msg_id = m.get("msg_id") or m.get("msgId") or m.get("id")
        if msg_id is None:
            return None
        msg_type = m.get("type") or m.get("msgType") or 0
        if msg_type in SYSTEM_MSG_TYPES:
            return None

        frm = m.get("from") or {}
        to = m.get("to") or {}
        from_uid = str(frm.get("uid") if isinstance(frm, dict) else frm or "")
        to_uid = str(to.get("uid") if isinstance(to, dict) else to or "")
        role_f = frm.get("role") if isinstance(frm, dict) else None
        role_t = to.get("role") if isinstance(to, dict) else None

        # 会话维度 uid 优先取买家(role=user),避免 mall 最后一条消息时误用商家 uid
        if role_f == "user" and from_uid and from_uid != "-1":
            uid = from_uid
        elif role_t == "user" and to_uid and to_uid != "-1":
            uid = to_uid
        elif default_uid:
            uid = default_uid
        else:
            uid = from_uid or to_uid
        if not uid or uid == "-1":
            return None

        content = m.get("content")
        if isinstance(content, dict):
            # 富文本/卡片消息时 content 是 dict
            content = content.get("text") or json.dumps(content, ensure_ascii=False)
        content = str(content or "")

        out = {
            "kind": "chat_msg",
            "uid": uid,
            "from_uid": from_uid,
            "to_uid": to_uid,
            "msg_id": str(msg_id),
            "msg_type": msg_type,
            "content": content,
            "ts": m.get("ts") or m.get("time") or 0,
            "raw": m,
        }
        if event_source:
            out["event_source"] = event_source
        return out

    async def _put(self, ev: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(ev)
        except asyncio.QueueFull:
            logger.warning("[net] 队列已满,丢弃事件 {}", ev.get("kind"))

    def close(self) -> None:
        """预留：主循环退出时可调用。"""
        pass
