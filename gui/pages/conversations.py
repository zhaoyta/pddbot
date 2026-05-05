"""会话记录查看 - 左 uid 列表 + 右聊天流"""
from __future__ import annotations

from datetime import datetime

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import store as store_mod


ROLE_LABEL = {
    "user": "客户",
    "mall_cs": "人工客服",
    "bot": "Bot",
}
ROLE_COLOR = {
    "user": "#0d47a1",      # 蓝
    "mall_cs": "#5d4037",   # 棕
    "bot": "#2e7d32",       # 绿
}


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


class ConversationsPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._all_uids: list[dict] = []
        self._current_uid: str | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(10)

        title = QLabel("会话记录")
        title.setFont(QFont("", 16, QFont.Bold))
        outer.addWidget(title)

        bar = QHBoxLayout()
        self.le_search = QLineEdit()
        self.le_search.setPlaceholderText("按 uid 搜索 / 过滤")
        self.le_search.textChanged.connect(self._filter)
        bar.addWidget(self.le_search, 1)
        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)
        self.lbl_total = QLabel("共 0 个会话")
        self.lbl_total.setStyleSheet("color:#888; padding-left:8px;")
        bar.addWidget(self.lbl_total)
        outer.addLayout(bar)

        # 左右分栏
        split = QSplitter(Qt.Horizontal)

        # 左:uid 表格
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["uid", "条数", "最近一条"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.table)

        # 右:聊天流
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)
        self.lbl_uid = QLabel("← 在左边选一个 uid")
        self.lbl_uid.setStyleSheet("color:#666; padding:4px 6px;")
        rlay.addWidget(self.lbl_uid)
        self.te_chat = QTextEdit()
        self.te_chat.setReadOnly(True)
        self.te_chat.setStyleSheet(
            "QTextEdit { background:#fafafa; padding:8px; "
            "font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; }"
        )
        rlay.addWidget(self.te_chat, 1)
        split.addWidget(right)

        split.setSizes([320, 800])
        outer.addWidget(split, 1)

    # ---------- 数据 ----------
    def refresh(self) -> None:
        self._all_uids = store_mod.get().list_chat_uids(limit=500)
        self._render_uid_list(self._all_uids)
        self.lbl_total.setText(f"共 {len(self._all_uids)} 个会话")
        # 当前选中的 uid 仍存在则刷新右侧
        if self._current_uid:
            self._render_chat(self._current_uid)
        logger.info("[GUI] 会话页刷新, {} 个 uid", len(self._all_uids))

    def _filter(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._render_uid_list(self._all_uids)
        else:
            filt = [u for u in self._all_uids if text in str(u["uid"])]
            self._render_uid_list(filt)

    def _render_uid_list(self, uids: list[dict]) -> None:
        self.table.setRowCount(0)
        for u in uids:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(str(u["uid"])))
            self.table.setItem(r, 1, QTableWidgetItem(str(u["msg_count"])))
            preview = u.get("preview") or ""
            ts = _fmt_ts(u.get("last_ts"))
            self.table.setItem(r, 2, QTableWidgetItem(f"{ts}  {preview}"))
            self.table.item(r, 0).setData(Qt.UserRole, u["uid"])

    def _on_select(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0)
        if not item:
            return
        uid = item.data(Qt.UserRole)
        self._current_uid = str(uid)
        self._render_chat(self._current_uid)

    def _render_chat(self, uid: str) -> None:
        s = store_mod.get()
        msgs = s.list_chat_messages(uid, limit=2000)
        self.lbl_uid.setText(f"uid={uid}    共 {len(msgs)} 条")

        self.te_chat.clear()
        cursor = self.te_chat.textCursor()
        for m in msgs:
            role = m["role"] or "?"
            label = ROLE_LABEL.get(role, role)
            color = ROLE_COLOR.get(role, "#444")
            ts = _fmt_ts(m["ts"])
            content = (m["content"] or "").rstrip()

            # 头部：[时间] 角色:
            head_fmt = QTextCharFormat()
            head_fmt.setForeground(QColor(color))
            head_fmt.setFontWeight(QFont.Bold)
            cursor.setCharFormat(head_fmt)
            cursor.insertText(f"[{ts}] {label}:\n")

            # 正文
            body_fmt = QTextCharFormat()
            body_fmt.setForeground(QColor("#222"))
            cursor.setCharFormat(body_fmt)
            cursor.insertText(content + "\n\n")

        # 滚到底
        self.te_chat.moveCursor(QTextCursor.End)
