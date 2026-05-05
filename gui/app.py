"""GUI 主入口

启动方式(任选其一):
    ./start.sh              # 推荐:自动装 uv / 同步依赖 / 装 chromium / 启 GUI
    uv run python -m gui.app
"""
from __future__ import annotations

import sys

from loguru import logger
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core import config
from core import settings, store
from core.llm_log_sink import configure_llm_log_sink
from gui.main_window import MainWindow


def main() -> int:
    # 1. 配置日志：写文件 + GUI 日志页面
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(
        config.LOGS_DIR / "pddbot_{time:YYYYMMDD}.log",
        rotation="10 MB",
        retention="14 days",
        level="DEBUG",
    )

    # 2. 初始化 DB + settings 表（首次启动从 .env 灌默认值）
    store.get()
    settings.initialize_from_env()
    configure_llm_log_sink()
    logger.info("数据库已就绪：{}", config.DB_PATH)

    # 3. 启 Qt
    app = QApplication(sys.argv)
    app.setApplicationName("pddbot")
    app.setOrganizationName("pddbot")

    icon_path = config.ASSETS_APP_ICON
    if not icon_path.is_file():
        fallback = config.ASSETS_DIR / "icon.png"
        if fallback.is_file():
            icon_path = fallback
    app_icon = QIcon(str(icon_path)) if icon_path.is_file() else None
    if app_icon is not None:
        app.setWindowIcon(app_icon)
        logger.debug("应用图标: {}", icon_path)

    win = MainWindow()
    if app_icon is not None:
        win.setWindowIcon(app_icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
