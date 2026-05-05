"""左侧会话列表: 以 DOM 为准选中会话(与 WS / HTTP 事件对齐).

设计:
    - WS 收到新消息后,bot 调用本模块在左侧列表里**遍历候选行**,综合
      data-* / 文案中的 uid 后缀 / 未读红点 / 与 WS 摘要相同的预览 打分,再点击。
    - 不依赖「先有完整 latest_conversations JSON」才能点选;列表接口仍可用于冷启动补消息。
"""
from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Page


def uid_variants(uid: str) -> list[str]:
    """界面常脱敏,多试 uid 后若干位."""
    s = str(uid).strip()
    if not s:
        return []
    out: list[str] = [s]
    for n in (12, 10, 8, 6, 4):
        if len(s) >= n:
            t = s[-n:]
            if t not in out:
                out.append(t)
    return out


def _iter_frames(page: "Page") -> list:
    out = [page.main_frame]
    for fr in page.frames:
        if fr is not page.main_frame:
            out.append(fr)
    return out


_ACTIVATE_JS = """
([keys, previewNorm, preferUnread]) => {
  const keyList = Array.isArray(keys) ? keys.map(String) : [String(keys)];
  const pv = String(previewNorm || '').replace(/\\s+/g, '').slice(0, 48);

  function fireClick(el) {
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      el.click();
      return true;
    } catch (e) {
      return false;
    }
  }

  function haystack(el) {
    let s = '';
    for (const a of Array.from(el.attributes || [])) {
      s += ' ' + a.name + '=' + String(a.value || '');
    }
    try {
      const ds = el.dataset || {};
      for (const k of Object.keys(ds)) s += ' @' + k + '=' + String(ds[k] || '');
    } catch (e) {}
    s += ' |' + String(el.innerText || '').replace(/\\s+/g, ' ').slice(0, 200);
    return s.replace(/\\s+/g, '');
  }

  function uidPartScore(hay) {
    let sc = 0;
    for (const k of keyList) {
      if (!k) continue;
      if (hay.includes(k)) sc += 110;
    }
    return sc;
  }

  function hasUnreadBadge(el) {
    if (el.querySelector(
      '[class*="unread" i], [class*="Unread" i], [class*="badge" i], [class*="Badge" i], '
      + '[class*="reddot" i], [class*="red-dot" i], [class*="newmsg" i], [class*="dot" i]'
    )) return true;
    const cs = String(el.className || '');
    if (cs.includes('unread') || cs.includes('Unread')) return true;
    for (const c of el.querySelectorAll('i, span, em, b, div')) {
      const r = c.getBoundingClientRect();
      if (r.width >= 4 && r.width <= 16 && r.height >= 4 && r.height <= 16
          && r.x < 280 && r.y < 900) {
        const style = window.getComputedStyle(c);
        const bg = style.backgroundColor || '';
        if (bg.includes('255') || bg.includes('rgb(255')) continue;
        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return true;
      }
    }
    return false;
  }

  function previewScore(el) {
    if (!pv) return 0;
    const t = String(el.innerText || '').replace(/\\s+/g, '');
    return t.includes(pv) ? 48 : 0;
  }

  const rootSelectors = [
    'div.left-panel', '.left-panel', 'div[class*="LeftPanel"]',
    'div[class*="conv-list" i]', 'div[class*="session-list" i]',
    'div[class*="conversation-list" i]', 'div.app-container div.left-panel',
    'aside', 'div[class*="sidebar" i]',
  ];
  const roots = [];
  for (const sel of rootSelectors) {
    try {
      const n = document.querySelector(sel);
      if (n) roots.push(n);
    } catch (e) {}
  }
  roots.push(document.body);

  let best = null;
  let bestScore = -1;
  let bestUnread = false;

  for (const root of roots) {
    const nodes = root.querySelectorAll('div, li, a, section, article');
    for (const el of nodes) {
      const r = el.getBoundingClientRect();
      if (r.width < 72 || r.height < 24 || r.width > 520) continue;
      if (r.x > 480 || r.y < 32) continue;

      const hay = haystack(el);
      let score = uidPartScore(hay);
      score += previewScore(el);
      const unread = hasUnreadBadge(el);
      if (unread) score += preferUnread ? 38 : 10;

      if (score < 28) continue;
      if (score > bestScore || (score === bestScore && unread && !bestUnread)) {
        bestScore = score;
        best = el;
        bestUnread = unread;
      }
    }
  }

  if (best && bestScore >= 52) {
    if (fireClick(best)) return { ok: true, score: bestScore, unread: bestUnread };
  }
  return { ok: false, score: bestScore, unread: false };
}
"""


async def activate_session(
    page: "Page",
    uid: str,
    *,
    content_preview: str = "",
    prefer_unread_badge: bool = True,
    timeout_ms: int = 12000,
) -> bool:
    """遍历左侧会话 DOM,按 uid/预览/未读标记打分后点击最佳一行."""
    uid = str(uid).strip()
    if not uid:
        return False
    keys = uid_variants(uid)
    pv = (content_preview or "").strip().replace("\n", " ")[:200]

    for fr in _iter_frames(page):
        try:
            ret = await fr.evaluate(_ACTIVATE_JS, [keys, pv, prefer_unread_badge])
            if isinstance(ret, dict) and ret.get("ok"):
                logger.info(
                    "[session_dom] 已点击左侧会话 uid={} score={} unread={}",
                    uid, ret.get("score"), ret.get("unread"),
                )
                await asyncio.sleep(random.uniform(0.35, 0.85))
                return True
            if isinstance(ret, dict) and ret.get("score", 0) >= 0:
                logger.debug(
                    "[session_dom] 未命中可点击行 uid={} best_score={}",
                    uid, ret.get("score"),
                )
        except Exception as e:
            logger.debug("[session_dom] evaluate uid={}: {}", uid, e)

    try:
        panel = page.locator(
            "div.left-panel, div.app-container .left-panel, aside[class*='left' i]",
        ).first
        if await panel.count() > 0:
            for k in keys:
                if len(k) < 4:
                    continue
                cell = panel.get_by_text(k, exact=False).first
                if await cell.count() > 0:
                    await cell.click(timeout=timeout_ms)
                    logger.info("[session_dom] get_by_text 点击 key={} (uid={})", k, uid)
                    await asyncio.sleep(random.uniform(0.35, 0.85))
                    return True
    except Exception as e:
        logger.debug("[session_dom] get_by_text: {}", e)

    logger.warning("[session_dom] 未选中会话 uid={} keys={}", uid, keys[:5])
    return False
