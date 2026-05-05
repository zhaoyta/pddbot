"""店铺 QA 知识库：人工录入「暂无法自动答」的问题与标准答复，注入每次 LLM 用户消息。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import store as store_mod


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        return "—"


class QaEditDialog(QDialog):
    """新增 / 编辑 qa_item"""

    def __init__(
        self,
        parent: QWidget | None = None,
        row_data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑问答" if row_data else "新增问答")
        self.resize(560, 460)
        self._row_id: int | None = row_data["id"] if row_data else None

        form = QFormLayout()

        self.te_q = QTextEdit()
        self.te_q.setPlaceholderText(
            "记录客户常问的、或当前 bot 答不好的问题（可写关键词或完整句子）",
        )
        self.te_q.setFixedHeight(90)

        self.te_a = QTextEdit()
        self.te_a.setPlaceholderText(
            "人工撰写的标准答复（留空则暂不注入 LLM，仅供备忘）",
        )
        self.te_a.setFixedHeight(180)

        self.cb_enabled = QCheckBox("启用（有答复时注入每次 LLM 对话）")
        self.cb_enabled.setChecked(True)

        if row_data:
            self.te_q.setPlainText(row_data.get("question") or "")
            self.te_a.setPlainText(row_data.get("answer") or "")
            self.cb_enabled.setChecked(bool(row_data.get("enabled", 1)))

        form.addRow("问题", self.te_q)
        form.addRow("标准答复", self.te_a)
        form.addRow("", self.cb_enabled)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(btns)

    def _on_ok(self) -> None:
        q = self.te_q.toPlainText().strip()
        if not q:
            QMessageBox.warning(self, "校验", "问题不能为空")
            return
        a = self.te_a.toPlainText().strip()
        en = 1 if self.cb_enabled.isChecked() else 0
        st = store_mod.get()
        try:
            if self._row_id is not None:
                st.update_qa_item(self._row_id, q, a, enabled=en)
            else:
                st.insert_qa_item(q, a, enabled=en)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        logger.info("[GUI] 保存 QA id={}", self._row_id)
        self.accept()


class QaPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("店铺 QA 知识库")
        title.setFont(QFont("", 16, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "收录「当时答不上」或易误解的客户问题，由人工写好标准答复。"
            "勾选启用且答复非空时，会自动写入每次 LLM 调用的用户消息上方（见 qa_item 表）。"
        )
        desc.setStyleSheet("color:#888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("➕ 新增")
        self.btn_edit = QPushButton("✏️ 编辑")
        self.btn_del = QPushButton("🗑 删除")
        self.btn_refresh = QPushButton("🔄 刷新")
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_refresh):
            bar.addWidget(b)
        bar.addStretch()
        self.lbl_count = QLabel("共 0 条")
        self.lbl_count.setStyleSheet("color:#888;")
        bar.addWidget(self.lbl_count)
        layout.addLayout(bar)

        self.btn_add.clicked.connect(self.on_add)
        self.btn_edit.clicked.connect(self.on_edit)
        self.btn_del.clicked.connect(self.on_delete)
        self.btn_refresh.clicked.connect(self.refresh)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["启用", "问题", "答复", "更新时间"],
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self.on_edit)

        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        rows = store_mod.get().list_qa_items()
        self.table.setRowCount(0)
        for r in rows:
            rd = {k: r[k] for k in r.keys()}
            i = self.table.rowCount()
            self.table.insertRow(i)
            en = "✓" if rd.get("enabled") else "—"
            self.table.setItem(i, 0, QTableWidgetItem(en))
            qq = (rd.get("question") or "").replace("\n", " ")
            if len(qq) > 60:
                qq = qq[:60] + "…"
            aa = (rd.get("answer") or "").replace("\n", " ")
            if not aa.strip():
                aa = "（待填写）"
            elif len(aa) > 60:
                aa = aa[:60] + "…"
            self.table.setItem(i, 1, QTableWidgetItem(qq))
            self.table.setItem(i, 2, QTableWidgetItem(aa))
            self.table.setItem(i, 3, QTableWidgetItem(_fmt_ts(rd.get("updated_at"))))
            self.table.item(i, 0).setData(Qt.UserRole, rd)
        self.lbl_count.setText(f"共 {len(rows)} 条")

    def _selected_row(self) -> dict[str, Any] | None:
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        it = self.table.item(sel[0].row(), 0)
        return it.data(Qt.UserRole) if it else None

    def on_add(self) -> None:
        dlg = QaEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def on_edit(self) -> None:
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "提示", "先选中一行")
            return
        dlg = QaEditDialog(self, row_data=row)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def on_delete(self) -> None:
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "提示", "先选中一行")
            return
        ok = QMessageBox.question(
            self,
            "确认删除",
            f"删除 QA #{row['id']} ?",
        )
        if ok != QMessageBox.Yes:
            return
        store_mod.get().delete_qa_item(int(row["id"]))
        logger.info("[GUI] 删除 QA id={}", row["id"])
        self.refresh()
