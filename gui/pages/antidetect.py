"""风控反检测配置页

集中配置:
    - 浏览器渠道(chrome/chromium)
    - User-Agent / viewport / slow_mo
    - user_data_dir(持久化 profile)
    - 代理 / 时区 / locale
    - 启动温身延迟(browser.warmup_delay_*)

（按分钟回复配额、夜间静默等若落地，可另页或与本页扩展；当前不在此页。）

存储:全部走 settings 表(GUI 改完即时生效,下次启动 bot 加载)。
"""
from __future__ import annotations

import platform

from loguru import logger
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import settings


class FingerprintProbeWorker(QThread):
    """新起一个临时浏览器跑指纹探测页 (bot.sannysoft.com),
    抓取关键检查项的结果回来。耗时一般 10~30 秒。"""
    finished_with = Signal(bool, str)

    def run(self) -> None:
        try:
            from runtime.browser import BrowserSession
        except Exception as e:
            self.finished_with.emit(False, f"导入失败: {e}")
            return

        import asyncio

        async def _probe() -> tuple[bool, str]:
            try:
                # 用临时 storage_state 路径,避免污染真实登录态
                import tempfile, os, shutil
                tmp_dir = tempfile.mkdtemp(prefix="pddbot_probe_")
                tmp_state = os.path.join(tmp_dir, "storage_state.json")
                # 把现在的真实 storage_state 复制过去当起点(可能不存在)
                from pathlib import Path
                sspath = settings.storage_state_path()
                if sspath.exists():
                    shutil.copy(sspath, tmp_state)
                else:
                    Path(tmp_state).write_text('{"cookies":[],"origins":[]}')

                sess = BrowserSession(
                    chat_url="https://bot.sannysoft.com/",
                    storage_state_path=Path(tmp_state),
                    auto_save_interval=0,
                )
                await sess.start()
                page = sess.page
                # 跑 sannysoft 的指纹检测
                await asyncio.sleep(3.0)
                rows = await page.evaluate(
                    """() => {
                        const out = [];
                        document.querySelectorAll('table tr').forEach(tr => {
                            const tds = tr.querySelectorAll('td, th');
                            if (tds.length >= 2) {
                                out.push([tds[0].innerText.trim(),
                                          tds[1].innerText.trim()]);
                            }
                        });
                        return out;
                    }"""
                )
                await sess.stop()
                lines = []
                for k, v in rows[:40]:
                    lines.append(f"  {k:<35}{v}")
                # 自检结束清理临时目录
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass
                return True, "\n".join(lines) if lines else "未抓到指纹表"
            except Exception as e:
                import traceback
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass
                return False, f"{e}\n\n{traceback.format_exc()[:1500]}"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ok, msg = loop.run_until_complete(_probe())
        finally:
            loop.close()
        self.finished_with.emit(ok, msg)


class AntiDetectPage(QWidget):
    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(10)

        title = QLabel("风控 / 反检测")
        title.setFont(QFont("", 16, QFont.Bold))
        outer.addWidget(title)

        desc = QLabel(
            "集中配置浏览器指纹。\n"
            "改完点「保存」立即生效;启动新 bot 时浏览器层会自动按这里的配置启动。"
        )
        desc.setStyleSheet("color:#888;")
        desc.setWordWrap(True)
        outer.addWidget(desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:#fafafa; }")
        inner = QWidget()
        inner.setStyleSheet("background:#fafafa;")
        v = QVBoxLayout(inner)
        v.setSpacing(12)

        # ---- 浏览器组 ----
        gb_browser = QGroupBox("浏览器与指纹")
        gb_browser.setStyleSheet("QGroupBox { background:white; border:1px solid #e0e0e0; "
                                  "border-radius:8px; padding-top:14px; }")
        f1 = QFormLayout(gb_browser)
        f1.setSpacing(10)

        self.cb_channel = QComboBox()
        self.cb_channel.addItems(["chrome", "chromium", "msedge"])
        self.cb_channel.setToolTip(
            "chrome = 用本机真实 Google Chrome(强烈推荐,反检测最佳)\n"
            "chromium = 用 playwright 自带的 chromium(有自动化痕迹)\n"
            "msedge = 用 Edge(若装了)"
        )

        self.le_ua = QLineEdit()
        self.le_ua.setPlaceholderText(
            "留空 = 用 chromium 真实 UA(已自动去掉 HeadlessChrome)。"
            "需要伪造可填,如 Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ..."
        )

        self.sp_vw = QSpinBox()
        self.sp_vw.setRange(800, 3840); self.sp_vw.setSingleStep(10)
        self.sp_vh = QSpinBox()
        self.sp_vh.setRange(600, 2160); self.sp_vh.setSingleStep(10)
        viewport_box = QHBoxLayout()
        viewport_box.addWidget(self.sp_vw); viewport_box.addWidget(QLabel("×"))
        viewport_box.addWidget(self.sp_vh); viewport_box.addStretch(1)
        viewport_wrap = QWidget(); viewport_wrap.setLayout(viewport_box)
        viewport_box.setContentsMargins(0, 0, 0, 0)

        self.sp_slowmo = QSpinBox()
        self.sp_slowmo.setRange(0, 1000); self.sp_slowmo.setSuffix(" ms")
        self.sp_slowmo.setToolTip("每次 playwright 操作之间额外等待(降低自动化感)")

        self.sp_warmup_min = QDoubleSpinBox()
        self.sp_warmup_min.setRange(0.0, 30.0); self.sp_warmup_min.setSingleStep(0.5)
        self.sp_warmup_min.setSuffix(" s")
        self.sp_warmup_max = QDoubleSpinBox()
        self.sp_warmup_max.setRange(0.0, 30.0); self.sp_warmup_max.setSingleStep(0.5)
        self.sp_warmup_max.setSuffix(" s")
        warm_box = QHBoxLayout()
        warm_box.addWidget(self.sp_warmup_min); warm_box.addWidget(QLabel("~"))
        warm_box.addWidget(self.sp_warmup_max); warm_box.addStretch(1)
        warm_wrap = QWidget(); warm_wrap.setLayout(warm_box)
        warm_box.setContentsMargins(0, 0, 0, 0)

        f1.addRow("浏览器渠道", self.cb_channel)
        f1.addRow("User-Agent", self.le_ua)
        f1.addRow("Viewport", viewport_wrap)
        f1.addRow("Slow Mo", self.sp_slowmo)
        f1.addRow("启动温身延迟", warm_wrap)

        # 持久化 profile
        self.le_userdata = QLineEdit()
        self.le_userdata.setPlaceholderText(
            "留空 = 不用(只靠 storage_state.json)。"
            "填一个目录,playwright 会把 profile 持久化在那里(指纹更稳)"
        )
        self.btn_userdata_browse = QPushButton("选目录…")
        self.btn_userdata_browse.clicked.connect(self._pick_user_data_dir)
        ud_box = QHBoxLayout()
        ud_box.addWidget(self.le_userdata, 1)
        ud_box.addWidget(self.btn_userdata_browse)
        ud_wrap = QWidget(); ud_wrap.setLayout(ud_box)
        ud_box.setContentsMargins(0, 0, 0, 0)
        f1.addRow("User Data Dir", ud_wrap)

        self.le_proxy = QLineEdit()
        self.le_proxy.setPlaceholderText("http://user:pass@host:port 或 socks5://...,空 = 不用")

        self.le_tz = QLineEdit()
        self.le_tz.setPlaceholderText("Asia/Shanghai")

        self.le_locale = QLineEdit()
        self.le_locale.setPlaceholderText("zh-CN")

        f1.addRow("代理", self.le_proxy)
        f1.addRow("时区", self.le_tz)
        f1.addRow("Locale", self.le_locale)
        v.addWidget(gb_browser)

        v.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ---- 操作 ----
        bar = QHBoxLayout()
        self.btn_save = QPushButton("💾 保存")
        self.btn_reset = QPushButton("↺ 重新载入")
        self.btn_probe = QPushButton("🔍 指纹自检")
        self.btn_probe.setToolTip(
            "新开一个临时浏览器,访问 bot.sannysoft.com 跑反爬指纹检测,"
            "结果显示在下方。耗时 10~30 秒。"
        )
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_reset)
        bar.addWidget(self.btn_probe)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.btn_save.clicked.connect(self._save)
        self.btn_reset.clicked.connect(self._load)
        self.btn_probe.clicked.connect(self._on_probe)

        self.out = QTextEdit()
        self.out.setReadOnly(True)
        self.out.setPlaceholderText("「指纹自检」结果会打印在这里")
        self.out.setStyleSheet("font-family: Menlo, Consolas, monospace; "
                               "background:#fafafa;")
        self.out.setMaximumHeight(220)
        outer.addWidget(self.out)

    # ---------- 数据 ----------
    def _load(self) -> None:
        idx = self.cb_channel.findText(settings.get("browser.channel", "chrome"))
        self.cb_channel.setCurrentIndex(max(idx, 0))
        self.le_ua.setText(settings.get("browser.user_agent"))
        self.sp_vw.setValue(settings.get_int("browser.viewport_w", 1440))
        self.sp_vh.setValue(settings.get_int("browser.viewport_h", 900))
        self.sp_slowmo.setValue(settings.get_int("browser.slow_mo_ms", 80))
        self.sp_warmup_min.setValue(settings.get_float("browser.warmup_delay_min", 1.5))
        self.sp_warmup_max.setValue(settings.get_float("browser.warmup_delay_max", 3.5))
        self.le_userdata.setText(settings.get("browser.user_data_dir"))
        self.le_proxy.setText(settings.get("browser.proxy"))
        self.le_tz.setText(settings.get("browser.timezone", "Asia/Shanghai"))
        self.le_locale.setText(settings.get("browser.locale", "zh-CN"))

        logger.info("[GUI] 风控配置已载入")

    def _save(self) -> None:
        settings.set("browser.channel", self.cb_channel.currentText())
        settings.set("browser.user_agent", self.le_ua.text().strip())
        settings.set("browser.viewport_w", self.sp_vw.value())
        settings.set("browser.viewport_h", self.sp_vh.value())
        settings.set("browser.slow_mo_ms", self.sp_slowmo.value())
        settings.set("browser.warmup_delay_min", f"{self.sp_warmup_min.value():.2f}")
        settings.set("browser.warmup_delay_max", f"{self.sp_warmup_max.value():.2f}")
        settings.set("browser.user_data_dir", self.le_userdata.text().strip())
        settings.set("browser.proxy", self.le_proxy.text().strip())
        settings.set("browser.timezone", self.le_tz.text().strip() or "Asia/Shanghai")
        settings.set("browser.locale", self.le_locale.text().strip() or "zh-CN")

        QMessageBox.information(self, "已保存",
                                "风控配置已写入 settings,下次启动 bot 即生效")
        logger.info("[GUI] 风控配置已保存")

    def _pick_user_data_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择持久化 profile 目录")
        if d:
            self.le_userdata.setText(d)

    def _on_probe(self) -> None:
        worker = getattr(self.main_window, "_bot_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.warning(
                self, "指纹自检",
                "Bot 正在运行,请先停止 Bot 再做自检(自检会另起一个临时浏览器)。",
            )
            return
        self.out.setPlainText(
            "⏳ 正在新开浏览器并访问 bot.sannysoft.com 做指纹检测...\n"
            "(预计 10~30 秒)"
        )
        self.btn_probe.setEnabled(False)
        self._probe = FingerprintProbeWorker()
        self._probe.finished_with.connect(self._on_probe_done)
        self._probe.start()

    def _on_probe_done(self, ok: bool, msg: str) -> None:
        head = "✅ 指纹检测结果(关注 webdriver / chrome / plugins / WebGL):" if ok \
                else "❌ 指纹检测失败:"
        self.out.setPlainText(head + "\n\n" + msg)
        self.btn_probe.setEnabled(True)
        logger.info("[GUI] 风控自检完成 ok={}", ok)
