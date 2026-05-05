"""DeepSeek 模型配置页 - 表单 + 测试连接"""
from __future__ import annotations

from loguru import logger
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import settings


class TestConnectionWorker(QThread):
    """异步跑 DeepSeek chat completions,避免 GUI 卡住。"""
    finished_with = Signal(bool, str)  # (ok, message)

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def run(self) -> None:
        try:
            from openai import OpenAI
        except Exception as e:
            self.finished_with.emit(False, f"openai 未安装: {e}")
            return

        try:
            cli = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=10.0)
            resp = cli.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping(只回'pong')"}],
                max_tokens=8,
            )
            content = ""
            if resp.choices:
                content = (resp.choices[0].message.content or "").strip()
            self.finished_with.emit(
                True,
                f"✅ 连接成功 model={self.model}\n回复: {content!r}\n\n"
                f"用量 prompt={resp.usage.prompt_tokens if resp.usage else '?'} "
                f"completion={resp.usage.completion_tokens if resp.usage else '?'}"
            )
        except Exception as e:
            self.finished_with.emit(False, f"❌ 调用失败:\n{type(e).__name__}: {e}")


class ModelPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("LLM 模型设置")
        title.setFont(QFont("", 16, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "DeepSeek 通过 OpenAI 兼容协议接入。修改保存后立即生效。\n"
            "充值/查 key:https://platform.deepseek.com/usage"
        )
        desc.setStyleSheet("color:#888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(10)

        self.le_provider = QLineEdit()
        self.le_provider.setReadOnly(True)
        self.le_provider.setStyleSheet("color:#888; background:#fafafa;")

        self.le_base_url = QLineEdit()
        self.le_base_url.setPlaceholderText("https://api.deepseek.com")

        self.le_api_key = QLineEdit()
        self.le_api_key.setPlaceholderText("sk-...")
        self.le_api_key.setEchoMode(QLineEdit.Password)

        self.btn_show_key = QPushButton("👁")
        self.btn_show_key.setCheckable(True)
        self.btn_show_key.setFixedWidth(36)
        self.btn_show_key.toggled.connect(
            lambda c: self.le_api_key.setEchoMode(
                QLineEdit.Normal if c else QLineEdit.Password
            )
        )
        key_box = QHBoxLayout()
        key_box.addWidget(self.le_api_key, 1)
        key_box.addWidget(self.btn_show_key)
        key_wrap = QWidget()
        key_wrap.setLayout(key_box)
        key_box.setContentsMargins(0, 0, 0, 0)

        self.le_model = QLineEdit()
        self.le_model.setPlaceholderText("deepseek-chat")

        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.1)
        self.sp_temp.setDecimals(2)

        self.sp_max_tokens = QSpinBox()
        self.sp_max_tokens.setRange(64, 8192)
        self.sp_max_tokens.setSingleStep(64)

        form.addRow("Provider", self.le_provider)
        form.addRow("Base URL", self.le_base_url)
        form.addRow("API Key", key_wrap)
        form.addRow("Model", self.le_model)
        form.addRow("Temperature", self.sp_temp)
        form.addRow("Max Tokens", self.sp_max_tokens)
        layout.addLayout(form)

        # 操作按钮
        bar = QHBoxLayout()
        self.btn_save = QPushButton("💾 保存")
        self.btn_test = QPushButton("🔌 测试连接")
        self.btn_reset = QPushButton("↺ 重新载入")
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_test)
        bar.addWidget(self.btn_reset)
        bar.addStretch()
        layout.addLayout(bar)

        self.btn_save.clicked.connect(self._save)
        self.btn_test.clicked.connect(self._test)
        self.btn_reset.clicked.connect(self._load)

        # 测试结果输出区
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        self.out.setPlaceholderText("点击「测试连接」后这里显示结果")
        self.out.setStyleSheet(
            "font-family: Menlo, Consolas, monospace; background:#fafafa;"
        )
        layout.addWidget(self.out, 1)

    # ---------- 数据 ----------
    def _load(self) -> None:
        self.le_provider.setText(settings.get("llm.provider", "deepseek"))
        self.le_base_url.setText(settings.get("llm.base_url"))
        self.le_api_key.setText(settings.get("llm.api_key"))
        self.le_model.setText(settings.get("llm.model"))
        self.sp_temp.setValue(settings.get_float("llm.temperature", 0.3))
        self.sp_max_tokens.setValue(settings.get_int("llm.max_tokens", 800))
        logger.info("[GUI] 模型设置已载入")

    def _save(self) -> None:
        base_url = self.le_base_url.text().strip()
        api_key = self.le_api_key.text().strip()
        model = self.le_model.text().strip()
        if not base_url or not model:
            QMessageBox.warning(self, "校验", "Base URL / Model 不能为空")
            return

        settings.set("llm.base_url", base_url)
        settings.set("llm.api_key", api_key)
        settings.set("llm.model", model)
        settings.set("llm.temperature", f"{self.sp_temp.value():.2f}")
        settings.set("llm.max_tokens", self.sp_max_tokens.value())
        QMessageBox.information(self, "已保存",
                                "模型配置已写入 settings 表,新对话立即生效")
        logger.info("[GUI] 模型设置已保存 model={} base_url={}", model, base_url)

    def _test(self) -> None:
        base_url = self.le_base_url.text().strip()
        api_key = self.le_api_key.text().strip()
        model = self.le_model.text().strip()
        if not base_url or not api_key or not model:
            QMessageBox.warning(self, "校验",
                                "Base URL / API Key / Model 都需填好才能测试")
            return

        self.out.setPlainText("⏳ 正在测试 ...")
        self.btn_test.setEnabled(False)

        self._worker = TestConnectionWorker(base_url, api_key, model)
        self._worker.finished_with.connect(self._on_test_done)
        self._worker.start()

    def _on_test_done(self, ok: bool, msg: str) -> None:
        self.out.setPlainText(msg)
        self.btn_test.setEnabled(True)
        logger.info("[GUI] DeepSeek 测试连接 ok={}", ok)
