"""配置层抽象

读取顺序: settings 表 > .env > 内置默认值。

GUI 写到 settings 表里的值运行时即时生效（无需重启）。
.env 仅用于首次启动时的默认值，之后所有改动都通过 GUI 完成。

**与 ``core/config.py`` 的分工**: 该文件只保留项目根路径、内置常量默认值；
运行时可改的业务项一律走本模块 + SQLite `settings` 表（GUI「应用」页可改 URL/路径等）。

约定的 setting key:
    llm.provider           "deepseek"
    llm.base_url           https://api.deepseek.com
    llm.api_key            sk-...
    llm.model              deepseek-chat
    llm.temperature        "0.3"
    llm.max_tokens         "800"

    notify.feishu_webhook  https://open.feishu.cn/open-apis/bot/v2/hook/xxx
    notify.enabled         "true"
    notify.events          "escalate,redeem_fail,session_expired,daily_report"

    bot.enabled            "true"
    bot.dry_run            "false"
    bot.whitelist_uids     "uid1,uid2"

    rate.reply_delay_min   "1.5"
    rate.reply_delay_max   "3.5"
    rate.per_uid_per_min   "3"
    rate.global_per_min    "30"
    （rate.*：可由 .env 写入 settings；当前 bot 主循环未读取，分钟配额/夜间静默等待实现。）

    app.chat_url           ""  (空则 config.CHAT_URL)
    app.login_url          ""  (空则 config.LOGIN_URL)
    app.storage_state_path ""  (空则项目根 storage_state.json)
    app.redeem_page_url    ""  (空则 config.REDEEM_PAGE_URL)
    app.order_list_api     ""  (空则 config.ORDER_LIST_API)
    app.captures_dir       ""  (空则项目 captures/)

    logging.llm_message_file  ""  (空则 logs/llm_message_{time:YYYYMMDD}.log；仅写含 [LLM交互]/[LLM回调] 的行)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core import store as store_mod
from core import config

# ---------- 默认值（兜底）----------
_DEFAULTS: dict[str, str] = {
    "llm.provider": "deepseek",
    "llm.base_url": "https://api.deepseek.com",
    "llm.api_key": "",
    "llm.model": "deepseek-chat",
    "llm.temperature": "0.3",
    "llm.max_tokens": "800",

    "notify.feishu_webhook": "",
    "notify.enabled": "false",
    "notify.events": "escalate,redeem_fail,session_expired,daily_report",

    "bot.enabled": "true",
    "bot.dry_run": "false",
    "bot.whitelist_uids": "",

    "rate.reply_delay_min": "1.5",
    "rate.reply_delay_max": "3.5",
    "rate.per_uid_per_min": "3",
    "rate.global_per_min": "30",

    # ---- 浏览器风控相关 ----
    # browser_channel: "chrome"(优先用本机真实 Chrome) / "chromium"(降级)
    "browser.channel": "chrome",
    # 自定义 UA;空 = 用 chromium 启动后从浏览器拿真实 UA(去掉 HeadlessChrome 字样)
    "browser.user_agent": "",
    "browser.viewport_w": "1440",
    "browser.viewport_h": "900",
    # slow_mo:每个 playwright 操作之间额外等待毫秒,默认 80 ms 让动作不像机器
    "browser.slow_mo_ms": "80",
    # 持久化 user_data_dir(高阶):空 = 不用,走 storage_state.json
    # 用了之后 cookies / localStorage / IndexedDB 全在,指纹更稳
    "browser.user_data_dir": "",
    # 启动时随机延迟范围(秒):热身,降低自动化感
    "browser.warmup_delay_min": "1.5",
    "browser.warmup_delay_max": "3.5",
    # HTTP/HTTPS 代理 URL,如 "http://user:pass@host:port" 或 socks5://;空 = 不用
    "browser.proxy": "",
    # 时区/语言/locale
    "browser.timezone": "Asia/Shanghai",
    "browser.locale": "zh-CN",

    # ---- 应用级 URL / 路径(空字符串 = 使用 core/config.py 内置默认) ----
    "app.chat_url": "",
    "app.login_url": "",
    # 登录态 JSON 路径:相对项目根或绝对路径;空 = 根目录 storage_state.json
    "app.storage_state_path": "",
    "app.redeem_page_url": "",
    "app.order_list_api": "",
    # 抓包等输出目录;空 = 项目下 captures/
    "app.captures_dir": "",
    # LLM 专用日志文件路径 pattern；空 = LOGS_DIR 下按日 llm_message_{time:YYYYMMDD}.log
    "logging.llm_message_file": "",
}

# 把 .env 里的值映射到 settings key（首次启动 / 兜底用）
_ENV_MAP: dict[str, str] = {
    "llm.api_key": "DEEPSEEK_API_KEY",
    "llm.base_url": "DEEPSEEK_BASE_URL",
    "llm.model": "DEEPSEEK_MODEL",
    "notify.feishu_webhook": "ESCALATE_WEBHOOK",
    "bot.enabled": "BOT_ENABLED",
    "bot.dry_run": "DRY_RUN",
    "bot.whitelist_uids": "WHITELIST_UIDS",
    "rate.reply_delay_min": "REPLY_DELAY_MIN",
    "rate.reply_delay_max": "REPLY_DELAY_MAX",
    "rate.per_uid_per_min": "RATE_LIMIT_PER_UID_PER_MIN",
    "rate.global_per_min": "RATE_LIMIT_GLOBAL_PER_MIN",
}


def get(key: str, default: str | None = None) -> str:
    """读一个配置项,优先级:settings 表 > .env > _DEFAULTS > default"""
    s = store_mod.get()
    v = s.get_setting(key)
    if v is not None:
        return v

    env_name = _ENV_MAP.get(key)
    if env_name:
        env_v = os.getenv(env_name)
        if env_v is not None and env_v != "":
            return env_v

    if key in _DEFAULTS:
        return _DEFAULTS[key]
    return default if default is not None else ""


def get_bool(key: str, default: bool = False) -> bool:
    v = get(key)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, str(default)))
    except (ValueError, TypeError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _path_under_root(rel_or_abs: str, *, default: Path) -> Path:
    """rel_or_abs 为空则用 default;否则解析为绝对路径(相对项目根)。"""
    v = (rel_or_abs or "").strip()
    if not v:
        return default
    p = Path(v)
    return p if p.is_absolute() else (config.ROOT / p)


def chat_url() -> str:
    v = get("app.chat_url", "").strip()
    return v or config.CHAT_URL


def login_url() -> str:
    v = get("app.login_url", "").strip()
    return v or config.LOGIN_URL


def storage_state_path() -> Path:
    return _path_under_root(
        get("app.storage_state_path", ""),
        default=config.STORAGE_STATE_PATH,
    )


def redeem_page_url() -> str:
    v = get("app.redeem_page_url", "").strip()
    return v or config.REDEEM_PAGE_URL


def order_list_api_url() -> str:
    v = get("app.order_list_api", "").strip()
    return v or config.ORDER_LIST_API


def captures_dir() -> Path:
    d = _path_under_root(
        get("app.captures_dir", ""),
        default=config.CAPTURES_DIR,
    )
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def set(key: str, value: Any) -> None:
    s = store_mod.get()
    if value is None:
        s.set_setting(key, None)
    else:
        s.set_setting(key, str(value))


def all() -> dict[str, str]:
    """返回所有 settings（含 .env 与默认值合并后的）。GUI 表单初始化用。"""
    s = store_mod.get()
    db_values = s.all_settings()
    result = {}
    for k in _DEFAULTS:
        if k in db_values and db_values[k] is not None:
            result[k] = db_values[k]
        else:
            env_name = _ENV_MAP.get(k)
            env_v = os.getenv(env_name) if env_name else None
            if env_v:
                result[k] = env_v
            else:
                result[k] = _DEFAULTS[k]
    return result


def initialize_from_env() -> None:
    """首次启动时,如果 settings 表为空,把 .env 里的默认值写一份到 settings."""
    s = store_mod.get()
    existing = s.all_settings()
    if existing:
        return  # 已经初始化过了

    for setting_key, env_name in _ENV_MAP.items():
        env_v = os.getenv(env_name)
        if env_v is not None and env_v != "":
            s.set_setting(setting_key, env_v)
