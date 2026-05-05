"""应用级配置:页面 URL、UID 白名单

路径、抓包目录、WS 帧落盘见「设置」页。
全部写入 SQLite settings 表,运行时通过 core.settings 读取。
空字段表示使用 ``core/config.py`` 中的内置默认值。
"""
from __future__ import annotations

from loguru import logger
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import config
from core import settings


class AppConfigPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build()
        self._load()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("页面与过滤")
        title.setFont(QFont("", 16, QFont.Bold))
        root.addWidget(title)

        hint = QLabel(
            "以下项保存在数据库 settings 表,优先级高于 .env。\n"
            "URL 留空则使用内置默认。登录态路径、抓包目录见左侧「设置」。"
            f"\n项目根: {config.ROOT}"
        )
        hint.setStyleSheet("color:#666;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(14)

        g_url = QGroupBox("页面 URL")
        f_url = QFormLayout(g_url)
        self.le_chat = QLineEdit()
        self.le_chat.setPlaceholderText(f"默认: {config.CHAT_URL}")
        self.le_login = QLineEdit()
        self.le_login.setPlaceholderText(f"默认: {config.LOGIN_URL}")
        self.le_redeem = QLineEdit()
        self.le_redeem.setPlaceholderText(f"默认: {config.REDEEM_PAGE_URL}")
        self.le_order_api = QLineEdit()
        self.le_order_api.setPlaceholderText(f"默认: {config.ORDER_LIST_API}")
        f_url.addRow("聊天页 app.chat_url", self.le_chat)
        f_url.addRow("登录页 app.login_url", self.le_login)
        f_url.addRow("核销页 app.redeem_page_url", self.le_redeem)
        f_url.addRow("订单接口 app.order_list_api", self.le_order_api)
        lay.addWidget(g_url)

        g_bot = QGroupBox("Bot 其它")
        f_bot = QFormLayout(g_bot)
        self.te_whitelist = QTextEdit()
        self.te_whitelist.setPlaceholderText(
            "仅处理这些买家 uid,逗号或换行分隔;留空=不限制"
        )
        self.te_whitelist.setMaximumHeight(72)
        f_bot.addRow("UID 白名单 bot.whitelist_uids", self.te_whitelist)
        lay.addWidget(g_bot)

        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        bar = QHBoxLayout()
        self.btn_save = QPushButton("💾 保存")
        self.btn_reload = QPushButton("↺ 重新载入")
        self.btn_open_root = QPushButton("📂 打开项目目录")
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_reload)
        bar.addWidget(self.btn_open_root)
        bar.addStretch()
        root.addLayout(bar)

        self.btn_save.clicked.connect(self._save)
        self.btn_reload.clicked.connect(self._load)
        self.btn_open_root.clicked.connect(self._open_root)

    def _load(self) -> None:
        self.le_chat.setText(settings.get("app.chat_url", ""))
        self.le_login.setText(settings.get("app.login_url", ""))
        self.le_redeem.setText(settings.get("app.redeem_page_url", ""))
        self.le_order_api.setText(settings.get("app.order_list_api", ""))
        wl = settings.get("bot.whitelist_uids", "")
        self.te_whitelist.setPlainText(wl.replace(",", "\n") if wl else "")
        logger.info("[GUI] 应用配置已载入")

    def _save(self) -> None:
        settings.set("app.chat_url", self.le_chat.text().strip())
        settings.set("app.login_url", self.le_login.text().strip())
        settings.set("app.redeem_page_url", self.le_redeem.text().strip())
        settings.set("app.order_list_api", self.le_order_api.text().strip())
        raw = self.te_whitelist.toPlainText()
        parts = [p.strip() for p in raw.replace(",", "\n").split() if p.strip()]
        settings.set("bot.whitelist_uids", ",".join(parts))
        QMessageBox.information(self, "已保存", "应用配置已写入 settings 表。")
        logger.info("[GUI] 应用配置已保存")

    def _open_root(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.ROOT)))
