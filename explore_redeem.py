"""核销页探查脚本

专门用于抓 https://mms.pinduoduo.com/orders/order/verify 页面的：
    1. 券码输入框 selector
    2. "获取订单信息" 按钮 selector
    3. "开始核销" 按钮 selector
    4. 订单信息展示区 selector（核销前先验证商品是否对得上）
    5. 核销记录表格 selector
    6. 提交核销时的 HTTP API（路径、参数、响应）—— 用来确认核销结果

用法：
    1. 已经跑过 login.py，根目录有 storage_state.json
    2. python explore_redeem.py
    3. 按提示在弹出的浏览器里【手动完整跑一次核销】：
        a. 输入一个真实的核销码 → 点【获取订单信息】
        b. 等订单信息出来，点【开始核销】
        c. 看到"核销成功"或类似提示
    4. Ctrl+C 结束

产物：
    captures/redeem_dom_<ts>.json   核销页 DOM 探针
    captures/redeem_http_<ts>.jsonl 核销页相关 HTTP 请求/响应（完整保留）
    captures/redeem_console_<ts>.log
"""
from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime

from loguru import logger
from playwright.sync_api import sync_playwright, Request, Response

import config


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main() -> None:
    if not config.STORAGE_STATE_PATH.exists():
        logger.error("没找到 storage_state.json，请先运行 `python login.py` 完成扫码登录")
        sys.exit(1)

    run_id = _ts()
    dom_path = config.CAPTURES_DIR / f"redeem_dom_{run_id}.json"
    dom_latest = config.CAPTURES_DIR / f"redeem_dom_latest_{run_id}.json"
    http_path = config.CAPTURES_DIR / f"redeem_http_{run_id}.jsonl"
    console_path = config.CAPTURES_DIR / f"redeem_console_{run_id}.log"

    http_fp = http_path.open("a", encoding="utf-8")
    console_fp = console_path.open("a", encoding="utf-8")

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

        # ---- HTTP 监听：核销页的所有请求都完整保留 ----
        # 关键字命中即记录，看到 verify/order/coupon/cardcode/redeem 都纳入
        VERIFY_KEYWORDS = (
            "/orders/order/verify",
            "/order/verify",
            "/api/.*/verify",
            "verifycode",
            "verify_code",
            "/redeem",
            "/cardcode",
            "card_code",
            "/coupon",
            "/voucher",
            "/ticket",
            "writeoff",
            "write_off",
        )

        def is_verify_related(url: str) -> bool:
            low = url.lower()
            return any(k in low for k in VERIFY_KEYWORDS)

        def on_request(req: Request) -> None:
            if "pinduoduo.com" not in req.url:
                return
            # 核销页打开后所有 mms 域请求都很可能与核销相关，统一记
            write_jsonl(
                http_fp,
                {
                    "event": "request",
                    "t": time.time(),
                    "method": req.method,
                    "url": req.url,
                    "post_data": req.post_data,
                    "headers": dict(req.headers),
                    "verify_hit": is_verify_related(req.url),
                },
            )
            if is_verify_related(req.url):
                logger.info("[VERIFY-REQ] {} {}", req.method, req.url)

        def on_response(resp: Response) -> None:
            if "pinduoduo.com" not in resp.url:
                return
            ct = resp.headers.get("content-type", "")
            body = None
            if "json" in ct or "text" in ct:
                try:
                    body = resp.text()
                except Exception:
                    body = None
            write_jsonl(
                http_fp,
                {
                    "event": "response",
                    "t": time.time(),
                    "status": resp.status,
                    "url": resp.url,
                    "body": body,
                    "verify_hit": is_verify_related(resp.url),
                },
            )
            if is_verify_related(resp.url):
                logger.info("[VERIFY-RESP] {} {}", resp.status, resp.url)

        page.on("request", on_request)
        page.on("response", on_response)
        page.on(
            "console",
            lambda msg: console_fp.write(
                f"[{datetime.now().isoformat()}] [{msg.type}] {msg.text}\n"
            )
            or console_fp.flush(),
        )

        # ---- 打开核销页 ----
        logger.info("打开核销页：{}", config.REDEEM_PAGE_URL)
        page.goto(config.REDEEM_PAGE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # ---- DOM 探针 ----
        probe_script = r"""
        () => {
            const desc = (el, withText = true) => {
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {
                    tag: el.tagName,
                    cls: (el.className?.toString?.() || '').slice(0, 200),
                    id: el.id || '',
                    role: el.getAttribute && el.getAttribute('role'),
                    placeholder: el.getAttribute && el.getAttribute('placeholder'),
                    name: el.getAttribute && el.getAttribute('name'),
                    type: el.getAttribute && el.getAttribute('type'),
                    rect: { x: Math.round(r.x), y: Math.round(r.y),
                            w: Math.round(r.width), h: Math.round(r.height) },
                    visible: r.width > 0 && r.height > 0,
                    text: withText ? (el.innerText || '').slice(0, 80).replace(/\s+/g, ' ') : ''
                };
            };
            const path = (el, max = 8) => {
                const out = [];
                let n = el, i = 0;
                while (n && n.tagName && n.tagName !== 'BODY' && i < max) {
                    const cls = (n.className?.toString?.() || '')
                        .split(/\s+/).filter(Boolean).slice(0, 2).join('.');
                    out.unshift(n.tagName.toLowerCase() + (cls ? '.' + cls : ''));
                    n = n.parentElement;
                    i++;
                }
                return out.join(' > ');
            };

            const result = {};

            // 券码输入框（看截图：placeholder 应是 "请输入"，且旁边带 "获取订单信息"）
            const inputs = [];
            for (const el of document.querySelectorAll('input, textarea')) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                inputs.push({ ...desc(el), path: path(el) });
            }
            result["输入框候选"] = inputs;

            // 关键按钮：用文字精确匹配
            const btnTexts = ["获取订单信息", "开始核销", "重置", "门店管理"];
            const btns = {};
            for (const t of btnTexts) btns[t] = [];
            for (const el of document.querySelectorAll('button, a, span, div')) {
                const txt = (el.innerText || '').trim();
                if (btnTexts.includes(txt)) {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    btns[txt].push({ ...desc(el), path: path(el) });
                }
            }
            result["按钮"] = btns;

            // 下拉选择（核销门店）
            const selects = [];
            for (const el of document.querySelectorAll(
                'select, [class*="select" i], [class*="dropdown" i], [class*="cascader" i]'
            )) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const txt = (el.innerText || '').slice(0, 60);
                if (!txt) continue;
                selects.push({ ...desc(el), path: path(el) });
            }
            result["下拉选择候选"] = selects.slice(0, 10);

            // 订单信息展示区（截图里有 "订单信息" 标签 + 一个大空白区）
            const labels = [];
            for (const el of document.querySelectorAll('label, span, div')) {
                const t = (el.innerText || '').trim();
                if (t === '订单信息' || t === '*券码' || t === '券码' || t === '核销门店') {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    labels.push({ label: t, ...desc(el, false), path: path(el) });
                }
            }
            result["关键标签"] = labels;

            // 表格（核销记录区）
            const tables = [];
            for (const el of document.querySelectorAll('table, [class*="table" i]')) {
                const r = el.getBoundingClientRect();
                if (r.width < 200 || r.height < 50) continue;
                tables.push({ ...desc(el, false), path: path(el) });
            }
            result["表格候选"] = tables.slice(0, 5);

            // 检测 iframe（万一核销表单在 iframe 内）
            result["iframe"] = Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: f.src, name: f.name,
            }));

            return result;
        }
        """

        try:
            probe = page.evaluate(probe_script)
            dom_path.write_text(
                json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            dom_latest.write_text(
                json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.success("初始 DOM 探针写入：{}", dom_path)
        except Exception as e:
            logger.warning("DOM 探针失败：{}", e)

        # ---- 阻塞等用户操作 ----
        print("\n" + "=" * 78)
        print("核销页已打开。请按以下步骤【完整跑一次核销】，让我抓到接口和 selector：")
        print()
        print("  [1] 在【*券码】输入框输入一个真实的核销码")
        print("  [2] 点【获取订单信息】，等右下方'订单信息'区域显示出商品和金额")
        print("      （此时会触发一个 HTTP 请求，我会自动抓到）")
        print("  [3] 如有【核销门店】下拉，选一个")
        print("  [4] 点【开始核销】，等成功提示")
        print("      （核销成功的接口最关键，请务必让它走完）")
        print("  [5] Ctrl+C 退出")
        print()
        print("脚本期间会每 5 秒重新探一次 DOM，覆盖到 redeem_dom_latest_*.json")
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
                if tick % 5 == 0:
                    try:
                        probe = page.evaluate(probe_script)
                        dom_latest.write_text(
                            json.dumps(probe, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
        finally:
            http_fp.close()
            console_fp.close()
            try:
                browser.close()
            except Exception:
                pass

    logger.success("核销页探查完成。请把以下文件贴给我：")
    logger.success("  - {}  ★ 核销 DOM 结构", dom_latest)
    logger.success("  - {}  ★ 核销 API（含开始核销时的请求体）", http_path)
    logger.success("  - {}", dom_path)
    logger.success("  - {}", console_path)


if __name__ == "__main__":
    main()
