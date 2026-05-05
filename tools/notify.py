"""飞书 webhook 通知

用法:
    from tools.notify import send_feishu, EVENTS
    send_feishu("escalate", title="转人工", text="uid=xxx 触发售后规则")

事件类型(EVENTS):
    escalate          转人工告警
    redeem_fail       核销失败
    session_expired   登录过期
    daily_report      每日 23:00 汇总

读取配置 (来自 core.settings):
    notify.feishu_webhook
    notify.enabled         "true"/"false"
    notify.events          逗号分隔需要推的事件
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from core import settings


EVENTS: dict[str, str] = {
    "escalate": "转人工告警",
    "redeem_fail": "核销失败",
    "session_expired": "登录过期",
    "daily_report": "每日报表",
}


def _enabled_events() -> set[str]:
    s = settings.get("notify.events") or ""
    return {x.strip() for x in s.split(",") if x.strip()}


def _post(webhook: str, payload: dict[str, Any], timeout: float = 6.0) -> tuple[bool, str]:
    """直接用 stdlib 发(避免再装 requests)"""
    import urllib.request
    import urllib.error

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return resp.status == 200, text
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_feishu(event: str, *, title: str, text: str,
                webhook: str | None = None) -> bool:
    """推一条飞书消息。

    - 检查总开关 notify.enabled
    - 检查事件白名单 notify.events
    - 没配 webhook 则直接跳过
    """
    if event not in EVENTS:
        logger.warning("[notify] 未知事件 {!r},仍尝试发送", event)

    if webhook is None:
        if not settings.get_bool("notify.enabled", False):
            logger.debug("[notify] notify.enabled=false 跳过")
            return False
        if event not in _enabled_events() and event in EVENTS:
            logger.debug("[notify] 事件 {} 不在白名单 跳过", event)
            return False
        webhook = settings.get("notify.feishu_webhook") or ""

    if not webhook:
        logger.warning("[notify] 没配置 webhook,跳过")
        return False

    payload = {
        "msg_type": "text",
        "content": {"text": f"[{title}]\n{text}"},
    }
    ok, resp = _post(webhook, payload)
    if ok:
        logger.info("[notify] 发送成功 event={} resp={}", event, resp[:200])
    else:
        logger.warning("[notify] 发送失败 event={} resp={}", event, resp[:200])
    return ok


def test_send(webhook: str) -> tuple[bool, str]:
    """飞书页「测试发送」按钮专用,不走 notify.enabled / events 校验。"""
    payload = {
        "msg_type": "text",
        "content": {
            "text": "[pddbot 测试消息]\n如果你看到这条消息,说明 webhook 配置无误 ✅"
        },
    }
    ok, resp = _post(webhook, payload)
    return ok, resp
