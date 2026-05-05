"""项目级内置常量：URL、路径、与 .env 同步的默认值。

与 ``core.settings`` 的分工:本模块只放**仓库内置**常量/路径锚点;可运行时覆盖的项在
SQLite ``settings`` 表,由 GUI 读写,空值再回退到此处。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 本文件位于 core/,项目根为其上一级
ROOT = Path(__file__).resolve().parent.parent
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
# GUI 应用图标(拼多多客服 bot)
ASSETS_APP_ICON = ASSETS_DIR / "@assets" / "icon.png"
DB_DIR = ROOT / "db"
DB_PATH = DB_DIR / "pddbot.db"

# S2 引导「如何获取卡券码」教程图：默认使用 @assets/cardguide.JPG，若无则回退 card_code_guide.png
_CARD_GUIDE_PRIMARY = ASSETS_DIR / "@assets" / "cardguide.JPG"
_CARD_GUIDE_LEGACY = ASSETS_DIR / "card_code_guide.png"
CARD_CODE_GUIDE_IMAGE = (
    _CARD_GUIDE_PRIMARY
    if _CARD_GUIDE_PRIMARY.is_file()
    else _CARD_GUIDE_LEGACY
)

for d in (CAPTURES_DIR, LOGS_DIR, ASSETS_DIR, ASSETS_DIR / "@assets", DB_DIR):
    d.mkdir(exist_ok=True)

# ----- 浏览器 -----
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1440, "height": 900}

# ----- 总开关 -----
def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


BOT_ENABLED = _bool("BOT_ENABLED", True)
DRY_RUN = _bool("DRY_RUN", False)

# ----- LLM -----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ----- 行为参数 -----
REPLY_DELAY_MIN = float(os.getenv("REPLY_DELAY_MIN", "1.5"))
REPLY_DELAY_MAX = float(os.getenv("REPLY_DELAY_MAX", "3.5"))
RATE_LIMIT_PER_UID_PER_MIN = int(os.getenv("RATE_LIMIT_PER_UID_PER_MIN", "3"))
RATE_LIMIT_GLOBAL_PER_MIN = int(os.getenv("RATE_LIMIT_GLOBAL_PER_MIN", "30"))

ESCALATE_WEBHOOK = os.getenv("ESCALATE_WEBHOOK", "").strip() or None

_wl = (os.getenv("WHITELIST_UIDS") or "").strip()
WHITELIST_UIDS = {u.strip() for u in _wl.split(",") if u.strip()}
