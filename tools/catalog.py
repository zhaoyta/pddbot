"""商品 → 资料全文 映射查询（数据源:SQLite catalog_item 表）

查询优先级:goods_id > sku_id > keyword（最长命中）

每条映射仅存 ``share_body``：百度网盘「复制全文」的整段话术（不拆字段）。

使用方式:
    from tools import catalog
    item = catalog.lookup(goods_id=928035245974)
    print(item.to_message())
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core import store as store_mod

_PAN_LINK_RE = re.compile(
    r"https://pan\.baidu\.com/s/[a-zA-Z0-9_-]+(?:\?[^\s]+)?",
    re.IGNORECASE,
)
_PWD_IN_URL_RE = re.compile(r"[?&]pwd=([^&\s]+)", re.IGNORECASE)


@dataclass
class CatalogItem:
    share_body: str

    @property
    def title(self) -> str:
        """首行摘要，供模板变量 ``title`` / 日志。"""
        for line in (self.share_body or "").split("\n"):
            t = line.strip()
            if t:
                return t[:160]
        return ""

    @property
    def pwd(self) -> str:
        """从 ``share_url`` 查询串解析提取码（若有）。"""
        u = self.share_url
        m = _PWD_IN_URL_RE.search(u)
        return m.group(1) if m else ""

    @property
    def share_url(self) -> str:
        """从正文正则抽出第一条 ``pan.baidu.com`` 链接。"""
        sb = (self.share_body or "").strip()
        if not sb:
            return ""
        m = _PAN_LINK_RE.search(sb)
        if m:
            return m.group(0).rstrip(".,;，。）)")
        return ""

    def to_message(self) -> str:
        return (self.share_body or "").strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.share_url,
            "pwd": self.pwd,
            "message": self.to_message(),
        }

    @classmethod
    def from_row(cls, row: Any) -> "CatalogItem":
        rd = {k: row[k] for k in row.keys()}
        return cls(share_body=(rd.get("share_body") or ""))


def lookup(
    goods_id: int | str | None = None,
    sku_id: int | str | None = None,
    goods_name: str | None = None,
) -> CatalogItem | None:
    """按优先级查找映射:goods_id > sku_id > keyword(最长命中)。"""
    s = store_mod.get()
    row = s.find_catalog(goods_id=goods_id, sku_id=sku_id, goods_name=goods_name)
    if row is None:
        return None
    return CatalogItem.from_row(row)


def all_items() -> list[dict[str, Any]]:
    """GUI 商品页 + 调试用。"""
    s = store_mod.get()
    return [
        {
            "id": r["id"],
            "match_type": r["match_type"],
            "match_value": r["match_value"],
            "share_body": r["share_body"] or "",
            "updated_at": r["updated_at"],
        }
        for r in s.list_catalog_items()
    ]
