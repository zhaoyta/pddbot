"""页面探查脚本

目的：
    在不发任何消息的前提下，被动观察拼多多商家客服页面，把以下信息落盘，
    供后续设计自动监听 / 自动回复方案使用：

    1. 所有 WebSocket 帧（请求 URL、收发方向、payload）→ captures/ws_*.jsonl
    2. 所有 XHR / fetch 请求摘要                         → captures/http_*.jsonl
    3. 关键 DOM 选择器探测结果                           → captures/dom_probe.json
    4. 控制台日志                                        → captures/console.log

用法：
    1. 先跑过 login.py，确保根目录已有 storage_state.json
    2. python explore.py
    3. 让浏览器停在聊天页面，正常收一两条客户消息后按 Ctrl+C 结束。
"""
from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from playwright.sync_api import sync_playwright, WebSocket, Request, Response

import config


# ---------- 工具 ----------

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_text(payload: str | bytes) -> str:
    if isinstance(payload, bytes):
        try:
            return payload.decode("utf-8", errors="replace")
        except Exception:
            return f"<bytes:{len(payload)}>"
    return payload


# ---------- 主流程 ----------

def main() -> None:
    if not config.STORAGE_STATE_PATH.exists():
        logger.error("没找到 storage_state.json，请先运行 `python login.py` 完成扫码登录")
        sys.exit(1)

    run_id = _ts()
    ws_log = config.CAPTURES_DIR / f"ws_{run_id}.jsonl"
    http_log = config.CAPTURES_DIR / f"http_{run_id}.jsonl"
    chat_log = config.CAPTURES_DIR / f"chat_{run_id}.jsonl"  # 仅 chat/* 相关接口
    console_log = config.CAPTURES_DIR / f"console_{run_id}.log"
    dom_probe_path = config.CAPTURES_DIR / f"dom_probe_{run_id}.json"

    logger.add(config.LOGS_DIR / f"explore_{run_id}.log", rotation="10 MB")
    logger.info("本次 run id = {}", run_id)
    logger.info("WS 日志：{}", ws_log)
    logger.info("HTTP 日志：{}", http_log)
    logger.info("Chat 接口日志（完整）：{}", chat_log)

    ws_fp = ws_log.open("a", encoding="utf-8")
    http_fp = http_log.open("a", encoding="utf-8")
    chat_fp = chat_log.open("a", encoding="utf-8")
    console_fp = console_log.open("a", encoding="utf-8")

    def write_jsonl(fp, obj: dict) -> None:
        fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fp.flush()

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
            storage_state=str(config.STORAGE_STATE_PATH),
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = context.new_page()

        # ---- 1. WebSocket 监听 ----
        def on_websocket(ws: WebSocket) -> None:
            logger.info("[WS open] {}", ws.url)
            write_jsonl(ws_fp, {"event": "open", "url": ws.url, "t": time.time()})

            ws.on(
                "framereceived",
                lambda payload: write_jsonl(
                    ws_fp,
                    {
                        "event": "recv",
                        "url": ws.url,
                        "t": time.time(),
                        "payload": _safe_text(payload),
                    },
                ),
            )
            ws.on(
                "framesent",
                lambda payload: write_jsonl(
                    ws_fp,
                    {
                        "event": "send",
                        "url": ws.url,
                        "t": time.time(),
                        "payload": _safe_text(payload),
                    },
                ),
            )
            ws.on(
                "close",
                lambda: write_jsonl(
                    ws_fp, {"event": "close", "url": ws.url, "t": time.time()}
                ),
            )

        page.on("websocket", on_websocket)

        # ---- 2. HTTP 请求监听 ----
        # 命中以下关键字时认为是"聊天/订单/客户上下文"相关接口，单独记录、完整保存
        CHAT_KEYWORDS = (
            # —— 聊天 ——
            "/chat/",
            "/plateau/chat",
            "/api/rainbow",  # 拼多多消息推送相关
            "send_msg",
            "msg/send",
            "msg/list",
            "conv/",
            "/msg",
            # —— 订单 ——
            "/latitude/order",
            "userallorder",
            "/order/",
            # —— 商品 / 客户上下文 ——
            "/goods/",
            "/customer/",
            "/user/",
            # —— 卡券核销 ——
            "/coupon",
            "/cardcode",
            "card_code",
            "/redeem",
            "/verify",
            "/voucher",
            "writeoff",
            "write_off",
            "/ticket",
            "verify_code",
        )

        def is_chat_related(url: str) -> bool:
            low = url.lower()
            return any(k in low for k in CHAT_KEYWORDS)

        def on_request(req: Request) -> None:
            url = req.url
            if "pinduoduo.com" not in url:
                return
            record = {
                "event": "request",
                "t": time.time(),
                "method": req.method,
                "url": url,
                "post_data": req.post_data,
                "headers": dict(req.headers),
            }
            if is_chat_related(url):
                write_jsonl(chat_fp, record)
                logger.info("[CHAT-REQ] {} {}", req.method, url)
            write_jsonl(http_fp, record)

        def on_response(resp: Response) -> None:
            url = resp.url
            if "pinduoduo.com" not in url:
                return
            chat_hit = is_chat_related(url)
            ct = resp.headers.get("content-type", "")
            body = None
            if "json" in ct or "text" in ct:
                try:
                    body = resp.text()
                except Exception:
                    body = None

            full_record = {
                "event": "response",
                "t": time.time(),
                "status": resp.status,
                "url": url,
                "headers": dict(resp.headers),
                "body": body,
            }
            if chat_hit:
                # chat 接口完整保留（不截断），方便分析消息结构和发送参数
                write_jsonl(chat_fp, full_record)
                logger.info("[CHAT-RESP] {} {}", resp.status, url)
            # 通用日志为了体积考虑做截断
            if body and len(body) > 4000:
                body_short = body[:4000] + "...<truncated>"
            else:
                body_short = body
            write_jsonl(
                http_fp,
                {
                    "event": "response",
                    "t": time.time(),
                    "status": resp.status,
                    "url": url,
                    "body": body_short,
                },
            )

        page.on("request", on_request)
        page.on("response", on_response)

        # ---- 3. 控制台日志 ----
        page.on(
            "console",
            lambda msg: console_fp.write(
                f"[{datetime.now().isoformat()}] [{msg.type}] {msg.text}\n"
            )
            or console_fp.flush(),
        )

        # ---- 4. 进入聊天页 ----
        logger.info("打开聊天页：{}", config.CHAT_URL)
        page.goto(config.CHAT_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # ---- 5. DOM 探针（重点找：会话列表项 / 输入框 / 发送按钮）----
        probe_script = r"""
        () => {
            // 工具：拿元素的关键描述
            const desc = (el, withText = true) => {
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {
                    tag: el.tagName,
                    cls: (el.className?.toString?.() || '').slice(0, 200),
                    id: el.id || '',
                    role: el.getAttribute && el.getAttribute('role'),
                    placeholder: el.getAttribute && el.getAttribute('placeholder'),
                    contenteditable: el.getAttribute && el.getAttribute('contenteditable'),
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y),
                            w: Math.round(rect.width), h: Math.round(rect.height) },
                    visible: rect.width > 0 && rect.height > 0,
                    text: withText ? (el.innerText || '').slice(0, 80).replace(/\s+/g, ' ') : ''
                };
            };

            // 路径：从 el 一直往上找到 body，输出 tag.cls 的链
            const path = (el, max = 6) => {
                const out = [];
                let n = el, i = 0;
                while (n && n.tagName && n.tagName !== 'BODY' && i < max) {
                    const cls = (n.className?.toString?.() || '').split(/\s+/).filter(Boolean).slice(0, 2).join('.');
                    out.unshift(n.tagName.toLowerCase() + (cls ? '.' + cls : ''));
                    n = n.parentElement;
                    i++;
                }
                return out.join(' > ');
            };

            const result = {};

            // ----- 1) 输入框候选 -----
            const inputs = [];
            for (const el of document.querySelectorAll('textarea, [contenteditable="true"], [contenteditable=""]')) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;  // 不可见跳过
                inputs.push({ ...desc(el), path: path(el, 8) });
            }
            result["输入框候选"] = inputs;

            // ----- 2) 发送按钮候选 -----
            // 策略 a：文字精确匹配 "发送"
            const sendByText = [];
            for (const el of document.querySelectorAll('button, div, span, a')) {
                const t = (el.innerText || '').trim();
                if (t === '发送') {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    sendByText.push({ ...desc(el), path: path(el, 8) });
                }
            }
            result["发送按钮(文字=发送)"] = sendByText;

            // 策略 b：类名带 send 的可见按钮
            const sendByClass = [];
            for (const el of document.querySelectorAll('[class*="send" i], [class*="Send"]')) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const t = (el.innerText || '').trim();
                if (t.length > 10) continue;  // 过滤大段文本容器
                sendByClass.push({ ...desc(el), path: path(el, 8) });
            }
            result["发送按钮(类名含 send)"] = sendByClass.slice(0, 10);

            // ----- 3) 会话列表项 -----
            // 经验：左侧是一列高度差不多的可点击行，且数量 >= 5
            const candidates = new Map();  // key=parent => list
            for (const el of document.querySelectorAll('div, li')) {
                const r = el.getBoundingClientRect();
                if (r.width < 100 || r.width > 400) continue;
                if (r.height < 40 || r.height > 120) continue;
                if (r.x > 400) continue;  // 限定左侧
                const p = el.parentElement;
                if (!p) continue;
                if (!candidates.has(p)) candidates.set(p, []);
                candidates.get(p).push(el);
            }
            // 取数量最多的那个 parent，认为是会话列表
            let bestParent = null, bestCount = 0;
            for (const [p, list] of candidates) {
                if (list.length > bestCount) { bestCount = list.length; bestParent = p; }
            }
            if (bestParent) {
                const list = candidates.get(bestParent);
                result["会话列表"] = {
                    count: list.length,
                    parent: { ...desc(bestParent, false), path: path(bestParent, 8) },
                    samples: list.slice(0, 5).map(el => ({ ...desc(el), path: path(el, 8) })),
                };
            } else {
                result["会话列表"] = null;
            }

            // ----- 4) 消息区域气泡 -----
            const bubbles = [];
            for (const el of document.querySelectorAll('[class*="message" i], [class*="msg" i], [class*="bubble" i]')) {
                const r = el.getBoundingClientRect();
                if (r.width < 50 || r.height < 20) continue;
                if (r.x < 250) continue;  // 排除左侧
                const t = (el.innerText || '').trim();
                if (!t) continue;
                bubbles.push({ ...desc(el), path: path(el, 6) });
                if (bubbles.length >= 8) break;
            }
            result["消息气泡样本"] = bubbles;

            // ----- 5) iframe 检测（如果聊天主体在 iframe 内，操作方式不同）-----
            const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: f.src,
                name: f.name,
                rect: f.getBoundingClientRect(),
            }));
            result["iframe"] = iframes;

            return result;
        }
        """
        # 启动时先跑一次（多半还没选会话，输入框为空也无所谓，作初始基线）
        dom_probe_latest = config.CAPTURES_DIR / f"dom_probe_latest_{run_id}.json"
        try:
            probe = page.evaluate(probe_script)
            dom_probe_path.write_text(
                json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            dom_probe_latest.write_text(
                json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.success("DOM 初始探针写入：{}", dom_probe_path)
            logger.info("循环期间会每 5 秒覆盖最新探针到：{}", dom_probe_latest)
        except Exception as e:
            logger.warning("DOM 探针失败：{}", e)

        # ---- 6. 注入 MutationObserver 把会话/消息变化打到 console ----
        observer_script = r"""
        () => {
            if (window.__pddbot_observer__) return 'already';
            const obs = new MutationObserver(muts => {
                for (const m of muts) {
                    if (m.addedNodes && m.addedNodes.length) {
                        for (const n of m.addedNodes) {
                            if (n.nodeType === 1) {
                                const t = (n.innerText || '').slice(0, 120).replace(/\s+/g, ' ');
                                if (t) console.log('[PDDBOT_DOM_ADD]', t);
                            }
                        }
                    }
                }
            });
            obs.observe(document.body, {childList: true, subtree: true});
            window.__pddbot_observer__ = obs;
            return 'installed';
        }
        """
        try:
            r = page.evaluate(observer_script)
            logger.info("MutationObserver: {}", r)
        except Exception as e:
            logger.warning("注入 MutationObserver 失败：{}", e)

        # ---- 7. 阻塞，等用户操作 ----
        print("\n" + "=" * 78)
        print("浏览器已打开聊天页，正在抓取 WS / HTTP / DOM 数据。")
        print("请按以下顺序操作（顺序不能省，否则后面写代码缺数据）：")
        print()
        print("  [1] 切 2~3 个会话（让 chat/list 多触发几次）")
        print("  [2] 在每个会话右侧都点一下【最新订单 → 个人订单】")
        print("      （让 userAllOrder 触发，看请求是否带 uid 参数）")
        print("  [3] 在新标签页里打开商家后台的【卡券核销】页面，")
        print("      手动跑一次完整核销：复制一个码 → 粘贴 → 提交 → 看到结果。")
        print("      （如果不知道在哪：商家工作台首页搜索『核销』，")
        print("        或在『订单管理 → 虚拟订单』里找核销入口）")
        print("  [4] 等 1~2 条真实客户消息，然后选中一个会话停在那里，")
        print("      让输入框可见，等 5 秒以上让 dom_probe_latest 覆盖")
        print("  [5] Ctrl+C 结束")
        print()
        print("结束后把 captures/chat_*.jsonl + dom_probe_latest_*.json 贴给我")
        print("=" * 78 + "\n")

        stop = {"flag": False}

        def _sigint(_sig, _frm):
            logger.info("收到 Ctrl+C，准备退出…")
            stop["flag"] = True

        signal.signal(signal.SIGINT, _sigint)

        try:
            tick = 0
            while not stop["flag"]:
                page.wait_for_timeout(1000)
                if page.is_closed():
                    logger.info("页面已被关闭，退出")
                    break
                tick += 1
                # 每 5 秒重跑一次 DOM 探针，覆盖 latest
                if tick % 5 == 0:
                    try:
                        probe = page.evaluate(probe_script)
                        dom_probe_latest.write_text(
                            json.dumps(probe, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception as e:
                        logger.debug("周期性 DOM 探针失败：{}", e)
        finally:
            ws_fp.close()
            http_fp.close()
            chat_fp.close()
            console_fp.close()
            try:
                browser.close()
            except Exception:
                pass

    logger.success("抓取完成。请把以下文件发给我做下一步分析：")
    logger.success("  - {}  ★ 监听新消息用", chat_log)
    logger.success("  - {}  ★ 发送消息用（请确认抓的是已选好会话的状态）", dom_probe_latest)
    logger.success("  - {}", dom_probe_path)
    logger.success("  - {}", ws_log)
    logger.success("  - {}", http_log)
    logger.success("  - {}", console_log)


if __name__ == "__main__":
    main()
