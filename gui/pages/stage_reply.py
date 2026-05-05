"""阶段回复策略页 - 5 个 stage 卡片,每个支持 智能/模板 模式"""
from __future__ import annotations

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import store as store_mod
from llm.runner import TEMPLATE_PLACEHOLDERS, render_template


# 5 个阶段：(stage_key, 中文名, 业务说明, 默认 auto 时 LLM 干啥, 默认模板示例)
STAGES: list[tuple[str, str, str, str, str]] = [
    (
        "S0_GREET",
        "S0 首次问候",
        "首次接待该客户，用于打招呼",
        "智能：让 LLM 生成一句自然问候",
        "您好，感谢咨询。请问有什么可以帮您？",
    ),
    (
        "S1_CONSULT",
        "S1 咨询答疑",
        "客户尚未下单，咨询课程/价格/适用人群",
        "智能：让 LLM 根据问题答疑、引导下单",
        "您好，我们的【{goods_name}】课程包含完整视频+资料。\n下单后会自动发送领取链接，欢迎在店铺直接拍单~",
    ),
    (
        "S2_GUIDE",
        "S2 引导核销",
        "客户已下单但未发核销码，引导发卡密图片",
        "智能：让 LLM 引导客户发送卡密图片",
        "您好，您已成功购买【{goods_name}】(订单号 {order_sn})。\n请按照下方截图获取并发送 16 位卡密给我，核销后立即发资料~",
    ),
    (
        "S3_REDEEM",
        "S3 收码核销",
        "已收到客户发的卡密，调用核销接口",
        "智能：让 LLM 调 submit_card_code 工具",
        "已收到您的卡密，正在为您核销，请稍等几秒~",
    ),
    (
        "S4_DELIVER",
        "S4 发资料",
        "核销成功后，根据 goodsId/skuId 查映射并发资料",
        "智能：让 LLM 调 lookup_product_url 工具",
        "您好，资料已为您查询到~\n{material_message}\n下载有问题随时找我！",
    ),
]


class StageCard(QFrame):
    """单个 stage 的卡片：标题 + 模式选择 + 模板编辑"""

    def __init__(self, stage_key: str, name: str, desc: str,
                 auto_hint: str, default_template: str) -> None:
        super().__init__()
        self.stage_key = stage_key
        self.default_template = default_template

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            StageCard {
                background: white;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # 标题
        head = QHBoxLayout()
        title = QLabel(name)
        title.setFont(QFont("", 14, QFont.Bold))
        head.addWidget(title)
        head.addStretch()
        sub = QLabel(desc)
        sub.setStyleSheet("color:#888;")
        head.addWidget(sub)
        layout.addLayout(head)

        # 模式选择
        mode_box = QHBoxLayout()
        self.rb_auto = QRadioButton("智能(LLM)")
        self.rb_template = QRadioButton("自定义模板")
        bg = QButtonGroup(self)
        bg.addButton(self.rb_auto)
        bg.addButton(self.rb_template)
        mode_box.addWidget(self.rb_auto)
        mode_box.addWidget(self.rb_template)
        mode_box.addStretch()
        self.btn_use_default = QPushButton("使用默认模板")
        self.btn_use_default.clicked.connect(self._fill_default)
        mode_box.addWidget(self.btn_use_default)
        layout.addLayout(mode_box)

        self.lbl_hint = QLabel(auto_hint)
        self.lbl_hint.setStyleSheet("color:#666; padding-left:6px;")
        self.lbl_hint.setWordWrap(True)
        layout.addWidget(self.lbl_hint)

        # 模板编辑
        self.te_template = QTextEdit()
        self.te_template.setPlaceholderText(
            "自定义模板。可用占位符（{xxx}）见下方说明。\n"
            "切换到「智能」模式时此模板被忽略。"
        )
        self.te_template.setMinimumHeight(110)
        self.te_template.setStyleSheet("font-family: Menlo, Consolas, monospace;")
        layout.addWidget(self.te_template)

        # 联动：选 auto 时模板灰显
        self.rb_auto.toggled.connect(
            lambda checked: self.te_template.setEnabled(not checked)
        )

    def set_data(self, mode: str, template: str) -> None:
        if mode == "template":
            self.rb_template.setChecked(True)
            self.te_template.setEnabled(True)
        else:
            self.rb_auto.setChecked(True)
            self.te_template.setEnabled(False)
        self.te_template.setPlainText(template or "")

    def get_data(self) -> tuple[str, str]:
        mode = "template" if self.rb_template.isChecked() else "auto"
        template = self.te_template.toPlainText().strip()
        return mode, template

    def _fill_default(self) -> None:
        self.te_template.setPlainText(self.default_template)
        self.rb_template.setChecked(True)
        self.te_template.setEnabled(True)


class StageReplyPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.cards: dict[str, StageCard] = {}
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(10)

        title = QLabel("阶段回复策略")
        title.setFont(QFont("", 16, QFont.Bold))
        outer.addWidget(title)

        desc = QLabel(
            "每个阶段独立配置:智能模式=走 LLM(可调 tool);自定义模板=直接发模板,不调 LLM。"
        )
        desc.setStyleSheet("color:#888;")
        desc.setWordWrap(True)
        outer.addWidget(desc)

        # 占位符提示条
        ph_lines = ["可用占位符："]
        for k, v in TEMPLATE_PLACEHOLDERS.items():
            ph_lines.append(f"  {{{k}}}  —  {v}")
        ph_label = QLabel("\n".join(ph_lines))
        ph_label.setStyleSheet(
            "background:#f8f8fa; padding:10px 14px; border:1px solid #eee;"
            "border-radius:6px; color:#555; font-family: Menlo, Consolas, monospace;"
        )
        ph_label.setWordWrap(True)
        outer.addWidget(ph_label)

        # 操作按钮
        bar = QHBoxLayout()
        self.btn_save = QPushButton("💾 保存全部")
        self.btn_reset = QPushButton("↺ 重新载入")
        self.btn_preview = QPushButton("👀 预览渲染")
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_reset)
        bar.addWidget(self.btn_preview)
        bar.addStretch()
        outer.addLayout(bar)

        self.btn_save.clicked.connect(self._save_all)
        self.btn_reset.clicked.connect(self._load)
        self.btn_preview.clicked.connect(self._preview)

        # 滚动区放卡片
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:#fafafa; }")
        inner = QWidget()
        inner.setStyleSheet("background:#fafafa;")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        for stage_key, name, ddesc, auto_hint, default_tpl in STAGES:
            card = StageCard(stage_key, name, ddesc, auto_hint, default_tpl)
            self.cards[stage_key] = card
            layout.addWidget(card)
        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

    # ---------- 数据 ----------
    def _load(self) -> None:
        all_cfg = store_mod.get().all_stage_configs()
        for stage_key, card in self.cards.items():
            cfg = all_cfg.get(stage_key) or {}
            card.set_data(
                mode=cfg.get("mode") or "auto",
                template=cfg.get("template") or "",
            )
        logger.info("[GUI] 阶段配置已载入")

    def _save_all(self) -> None:
        s = store_mod.get()
        for stage_key, card in self.cards.items():
            mode, tpl = card.get_data()
            if mode == "template" and not tpl:
                QMessageBox.warning(self, "校验",
                                    f"{stage_key} 选择了模板模式但模板为空")
                return
            s.set_stage_config(stage_key, mode, tpl if mode == "template" else None)
        QMessageBox.information(self, "已保存", "阶段配置已写入 settings,新对话立即生效")
        logger.info("[GUI] 阶段配置已保存")

    def _preview(self) -> None:
        """预览每个 stage 在示例 context 下的渲染结果（仅 template 模式有意义）"""
        sample_ctx = {
            "latest_message": "我已经核销了,发资料",
            "order": {
                "orderSn": "240502123456",
                "orderStatusStr": "已发货",
                "orderGoodsList": {
                    "goodsId": "928035245974",
                    "skuId": "999",
                    "goodsName": "【系统教学】散打基础教程视频版",
                },
            },
        }
        lines: list[str] = ["示例订单：S022-散打 / orderSn=240502123456\n"]
        for stage_key, card in self.cards.items():
            mode, tpl = card.get_data()
            lines.append(f"--- {stage_key}  (mode={mode}) ---")
            if mode == "template":
                lines.append(render_template(tpl, sample_ctx))
            else:
                lines.append("(智能模式 - 实际由 LLM 实时生成,不在此处展示)")
            lines.append("")
        self._show_preview("\n".join(lines))

    def _show_preview(self, text: str) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("渲染预览")
        dlg.resize(680, 540)
        v = QVBoxLayout(dlg)
        out = QTextEdit()
        out.setReadOnly(True)
        out.setPlainText(text)
        out.setStyleSheet("font-family: Menlo, Consolas, monospace;")
        v.addWidget(out, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        v.addWidget(btns)
        dlg.exec()
