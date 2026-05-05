"""runtime 子包:Playwright 浏览器与网络事件适配。

- ``browser``: 会话生命周期、storage_state、反检测 init_script
- ``network``: 页面 ``response``（HTTP）→ 归一化业务事件入队
- ``stealth``: 注入脚本(由 browser 引用)

DOM 选择器与点击发送见 ``tools/`` 包(session_dom、messaging、orders_fetch 等)。
"""
