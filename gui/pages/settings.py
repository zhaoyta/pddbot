"""设置页：Bot 开关、路径与存储；快捷跳转到应用/风控/模型等。"""
from __future__ import annotations

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import config, settings
from core.llm_log_sink import configure_llm_log_sink, default_llm_log_pattern


class SettingsPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build()
        self.main_window.bot_state_changed.connect(self._on_bot_state)
        self._sync_from_store()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._sync_from_store()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(18)

        title = QLabel("设置")
        title.setFont(QFont("", 20, QFont.Bold))
        root.addWidget(title)

        sub = QLabel(
            "以下项写入数据库 settings 表，多数即时生效；"
            "若 Bot 已在运行，部分项会在下一条消息起按新配置处理。"
        )
        sub.setStyleSheet("color:#666;")
        sub.setWordWrap(True)
        root.addWidget(sub)

        g_bot = QGroupBox("机器人")
        lay_bot = QVBoxLayout(g_bot)
        row1 = QHBoxLayout()
        self.chk_enabled = QCheckBox("BOT_ENABLED（总开关：关闭后只监听不自动回复）")
        self.chk_enabled.toggled.connect(
            lambda v: settings.set("bot.enabled", "true" if v else "false"),
        )
        self.chk_dryrun = QCheckBox("DRY_RUN（干跑：走 LLM 与 action_log，不在聊天框真发）")
        self.chk_dryrun.toggled.connect(
            lambda v: settings.set("bot.dry_run", "true" if v else "false"),
        )
        row1.addWidget(self.chk_enabled)
        row1.addSpacing(24)
        row1.addWidget(self.chk_dryrun)
        row1.addStretch(1)
        lay_bot.addLayout(row1)

        wake_row = QHBoxLayout()
        lbl_wake = QLabel("新消息来源：仅 HTTP")
        lbl_wake.setStyleSheet("color:#333;")
        hint_wake = QLabel(
            "由 /plateau/sync/message、chat/list 等 HTTP 接口驱动新消息与补扫。"
        )
        hint_wake.setStyleSheet("color:#666;font-size:12px;")
        hint_wake.setWordWrap(True)
        wake_row.addWidget(lbl_wake)
        wake_row.addStretch(1)
        lay_bot.addLayout(wake_row)
        lay_bot.addWidget(hint_wake)
        root.addWidget(g_bot)

        g_path = QGroupBox("路径与存储")
        lay_path = QVBoxLayout(g_path)
        hint_path = QLabel(
            "相对路径以项目根为基准；留空则用内置默认。"
            f"\n项目根: {config.ROOT}"
        )
        hint_path.setStyleSheet("color:#666;font-size:12px;")
        hint_path.setWordWrap(True)
        lay_path.addWidget(hint_path)
        f_path = QFormLayout()
        self.le_storage = QLineEdit()
        self.le_storage.setPlaceholderText(
            f"默认: {config.STORAGE_STATE_PATH.relative_to(config.ROOT)}"
        )
        self.le_captures = QLineEdit()
        self.le_captures.setPlaceholderText(
            f"默认: {config.CAPTURES_DIR.relative_to(config.ROOT)}"
        )
        self.le_llm_log = QLineEdit()
        self.le_llm_log.setPlaceholderText(f"默认: {default_llm_log_pattern()}")
        self.le_llm_log.setToolTip(
            "仅写入含 [LLM交互] / [LLM回调] 的日志行；"
            "可使用 loguru 占位符 {time:YYYYMMDD} 按日切分。"
        )
        f_path.addRow("登录态 JSON app.storage_state_path", self.le_storage)
        f_path.addRow("抓包/调试输出目录 app.captures_dir", self.le_captures)
        f_path.addRow("LLM 专用日志 logging.llm_message_file", self.le_llm_log)
        lay_path.addLayout(f_path)
        root.addWidget(g_path)

        path_bar = QHBoxLayout()
        self.btn_save_paths = QPushButton("💾 保存路径")
        self.btn_save_paths.setToolTip(
            "写入 app.storage_state_path / app.captures_dir / logging.llm_message_file，并重挂 LLM 日志文件。"
        )
        self.btn_open_root = QPushButton("📂 打开项目目录")
        self.btn_save_paths.clicked.connect(self._save_paths)
        self.btn_open_root.clicked.connect(self._open_root)
        path_bar.addWidget(self.btn_save_paths)
        path_bar.addWidget(self.btn_open_root)
        path_bar.addStretch(1)
        root.addLayout(path_bar)

        g_nav = QGroupBox("更多配置（在其它页）")
        lay_nav = QVBoxLayout(g_nav)
        lay_nav.addWidget(
            QLabel("页面 URL、UID 白名单 → 「应用」页"),
        )
        lay_nav.addWidget(
            QLabel("回复随机延时、单 UID / 全局限流 → 「风控」页「接待节奏」"),
        )
        lay_nav.addWidget(QLabel("DeepSeek API Key、模型名 → 「模型」页"))

        row_nav = QHBoxLayout()
        for text, needle in (
            ("打开「应用」", "应用"),
            ("打开「风控」", "风控"),
            ("打开「模型」", "模型"),
        ):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, n=needle: self._go_nav(n))
            row_nav.addWidget(b)
        row_nav.addStretch(1)
        lay_nav.addLayout(row_nav)
        root.addWidget(g_nav)

        root.addStretch(1)

    def _go_nav(self, needle: str) -> None:
        nav = self.main_window.nav
        for i in range(nav.count()):
            if needle in nav.item(i).text():
                nav.setCurrentRow(i)
                return

    def _sync_from_store(self) -> None:
        self.chk_enabled.blockSignals(True)
        self.chk_enabled.setChecked(settings.get_bool("bot.enabled", True))
        self.chk_enabled.blockSignals(False)
        self.chk_dryrun.blockSignals(True)
        self.chk_dryrun.setChecked(settings.get_bool("bot.dry_run", False))
        self.chk_dryrun.blockSignals(False)
        self.le_storage.setText(settings.get("app.storage_state_path", ""))
        self.le_captures.setText(settings.get("app.captures_dir", ""))
        self.le_llm_log.setText(settings.get("logging.llm_message_file", ""))

    def _on_bot_state(self, _state: str) -> None:
        self._sync_from_store()

    def _save_paths(self) -> None:
        settings.set("app.storage_state_path", self.le_storage.text().strip())
        settings.set("app.captures_dir", self.le_captures.text().strip())
        settings.set("logging.llm_message_file", self.le_llm_log.text().strip())
        configure_llm_log_sink()
        QMessageBox.information(self, "已保存", "路径已写入 settings 表。")
        logger.info("[GUI] 设置页：路径已保存")

    def _open_root(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.ROOT)))
