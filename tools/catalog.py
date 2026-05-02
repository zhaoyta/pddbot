"""商品 → 资料链接 映射查询

数据源：catalog/product_map.json
查询优先级：by_sku_id > by_goods_id > by_keyword（最长命中）

使用方式：
    from tools import catalog
    item = catalog.lookup(sku_id=1876675563671)
    item = catalog.lookup(goods_name="【系统教学】散打基础教程视频版")
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

import config

_lock = threading.RLock()
_cache: dict[str, Any] | None = None
_mtime: float | None = None


@dataclass
class CatalogItem:
    title: str
    url: str
    pwd: str
    extra_text: str = ""

    def to_message(self) -> str:
        """生成给客户的回复文本（按百度网盘分享格式）。"""
        parts = [
            f"亲，您订购的【{self.title}】资料如下：",
            f"链接：{self.url}",
            f"提取码：{self.pwd}",
        ]
        if self.extra_text:
            parts.append(self.extra_text)
        return "\n".join(parts)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CatalogItem":
        return cls(
            title=d.get("title", ""),
            url=d.get("url", ""),
            pwd=d.get("pwd", ""),
            extra_text=d.get("extra_text", ""),
        )


def _load() -> dict[str, Any]:
    """加载 product_map.json，文件 mtime 变化时自动重载。"""
    global _cache, _mtime
    path = config.PRODUCT_MAP_PATH
    if not path.exists():
        return {"by_sku_id": {}, "by_goods_id": {}, "by_keyword": []}

    cur_mtime = path.stat().st_mtime
    with _lock:
        if _cache is None or _mtime != cur_mtime:
            _cache = json.loads(path.read_text(encoding="utf-8"))
            _mtime = cur_mtime
    return _cache  # type: ignore[return-value]


def lookup(
    sku_id: int | str | None = None,
    goods_id: int | str | None = None,
    goods_name: str | None = None,
) -> CatalogItem | None:
    """按优先级查找映射。命中返回 CatalogItem，否则 None。"""
    data = _load()

    if sku_id is not None:
        hit = data.get("by_sku_id", {}).get(str(sku_id))
        if hit:
            return CatalogItem.from_dict(hit)

    if goods_id is not None:
        hit = data.get("by_goods_id", {}).get(str(goods_id))
        if hit:
            return CatalogItem.from_dict(hit)

    if goods_name:
        # 关键字最长命中
        best: tuple[int, dict] | None = None
        for entry in data.get("by_keyword", []):
            for kw in entry.get("match", []):
                if kw and kw in goods_name:
                    score = len(kw)
                    if best is None or score > best[0]:
                        best = (score, entry)
        if best:
            return CatalogItem.from_dict(best[1])

    return None


def all_entries() -> dict[str, Any]:
    """完整数据，给管理脚本用。"""
    return _load()


def reload() -> None:
    """强制重载（管理脚本写完后调用）。"""
    global _cache, _mtime
    with _lock:
        _cache = None
        _mtime = None
