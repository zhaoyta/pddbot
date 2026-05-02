"""首次登录脚本

用法：
    python login.py

流程：
    1. 打开有头浏览器，访问拼多多商家工作台。
    2. 你手动完成扫码登录。
    3. 看到聊天页面后，回到终端按回车，脚本会把会话 cookies / localStorage
       保存到 storage_state.json，后续探查 / 自动回复脚本可直接复用。
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright
from loguru import logger

import config


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport=config.VIEWPORT,
        )
        # 抹掉 webdriver 标志
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = context.new_page()
        logger.info("打开登录页：{}", config.LOGIN_URL)
        page.goto(config.LOGIN_URL, wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print("请在浏览器里扫码登录，进入【商家工作台 → 多多客服】聊天页。")
        print("看到聊天会话列表后，回到这里按【回车】保存登录状态。")
        print("=" * 60 + "\n")
        input(">>> 登录完成后按回车继续：")

        context.storage_state(path=str(config.STORAGE_STATE_PATH))
        logger.success("登录状态已保存到：{}", config.STORAGE_STATE_PATH)

        browser.close()


if __name__ == "__main__":
    main()
