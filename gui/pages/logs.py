"""日志窗口：实时显示 loguru 输出（按等级染色 + 自动滚动）"""
from __future__ import annotations

import os
import platform
import subprocess

from loguru import logger
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import config


_LEVEL_COLORS = {
    "DEBUG": QColor("#888888"),
    "INFO": QColor("#222222"),
    "SUCCESS": QColor("#2e7d32"),
    "WARNING": QColor("#ef6c00"),
    "ERROR": QColor("#c62828"),
    "CRITICAL": QColor("#b71c1c"),
}


class _LogEmitter(QObject):
    line = Signal(str, str)  # (level, message)


_emitter = _LogEmitter()


def _gui_sink(message) -> None:
    rec = message.record
    text = message.rstrip("\n")
    _emitter.line.emit(rec["level"].name, text)


# 安装一次全局 sink，让 GUI 可以拿到所有日志
logger.add(_gui_sink, level="DEBUG", format="{time:HH:mm:ss} | {level:<7} | {message}")


class LogsPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self._build()
        _emitter.line.connect(self._append_line)
        self._level_filter = "DEBUG"

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        # 顶部工具条
        bar = QHBoxLayout()
        bar.addWidget(QLabel("最低级别:"))
        self.cmb = QComboBox()
        self.cmb.addItems(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"])
        self.cmb.setCurrentText("DEBUG")
        self.cmb.currentTextChanged.connect(self._on_level_changed)
        bar.addWidget(self.cmb)
        bar.addStretch(1)

        btn_open = QPushButton("打开 logs/ 目录")
        btn_open.clicked.connect(self._open_logs_dir)
        bar.addWidget(btn_open)

        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(lambda: self.editor.clear())
        bar.addWidget(btn_clear)

        root.addLayout(bar)

        # 日志显示
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setMaximumBlockCount(5000)
        self.editor.setStyleSheet(
            "QPlainTextEdit{font-family:'JetBrains Mono','Menlo','Consolas',monospace;"
            "font-size:12px;background:#fafafa;border:1px solid #e8e8ee;"
            "border-radius:6px;padding:6px;}"
        )
        root.addWidget(self.editor, 1)

    def _on_level_changed(self, level: str) -> None:
        self._level_filter = level

    def _level_passes(self, level: str) -> bool:
        order = ["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]
        try:
            return order.index(level) >= order.index(self._level_filter)
        except ValueError:
            return True

    def _append_line(self, level: str, text: str) -> None:
        if not self._level_passes(level):
            return
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)

        fmt = QTextCharFormat()
        fmt.setForeground(_LEVEL_COLORS.get(level, QColor("#222")))
        cursor.insertText(text + "\n", fmt)

        # 自动滚到底
        sb = self.editor.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _open_logs_dir(self) -> None:
        path = str(config.LOGS_DIR)
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            logger.warning("打开 logs 目录失败: {}", e)
