"""项目级配置：URL、路径、运行参数"""
from __future__ import annotations

import os
from datetime import time as dtime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# ----- URL -----
CHAT_URL = "https://mms.pinduoduo.com/chat-merchant/index.html"
LOGIN_URL = "https://mms.pinduoduo.com/login"
ORDER_LIST_API = "https://mms.pinduoduo.com/latitude/order/userAllOrder"
CHAT_LIST_API = "https://mms.pinduoduo.com/plateau/chat/list"
REDEEM_PAGE_URL = "https://mms.pinduoduo.com/orders/order/verify"

# ----- 路径 -----
STORAGE_STATE_PATH = ROOT / "storage_state.json"
CAPTURES_DIR = ROOT / "captures"
LOGS_DIR = ROOT / "logs"
ASSETS_DIR = ROOT / "assets"
CATALOG_DIR = ROOT / "catalog"
DB_PATH = ROOT / "pddbot.db"

CARD_CODE_GUIDE_IMAGE = ASSETS_DIR / "card_code_guide.png"
PRODUCT_MAP_PATH = CATALOG_DIR / "product_map.json"

for d in (CAPTURES_DIR, LOGS_DIR, ASSETS_DIR, CATALOG_DIR):
    d.mkdir(exist_ok=True)

# ----- 浏览器 -----
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1440, "height": 900}

# ----- LLM -----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ----- 行为参数 -----
REPLY_DELAY_MIN = float(os.getenv("REPLY_DELAY_MIN", "1.5"))
REPLY_DELAY_MAX = float(os.getenv("REPLY_DELAY_MAX", "3.5"))
RATE_LIMIT_PER_UID_PER_MIN = int(os.getenv("RATE_LIMIT_PER_UID_PER_MIN", "3"))
RATE_LIMIT_GLOBAL_PER_MIN = int(os.getenv("RATE_LIMIT_GLOBAL_PER_MIN", "30"))


def _parse_time(s: str | None) -> dtime | None:
    if not s:
        return None
    try:
        h, m = s.split(":")
        return dtime(int(h), int(m))
    except Exception:
        return None


NIGHT_QUIET_START = _parse_time(os.getenv("NIGHT_QUIET_START", "23:00"))
NIGHT_QUIET_END = _parse_time(os.getenv("NIGHT_QUIET_END", "08:00"))

ESCALATE_WEBHOOK = os.getenv("ESCALATE_WEBHOOK", "").strip() or None

_wl = (os.getenv("WHITELIST_UIDS") or "").strip()
WHITELIST_UIDS = {u.strip() for u in _wl.split(",") if u.strip()}
