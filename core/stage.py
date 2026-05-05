"""阶段决策器 (StageMachine)

依据 D1~D7 决策规则,根据 (uid, 最新消息, 订单, conv_state) 决定下一步走哪个 stage。

stage:
    S0_GREET     首次接待问候
    S1_CONSULT   咨询答疑(尚未下单)
    S2_GUIDE     已下单未核销,引导发卡密
    S3_REDEEM    收到客户卡密,准备核销
    S4_DELIVER   已核销,发资料
    S_HUMAN      转人工(售后/敏感词/链接失效)

输入 context dict:
    uid:               str
    latest_message:    str  (客户刚发的最后一条消息文本,可空)
    order:             dict | None  (最新一笔订单,见 tools/orders.py)
    conv_state:        dict | None  (DB 里 conv_state 表的行,可能没记录)
    history_user_msgs: list[str]   (该 uid 最近 N 条客户消息)
"""
from __future__ import annotations

import re
from typing import Any

# 卡密识别(D8 在 architecture §7,先用宽松规则)
CARD_CODE_PATTERNS = [
    re.compile(r"\b\d{12}\b"),
    re.compile(r"\b\d{10,18}\b"),
]

# 转人工敏感词(D2 / D3 / 投诉 等)
ESCALATE_KEYWORDS = (
    "投诉", "曝光", "12315", "差评", "315",
    "链接失效", "打不开", "下载不了", "下载不了", "没收到", "再发一份", "重发",
    "退款", "退钱", "举报",
)


def detect_card_code(text: str) -> str | None:
    if not text:
        return None
    text = text.strip()
    for p in CARD_CODE_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(0)
    return None


def has_escalate_keyword(text: str) -> str | None:
    """命中返回触发的关键词,未命中返回 None"""
    if not text:
        return None
    for kw in ESCALATE_KEYWORDS:
        if kw in text:
            return kw
    return None


def has_after_sales(order: dict | None) -> bool:
    """D3:售后单或赔付单存在 → 转人工"""
    if not order:
        return False
    if order.get("afterSalesInfo") is not None:
        return True
    comp = order.get("compensateInfo") or {}
    if isinstance(comp, dict) and comp.get("status"):
        return True
    return False


def is_redeemed(order: dict | None) -> bool:
    """订单是否已核销 (S4 的判据)。

    注意:不能简单用 "核销" 关键词,因为 "待核销" 也含 "核销"。
    用更精确的成品状态。

    拼多多 ``userAllOrder`` 在卡券核销后仍常为「已发货,待签收」,
    因此 **必须以本地 ``order_state.redeemed_at`` 为准**
    (由 ``submit_card_code`` 成功后写入)。
    """
    if not order:
        return False
    sn = str(order.get("orderSn") or "").strip()
    if sn:
        try:
            from core import store as store_mod

            if store_mod.get().is_order_redeemed(sn):
                return True
        except Exception:
            pass
    status = (order.get("orderStatusStr") or "").strip()
    REDEEMED_STATUSES = (
        "已核销", "核销成功", "已使用", "已完成", "已收货", "交易成功",
    )
    if any(s in status for s in REDEEMED_STATUSES):
        return True
    # 有核销门店信息一般表示已核销
    if order.get("storeId") or order.get("storeName"):
        return True
    return False


def decide(context: dict[str, Any]) -> tuple[str, str]:
    """主决策入口。

    返回 (stage, reason) -- reason 是中文一句话,落 action_log 时方便排查。
    """
    uid = context.get("uid", "?")
    latest = (context.get("latest_message") or "").strip()
    order = context.get("order")
    conv = context.get("conv_state")

    # D2: 客户说链接失效/再发一份 → 转人工
    kw = has_escalate_keyword(latest)
    if kw:
        return ("S_HUMAN", f"敏感词命中:{kw}")

    # D3: 有售后单/赔付 → 转人工
    if has_after_sales(order):
        return ("S_HUMAN", "订单存在售后/赔付")

    # D4: 首次接待 → S0
    if conv is None or not conv.get("last_msg_id"):
        return ("S0_GREET", "首次接待")

    # 没订单 → S1 咨询
    if not order:
        return ("S1_CONSULT", "无订单,走咨询答疑")

    # 已核销且资料已发过 → 当普通咨询 (避免重复发网盘整段)
    sn = str(order.get("orderSn") or "").strip()
    if sn:
        try:
            from core import store as store_mod

            st = store_mod.get()
            if st.is_order_redeemed(sn) and st.is_order_delivered(sn):
                return ("S1_CONSULT", "订单已核销且已发过资料,答疑接待")
        except Exception:
            pass

    # 已核销 → S4 发资料
    if is_redeemed(order):
        return ("S4_DELIVER", "订单已核销,发资料")

    # 客户消息里有疑似卡密 → S3
    if detect_card_code(latest):
        return ("S3_REDEEM", "检测到疑似卡密,走核销")

    # 已下单未核销且没收到卡密 → S2 引导
    return ("S2_GUIDE", "已下单未核销,引导发卡密")
