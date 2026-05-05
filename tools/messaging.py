"""拼多多商家聊天页: 选左侧会话 + 输入框打字 + 点「发送」(纯 DOM,不调发送 API.

与 md/protocol.md 一致:用 Playwright 模拟真人,降低风控概率。
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Frame, Locator, Page

_MAX_CHARS = 2000


def _iter_frames(page: "Page") -> list:
    """主 frame 优先,再扫子 frame(部分运营组件在 iframe)."""
    out = [page.main_frame]
    for fr in page.frames:
        if fr is not page.main_frame:
            out.append(fr)
    return out


async def focus_conversation(page: "Page", uid: str, *, timeout_ms: int = 12000) -> bool:
    """兼容旧名: 委托 session_dom 在左侧列表选中会话."""
    from tools.session_dom import activate_session

    return await activate_session(
        page,
        uid,
        content_preview="",
        prefer_unread_badge=True,
        timeout_ms=timeout_ms,
    )


async def _dismiss_compliance_banner(page: "Page") -> None:
    """关闭输入框上方的「服务用语须严格遵守」等横幅,避免遮挡发送按钮."""
    try:
        bar = page.locator("div").filter(has_text="服务用语须严格遵守").first
        if await bar.count() == 0:
            return
        for sel in (
            ".el-icon-close",
            "[class*='close' i]",
            "i[class*='close' i]",
            "button",
        ):
            try:
                btn = bar.locator(sel).first
                if await btn.count() > 0:
                    await btn.click(timeout=2000, force=True)
                    logger.info("[messaging] 已关闭服务用语提示条 ({})", sel)
                    await asyncio.sleep(0.3)
                    return
            except Exception:
                continue
    except Exception as e:
        logger.debug("[messaging] dismiss banner: {}", e)


async def _send_coords_from_input(page: "Page", box: "Locator") -> tuple[float, float] | None:
    """从输入框出发找最近「发送」控件中心视口坐标,供 mouse.click 穿透遮挡."""
    js = """
    (inputEl) => {
      function txt(b) {
        return (b.innerText || b.textContent || '').replace(/\\s+/g, '');
      }
      function isSend(b) {
        const cls = (b.className && String(b.className)) || '';
        if (cls.includes('send-btn')) return true;
        const t = txt(b);
        return t === '发送' || t === '发 送';
      }
      const ir = inputEl.getBoundingClientRect();
      let best = null, bestD = 1e12;
      const hitSel = 'div.send-btn, .send-btn, button, span.el-button, span[class*="el-button"], '
        + '[role="button"], div[role="button"]';
      for (const b of inputEl.ownerDocument.querySelectorAll(hitSel)) {
        if (!isSend(b)) continue;
        const r = b.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
        const dx = cx - (ir.left + ir.right) / 2, dy = cy - ir.bottom;
        const d = dx * dx + dy * dy;
        if (cx > ir.left - 40 && cy > ir.top - 20 && d < bestD) {
          bestD = d;
          best = b;
        }
      }
      if (!best) return null;
      const r = best.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    """
    try:
        pos = await box.evaluate(js)
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            return float(pos["x"]), float(pos["y"])
    except Exception as e:
        logger.debug("[messaging] send coords: {}", e)
    return None


async def _best_chat_input(fr: "Frame") -> "Locator | None":
    """选「主聊天区」里面积最大的可见输入框,避免点到左侧/隐藏占位 textarea."""
    candidates: list[tuple[float, Any]] = []
    for sel in (
        "div.reply-input textarea:visible",
        "div.reply-input [contenteditable='true']:visible",
        "textarea:visible",
        "div[contenteditable='true']:visible",
    ):
        loc = fr.locator(sel)
        try:
            n = await loc.count()
        except Exception:
            continue
        for i in range(min(n, 12)):
            item = loc.nth(i)
            try:
                box = await item.bounding_box()
            except Exception:
                continue
            if not box or box["width"] < 80 or box["height"] < 18:
                continue
            # 主输入区一般在视口偏右(排除左侧列表里误放的 textarea)
            if box["x"] < 220:
                continue
            area = box["width"] * box["height"]
            candidates.append((area, item))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


_CLICK_SEND_JS = """
(inputEl) => {
  function fire(b) {
    try {
      b.scrollIntoView({ block: 'center', inline: 'nearest' });
      b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
      b.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
      b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
      if (typeof b.click === 'function') b.click();
      return true;
    } catch (e) {
      return false;
    }
  }
  function isSendBtn(b) {
    const cls = (b.className && String(b.className)) || '';
    if (cls.includes('send-btn')) return true;
    const t = (b.innerText || b.textContent || '').replace(/\\s+/g, '');
    return t === '发送' || t === '发 送';
  }
  const hitSel = 'div.send-btn, .send-btn, button, [role="button"], span.el-button, '
    + 'span[class*="el-button"], a, div[role="button"]';
  let n = inputEl;
  for (let depth = 0; depth < 32 && n; depth++) {
    for (const b of n.querySelectorAll(hitSel)) {
      if (!isSendBtn(b)) continue;
      if (fire(b)) return true;
    }
    n = n.parentElement;
  }
  const ir = inputEl.getBoundingClientRect();
  const doc = inputEl.ownerDocument;
  let best = null;
  let bestD = 1e12;
  for (const b of doc.querySelectorAll(hitSel)) {
    if (!isSendBtn(b)) continue;
    const r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const cx = (r.left + r.right) / 2;
    const cy = (r.top + r.bottom) / 2;
    const dx = cx - (ir.left + ir.right) / 2;
    const dy = cy - ir.bottom;
    const d = dx * dx + dy * dy;
    if (cx > ir.left - 20 && d < bestD) {
      bestD = d;
      best = b;
    }
  }
  if (best && fire(best)) return true;
  return false;
}
"""


async def _try_click_send_div(fr: "Frame") -> bool:
    """拼多多客服页:发送常为 div.send-btn(在 .reply-footer 内),不是 button."""
    for sel in (
        "div.reply-box div.reply-footer div.send-btn",
        "div.reply-footer div.send-btn",
        ".reply-footer .send-btn",
        "div.content-box div.send-btn",
        "div.reply-box div.send-btn",
        "div.send-btn:visible",
    ):
        try:
            loc = fr.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.scroll_into_view_if_needed(timeout=3000)
            await loc.click(timeout=5000, force=True)
            logger.info("[messaging] 已点击 div.send-btn ({})", sel)
            return True
        except Exception:
            continue
    return False


async def _confirm_duplicate_content_modal(page: "Page", *, timeout_ms: int = 8000) -> bool:
    """拦截「相同内容」时的 Vue 弹窗：服务态度提醒 → 点「继续发送」."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            tip = page.locator("div.modal:visible").filter(
                has_text=re.compile(r"服务态度提醒|相同内容|确认继续发送")
            )
            if await tip.count() == 0:
                await asyncio.sleep(0.12)
                continue
            root = tip.first
            cont = root.locator(
                "span.btn-ok, .modal-footer span.btn-ok, button",
            ).filter(has_text=re.compile(r"^\s*继续发送\s*$"))
            if await cont.count() > 0:
                await cont.first.click(timeout=5000)
                logger.info("[messaging] 已点击「继续发送」(服务态度/重复内容提醒)")
                await asyncio.sleep(0.45)
                return True
            fallback = root.get_by_text("继续发送", exact=False).first
            if await fallback.count() > 0:
                await fallback.click(timeout=5000)
                logger.info("[messaging] 已点击「继续发送」(fallback)")
                await asyncio.sleep(0.45)
                return True
        except Exception as e:
            logger.debug("[messaging] _confirm_duplicate_content_modal: {}", e)
        await asyncio.sleep(0.12)
    return False


async def send_chat_message(
    page: "Page",
    text: str,
    *,
    min_delay: int = 38,
    max_delay: int = 92,
) -> tuple[bool, str | None]:
    """在已打开会话页:选右侧主输入框 → 模拟敲键 → 点「发送」(含祖先内 DOM 点击)."""
    text = (text or "").strip()
    if not text:
        return False, "empty_reply"
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
        logger.warning("[messaging] 回复过长,截断至 {} 字", _MAX_CHARS)

    delay = random.randint(min_delay, max_delay)
    pause = random.uniform(0.45, 1.25)
    typed_any = False

    for fr in _iter_frames(page):
        box = await _best_chat_input(fr)
        if box is None:
            continue

        try:
            await box.click(timeout=8000)
            try:
                await box.fill("")
            except Exception:
                pass

            try:
                await box.press_sequentially(text, delay=delay)
            except AttributeError:
                await box.type(text, delay=delay)

            typed_any = True

            await asyncio.sleep(pause)

            await _dismiss_compliance_banner(page)

            if await _try_click_send_div(fr):
                await _confirm_duplicate_content_modal(page)
                logger.info("[messaging] div.send-btn 已点击 len={}", len(text))
                return True, None

            clicked = await box.evaluate(_CLICK_SEND_JS)
            if clicked:
                await _confirm_duplicate_content_modal(page)
                logger.info("[messaging] 已通过脚本点击「发送」 len={}", len(text))
                return True, None

            coords = await _send_coords_from_input(page, box)
            if coords:
                x, y = coords
                await page.mouse.move(x, y)
                await asyncio.sleep(0.08)
                await page.mouse.click(x, y)
                await _confirm_duplicate_content_modal(page)
                logger.info("[messaging] 已 mouse.click 发送坐标 ({:.0f},{:.0f}) len={}", x, y, len(text))
                return True, None

            btns = fr.locator("button:visible").filter(has_text="发送")
            btn_count = await btns.count()
            for bi in range(btn_count - 1, -1, -1):
                try:
                    b = btns.nth(bi)
                    bb = await b.bounding_box()
                    if bb and bb["y"] > 120:
                        await b.click(timeout=4000, force=True)
                        await _confirm_duplicate_content_modal(page)
                        logger.info("[messaging] 已 force 点击可见「发送」 idx={} len={}", bi, len(text))
                        return True, None
                except Exception:
                    continue

            alt = fr.locator(
                "div[class*='editor' i] button:visible, "
                "div[class*='footer' i] button:visible, "
                "div[class*='input' i] button:visible, "
                "span.el-button:visible",
            ).filter(has_text="发送").first
            if await alt.count():
                await alt.click(timeout=5000, force=True)
                await _confirm_duplicate_content_modal(page)
                logger.info("[messaging] 已 force 点击区域发送 len={}", len(text))
                return True, None

            try:
                await box.press("Enter")
                await asyncio.sleep(0.5)
                try:
                    left = await box.input_value()
                except Exception:
                    left = await box.evaluate("el => (el.innerText || el.textContent || '').trim()")
                if len(str(left).strip()) < max(8, len(text) // 6):
                    await _confirm_duplicate_content_modal(page)
                    logger.info("[messaging] Enter 后输入区已清空,认为已发送 len={}", len(text))
                    return True, None
                logger.warning("[messaging] Enter 后输入区仍有内容,可能未发送")
            except Exception:
                try:
                    await page.keyboard.press("Control+Enter")
                    await asyncio.sleep(0.4)
                    logger.info("[messaging] 已尝试 Ctrl+Enter len={}", len(text))
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[messaging] 当前 frame 发送失败: {}", e)
            continue

    if typed_any:
        try:
            loc = page.locator(
                "div.reply-box div.reply-footer div.send-btn, "
                "div.reply-footer div.send-btn",
            ).first
            if await loc.count() > 0:
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=5000, force=True)
                await _confirm_duplicate_content_modal(page)
                logger.info("[messaging] page 级兜底 div.send-btn len={}", len(text))
                return True, None
        except Exception as e:
            logger.debug("[messaging] page 兜底 send-btn: {}", e)

    return False, "no_input_or_send"


async def _confirm_send_image_modal(page: "Page", *, timeout_ms: int = 12000) -> bool:
    """选图后弹出「是否发送图片」确认层，需再点「发送」。

    拼多多商家端为 Vue：``.modal`` + ``span.btn-ok``；部分环境仍为 Element ``el-dialog``。
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            # 1) Vue 模态（devtools: div.modal > .modal-header 是否发送图片 > .btn-ok）
            vue = page.locator("div.modal:visible").filter(
                has_text=re.compile(r"是否发送图片|发送图片")
            )
            if await vue.count() > 0:
                root = vue.first
                for sel in (
                    "span.btn-ok",
                    ".modal-footer span.btn-ok",
                    ".modal-box .modal-footer .btn-ok",
                ):
                    hit = root.locator(sel).first
                    if await hit.count() > 0:
                        try:
                            await hit.click(timeout=5000)
                            logger.info(
                                "[messaging] 已点击发图确认框 span.btn-ok（.modal）",
                            )
                            await asyncio.sleep(0.45)
                            return True
                        except Exception:
                            continue

            # 2) Element / role=dialog
            dlg = page.locator(
                ".el-dialog:visible, [role='dialog']:visible"
            ).filter(has_text=re.compile(r"是否发送图片|发送图片"))
            if await dlg.count() > 0:
                box = dlg.first
                candidates = (
                    box.locator("button.el-button--primary").filter(
                        has_text=re.compile(r"发送")
                    ),
                    box.locator("button").filter(has_text=re.compile(r"^\s*发送\s*$")),
                    box.get_by_role("button", name=re.compile(r"^\s*发送\s*$")),
                )
                for loc in candidates:
                    try:
                        if await loc.count() > 0:
                            await loc.first.click(timeout=5000)
                            logger.info(
                                "[messaging] 已点击发图确认框（el-dialog）",
                            )
                            await asyncio.sleep(0.45)
                            return True
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("[messaging] _confirm_send_image_modal: {}", e)
        await asyncio.sleep(0.12)
    return False


CARD_GUIDE_DEFAULT_CAPTION = (
    "您好，请在拼多多 APP：个人中心 → 我的订单 → 待收货 → 查看卡券，"
    "复制券码发到本窗口；核销成功后我们会自动发资料链接~"
)


async def send_card_code_guide(page: "Page", caption: str | None = None) -> tuple[bool, str | None]:
    """发送「如何获取券码」教程图（若页面上传失败则仅发文字指引）。

    图片路径见 ``core.config.CARD_CODE_GUIDE_IMAGE``（默认 ``assets/@assets/cardguide.JPG``）。
    """
    from core.config import CARD_CODE_GUIDE_IMAGE

    cap = (caption or CARD_GUIDE_DEFAULT_CAPTION).strip()
    img = Path(CARD_CODE_GUIDE_IMAGE)
    if not img.is_file():
        logger.warning("[messaging] 教程图不存在 {},仅发文", img)
        return await send_chat_message(page, cap)

    uploaded = False
    for fr in _iter_frames(page):
        inputs = fr.locator("input[type='file']")
        cnt = await inputs.count()
        for i in range(cnt):
            inp = inputs.nth(i)
            try:
                await inp.set_input_files(str(img))
                uploaded = True
                await asyncio.sleep(random.uniform(0.55, 1.1))
                logger.info("[messaging] 已通过 input[type=file] 选择教程图 {}", img.name)
                break
            except Exception as e:
                logger.debug("[messaging] set_input_files: {}", e)
                continue
        if uploaded:
            break

    if uploaded:
        await asyncio.sleep(random.uniform(0.2, 0.45))
        confirmed = await _confirm_send_image_modal(page)
        if not confirmed:
            logger.warning(
                "[messaging] 未在超时内点到「是否发送图片」弹窗的发送键,"
                "图片可能仍停留在预览；将继续尝试发送配文",
            )
        await asyncio.sleep(random.uniform(0.35, 0.75))
        return await send_chat_message(page, cap)

    logger.warning("[messaging] 未找到可用文件上传控件,仅发送文字指引")
    fallback = (
        cap + "\n（当前页面未能自动附带教程图，请打开订单详情 → 查看卡券，复制券码发我）"
    )
    return await send_chat_message(page, fallback)
