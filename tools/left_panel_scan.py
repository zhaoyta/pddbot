"""左侧会话列表扫描:未回复 / 小红点 / un-watch 等,供 sync/message 唤醒后批量处理."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Page

_COLLECT_JS = """
() => {
  const selectors = [
    'ul.timeout-unreply li.chat-item',
    '.left-panel li.chat-item',
    'div.left-panel li.chat-item',
    'aside li.chat-item',
  ];
  const seen = new Set();
  const rows = [];
  for (const sel of selectors) {
    try {
      for (const li of document.querySelectorAll(sel)) {
        if (seen.has(li)) continue;
        seen.add(li);
        rows.push(li);
      }
    } catch (e) {}
  }

  const out = [];
  for (const li of rows) {
    const box = li.querySelector('.chat-item-box') || li;
    const cls = String(box.className || '');
    const text = String(li.innerText || '').replace(/\\s+/g, ' ').trim();

    const unWatch = cls.includes('un-watch') || cls.includes('unwatch');
    const waitHint = /已等待\\s*\\d+\\s*分钟/.test(text);
    let redDot = false;
    const portrait = li.querySelector('.chat-portrait');
    if (portrait) {
      for (const iel of portrait.querySelectorAll('i')) {
        const st = window.getComputedStyle(iel);
        if (st.display === 'none' || st.visibility === 'hidden') continue;
        const r = iel.getBoundingClientRect();
        if (r.width < 3 || r.height < 3 || r.width > 20 || r.height > 20) continue;
        const bg = st.backgroundColor || '';
        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
          redDot = true;
          break;
        }
      }
    }

    if (!unWatch && !waitHint && !redDot) continue;

    let uid = '';
    const attrNames = [
      'data-uid', 'data-user-id', 'data-userid', 'data-buyer-id',
      'data-conv-uid', 'data-convid', 'data-id',
    ];
    const nodes = [li, box];
    for (const el of nodes) {
      if (!el || !el.getAttribute) continue;
      for (const a of attrNames) {
        const v = el.getAttribute(a);
        if (v && /^\\d{6,}$/.test(String(v).trim())) {
          uid = String(v).trim();
          break;
        }
      }
      if (uid) break;
    }
    if (!uid) {
      const html = (li.outerHTML || '').slice(0, 1200);
      const m = html.match(/\\b(\\d{10,20})\\b/);
      if (m) uid = m[1];
    }

    out.push({
      uid,
      preview: text.slice(0, 200),
      un_watch: unWatch,
      wait_hint: waitHint,
      red_dot: redDot,
    });
  }
  return out;
}
"""


async def collect_rows_needing_action(page: "Page") -> list[dict[str, Any]]:
    """返回需跟进的会话行摘要(含未回复文案、小红点、un-watch). uid 可能为空(需依赖后续点选)."""
    for fr in [page.main_frame, *page.frames]:
        if fr.is_detached():
            continue
        try:
            raw = await fr.evaluate(_COLLECT_JS)
        except Exception as e:
            logger.debug("[left_panel_scan] frame evaluate: {}", e)
            continue
        if not isinstance(raw, list) or not raw:
            continue
        by_uid: dict[str, dict[str, Any]] = {}
        no_uid: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            rec = {
                "uid": uid,
                "preview": str(row.get("preview") or ""),
                "un_watch": bool(row.get("un_watch")),
                "wait_hint": bool(row.get("wait_hint")),
                "red_dot": bool(row.get("red_dot")),
            }
            if uid:
                if uid in by_uid:
                    p = by_uid[uid]
                    p["un_watch"] = p["un_watch"] or rec["un_watch"]
                    p["wait_hint"] = p["wait_hint"] or rec["wait_hint"]
                    p["red_dot"] = p["red_dot"] or rec["red_dot"]
                    if len(rec["preview"]) > len(p["preview"]):
                        p["preview"] = rec["preview"]
                else:
                    by_uid[uid] = rec
            else:
                no_uid.append(rec)
        out = list(by_uid.values()) + no_uid
        if out:
            logger.info("[left_panel_scan] 命中 {} 条待跟进会话行(有 uid 已去重)", len(out))
            return out
    logger.debug("[left_panel_scan] 未扫到待跟进行(无 un-watch/已等待/红点)")
    return []
