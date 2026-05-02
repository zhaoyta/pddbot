"""商品 → 资料映射 管理 CLI

用法：
    # 查看全部
    python catalog_admin.py list

    # 测试查询
    python catalog_admin.py lookup --sku-id 1876675563671
    python catalog_admin.py lookup --goods-name "【系统教学】散打基础教程视频版"

    # 增加 / 更新 sku 映射
    python catalog_admin.py set-sku 1876675563671 \
        --title "散打教程" \
        --url "https://pan.baidu.com/s/xxx" \
        --pwd "abcd"

    # 增加关键字映射
    python catalog_admin.py add-keyword \
        --match "单片机,S010,STM32" \
        --title "单片机教程" \
        --url "https://pan.baidu.com/s/xxx" \
        --pwd "3ua3"

    # 从 chat_*.jsonl 里抽取所有出现过的商品（辅助录入）
    python catalog_admin.py scan-chat captures/chat_xxxxxx.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from tools import catalog


def _save(data: dict) -> None:
    config.PRODUCT_MAP_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    catalog.reload()
    print(f"已写入 {config.PRODUCT_MAP_PATH}")


def cmd_list(_: argparse.Namespace) -> None:
    data = catalog.all_entries()
    print("=== by_sku_id ===")
    for k, v in data.get("by_sku_id", {}).items():
        print(f"  {k}  →  {v.get('title')}  |  {v.get('url')}  ({v.get('pwd')})")
    print("\n=== by_goods_id ===")
    for k, v in data.get("by_goods_id", {}).items():
        print(f"  {k}  →  {v.get('title')}  |  {v.get('url')}  ({v.get('pwd')})")
    print("\n=== by_keyword ===")
    for ent in data.get("by_keyword", []):
        print(f"  {ent.get('match')}  →  {ent.get('title')}  |  {ent.get('url')}  ({ent.get('pwd')})")


def cmd_lookup(ns: argparse.Namespace) -> None:
    item = catalog.lookup(
        sku_id=ns.sku_id,
        goods_id=ns.goods_id,
        goods_name=ns.goods_name,
    )
    if not item:
        print("未命中映射")
        sys.exit(1)
    print("命中：")
    print(f"  title  : {item.title}")
    print(f"  url    : {item.url}")
    print(f"  pwd    : {item.pwd}")
    print(f"  extra  : {item.extra_text}")
    print()
    print("示意回复：")
    print(item.to_message())


def cmd_set_sku(ns: argparse.Namespace) -> None:
    data = catalog.all_entries()
    by_sku = data.setdefault("by_sku_id", {})
    by_sku[str(ns.sku_id)] = {
        "title": ns.title,
        "url": ns.url,
        "pwd": ns.pwd,
        "extra_text": ns.extra or "",
    }
    _save(data)


def cmd_set_goods(ns: argparse.Namespace) -> None:
    data = catalog.all_entries()
    by_goods = data.setdefault("by_goods_id", {})
    by_goods[str(ns.goods_id)] = {
        "title": ns.title,
        "url": ns.url,
        "pwd": ns.pwd,
        "extra_text": ns.extra or "",
    }
    _save(data)


def cmd_add_keyword(ns: argparse.Namespace) -> None:
    data = catalog.all_entries()
    arr = data.setdefault("by_keyword", [])
    arr.append({
        "match": [s.strip() for s in ns.match.split(",") if s.strip()],
        "title": ns.title,
        "url": ns.url,
        "pwd": ns.pwd,
        "extra_text": ns.extra or "",
    })
    _save(data)


def cmd_scan_chat(ns: argparse.Namespace) -> None:
    """从 explore.py 抓出来的 chat_*.jsonl 里扫商品名，辅助录入。"""
    seen: dict[str, dict] = {}
    p = Path(ns.path)
    if not p.exists():
        print(f"文件不存在：{p}")
        sys.exit(1)

    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            body = rec.get("body")
            if not body or not isinstance(body, str):
                continue
            try:
                obj = json.loads(body)
            except Exception:
                continue

            # userAllOrder 响应里的 orders[*].orderGoodsList
            orders = (obj.get("result") or {}).get("orders") or []
            for o in orders:
                g = o.get("orderGoodsList") or {}
                gid = str(g.get("goodsId") or "")
                sid = str(g.get("skuId") or "")
                key = f"{gid}|{sid}"
                if key not in seen and (gid or sid):
                    seen[key] = {
                        "goodsId": gid,
                        "skuId": sid,
                        "goodsName": g.get("goodsName"),
                        "spec": g.get("spec"),
                        "price": (g.get("goodsPrice") or 0) / 100,
                    }

    print(f"共扫到 {len(seen)} 个不同商品：\n")
    for v in seen.values():
        print(f"  goodsId={v['goodsId']}  skuId={v['skuId']}  ¥{v['price']}")
        print(f"    {v['goodsName']}  /  {v['spec']}")
    print("\n建议接下来用 `set-sku` 或 `set-goods` 把它们的资料链接录入。")


def main() -> None:
    ap = argparse.ArgumentParser(description="商品 → 资料映射管理 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出全部映射").set_defaults(func=cmd_list)

    p = sub.add_parser("lookup", help="模拟查询")
    p.add_argument("--sku-id", dest="sku_id")
    p.add_argument("--goods-id", dest="goods_id")
    p.add_argument("--goods-name", dest="goods_name")
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser("set-sku", help="设置 sku 映射")
    p.add_argument("sku_id")
    p.add_argument("--title", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--pwd", required=True)
    p.add_argument("--extra", default="")
    p.set_defaults(func=cmd_set_sku)

    p = sub.add_parser("set-goods", help="设置 goodsId 映射")
    p.add_argument("goods_id")
    p.add_argument("--title", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--pwd", required=True)
    p.add_argument("--extra", default="")
    p.set_defaults(func=cmd_set_goods)

    p = sub.add_parser("add-keyword", help="增加关键字映射")
    p.add_argument("--match", required=True, help="逗号分隔的关键字")
    p.add_argument("--title", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--pwd", required=True)
    p.add_argument("--extra", default="")
    p.set_defaults(func=cmd_add_keyword)

    p = sub.add_parser("scan-chat", help="从 explore 抓的 chat_*.jsonl 扫出商品列表辅助录入")
    p.add_argument("path")
    p.set_defaults(func=cmd_scan_chat)

    ns = ap.parse_args()
    ns.func(ns)


if __name__ == "__main__":
    main()
