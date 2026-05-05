"""拼多多商家后台「卡券核销」页：`submit_card_code`。

判定逻辑以接口响应为准（与探查脚本 captures 一致）：

- 点击「获取订单信息」后等待
  ``/maryland/api/carolina/ota/verification/preview`` —— ``success`` / ``result.orderSn``
  判断券码是否对应真实订单。
- 点击「开始核销」后等待
  ``/maryland/api/carolina/ota/verification``（无 ``/preview`` 后缀）—— ``success``
  判断核销是否成功；若返回 ``errorCode=60010``（该券已被核销）则**视同成功**，继续后续流程。

页面 URL 见 ``core.settings.redeem_page_url()``。

虚拟商品等场景下「核销门店」常为可选或仅占位；脚本**不会**自动选门店,以免误选实体门店。
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from loguru import logger

from core import settings as settings_mod

_ORDER_SN_RE = re.compile(r"\b\d{6}-\d{10,}\b")

# 提交核销接口：平台表示「该卡券已被核销」时，业务上按成功走后续发资料
_VERIFICATION_ALREADY_REDEEMED_CODES = frozenset({60010})

# 券码预览（订单信息）
_PREVIEW_MARK = "/maryland/api/carolina/ota/verification/preview"


def _is_submit_verification_url(url: str) -> bool:
    """提交核销接口：路径以 ``.../ota/verification`` 结尾，且不是 preview。"""
    base = url.split("?")[0].rstrip("/")
    return base.endswith("/verification") and not base.endswith("/preview")


def _is_preview_response(resp: Any) -> bool:
    if resp.request.method in ("OPTIONS", "HEAD"):
        return False
    return _PREVIEW_MARK in resp.url


def _is_submit_verification_response(resp: Any) -> bool:
    if resp.request.method in ("OPTIONS", "HEAD"):
        return False
    return _is_submit_verification_url(resp.url)


def _verification_means_already_redeemed(body: dict[str, Any]) -> bool:
    """提交核销返回 failure，但实际表示「该券已在系统中核销过」——按成功继续后续流程。"""
    if body.get("success"):
        return False
    code = body.get("errorCode")
    try:
        if int(code) in _VERIFICATION_ALREADY_REDEEMED_CODES:
            return True
    except (TypeError, ValueError):
        pass
    msg = str(body.get("errorMsg") or "")
    return ("已被核销" in msg) or ("已经核销" in msg)


async def _response_json(resp: Any) -> dict[str, Any] | None:
    try:
        return await resp.json()
    except Exception:
        try:
            txt = await resp.text()
            logger.debug("[redeem] 响应非 JSON status={} url={} body[:200]={!r}", resp.status, resp.url, txt[:200])
        except Exception:
            pass
        return None


async def _verify_root(page: Any) -> Any:
    """等待并返回核销主容器定位器。SPA 往往在 domcontentloaded 后才挂载表单。"""
    selectors = (
        "div.verify-container",
        "[class*='verify-container']",
        "div.components-content-block div[class*='verify']",
    )
    last_exc: Exception | None = None
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="visible", timeout=25000)
            logger.debug("[redeem] 核销主区就绪 selector={}", sel)
            return loc
        except Exception as e:
            last_exc = e
            continue
    logger.warning("[redeem] 未匹配到常见核销容器,继续用 verify-container 兜底 err={}", last_exc)
    return page.locator("div.verify-container").first


async def _click_verify_label(scope: Any, label: str, *, timeout_ms: int = 30000) -> None:
    """点击核销区内文案控件。探针里「获取订单信息」多在 DIV 包装层,不一定是原生 button。"""
    errs: list[Exception] = []
    try:
        await scope.get_by_role("button", name=label).click(timeout=timeout_ms)
        return
    except Exception as e:
        errs.append(e)
    try:
        await scope.locator("button, [role='button']").filter(has_text=label).first.click(
            timeout=timeout_ms,
        )
        return
    except Exception as e:
        errs.append(e)
    try:
        await scope.get_by_text(label, exact=True).first.click(timeout=timeout_ms)
        return
    except Exception as e:
        errs.append(e)
    try:
        await scope.locator(f"text={label}").first.click(timeout=timeout_ms)
        return
    except Exception as e:
        errs.append(e)
    logger.warning("[redeem] 无法点击「{}」依次试过 role/button/filter/text err_tail={}", label, errs[-1])
    raise errs[-1]


async def _pick_code_input(page: Any) -> Any:
    """返回券码输入框 locator。

    顶栏全局搜索框 placeholder 也含「输入」类文案（见 captures 里
    ``mms-header_search_new_box_input``），必须用 ``.verify-container`` 限定，
    否则会填进搜索框并弹出「暂无符合条件的结果」。
    """
    root = await _verify_root(page)
    # 探针顺序：表单内第一个 placeholder=请输入 即 *券码 行（约在 y≈200+）
    for sel in (
        'form .form-container input[placeholder="请输入"]',
        'form input[placeholder="请输入"]',
        'input[placeholder="请输入"]',
    ):
        cand = root.locator(sel)
        try:
            if await cand.count() == 0:
                continue
            first = cand.first
            if await first.is_visible():
                return first
        except Exception:
            continue
    # 兼容 placeholder 含「券」或仅有 type=text 的定制皮肤
    for sel in (
        'form input[placeholder*="券"]',
        'form input[type="text"]',
    ):
        cand = root.locator(sel)
        try:
            if await cand.count() == 0:
                continue
            first = cand.first
            if await first.is_visible():
                return first
        except Exception:
            continue
    logger.warning("[redeem] 未在核销区内找到券码输入框,回退 verify-container 内首个 input")
    return root.locator("input").first


async def _extract_order_sn_dom(page: Any) -> str | None:
    try:
        body = await page.inner_text("body", timeout=5000)
    except Exception:
        return None
    m = _ORDER_SN_RE.search(body or "")
    return m.group(0) if m else None


async def _read_toast_or_banner(page: Any) -> str:
    parts: list[str] = []
    for sel in (
        ".el-message",
        ".el-message__content",
        ".ant-message-notice",
        "[class*='toast' i]",
    ):
        try:
            loc = page.locator(sel)
            n = await loc.count()
            for i in range(min(n, 3)):
                t = (await loc.nth(i).inner_text()).strip()
                if t and len(t) < 500:
                    parts.append(t)
        except Exception:
            continue
    return " | ".join(parts) if parts else ""


async def _fallback_verify_success_from_ui(page: Any, *, timeout_s: float = 8.0) -> tuple[bool | None, str | None]:
    """仅在拿到接口响应失败时作兜底。**禁止**扫整页 ``body`` 匹配「核销成功」——
    下方「核销记录」表格里常有历史「成功」字样,会误判本次点击的结果。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        toast = await _read_toast_or_banner(page)
        if toast:
            tl = toast.replace(" ", "")
            if "核销成功" in tl or "核销已完成" in tl:
                return True, None
            if "失败" in toast or "错误" in toast or "无法" in toast:
                return False, toast
            # 误匹配防护：不要用单独的「成功」+「券」(页面其它模块也会出现)
        await asyncio.sleep(0.25)
    return None, None


async def submit_card_code(page: Any, code: str) -> dict[str, Any]:
    """在核销页输入券码并提交。返回 ``{success, order_sn, error}``。"""
    code = (code or "").strip()
    if not code:
        return {"success": False, "order_sn": None, "error": "券码为空"}

    if page is None:
        return {"success": False, "order_sn": None, "error": "page 未就绪"}

    url = settings_mod.redeem_page_url()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        logger.warning("[redeem] 打开核销页失败: {}", e)
        return {"success": False, "order_sn": None, "error": f"打开核销页失败: {e}"}

    try:
        await _verify_root(page)
    except Exception as e:
        logger.warning("[redeem] 等待核销区域出现失败: {}", e)
        return {
            "success": False,
            "order_sn": None,
            "error": f"核销页主区域未加载(请确认已登录且 URL 可打开): {e}",
        }

    await page.wait_for_timeout(500)

    inp = await _pick_code_input(page)
    try:
        await inp.wait_for(state="visible", timeout=15000)
        await inp.click(timeout=5000)
        await inp.fill("", timeout=3000)
        await inp.fill(code, timeout=5000)
    except Exception as e:
        logger.warning("[redeem] 填写券码失败: {}", e)
        return {"success": False, "order_sn": None, "error": f"填写券码失败: {e}"}

    async def _click_fetch_order() -> None:
        root = await _verify_root(page)
        await _click_verify_label(root, "获取订单信息", timeout_ms=30000)

    # ---------- 1) preview：券码是否对应订单 ----------
    preview_body: dict[str, Any] | None = None
    try:
        async with page.expect_response(
            lambda r: _is_preview_response(r),
            timeout=40000,
        ) as resp_info:
            await _click_fetch_order()
        resp = await resp_info.value
        preview_body = await _response_json(resp)
    except Exception as e:
        logger.warning("[redeem] 等待 preview 接口超时或失败: {}", e)
        return {
            "success": False,
            "order_sn": None,
            "error": "未收到订单预览接口响应（请确认已点击「获取订单信息」且网络正常）",
        }

    if not preview_body:
        return {"success": False, "order_sn": None, "error": "预览接口响应无法解析为 JSON"}

    if not preview_body.get("success"):
        err = preview_body.get("errorMsg") or preview_body.get("errorCode")
        msg = str(err) if err not in (None, "") else "券码无效或无法匹配订单"
        logger.info("[redeem] preview 失败 code={} msg={}", code, msg)
        return {"success": False, "order_sn": None, "error": msg}

    result = preview_body.get("result") or {}
    order_sn = str(result.get("orderSn") or "").strip()
    if not order_sn:
        order_sn = (await _extract_order_sn_dom(page) or "").strip()

    # ---------- 2) verification：提交核销 ----------
    async def _click_start_redeem() -> None:
        root = await _verify_root(page)
        await _click_verify_label(root, "开始核销", timeout_ms=30000)

    verify_body: dict[str, Any] | None = None
    try:
        async with page.expect_response(
            lambda r: _is_submit_verification_response(r),
            timeout=40000,
        ) as resp_info:
            await _click_start_redeem()
        resp = await resp_info.value
        if resp.status >= 400:
            logger.warning(
                "[redeem] 提交核销 HTTP status={} url={}",
                resp.status,
                resp.url[:120],
            )
        verify_body = await _response_json(resp)
    except Exception as e:
        logger.warning("[redeem] 等待 verification 接口超时: {}，仅用 toast 兜底(不用整页文案)", e)
        ok_ui, err_ui = await _fallback_verify_success_from_ui(page)
        if ok_ui is True:
            logger.info(
                "[redeem] toast 兜底判定为核销成功 code={} order_sn={}",
                code,
                order_sn,
            )
            return {"success": True, "order_sn": order_sn or "", "error": None}
        if ok_ui is False:
            return {"success": False, "order_sn": order_sn or None, "error": err_ui or "核销失败"}
        return {
            "success": False,
            "order_sn": order_sn or None,
            "error": "未收到核销提交接口响应且 toast 无明确成功/失败",
        }

    if not verify_body:
        return {"success": False, "order_sn": order_sn or None, "error": "核销接口响应无法解析为 JSON"}

    if verify_body.get("success"):
        logger.info(
            "[redeem] 提交核销接口 success=true（本次点击成功）code={} order_sn={}",
            code,
            order_sn,
        )
        return {"success": True, "order_sn": order_sn or "", "error": None}

    if _verification_means_already_redeemed(verify_body):
        # 与「本次核销成功」区分：平台提示券早已核销过
        logger.info(
            "[redeem] 提交核销接口返回「券已在平台核销过」,按已核销继续发资料 code={} "
            "order_sn={} errorCode={} errorMsg={!r} body={}",
            code,
            order_sn,
            verify_body.get("errorCode"),
            verify_body.get("errorMsg"),
            verify_body,
        )
        return {
            "success": True,
            "order_sn": order_sn or "",
            "error": None,
            "already_redeemed": True,
        }

    err = verify_body.get("errorMsg") or verify_body.get("errorCode")
    msg = str(err) if err not in (None, "") else "核销失败"
    logger.info("[redeem] verification 未成功 code={} msg={}", code, msg)
    return {"success": False, "order_sn": order_sn or None, "error": msg}
