"""首页：启动/停止按钮 + 状态卡片 + 实时统计"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from core import settings, store as store_mod


def _stat_card(title: str, value: str = "—") -> tuple[QFrame, QLabel]:
    f = QFrame()
    f.setObjectName("statCard")
    f.setStyleSheet(
        """
        #statCard {
            background: white;
            border: 1px solid #e8e8ee;
            border-radius: 10px;
        }
        QLabel#statTitle { color: #888; font-size: 12px; }
        QLabel#statValue { color: #222; font-size: 22px; font-weight: 600; }
        """
    )
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(6)

    lbl_t = QLabel(title)
    lbl_t.setObjectName("statTitle")
    lbl_v = QLabel(value)
    lbl_v.setObjectName("statValue")

    lay.addWidget(lbl_t)
    lay.addWidget(lbl_v)
    return f, lbl_v


class HomePage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build()
        self.main_window.bot_state_changed.connect(self._on_state)

        # 每 5 秒刷新一次统计
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_stats)
        self._timer.start(5000)
        self.refresh_stats()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(20)

        title = QLabel("控制台")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        root.addWidget(title)

        # 登录方式下拉(放在启动按钮上方,启动后禁用)
        login_row = QHBoxLayout()
        login_label = QLabel("登录方式:")
        login_label.setStyleSheet("color: #555;")
        self.cb_login_mode = QComboBox()
        self.cb_login_mode.addItem("使用上次扫码的登录态(推荐)", "reuse")
        self.cb_login_mode.addItem("重新扫码登录(忽略已保存的)", "fresh")
        self.cb_login_mode.setMinimumHeight(34)
        # 没有 storage_state.json 时,默认就走"重新扫码登录",免得用户再去切
        if not settings.storage_state_path().exists():
            self.cb_login_mode.setCurrentIndex(1)
        self.cb_login_mode.currentIndexChanged.connect(self._refresh_login_hint)

        self.lbl_login_hint = QLabel("")
        self.lbl_login_hint.setStyleSheet("color: #888; font-size: 12px;")
        self.lbl_login_hint.setWordWrap(True)

        login_row.addWidget(login_label)
        login_row.addWidget(self.cb_login_mode, 1)
        root.addLayout(login_row)
        root.addWidget(self.lbl_login_hint)
        self._refresh_login_hint()

        # 启动/停止按钮
        self.btn = QPushButton("▶  启动机器人")
        self.btn.setMinimumHeight(58)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet(self._btn_style_start())
        self.btn.clicked.connect(self._on_btn_click)
        root.addWidget(self.btn)

        # 辅助按钮行(保存登录态/清除登录态)
        aux_row = QHBoxLayout()
        self.btn_save_login = QPushButton("💾 立即保存登录态")
        self.btn_save_login.setToolTip(
            "把当前浏览器里的 cookies 写回 storage_state.json,"
            "下次启动免再扫码。Bot 运行中也会每 5 分钟自动保存一次。"
        )
        self.btn_save_login.clicked.connect(self._on_save_login)

        self.btn_clear_login = QPushButton("🗑 清除已保存的登录态")
        self.btn_clear_login.setToolTip(
            "删除 storage_state.json,下次启动会自动跳到登录页等扫码。"
        )
        self.btn_clear_login.clicked.connect(self._on_clear_login)

        aux_row.addWidget(self.btn_save_login)
        aux_row.addWidget(self.btn_clear_login)
        aux_row.addStretch(1)
        root.addLayout(aux_row)

        link_set = QLabel(
            '<a href="#settings">⚙ Bot 总开关、干跑与新消息唤醒 → 打开「设置」页</a>'
        )
        link_set.setTextFormat(Qt.RichText)
        link_set.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link_set.linkActivated.connect(lambda _u: self.main_window.open_settings_page())
        link_set.setStyleSheet("font-size: 13px;")
        root.addWidget(link_set)

        # 统计卡片
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        c1, self.lbl_today = _stat_card("今日自动回复", "0")
        c2, self.lbl_escalate = _stat_card("今日转人工", "0")
        c3, self.lbl_uids = _stat_card("活跃客户数", "0")
        c4, self.lbl_browser = _stat_card("浏览器", "未启动")

        grid.addWidget(c1, 0, 0)
        grid.addWidget(c2, 0, 1)
        grid.addWidget(c3, 0, 2)
        grid.addWidget(c4, 0, 3)
        root.addLayout(grid)

        # 状态描述
        self.lbl_status = QLabel("点击上方按钮启动机器人")
        self.lbl_status.setStyleSheet("color: #888;")
        root.addWidget(self.lbl_status)

        root.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def _btn_style_start(self) -> str:
        return (
            "QPushButton{background:#2196f3;color:white;font-size:18px;"
            "font-weight:600;border-radius:10px;}"
            "QPushButton:hover{background:#1976d2;}"
        )

    def _btn_style_stop(self) -> str:
        return (
            "QPushButton{background:#e53935;color:white;font-size:18px;"
            "font-weight:600;border-radius:10px;}"
            "QPushButton:hover{background:#c62828;}"
        )

    def _on_btn_click(self) -> None:
        if self.main_window.bot_state in ("stopped",):
            mode = self.cb_login_mode.currentData()
            force_relogin = (mode == "fresh")
            self.main_window.request_start_bot(force_relogin=force_relogin)
        elif self.main_window.bot_state in ("running", "awaiting_login"):
            self.main_window.request_stop_bot()

    def _refresh_login_hint(self) -> None:
        """根据当前选项 + storage_state.json 是否存在,刷新下方提示文案。"""
        mode = self.cb_login_mode.currentData()
        has_state = settings.storage_state_path().exists()
        if mode == "fresh":
            self.lbl_login_hint.setText(
                "启动后浏览器会跳到登录页,请用手机扫码;扫完会自动保存,"
                "下次切回「使用上次扫码的登录态」就能免扫码。"
            )
        else:
            if has_state:
                self.lbl_login_hint.setText(
                    "将复用 storage_state.json 里的 cookies 直接进入聊天页;"
                    "若已过期,会自动跳到登录页等扫码。"
                )
            else:
                self.lbl_login_hint.setText(
                    "⚠️ 还没有保存过登录态,启动后仍需扫码;"
                    "建议直接选「重新扫码登录」更省事。"
                )

    def _on_state(self, state: str) -> None:
        if state == "stopped":
            self.btn.setText("▶  启动机器人")
            self.btn.setStyleSheet(self._btn_style_start())
            self.btn.setEnabled(True)
            self.cb_login_mode.setEnabled(True)
            self.btn_clear_login.setEnabled(True)
            self.lbl_status.setText("点击上方按钮启动机器人")
            self.lbl_browser.setText("未启动")
            self._refresh_login_hint()
        elif state == "starting":
            self.btn.setText("启动中…")
            self.btn.setEnabled(False)
            self.cb_login_mode.setEnabled(False)
            self.btn_clear_login.setEnabled(False)
            self.lbl_status.setText("正在启动浏览器、加载登录态…")
            self.lbl_browser.setText("启动中")
        elif state == "awaiting_login":
            self.btn.setText("■  取消(关闭浏览器)")
            self.btn.setStyleSheet(self._btn_style_stop())
            self.btn.setEnabled(True)
            self.cb_login_mode.setEnabled(False)
            self.lbl_status.setText(
                "🟡 等你在浏览器里手机扫码登录…扫完会自动保存,机器人接着进入聊天页。"
            )
            self.lbl_browser.setText("等待扫码")
        elif state == "running":
            self.btn.setText("■  停止机器人")
            self.btn.setStyleSheet(self._btn_style_stop())
            self.btn.setEnabled(True)
            self.cb_login_mode.setEnabled(False)
            dry = settings.get_bool("bot.dry_run", False)
            extra = "  (DRY_RUN 干跑)" if dry else ""
            self.lbl_status.setText("机器人运行中,按 GUI 配置自动接待客户" + extra)
            self.lbl_browser.setText("运行中" + (" / DRY_RUN" if dry else ""))
        elif state == "stopping":
            self.btn.setText("停止中…")
            self.btn.setEnabled(False)
            self.lbl_status.setText("正在关闭浏览器…")
            self.lbl_browser.setText("关闭中")

    def refresh_stats(self) -> None:
        try:
            s = store_mod.get().stats_today()
        except Exception:
            return
        self.lbl_today.setText(str(s.get("replies_today", 0)))
        self.lbl_escalate.setText(str(s.get("escalates_today", 0)))
        self.lbl_uids.setText(str(s.get("active_uids_24h", 0)))

    def _on_clear_login(self) -> None:
        """删掉 storage_state.json,下次启动会自动重新登录。"""
        if self.main_window.bot_state != "stopped":
            QMessageBox.information(
                self, "清除登录态",
                "请先停止 Bot 再清除登录态。",
            )
            return
        path = settings.storage_state_path()
        if not path.exists():
            QMessageBox.information(
                self, "清除登录态",
                f"未找到 {path.name},无需清除。下次启动会自动跳到登录页。",
            )
            self._refresh_login_hint()
            return
        ret = QMessageBox.question(
            self, "确认清除",
            f"确定要删除 {path.name} 吗?\n\n下次启动会重新跳到登录页,需要再次扫码。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            path.unlink()
            QMessageBox.information(self, "清除登录态", "✅ 已删除")
        except Exception as e:
            QMessageBox.warning(self, "清除登录态", f"❌ 删除失败:{e}")
        # 切到"重新登录"模式,免得用户再去切
        self.cb_login_mode.setCurrentIndex(1)
        self._refresh_login_hint()

    def _on_save_login(self) -> None:
        """把当前浏览器登录态主动写回磁盘。"""
        worker = getattr(self.main_window, "_bot_worker", None)
        if worker is None or not worker.isRunning():
            QMessageBox.information(
                self,
                "保存登录态",
                "Bot 未运行,无法保存。请先启动 Bot 并完成扫码,再点保存。",
            )
            return

        # 一次性连接 save_done,出结果就提示
        def _on_done(ok: bool) -> None:
            try:
                worker.save_done.disconnect(_on_done)
            except Exception:
                pass
            if ok:
                QMessageBox.information(
                    self, "保存登录态",
                    "✅ 登录态已保存到 storage_state.json,下次启动免扫码"
                )
            else:
                QMessageBox.warning(
                    self, "保存登录态",
                    "❌ 保存失败,详情见日志页"
                )

        worker.save_done.connect(_on_done)
        ok = self.main_window.request_save_storage_state()
        if not ok:
            try:
                worker.save_done.disconnect(_on_done)
            except Exception:
                pass
