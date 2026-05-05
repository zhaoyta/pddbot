"""GUI 与 bot 主循环的桥:QThread 里建独立 asyncio event loop 跑 bot.run()。

为啥不用 qasync?
    - 多一个依赖
    - 我们只需要"在后台跑 asyncio 主循环",不需要 Qt 信号触发 async 协程

接口:
    worker = BotWorker()
    worker.state_changed.connect(...)
    worker.error.connect(...)
    worker.start()        # 启动线程,线程内部建 loop 跑 bot.run

    worker.request_stop()  # 通过 loop.call_soon_threadsafe 设 stop_event
    worker.wait_for_stop()

线程在 bot.run() 返回后退出。再启用需重新 new BotWorker。
"""
from __future__ import annotations

import asyncio
import threading
import traceback
from typing import Optional

from loguru import logger
from PySide6.QtCore import QThread, Signal


class BotWorker(QThread):
    # "starting" | "running" | "awaiting_login" | "stopping" | "stopped"
    state_changed = Signal(str)
    error = Signal(str)               # 异常时传错误文本
    login_required = Signal(str)      # 浏览器卡在 login 页时触发,带当前 URL

    def __init__(self, *, force_relogin: bool = False) -> None:
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._loop_ready = threading.Event()
        # bot.run 会把当前活跃的 BrowserSession 放到 ['session']
        self._session_holder: dict = {}
        self._force_relogin = bool(force_relogin)

    # ---------- QThread 入口 ----------
    def run(self) -> None:
        """Qt 在新线程里调这个函数。"""
        self.state_changed.emit("starting")
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._stop_event = asyncio.Event()
            self._loop_ready.set()
            self.state_changed.emit("running")

            # 真正的主循环
            from bot import run as bot_run

            def _on_login_required(url: str) -> None:
                """在 bot 子线程的 loop 里被调用,转成 Qt 信号给 GUI。"""
                try:
                    self.state_changed.emit("awaiting_login")
                    self.login_required.emit(url or "")
                except Exception:
                    pass

            def _on_login_completed() -> None:
                """扫码成功落盘后把界面从「等待扫码」切回「运行中」。"""
                try:
                    self.state_changed.emit("running")
                except Exception:
                    pass

            loop.run_until_complete(
                bot_run(
                    self._stop_event,
                    session_holder=self._session_holder,
                    force_relogin=self._force_relogin,
                    on_login_required=_on_login_required,
                    on_login_completed=_on_login_completed,
                )
            )
        except Exception as e:
            tb = traceback.format_exc()
            logger.exception("[BotWorker] 异常退出: {}", e)
            self.error.emit(f"{type(e).__name__}: {e}\n\n{tb}")
        finally:
            try:
                # 取消所有未完成的任务
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self.state_changed.emit("stopped")
            logger.info("[BotWorker] 线程退出")

    # ---------- 主线程调用 ----------
    def request_stop(self) -> None:
        """从 GUI 主线程异步请求停止 bot 主循环。"""
        if not self.isRunning():
            logger.info("[BotWorker] 未运行,跳过 stop")
            return
        self.state_changed.emit("stopping")
        loop = self._loop
        ev = self._stop_event
        if loop is None or ev is None:
            logger.warning("[BotWorker] loop / stop_event 还没初始化好")
            return
        loop.call_soon_threadsafe(ev.set)

    def wait_for_stop(self, timeout_ms: int = 30000) -> bool:
        return self.wait(timeout_ms)

    # ---------- 跨线程请求保存登录态 ----------
    save_done = Signal(bool)  # 真正保存结果(True/False)

    def request_save_storage_state(self) -> bool:
        """从 GUI 主线程请求把当前 cookies+localStorage 写到 storage_state.json。

        返回 True 表示已成功调度(并不代表已落盘);
        实际结果通过 save_done 信号返回。
        """
        if not self.isRunning():
            logger.warning("[BotWorker] 未运行,无法保存登录态")
            return False
        loop = self._loop
        sess = self._session_holder.get("session")
        if loop is None or sess is None:
            logger.warning("[BotWorker] session 还没就绪")
            return False

        async def _do() -> None:
            ok = await sess.save_storage_state()
            # 信号是线程安全的(Qt 会自动 marshal)
            self.save_done.emit(ok)

        try:
            asyncio.run_coroutine_threadsafe(_do(), loop)
            return True
        except Exception as e:
            logger.warning("[BotWorker] schedule save 失败: {}", e)
            return False
