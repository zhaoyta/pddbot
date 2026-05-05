"""商品 ↔ 资料映射 - 表格 CRUD (数据存于 catalog_item 表)"""
from __future__ import annotations

from typing import Any

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import store as store_mod
from tools import catalog as catalog_mod


MATCH_TYPE_LABELS: dict[str, str] = {
    "goods_id": "商品 ID",
    "sku_id": "SKU ID",
    "keyword": "关键字",
}


class CatalogEditDialog(QDialog):
    """新增 / 编辑 一条 catalog_item"""

    def __init__(self, parent: QWidget | None = None,
                 row_data: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑商品映射" if row_data else "新增商品映射")
        self.resize(560, 520)
        self._row_id: int | None = row_data["id"] if row_data else None

        form = QFormLayout()

        self.cb_type = QComboBox()
        for k, v in MATCH_TYPE_LABELS.items():
            self.cb_type.addItem(v, k)
        if row_data:
            idx = self.cb_type.findData(row_data.get("match_type"))
            if idx >= 0:
                self.cb_type.setCurrentIndex(idx)

        self.le_value = QLineEdit()
        self.le_value.setPlaceholderText(
            "goods_id / sku_id 直接填数字; 关键字用英文逗号分隔, 例如: 散打,S022"
        )
        if row_data:
            self.le_value.setText(row_data.get("match_value") or "")

        self.te_share = QTextEdit()
        self.te_share.setPlaceholderText(
            "粘贴百度网盘「复制全文」的整段文案\n"
            "（含：通过百度网盘分享的文件… / 链接：https://… / 复制这段内容打开…）"
        )
        self.te_share.setFixedHeight(220)
        if row_data:
            self.te_share.setPlainText(row_data.get("share_body") or "")

        self.le_product_url = QLineEdit()
        self.le_product_url.setPlaceholderText(
            "可选。留空则从资料全文自动解析百度网盘链接"
        )
        self.le_description = QLineEdit()
        self.le_description.setPlaceholderText(
            "建议填写：咨询时仅本字段与「商品链接」会进入 LLM（整段 share_body 仅核销后发客户）"
        )
        if row_data:
            self.le_product_url.setText(row_data.get("product_url") or "")
            self.le_description.setText(row_data.get("description") or "")

        form.addRow("匹配类型", self.cb_type)
        form.addRow("匹配值", self.le_value)
        form.addRow("商品链接", self.le_product_url)
        form.addRow("描述", self.le_description)
        form.addRow("资料全文 share_body", self.te_share)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(btns)

    def _on_ok(self) -> None:
        match_type = self.cb_type.currentData()
        match_value = self.le_value.text().strip()
        share_body = self.te_share.toPlainText().strip()
        product_url = self.le_product_url.text().strip()
        description = self.le_description.text().strip()

        if not match_value:
            QMessageBox.warning(self, "校验", "匹配值不能为空")
            return
        if not share_body:
            QMessageBox.warning(self, "校验", "资料全文（share_body）不能为空")
            return

        try:
            store_mod.get().upsert_catalog_item(
                match_type=match_type,
                match_value=match_value,
                share_body=share_body,
                item_id=self._row_id,
                product_url=product_url,
                description=description,
            )
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return

        logger.info("[GUI] 保存商品映射 type={} value={}", match_type, match_value)
        self.accept()


class CatalogPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("商品 ↔ 资料映射")
        title.setFont(QFont("", 16, QFont.Bold))
        layout.addWidget(title)

        desc = QLabel(
            "命中规则:商品 ID → SKU ID → 关键字(最长命中)。"
            "每条含 share_body（发给客户的整段话术），另有可选「商品链接」「描述」供 LLM 与列表展示；"
            "未填时启动时会从 share_body 尝试解析/回填。"
        )
        desc.setStyleSheet("color:#888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 工具栏
        bar = QHBoxLayout()
        self.btn_add = QPushButton("➕ 新增")
        self.btn_edit = QPushButton("✏️ 编辑")
        self.btn_del = QPushButton("🗑 删除")
        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_test = QPushButton("🔎 命中测试")
        for b in (self.btn_add, self.btn_edit, self.btn_del,
                  self.btn_refresh, self.btn_test):
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
        self.btn_test.clicked.connect(self.on_test_match)

        # 表格：类型 | 匹配值 | 描述 | 商品链接 | 资料摘要
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["类型", "匹配值", "描述", "商品链接", "资料摘要"],
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self.on_edit)

        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

    # ---------- 事件 ----------
    def refresh(self) -> None:
        items = catalog_mod.all_items()
        self.table.setRowCount(0)
        for it in items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(
                MATCH_TYPE_LABELS.get(it["match_type"], it["match_type"])
            ))
            self.table.setItem(r, 1, QTableWidgetItem(it["match_value"]))
            desc = (it.get("description") or "").strip() or "—"
            if len(desc) > 48:
                desc = desc[:48] + "…"
            self.table.setItem(r, 2, QTableWidgetItem(desc))
            pu = (it.get("product_url") or "").strip() or "—"
            if len(pu) > 56:
                pu = pu[:56] + "…"
            self.table.setItem(r, 3, QTableWidgetItem(pu))
            sb = (it.get("share_body") or "").strip()
            prev = sb.replace("\n", " ") if sb else "—"
            if len(prev) > 80:
                prev = prev[:80] + "…"
            self.table.setItem(r, 4, QTableWidgetItem(prev))
            self.table.item(r, 0).setData(Qt.UserRole, it)
        self.lbl_count.setText(f"共 {len(items)} 条")

    def _selected_row(self) -> dict[str, Any] | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    def on_add(self) -> None:
        dlg = CatalogEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def on_edit(self) -> None:
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "提示", "先选中一行")
            return
        dlg = CatalogEditDialog(self, row_data=row)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def on_delete(self) -> None:
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "提示", "先选中一行")
            return
        ok = QMessageBox.question(
            self, "确认删除",
            f"删除 {MATCH_TYPE_LABELS.get(row['match_type'])} = {row['match_value']}",
        )
        if ok != QMessageBox.Yes:
            return
        store_mod.get().delete_catalog_item(row["id"])
        logger.info("[GUI] 删除商品映射 id={}", row["id"])
        self.refresh()

    def on_test_match(self) -> None:
        """弹一个对话框，输入 goods_id / sku_id / 商品名，看命中哪条 & 网盘消息"""
        dlg = QDialog(self)
        dlg.setWindowTitle("命中测试")
        dlg.resize(560, 420)
        form = QFormLayout()
        le_g = QLineEdit()
        le_s = QLineEdit()
        le_n = QLineEdit()
        le_n.setPlaceholderText("商品名（关键字命中用）")
        form.addRow("goods_id", le_g)
        form.addRow("sku_id", le_s)
        form.addRow("商品名", le_n)
        out = QTextEdit()
        out.setReadOnly(True)
        out.setStyleSheet("font-family: Menlo, Consolas, monospace;")
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btn_run = QPushButton("命中测试")
        btns.addButton(btn_run, QDialogButtonBox.ActionRole)

        def do_run():
            item = catalog_mod.lookup(
                goods_id=le_g.text().strip() or None,
                sku_id=le_s.text().strip() or None,
                goods_name=le_n.text().strip() or None,
            )
            if item is None:
                out.setPlainText("【未命中任何映射】")
            else:
                out.setPlainText(
                    f"描述：{item.description}\n"
                    f"商品链接(product_url)：{item.product_url}\n"
                    f"正文解析链接(share_url)：{item.share_url}\n\n"
                    f"--- 给客户的回复消息 ---\n"
                    f"{item.to_message()}"
                )

        btn_run.clicked.connect(do_run)
        btns.rejected.connect(dlg.reject)

        v = QVBoxLayout(dlg)
        v.addLayout(form)
        v.addWidget(out, 1)
        v.addWidget(btns)
        dlg.exec()
