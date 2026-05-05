"""Playwright 浏览器会话封装(含反检测加固)

职责:
    - 起浏览器:优先本机真实 Chrome(channel="chrome"),失败降级 chromium
    - 加载 storage_state.json (复用扫码登录态);可选 user_data_dir 持久化模式
    - 注入 stealth.js 抹除 webdriver / plugins / chrome 等指纹
    - 配置 viewport / locale / timezone / proxy / slow_mo,降低自动化痕迹
    - 启动后做"温身"动作:随机延迟 + 隐式鼠标移动
    - 自动检测 login 跳转,扫码完成后立即落盘
    - 周期(默认 5 分钟)保存一次登录态,防丢失
    - 暴露 page / context 给 NetworkRouter / 业务工具用

异常时上下文会自动 close。
"""
from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from core import settings
from runtime.stealth import build_stealth_js

# 登录页 URL 关键字(命中即认为还在登录页,用于首跳判断)
LOGIN_URL_KEY = "/login"
# 扫码登录的等待上限(秒);超时仍在 login 页则抛错
SCAN_LOGIN_TIMEOUT = 600  # 10 分钟
# 周期保存登录态的间隔(秒)
PERIODIC_SAVE_INTERVAL = 300  # 5 分钟

# Playwright 默认会塞 --enable-automation 等明显的自动化标识,这些都得屏蔽
IGNORE_DEFAULT_ARGS = [
    "--enable-automation",
]
# 我们手动给的 args(注意不要加 --no-sandbox,是个反检测点)
SAFE_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    # 让 UA 里不要出现 "HeadlessChrome"
    "--disable-features=UserAgentClientHintsGREASEUpdate",
]

# 真 Chrome 在 macOS 上的安装路径,channel="chrome" 失败时手动 fallback 用
MACOS_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
]


def _looks_left_login_page(url: str, *, saw_login_path: bool) -> bool:
    """判断是否已离开「扫码登录」页、saw_login_path 表示本轮等待中曾出现过含 /login 的地址。"""
    if not url:
        return False
    low = url.lower()
    if "chat-merchant" in low:
        return True
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        if "mms.pinduoduo.com" not in host and "yangkeduo.com" not in host:
            return False
        frag = (p.fragment or "").lower()
        combined = f"{p.path}{frag}".lower()
        if "/login" in combined:
            return False
        # path 已是业务路径
        if p.path and p.path not in ("/", ""):
            return True
        # 根 URL + hash 路由(如 #/chat-merchant/...),path 为 / 但 fragment 已进业务
        if frag and len(frag) > 2:
            return True
        # 兜底:整段 href 已不再出现 /login(须先见过 /login,避免首跳误判)
        if saw_login_path and "/login" not in low:
            return True
    except Exception:
        return False
    return False


class BrowserSession:
    """Playwright + 浏览器上下文的薄封装(已加反检测)。"""

    def __init__(
        self,
        *,
        headless: bool = False,
        chat_url: str | None = None,
        storage_state_path: Path | None = None,
        slow_mo_ms: int | None = None,
        auto_save_interval: int = PERIODIC_SAVE_INTERVAL,
        force_relogin: bool = False,
        on_login_required: Any = None,
        on_login_completed: Any = None,
    ) -> None:
        """
        force_relogin:    True 时忽略已保存的 storage_state,直接打开 login 页等扫码
        on_login_required: 可选回调,当检测到 login 页时调用(用于 GUI 切到 awaiting_login 状态)
        on_login_completed: 可选回调,扫码完成并已保存登录态后调用(用于 GUI 切回 running)
        """
        self.headless = headless
        self.chat_url = chat_url or settings.chat_url()
        self.storage_state_path = storage_state_path or settings.storage_state_path()
        # slow_mo 优先用 settings 配置,默认 80ms
        self.slow_mo_ms = (slow_mo_ms if slow_mo_ms is not None
                           else settings.get_int("browser.slow_mo_ms", 80))
        self.auto_save_interval = auto_save_interval
        self.force_relogin = force_relogin
        self.on_login_required = on_login_required
        self.on_login_completed = on_login_completed

        self._pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._save_task: asyncio.Task | None = None
        self._save_lock = asyncio.Lock()
        self._using_persistent_context = False

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        # 计算这次启动的"登录策略":
        #   reuse_state = True  → 加载已有 storage_state,失效时自动等扫码
        #   reuse_state = False → 全新登录(force_relogin) 或文件根本不存在
        user_data_dir = (settings.get("browser.user_data_dir") or "").strip()
        has_state = self.storage_state_path.exists()
        reuse_state = (
            (not self.force_relogin)
            and bool(user_data_dir or has_state)
        )
        self._reuse_state = reuse_state
        if not reuse_state:
            logger.info(
                "[browser] 本次启动将进行全新扫码登录 "
                "(force_relogin={}, has_state={}, user_data_dir={!r})",
                self.force_relogin, has_state, user_data_dir or "(无)",
            )

        # 读 settings 里的风控选项
        channel = (settings.get("browser.channel") or "chromium").strip()
        viewport = {
            "width": settings.get_int("browser.viewport_w", 1440),
            "height": settings.get_int("browser.viewport_h", 900),
        }
        proxy_str = (settings.get("browser.proxy") or "").strip()
        proxy = {"server": proxy_str} if proxy_str else None
        timezone_id = (settings.get("browser.timezone") or "Asia/Shanghai")
        locale = (settings.get("browser.locale") or "zh-CN")

        logger.info(
            "[browser] 启动 channel={} headless={} slow_mo={}ms viewport={}x{} "
            "user_data_dir={!r} proxy={!r}",
            channel, self.headless, self.slow_mo_ms,
            viewport["width"], viewport["height"],
            user_data_dir or "(无)", proxy_str or "(无)",
        )

        self._pw = await async_playwright().start()

        launch_kw: dict[str, Any] = dict(
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
            args=list(SAFE_ARGS),
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
        if proxy:
            launch_kw["proxy"] = proxy

        # 优先用真实 Chrome(channel="chrome"),不行则降级 chromium
        if user_data_dir:
            self._using_persistent_context = True
            self.context = await self._launch_persistent(
                channel=channel,
                user_data_dir=user_data_dir,
                viewport=viewport,
                locale=locale,
                timezone_id=timezone_id,
                launch_kw=launch_kw,
            )
            # 持久化模式下 context 已直接创建,page 也由它管
            pages = self.context.pages
            self.page = pages[0] if pages else await self.context.new_page()
        else:
            self.browser = await self._launch_browser(channel, launch_kw)
            self.context = await self._new_context(
                viewport=viewport, locale=locale, timezone_id=timezone_id,
            )
            self.page = await self.context.new_page()

        # 注入 stealth.js(每个 frame document_start 阶段执行)
        await self.context.add_init_script(build_stealth_js())

        # 温身:随机延迟,模拟"用户打开浏览器后停顿一下再点书签"
        await self._warmup_delay()

        logger.info("[browser] 跳转聊天页 {}", self.chat_url)
        await self.page.goto(self.chat_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(random.uniform(1.0, 2.0))
        logger.info("[browser] 当前 URL={}", self.page.url)

        # 隐式人类行为:鼠标随机移到一个无害位置
        await self._idle_mouse_move()

        # 如果跳到了 login 页(或主动选择重新登录),等用户扫码,扫完立即落盘
        if LOGIN_URL_KEY in self.page.url:
            # 通知外部(GUI)切到 awaiting_login 状态,显示提示
            if callable(self.on_login_required):
                try:
                    self.on_login_required(self.page.url)
                except Exception as e:
                    logger.debug("[browser] on_login_required 回调异常: {}", e)
            await self._wait_login_then_save()

        # 启动周期保存任务
        if self.auto_save_interval > 0 and not self._using_persistent_context:
            self._save_task = asyncio.create_task(self._periodic_save())

    # ---------- 启动子步骤 ----------
    async def _launch_browser(self, channel: str, launch_kw: dict) -> Browser:
        """常规模式:launch + new_context。"""
        # 1) channel="chrome" 走 playwright 自动找 Chrome
        if channel and channel != "chromium":
            try:
                return await self._pw.chromium.launch(channel=channel, **launch_kw)
            except Exception as e:
                logger.warning("[browser] channel={} 启动失败({}),降级 chromium",
                               channel, e)

        # 2) macOS 手动找 Chrome 路径
        for p in MACOS_CHROME_PATHS:
            if Path(p).exists():
                try:
                    return await self._pw.chromium.launch(
                        executable_path=p, **launch_kw,
                    )
                except Exception as e:
                    logger.warning("[browser] 用 {} 启动失败({}),降级", p, e)

        # 3) 兜底 chromium
        return await self._pw.chromium.launch(**launch_kw)

    async def _new_context(self, *, viewport, locale, timezone_id) -> BrowserContext:
        ctx_kw: dict[str, Any] = dict(
            viewport=viewport,
            locale=locale,
            timezone_id=timezone_id,
        )
        # 仅"复用模式"且文件存在时才加载 storage_state
        if getattr(self, "_reuse_state", True) and self.storage_state_path.exists():
            ctx_kw["storage_state"] = str(self.storage_state_path)
        ua = (settings.get("browser.user_agent") or "").strip()
        if ua:
            ctx_kw["user_agent"] = ua
        else:
            # 没配 UA 时,用 chromium 默认 UA 但去掉 HeadlessChrome 字样
            # (在 add_init_script 里通过 navigator.userAgent 也能改,这里 context 层
            # 同步改更彻底,Network 拦截到的 UA 也跟着改)
            ua_fix = await self._derive_clean_ua()
            if ua_fix:
                ctx_kw["user_agent"] = ua_fix
        return await self.browser.new_context(**ctx_kw)

    async def _launch_persistent(self, *, channel: str, user_data_dir: str,
                                  viewport, locale, timezone_id,
                                  launch_kw: dict) -> BrowserContext:
        """user_data_dir 持久化模式:用 launch_persistent_context 直出 context。"""
        kw: dict[str, Any] = dict(
            user_data_dir=user_data_dir,
            viewport=viewport,
            locale=locale,
            timezone_id=timezone_id,
            **launch_kw,
        )
        ua = (settings.get("browser.user_agent") or "").strip()
        if ua:
            kw["user_agent"] = ua

        if channel and channel != "chromium":
            try:
                return await self._pw.chromium.launch_persistent_context(
                    channel=channel, **kw,
                )
            except Exception as e:
                logger.warning("[browser] persistent channel={} 失败({}),降级 chromium",
                               channel, e)

        return await self._pw.chromium.launch_persistent_context(**kw)

    async def _derive_clean_ua(self) -> str | None:
        """启动后从 about:blank 拿到 chromium 真实 UA,去掉 HeadlessChrome 字样。"""
        try:
            tmp_ctx = await self.browser.new_context()
            tmp_page = await tmp_ctx.new_page()
            ua = await tmp_page.evaluate("() => navigator.userAgent")
            await tmp_ctx.close()
            if isinstance(ua, str) and ua:
                ua = ua.replace("HeadlessChrome", "Chrome")
                return ua
        except Exception as e:
            logger.debug("[browser] _derive_clean_ua 失败: {}", e)
        return None

    async def _warmup_delay(self) -> None:
        lo = settings.get_float("browser.warmup_delay_min", 1.5)
        hi = settings.get_float("browser.warmup_delay_max", 3.5)
        if hi < lo:
            hi = lo
        d = random.uniform(lo, hi)
        logger.debug("[browser] 温身延迟 {:.2f}s", d)
        await asyncio.sleep(d)

    async def _idle_mouse_move(self) -> None:
        """启动后随机往一个无害位置挪一下鼠标,降低"打开浏览器立马动作"的痕迹。"""
        try:
            x = random.randint(200, 800)
            y = random.randint(150, 400)
            await self.page.mouse.move(x, y, steps=random.randint(8, 16))
        except Exception as e:
            logger.debug("[browser] idle_mouse_move 失败: {}", e)

    async def _wait_login_then_save(self) -> None:
        """检测到当前在 login 页,等用户手动扫码 → 跳转走后立即保存登录态。"""
        if self.force_relogin:
            reason = "已选择「重新扫码登录」"
        elif not self.storage_state_path.exists():
            reason = "未找到已保存的登录态"
        else:
            reason = "cookies 已过期"
        logger.warning(
            "[browser] {},请在浏览器手动扫码登录 (最多等 {} 秒)",
            reason, SCAN_LOGIN_TIMEOUT,
        )
        deadline = time.monotonic() + float(SCAN_LOGIN_TIMEOUT)
        last_url = ""
        saw_login_path = False
        while time.monotonic() < deadline:
            url = self.page.url
            if LOGIN_URL_KEY in url:
                saw_login_path = True
            if url != last_url:
                logger.info("[browser] 等扫码中… URL={}", url)
                last_url = url
            if _looks_left_login_page(url, saw_login_path=saw_login_path):
                logger.info("[browser] 判定已离开登录页 URL={}", url)
                break
            await asyncio.sleep(0.35)
        else:
            err = f"等待登录超时({SCAN_LOGIN_TIMEOUT}s),最后 URL={last_url}"
            logger.error("[browser] {}", err)
            raise TimeoutError(err)

        # 先通知 GUI 切回「运行中」,再 sleep + 落盘(避免界面长时间卡在「等待扫码」)
        if callable(self.on_login_completed):
            try:
                self.on_login_completed()
            except Exception as e:
                logger.warning("[browser] on_login_completed 回调异常: {}", e)

        logger.info("[browser] 等待页面稳定后保存登录态…")
        await asyncio.sleep(3.0)
        await self.save_storage_state()

    async def save_storage_state(self) -> bool:
        """主动把当前 cookies + localStorage 写到 storage_state.json。

        线程/任务安全:多个 caller 并发调用不会互相打架。
        """
        if not self.context:
            logger.warning("[browser] context 未就绪,无法保存登录态")
            return False
        async with self._save_lock:
            try:
                await self.context.storage_state(
                    path=str(self.storage_state_path)
                )
                logger.info("[browser] 已保存登录态 → {}",
                            self.storage_state_path)
                return True
            except Exception as e:
                logger.warning("[browser] 保存登录态失败: {}", e)
                return False

    async def _periodic_save(self) -> None:
        """每 N 秒静默保存一次登录态。task 被 cancel 时自然退出。"""
        try:
            while True:
                await asyncio.sleep(self.auto_save_interval)
                if self.context is None:
                    return
                ok = await self.save_storage_state()
                if ok:
                    logger.debug("[browser] 周期保存成功")
        except asyncio.CancelledError:
            logger.debug("[browser] 周期保存任务已取消")

    async def stop(self) -> None:
        # 先停周期保存任务
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
            try:
                await self._save_task
            except (asyncio.CancelledError, Exception):
                pass
        self._save_task = None

        try:
            if self.context:
                logger.info("[browser] 退出前最后保存一次登录态")
                await self.save_storage_state()
                await self.context.close()
        except Exception as e:
            logger.warning("[browser] context.close 异常: {}", e)
        try:
            if self.browser:
                await self.browser.close()
        except Exception as e:
            logger.warning("[browser] browser.close 异常: {}", e)
        try:
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            logger.warning("[browser] playwright.stop 异常: {}", e)
        self._pw = self.browser = self.context = self.page = None
        logger.info("[browser] 已停止")

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()
