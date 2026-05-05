"""主窗口：左侧导航 + 右侧 Stack + 底部状态栏"""
from __future__ import annotations

from loguru import logger
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from core import settings
from gui.pages.home import HomePage
from gui.pages.settings import SettingsPage
from gui.pages.catalog import CatalogPage
from gui.pages.qa import QaPage
from gui.pages.model import ModelPage
from gui.pages.stage_reply import StageReplyPage
from gui.pages.notify import NotifyPage
from gui.pages.antidetect import AntiDetectPage
from gui.pages.app_config import AppConfigPage
from gui.pages.conversations import ConversationsPage
from gui.pages.logs import LogsPage


# 导航项: (label, factory)
NAV_LABEL_SETTINGS = "⚙  设置"

NAV_ITEMS = [
    ("🏠  首页", HomePage),
    ("🛍  商品", CatalogPage),
    ("📚  问答", QaPage),
    ("🤖  模型", ModelPage),
    ("💬  阶段", StageReplyPage),
    ("🔔  飞书", NotifyPage),
    ("🛡  风控", AntiDetectPage),
    ("⚙  应用", AppConfigPage),
    ("📜  会话", ConversationsPage),
    ("📋  日志", LogsPage),
    (NAV_LABEL_SETTINGS, SettingsPage),
]


class MainWindow(QMainWindow):
    bot_state_changed = Signal(str)  # "stopped" | "starting" | "running" | "stopping"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("pddbot - 拼多多客服自动回复")
        self.resize(1200, 760)

        self._build_ui()

        # 把状态信号连给状态栏 + 各页（首页要联动按钮文字）
        self.bot_state_changed.connect(self._on_bot_state_changed)

        # 初始状态
        self._set_bot_state("stopped")

    # ---------- UI ----------
    def _build_ui(self) -> None:
        # 左侧导航
        self.nav = QListWidget()
        self.nav.setFixedWidth(160)
        self.nav.setStyleSheet(
            """
            QListWidget {
                background: #f5f5f7;
                border: none;
                outline: 0;
                font-size: 14px;
            }
            QListWidget::item {
                padding: 12px 16px;
                border-radius: 6px;
                margin: 2px 6px;
            }
            QListWidget::item:selected {
                background: #2196f3;
                color: white;
            }
            QListWidget::item:hover:!selected {
                background: #e3e3e8;
            }
            """
        )

        # 右侧 Stack
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}

        for label, factory in NAV_ITEMS:
            QListWidgetItem(label, self.nav)
            page = factory(self)  # 把 main window 传进去,方便页面互相通信
            self.stack.addWidget(page)
            self.pages[label] = page

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # 主体容器
        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(body)

        # 状态栏
        sb = QStatusBar()
        self.lbl_state = QLabel("○ 已停止")
        self.lbl_stats = QLabel("今日回复 0 条 | 转人工 0 次")
        sb.addWidget(self.lbl_state)
        sb.addPermanentWidget(self.lbl_stats)
        self.setStatusBar(sb)

    def open_settings_page(self) -> None:
        """从首页等跳转「设置」导航项。"""
        for i in range(self.nav.count()):
            if self.nav.item(i).text() == NAV_LABEL_SETTINGS:
                self.nav.setCurrentRow(i)
                return

    # ---------- 状态联动 ----------
    def _set_bot_state(self, state: str) -> None:
        self.bot_state = state
        self.bot_state_changed.emit(state)

    def _on_bot_state_changed(self, state: str) -> None:
        text = {
            "stopped": "○ 已停止",
            "starting": "◐ 启动中…",
            "running": "● 运行中",
            "awaiting_login": "◓ 等待扫码…",
            "stopping": "◑ 关闭中…",
        }.get(state, state)
        self.lbl_state.setText(text)

    # ---------- 给 HomePage 调用 ----------
    def request_start_bot(self, *, force_relogin: bool = False) -> None:
        """启动 BotWorker:先做配置体检,再起线程。

        force_relogin: True 时忽略 storage_state.json,启动后浏览器直接跳到登录页等扫码。
        """
        if getattr(self, "_bot_worker", None) and self._bot_worker.isRunning():
            logger.info("[GUI] bot 已在运行,跳过启动")
            return

        # ---- 启动前体检 ----
        problems, hints = self._preflight(force_relogin=force_relogin)
        if problems:
            QMessageBox.warning(
                self,
                "启动前检查未通过",
                "请先解决以下问题再启动:\n\n• " + "\n• ".join(problems),
            )
            return
        if hints:
            # 提示型(不阻塞,但用户要确认才继续)
            ret = QMessageBox.question(
                self,
                "启动确认",
                "下列情况会直接进入扫码登录,请确认:\n\n• "
                + "\n• ".join(hints)
                + "\n\n继续启动 Bot?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ret != QMessageBox.Yes:
                return

        from gui.bot_worker import BotWorker

        self._bot_worker = BotWorker(force_relogin=force_relogin)
        self._bot_worker.state_changed.connect(self._on_worker_state)
        self._bot_worker.error.connect(self._on_worker_error)
        self._bot_worker.login_required.connect(self._on_login_required)
        logger.info("[GUI] 启动 BotWorker (force_relogin={})", force_relogin)
        self._bot_worker.start()

    def request_stop_bot(self) -> None:
        worker = getattr(self, "_bot_worker", None)
        if worker is None or not worker.isRunning():
            logger.info("[GUI] bot 未运行")
            self._set_bot_state("stopped")
            return
        logger.info("[GUI] 请求停止 BotWorker")
        worker.request_stop()
        # 不阻塞 UI,worker 会在 bot.run() 返回后 emit stopped

    def request_save_storage_state(self) -> bool:
        """让活动中的 BotWorker 把当前登录态写回磁盘。

        返回 True 表示已调度成功(实际结果走 BotWorker.save_done 信号)。
        """
        worker = getattr(self, "_bot_worker", None)
        if worker is None or not worker.isRunning():
            QMessageBox.information(
                self, "保存登录态",
                "Bot 未运行,无法保存。请先启动 Bot 并完成扫码,再点保存。",
            )
            return False
        logger.info("[GUI] 请求保存登录态")
        return worker.request_save_storage_state()

    def _preflight(self, *, force_relogin: bool = False) -> tuple[list[str], list[str]]:
        """返回 (硬性问题, 软性提示)。

        - 硬性问题:阻塞启动(API Key、商品映射)。
        - 软性提示:仅提示,会弹 Yes/No 确认(没登录态/重新登录都会走这里)。
        """
        problems: list[str] = []
        hints: list[str] = []
        if not settings.get("llm.api_key"):
            problems.append("DeepSeek API Key 未配置(模型页填一下)")

        from core import settings as _settings
        sspath = _settings.storage_state_path()
        has_state = sspath.exists()
        if force_relogin:
            hints.append("已选择「重新扫码登录」,启动后会直接打开登录页,请用手机扫码")
        elif not has_state:
            hints.append(
                f"未找到登录态 {sspath.name},"
                "启动后会跳到登录页,请用手机扫码;扫完会自动保存,下次直接复用"
            )

        from tools import catalog as catalog_mod
        if not catalog_mod.all_items():
            problems.append("商品映射为空(商品页至少加一条)")
        return problems, hints

    def _on_worker_state(self, state: str) -> None:
        """从 BotWorker 信号过来的状态更新。"""
        self._set_bot_state(state)

    def _on_worker_error(self, text: str) -> None:
        logger.error("[GUI] BotWorker 报错:\n{}", text)
        QMessageBox.critical(self, "Bot 异常退出",
                             text[:1500] + ("\n\n... (已截断,详情见 logs/)" if len(text) > 1500 else ""))

    def _on_login_required(self, url: str) -> None:
        """浏览器卡在 login 页时,GUI 给个友好提示。"""
        logger.warning("[GUI] 浏览器需要扫码登录 url={}", url)
        # 状态栏已经会切到「等待扫码」,这里只在初次提示一下,不阻塞
        # 用 information 不阻塞主循环(对话框关掉只是用户确认看到了)
