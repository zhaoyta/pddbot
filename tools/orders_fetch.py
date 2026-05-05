"""右侧订单区：点「最新订单」→「个人订单」→ 拦截 ``userAllOrder`` 取订单。

与聊天工作台 DOM 一致（见 captures / 开发者工具）::

    1. **左侧**点会话由 ``bot`` 先调用 ``session_dom.activate_session``，本模块不负责。
    2. **右侧** ``#right-panel`` 顶栏 ``.bar-box li.bar-item`` → 点 **「最新订单」**。
    3. 同面板内 ``ul.order-panel-header li.order-panel-second-bar`` → 点 **「个人订单」**。
    4. 监听 ``https://mms.pinduoduo.com/latitude/order/userAllOrder``（以路径
       ``/latitude/order/userAllOrder`` 匹配），解析 JSON，取该客户最新一笔订单。

使用 ``page.expect_response`` 在当前协程内等待这一条响应，不把结果塞回
``NetworkRouter`` 的 ``asyncio.Queue``，避免与主循环 ``queue.get()`` 互相等待。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import Page, Response

from core import config

USER_ALL_ORDER_PATH = urlparse(config.ORDER_LIST_API).path


def _order_sort_key(o: dict[str, Any]) -> int:
    v = o.get("orderTime") or o.get("createTime") or 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def pick_latest_order(orders: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not orders:
        return None
    return max(orders, key=_order_sort_key)


def latest_from_user_all_order_body(body: dict[str, Any]) -> dict[str, Any] | None:
    result = body.get("result") or body.get("data") or {}
    orders = (
        result.get("orderList")
        or result.get("list")
        or result.get("orders")
        or []
    )
    if not isinstance(orders, list) or not orders:
        return None
    return pick_latest_order(orders)


def _user_all_order_pred(uid: str) -> Callable[[Response], bool]:
    uid = str(uid).strip()

    def pred(resp: Response) -> bool:
        if USER_ALL_ORDER_PATH not in resp.url:
            return False
        if resp.status != 200:
            return False
        try:
            post = resp.request.post_data_json
        except Exception:
            return True
        if not isinstance(post, dict):
            return True
        u = str(post.get("uid") or post.get("user_id") or "")
        if not u:
            return True
        return u == uid

    return pred


async def _parse_response_json(resp: Response) -> dict[str, Any] | None:
    try:
        return await resp.json()
    except Exception:
        return None


async def _click_latest_orders_tab(page: Page) -> None:
    """右侧面板顶栏四个 tab 里的「最新订单」。"""
    root = page.locator("#right-panel")
    candidates = (
        root.locator(".bar-box li.bar-item").filter(has_text="最新订单").first,
        root.locator("li.bar-item").filter(has_text="最新订单").first,
    )
    last: Exception | None = None
    for loc in candidates:
        try:
            await loc.wait_for(state="visible", timeout=4000)
            await loc.click(timeout=4000)
            return
        except Exception as e:
            last = e
            continue
    raise RuntimeError("未找到可点击的「最新订单」(#right-panel .bar-item)") from last


async def _click_personal_orders_tab(page: Page) -> None:
    """「最新订单」展开后二级 tab「个人订单」。"""
    root = page.locator("#right-panel")
    loc = root.locator(
        "ul.order-panel-header li.order-panel-second-bar",
    ).filter(has_text="个人订单").first
    await loc.wait_for(state="visible", timeout=5000)
    await loc.click(timeout=4000)


async def refresh_latest_order_via_ui(page: Page, uid: str) -> dict[str, Any] | None:
    """先点「最新订单」再点「个人订单」，等首条匹配的 ``userAllOrder`` 响应。"""
    uid = str(uid).strip()
    if not uid:
        return None

    pred = _user_all_order_pred(uid)

    try:
        async with page.expect_response(pred, timeout=18000) as ri:
            await _click_latest_orders_tab(page)
            await asyncio.sleep(0.22)
            await _click_personal_orders_tab(page)
        resp = await ri.value
    except Exception as e:
        logger.warning(
            "[orders_fetch] uid={} 等待 {} 失败: {}",
            uid,
            USER_ALL_ORDER_PATH,
            e,
        )
        return None

    body = await _parse_response_json(resp)
    if not body:
        return None
    order = latest_from_user_all_order_body(body)
    if order:
        logger.info(
            "[orders_fetch] uid={} order_sn={}",
            uid,
            order.get("orderSn") or order.get("order_sn") or "?",
        )
    else:
        logger.debug("[orders_fetch] uid={} userAllOrder 无订单条目", uid)
    return order
