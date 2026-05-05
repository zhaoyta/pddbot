"""飞书通知配置页 - webhook 表单 + 测试发送"""
from __future__ import annotations

from loguru import logger
from PySide6.QtCore import QThread, Signal
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import settings
from tools import notify as notify_mod


class TestSendWorker(QThread):
    finished_with = Signal(bool, str)

    def __init__(self, webhook: str) -> None:
        super().__init__()
        self.webhook = webhook

    def run(self) -> None:
        ok, resp = notify_mod.test_send(self.webhook)
        self.finished_with.emit(ok, resp)


class NotifyPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("飞书通知")
        title.setFont(QFont("", 16, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "出现转人工/核销失败/登录过期等情况时,推送到飞书自定义机器人 webhook。\n"
            "创建 webhook:飞书群 → 设置 → 群机器人 → 添加 自定义机器人 → 复制 webhook 地址"
        )
        desc.setStyleSheet("color:#888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # ---- 总开关 + webhook ----
        form_box = QGroupBox("基本配置")
        form = QFormLayout(form_box)

        self.cb_enabled = QCheckBox("启用通知")
        form.addRow("总开关", self.cb_enabled)

        self.le_webhook = QLineEdit()
        self.le_webhook.setPlaceholderText("https://open.feishu.cn/open-apis/bot/v2/hook/xxx")
        form.addRow("Webhook", self.le_webhook)
        layout.addWidget(form_box)

        # ---- 事件订阅 ----
        events_box = QGroupBox("订阅事件")
        events_layout = QVBoxLayout(events_box)
        self.event_cbs: dict[str, QCheckBox] = {}
        for ek, ename in notify_mod.EVENTS.items():
            cb = QCheckBox(f"{ename}  ({ek})")
            self.event_cbs[ek] = cb
            events_layout.addWidget(cb)
        layout.addWidget(events_box)

        # ---- 操作 ----
        bar = QHBoxLayout()
        self.btn_save = QPushButton("💾 保存")
        self.btn_test = QPushButton("📨 测试发送")
        self.btn_reset = QPushButton("↺ 重新载入")
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_test)
        bar.addWidget(self.btn_reset)
        bar.addStretch()
        layout.addLayout(bar)

        self.btn_save.clicked.connect(self._save)
        self.btn_test.clicked.connect(self._test)
        self.btn_reset.clicked.connect(self._load)

        # 测试结果
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        self.out.setPlaceholderText("点「测试发送」会向飞书群推一条测试消息,结果在此展示")
        self.out.setStyleSheet(
            "font-family: Menlo, Consolas, monospace; background:#fafafa;"
        )
        layout.addWidget(self.out, 1)

    # ---------- 数据 ----------
    def _load(self) -> None:
        self.cb_enabled.setChecked(settings.get_bool("notify.enabled", False))
        self.le_webhook.setText(settings.get("notify.feishu_webhook"))
        events_str = settings.get("notify.events") or ""
        active = {x.strip() for x in events_str.split(",") if x.strip()}
        for ek, cb in self.event_cbs.items():
            cb.setChecked(ek in active)
        logger.info("[GUI] 飞书配置已载入")

    def _save(self) -> None:
        webhook = self.le_webhook.text().strip()
        if self.cb_enabled.isChecked() and not webhook:
            QMessageBox.warning(self, "校验", "启用通知时 webhook 不能为空")
            return

        settings.set("notify.enabled",
                     "true" if self.cb_enabled.isChecked() else "false")
        settings.set("notify.feishu_webhook", webhook)
        active = [k for k, cb in self.event_cbs.items() if cb.isChecked()]
        settings.set("notify.events", ",".join(active))

        QMessageBox.information(self, "已保存", "飞书配置已写入 settings,立即生效")
        logger.info("[GUI] 飞书配置已保存 enabled={} events={}",
                    self.cb_enabled.isChecked(), active)

    def _test(self) -> None:
        webhook = self.le_webhook.text().strip()
        if not webhook:
            QMessageBox.warning(self, "校验", "请先填 webhook")
            return
        self.out.setPlainText("⏳ 正在发送测试消息 ...")
        self.btn_test.setEnabled(False)

        self._worker = TestSendWorker(webhook)
        self._worker.finished_with.connect(self._on_test_done)
        self._worker.start()

    def _on_test_done(self, ok: bool, resp: str) -> None:
        head = "✅ 发送成功" if ok else "❌ 发送失败"
        self.out.setPlainText(f"{head}\n响应:\n{resp}")
        self.btn_test.setEnabled(True)
        logger.info("[GUI] 飞书测试发送 ok={}", ok)
