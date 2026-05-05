"""本地状态持久化（SQLite）

核心表（详见 md/architecture.md §5）：
    conv_state / order_state / card_code / action_log / settings / …
    catalog_item（商品资料映射：share_body + 可选 product_url/description）、qa_item（店铺 QA 知识库）等。

所有方法都是同步阻塞，单进程使用够了。多线程访问时 sqlite 自带锁。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core import config

_CATALOG_PAN_LINK_RE = re.compile(
    r"https://pan\.baidu\.com/s/[a-zA-Z0-9_-]+(?:\?[^\s]+)?",
    re.IGNORECASE,
)


def _catalog_extract_pan_url(sb: str) -> str:
    """从 share_body 中抽出第一条百度网盘链接（迁移回填用，与 tools.catalog 逻辑一致）。"""
    t = (sb or "").strip()
    if not t:
        return ""
    m = _CATALOG_PAN_LINK_RE.search(t)
    return m.group(0).rstrip(".,;，。）)") if m else ""


def _legacy_catalog_row_to_share_body(rd: dict[str, Any]) -> str:
    """旧版 catalog_item（title/url/pwd/extra/share_body）合并为一段发给客户的全文。"""
    sb = (rd.get("share_body") or "").strip()
    if sb:
        ex = (rd.get("extra_text") or "").strip()
        if ex and ex not in sb:
            return sb + "\n" + ex
        return sb
    title = (rd.get("title") or "").strip()
    url = (rd.get("url") or "").strip()
    pwd = (rd.get("pwd") or "").strip()
    extra = (rd.get("extra_text") or "").strip()
    if url:
        link = url.rstrip()
        if pwd and "pwd=" not in link:
            sep = "&" if "?" in link else "?"
            link = f"{link}{sep}pwd={pwd}"
        body = (
            "通过百度网盘分享的文件：" + title + "\n"
            "链接：" + link + "\n"
            "复制这段内容打开「百度网盘APP 即可获取」"
        )
    elif title:
        body = (
            "通过百度网盘分享的文件：" + title + "\n"
            "复制这段内容打开「百度网盘APP 即可获取」"
        )
    else:
        body = ""
    if extra:
        body = (body + "\n" + extra) if body else extra
    return body.strip()


_DDL = """
CREATE TABLE IF NOT EXISTS conv_state (
    uid             TEXT PRIMARY KEY,
    last_msg_id     TEXT,
    last_active_ts  INTEGER,
    silenced_until  INTEGER,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS order_state (
    order_sn        TEXT PRIMARY KEY,
    uid             TEXT,
    goods_id        TEXT,
    sku_id          TEXT,
    goods_name      TEXT,
    pay_status      INTEGER,
    guide_sent_at   INTEGER,
    redeemed_at     INTEGER,
    delivered_at    INTEGER,
    delivered_url   TEXT,
    raw_json        TEXT
);
CREATE INDEX IF NOT EXISTS ix_order_uid ON order_state(uid);

CREATE TABLE IF NOT EXISTS card_code (
    code            TEXT PRIMARY KEY,
    uid             TEXT,
    order_sn        TEXT,
    submitted_at    INTEGER,
    succeeded_at    INTEGER,
    error_msg       TEXT
);

CREATE TABLE IF NOT EXISTS action_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT,
    stage           TEXT,
    tool            TEXT,
    payload         TEXT,
    success         INTEGER,
    error_msg       TEXT,
    ts              INTEGER
);
CREATE INDEX IF NOT EXISTS ix_log_uid ON action_log(uid);
CREATE INDEX IF NOT EXISTS ix_log_ts ON action_log(ts);

-- GUI 全局配置（key/value），优先级高于 .env
CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      INTEGER
);

-- 每个 stage 的回复模式
-- mode: 'auto'(走 LLM) | 'template'(固定模板)
CREATE TABLE IF NOT EXISTS stage_config (
    stage           TEXT PRIMARY KEY,
    mode            TEXT NOT NULL DEFAULT 'auto',
    template        TEXT,
    updated_at      INTEGER
);

-- 完整聊天历史（GUI 会话页 + LLM 上下文复用）
-- role: 'user'(客户) | 'mall_cs'(人工客服) | 'bot'(本程序自动回复)
CREATE TABLE IF NOT EXISTS chat_message (
    msg_id          TEXT PRIMARY KEY,
    uid             TEXT NOT NULL,
    role            TEXT NOT NULL,
    msg_type        INTEGER,
    content         TEXT,
    ts              INTEGER,
    raw_json        TEXT
);
CREATE INDEX IF NOT EXISTS ix_chat_uid_ts ON chat_message(uid, ts);

-- 商品 → 资料全文（百度网盘复制出来的整段话术）
-- match_type: 'goods_id' | 'sku_id' | 'keyword'
-- match_value:  goodsId / skuId / 关键字逗号串(如"散打,S022")
CREATE TABLE IF NOT EXISTS catalog_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_type      TEXT NOT NULL,
    match_value     TEXT NOT NULL,
    share_body        TEXT NOT NULL DEFAULT '',
    product_url       TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    updated_at      INTEGER,
    UNIQUE(match_type, match_value)
);
CREATE INDEX IF NOT EXISTS ix_catalog_match ON catalog_item(match_type);

-- 店铺 QA：人工维护的标准问答；enabled=1 且答复非空时注入每次 LLM 用户消息
CREATE TABLE IF NOT EXISTS qa_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER,
    updated_at      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_qa_enabled ON qa_item(enabled);
"""


@dataclass
class OrderState:
    order_sn: str
    uid: str
    goods_name: str | None
    sku_id: str | None
    goods_id: str | None
    pay_status: int | None
    guide_sent_at: int | None
    redeemed_at: int | None
    delivered_at: int | None


class Store:
    def __init__(self, db_path: Path | str | None = None) -> None:
        path = Path(db_path or config.DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.commit()
            self._migrate_catalog_legacy_columns()
            self._migrate_catalog_product_columns()

    def _migrate_catalog_legacy_columns(self) -> None:
        """旧版 catalog_item 含 title/url/pwd/extra_text 时，合并为 share_body 后删表重建。"""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(catalog_item)").fetchall()
        }
        if not cols or "title" not in cols:
            return
        rows = list(self._conn.execute("SELECT * FROM catalog_item"))
        migrated: list[tuple[Any, ...]] = []
        for r in rows:
            rd = {k: r[k] for k in r.keys()}
            sb = _legacy_catalog_row_to_share_body(rd)
            if not sb:
                sb = "【资料文案缺失请在 GUI 中编辑】"
            migrated.append(
                (rd["id"], rd["match_type"], rd["match_value"], sb, self.now()),
            )
        self._conn.execute("DROP TABLE catalog_item")
        self._conn.execute(
            """
            CREATE TABLE catalog_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_type TEXT NOT NULL,
                match_value TEXT NOT NULL,
                share_body TEXT NOT NULL DEFAULT '',
                product_url TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                updated_at INTEGER,
                UNIQUE(match_type, match_value)
            )
            """,
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_catalog_match ON catalog_item(match_type)",
        )
        for mid, mt, mv, sb, ts in migrated:
            self._conn.execute(
                """
                INSERT INTO catalog_item(
                    id, match_type, match_value, share_body,
                    product_url, description, updated_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (mid, mt, mv, sb, "", "", ts),
            )
        self._conn.commit()

    def _migrate_catalog_product_columns(self) -> None:
        """为 catalog_item 增加 product_url / description，并对空的 product_url 从 share_body 解析回填。

        description 列不自动从正文推断，避免与咨询阶段 LLM 仅用「人工描述」的策略冲突。
        """
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(catalog_item)").fetchall()
        }
        if not cols:
            return
        with self._lock:
            if "product_url" not in cols:
                self._conn.execute(
                    "ALTER TABLE catalog_item ADD COLUMN product_url TEXT NOT NULL DEFAULT ''",
                )
            if "description" not in cols:
                self._conn.execute(
                    "ALTER TABLE catalog_item ADD COLUMN description TEXT NOT NULL DEFAULT ''",
                )
            self._conn.commit()
        rows = self._query("SELECT id, share_body, product_url, description FROM catalog_item")
        for r in rows:
            rid = int(r["id"])
            sb = r["share_body"] or ""
            pu = (r["product_url"] or "").strip()
            if pu:
                continue
            new_pu = _catalog_extract_pan_url(sb)
            if new_pu:
                self._exec(
                    "UPDATE catalog_item SET product_url=? WHERE id=?",
                    (new_pu, rid),
                )

    # ---------- 通用 ----------
    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    @staticmethod
    def now() -> int:
        return int(time.time())

    # ---------- conv_state ----------
    def get_conv_state(self, uid: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM conv_state WHERE uid=?", (uid,))
        if not rows:
            return None
        return {k: rows[0][k] for k in rows[0].keys()}

    def upsert_conv_state(
        self,
        uid: str,
        *,
        last_msg_id: str | None = None,
        last_active_ts: int | None = None,
        silenced_until: int | None = None,
        notes: str | None = None,
    ) -> None:
        """部分字段更新:None 的字段保持原值不变。"""
        existing = self.get_conv_state(uid) or {}
        new = {
            "last_msg_id": str(last_msg_id) if last_msg_id is not None else existing.get("last_msg_id"),
            "last_active_ts": last_active_ts if last_active_ts is not None else (existing.get("last_active_ts") or self.now()),
            "silenced_until": silenced_until if silenced_until is not None else existing.get("silenced_until"),
            "notes": notes if notes is not None else existing.get("notes"),
        }
        self._exec(
            """
            INSERT INTO conv_state(uid, last_msg_id, last_active_ts, silenced_until, notes)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                last_msg_id=excluded.last_msg_id,
                last_active_ts=excluded.last_active_ts,
                silenced_until=excluded.silenced_until,
                notes=excluded.notes
            """,
            (uid, new["last_msg_id"], new["last_active_ts"],
             new["silenced_until"], new["notes"]),
        )

    def get_last_msg_id(self, uid: str) -> str | None:
        rows = self._query("SELECT last_msg_id FROM conv_state WHERE uid=?", (uid,))
        return rows[0]["last_msg_id"] if rows else None

    def set_last_msg_id(self, uid: str, msg_id: str) -> None:
        self._exec(
            """
            INSERT INTO conv_state(uid, last_msg_id, last_active_ts)
            VALUES(?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                last_msg_id=excluded.last_msg_id,
                last_active_ts=excluded.last_active_ts
            """,
            (uid, str(msg_id), self.now()),
        )

    def is_silenced(self, uid: str) -> bool:
        rows = self._query("SELECT silenced_until FROM conv_state WHERE uid=?", (uid,))
        if not rows:
            return False
        until = rows[0]["silenced_until"] or 0
        return until > self.now()

    def silence_uid(self, uid: str, seconds: int, note: str = "") -> None:
        until = self.now() + seconds
        self._exec(
            """
            INSERT INTO conv_state(uid, silenced_until, notes)
            VALUES(?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                silenced_until=excluded.silenced_until,
                notes=COALESCE(?, conv_state.notes)
            """,
            (uid, until, note, note),
        )

    # ---------- order_state ----------
    def upsert_order(self, order: dict) -> None:
        """从 userAllOrder 响应里的一条 orders[i] 写入。"""
        g = order.get("orderGoodsList") or {}
        self._exec(
            """
            INSERT INTO order_state(order_sn, uid, goods_id, sku_id, goods_name,
                                    pay_status, raw_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_sn) DO UPDATE SET
                uid=excluded.uid,
                goods_id=excluded.goods_id,
                sku_id=excluded.sku_id,
                goods_name=excluded.goods_name,
                pay_status=excluded.pay_status,
                raw_json=excluded.raw_json
            """,
            (
                str(order.get("orderSn")),
                str(order.get("uid")),
                str(g.get("goodsId") or ""),
                str(g.get("skuId") or ""),
                g.get("goodsName"),
                order.get("payStatus"),
                json.dumps(order, ensure_ascii=False),
            ),
        )

    def get_order(self, order_sn: str) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM order_state WHERE order_sn=?", (order_sn,))
        return rows[0] if rows else None

    def list_orders_of_uid(self, uid: str) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM order_state WHERE uid=? ORDER BY order_sn DESC",
            (str(uid),),
        )

    def is_guide_sent(self, order_sn: str) -> bool:
        row = self.get_order(order_sn)
        return bool(row and row["guide_sent_at"])

    def mark_guide_sent(self, order_sn: str) -> None:
        self._exec(
            "UPDATE order_state SET guide_sent_at=? WHERE order_sn=?",
            (self.now(), order_sn),
        )

    def is_order_redeemed(self, order_sn: str) -> bool:
        row = self.get_order(order_sn)
        return bool(row and row["redeemed_at"])

    def mark_order_redeemed(self, order_sn: str) -> None:
        self._exec(
            "UPDATE order_state SET redeemed_at=? WHERE order_sn=?",
            (self.now(), order_sn),
        )

    def is_order_delivered(self, order_sn: str) -> bool:
        row = self.get_order(order_sn)
        return bool(row and row["delivered_at"])

    def mark_order_delivered(self, order_sn: str, url: str | None = None) -> None:
        self._exec(
            "UPDATE order_state SET delivered_at=?, delivered_url=? WHERE order_sn=?",
            (self.now(), url, order_sn),
        )

    # ---------- card_code ----------
    def is_code_redeemed(self, code: str) -> bool:
        rows = self._query(
            "SELECT succeeded_at FROM card_code WHERE code=?", (code,)
        )
        return bool(rows and rows[0]["succeeded_at"])

    def record_code_submit(self, code: str, uid: str, order_sn: str | None = None) -> None:
        self._exec(
            """
            INSERT INTO card_code(code, uid, order_sn, submitted_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                uid=excluded.uid,
                order_sn=COALESCE(excluded.order_sn, card_code.order_sn),
                submitted_at=excluded.submitted_at
            """,
            (code, uid, order_sn, self.now()),
        )

    def record_code_success(self, code: str, order_sn: str | None = None) -> None:
        self._exec(
            "UPDATE card_code SET succeeded_at=?, order_sn=COALESCE(?, order_sn) WHERE code=?",
            (self.now(), order_sn, code),
        )

    def record_code_error(self, code: str, error_msg: str) -> None:
        self._exec(
            "UPDATE card_code SET error_msg=? WHERE code=?",
            (error_msg, code),
        )

    # ---------- action_log ----------
    def log_action(
        self,
        uid: str,
        stage: str,
        tool: str,
        payload: Any,
        success: bool,
        error_msg: str | None = None,
    ) -> None:
        self._exec(
            """
            INSERT INTO action_log(uid, stage, tool, payload, success, error_msg, ts)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                stage,
                tool,
                json.dumps(payload, ensure_ascii=False, default=str),
                1 if success else 0,
                error_msg,
                self.now(),
            ),
        )

    def recent_failures(self, uid: str, within_seconds: int = 600) -> int:
        since = self.now() - within_seconds
        rows = self._query(
            "SELECT COUNT(*) c FROM action_log WHERE uid=? AND success=0 AND ts>=?",
            (uid, since),
        )
        return int(rows[0]["c"])

    def has_replied_to_incoming_msg_id(self, uid: str, msg_id: str) -> bool:
        """是否已对这条客户消息 msg_id 做过成功接待（读 action_log）。

        用于重启后 ``conv_state.last_msg_id`` 未对齐或丢失时，避免 ``latest_conversations`` 把旧消息再派一队。
        """
        mid = str(msg_id or "").strip()
        if not mid:
            return False
        uid_s = str(uid)
        try:
            rows = self._query(
                """
                SELECT 1 FROM action_log
                WHERE uid=?
                  AND success=1
                  AND tool IN ('llm_reply', 'escalate')
                  AND json_extract(payload, '$.incoming_msg_id') = ?
                LIMIT 1
                """,
                (uid_s, mid),
            )
            return bool(rows)
        except Exception:
            return False

    def count_actions_today(self, success_only: bool = True) -> int:
        """今日（本地时间 0 点起）的 action 计数。GUI 首页用。"""
        import time as _t
        from datetime import datetime
        midnight = int(datetime.now().replace(hour=0, minute=0, second=0,
                                              microsecond=0).timestamp())
        sql = "SELECT COUNT(*) c FROM action_log WHERE ts>=?"
        params: list[Any] = [midnight]
        if success_only:
            sql += " AND success=1"
        return int(self._query(sql, params)[0]["c"])

    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str | None) -> None:
        self._exec(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, value, self.now()),
        )

    def all_settings(self) -> dict[str, str]:
        rows = self._query("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}

    # ---------- stage_config ----------
    def get_stage_config(self, stage: str) -> dict[str, Any]:
        """返回 {'mode': 'auto'|'template', 'template': str|None}.

        无记录时默认 'auto'。
        """
        rows = self._query(
            "SELECT mode, template FROM stage_config WHERE stage=?", (stage,)
        )
        if not rows:
            return {"mode": "auto", "template": None}
        return {"mode": rows[0]["mode"], "template": rows[0]["template"]}

    def set_stage_config(self, stage: str, mode: str, template: str | None) -> None:
        if mode not in ("auto", "template"):
            raise ValueError(f"mode 必须是 auto/template,收到 {mode!r}")
        self._exec(
            """
            INSERT INTO stage_config(stage, mode, template, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(stage) DO UPDATE SET
                mode=excluded.mode,
                template=excluded.template,
                updated_at=excluded.updated_at
            """,
            (stage, mode, template, self.now()),
        )

    def all_stage_configs(self) -> dict[str, dict[str, Any]]:
        rows = self._query("SELECT stage, mode, template FROM stage_config")
        return {
            r["stage"]: {"mode": r["mode"], "template": r["template"]}
            for r in rows
        }

    # ---------- chat_message ----------
    def upsert_chat_message(
        self,
        msg_id: str,
        uid: str,
        role: str,
        content: str | None,
        msg_type: int | None = None,
        ts: int | None = None,
        raw: dict | None = None,
    ) -> None:
        self._exec(
            """
            INSERT INTO chat_message(msg_id, uid, role, msg_type, content, ts, raw_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(msg_id) DO UPDATE SET
                uid=excluded.uid,
                role=excluded.role,
                msg_type=excluded.msg_type,
                content=excluded.content,
                ts=COALESCE(excluded.ts, chat_message.ts),
                raw_json=COALESCE(excluded.raw_json, chat_message.raw_json)
            """,
            (str(msg_id), str(uid), role, msg_type, content,
             ts if ts is not None else self.now(),
             json.dumps(raw, ensure_ascii=False) if raw is not None else None),
        )

    def list_chat_uids(self, limit: int = 200) -> list[dict[str, Any]]:
        """会话页左侧列表用：每个 uid 的最后消息时间和最新一条预览。"""
        rows = self._query(
            """
            SELECT uid,
                   MAX(ts)         AS last_ts,
                   COUNT(*)        AS msg_count
            FROM chat_message
            GROUP BY uid
            ORDER BY last_ts DESC
            LIMIT ?
            """,
            (limit,),
        )
        result = []
        for r in rows:
            preview_rows = self._query(
                "SELECT content FROM chat_message WHERE uid=? ORDER BY ts DESC LIMIT 1",
                (r["uid"],),
            )
            preview = preview_rows[0]["content"] if preview_rows else ""
            result.append({
                "uid": r["uid"],
                "last_ts": r["last_ts"],
                "msg_count": r["msg_count"],
                "preview": (preview or "")[:60],
            })
        return result

    def list_chat_messages(self, uid: str, limit: int = 500) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM chat_message WHERE uid=? ORDER BY ts ASC LIMIT ?",
            (str(uid), limit),
        )

    def list_chat_messages_recent(self, uid: str, limit: int = 20) -> list[sqlite3.Row]:
        """按时间倒序取最近 limit 条,再按时间正序返回(给 LLM 对话上下文)."""
        rows = self._query(
            "SELECT * FROM chat_message WHERE uid=? ORDER BY ts DESC LIMIT ?",
            (str(uid), limit),
        )
        return list(reversed(rows))

    def get_latest_chat_message(
        self, uid: str, *, role: str | None = None,
    ) -> sqlite3.Row | None:
        """该 uid 最新一条聊天;指定 role 时只取该角色的最新一条。"""
        uid = str(uid)
        if role:
            rows = self._query(
                "SELECT * FROM chat_message WHERE uid=? AND role=? "
                "ORDER BY ts DESC LIMIT 1",
                (uid, role),
            )
        else:
            rows = self._query(
                "SELECT * FROM chat_message WHERE uid=? ORDER BY ts DESC LIMIT 1",
                (uid,),
            )
        return rows[0] if rows else None

    # ---------- 统计 (首页卡片用) ----------
    def stats_today(self) -> dict[str, int]:
        """返回今日凌晨 0 点起的若干统计。

        - replies_today      action_log 中 tool=llm_reply 且 success=1 的条数
        - escalates_today    action_log 中 tool=escalate 的条数
        - active_uids_24h    最近 24 小时有过新消息(role=user)的 uid 数
        """
        from datetime import datetime
        midnight = int(datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp())
        day_ago = self.now() - 24 * 3600

        replies = self._query(
            "SELECT COUNT(*) c FROM action_log WHERE ts>=? AND tool=? AND success=1",
            (midnight, "llm_reply"),
        )
        escalates = self._query(
            "SELECT COUNT(*) c FROM action_log WHERE ts>=? AND tool=?",
            (midnight, "escalate"),
        )
        actives = self._query(
            "SELECT COUNT(DISTINCT uid) c FROM chat_message "
            "WHERE ts>=? AND role='user'",
            (day_ago,),
        )
        return {
            "replies_today": int(replies[0]["c"]) if replies else 0,
            "escalates_today": int(escalates[0]["c"]) if escalates else 0,
            "active_uids_24h": int(actives[0]["c"]) if actives else 0,
        }

    def list_action_log(self, uid: str | None = None,
                        limit: int = 200) -> list[sqlite3.Row]:
        if uid:
            return self._query(
                "SELECT * FROM action_log WHERE uid=? ORDER BY ts DESC LIMIT ?",
                (uid, limit),
            )
        return self._query(
            "SELECT * FROM action_log ORDER BY ts DESC LIMIT ?",
            (limit,),
        )

    # ---------- catalog_item ----------
    def list_catalog_items(self) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM catalog_item ORDER BY match_type, match_value"
        )

    def get_catalog_by_id(self, item_id: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM catalog_item WHERE id=?", (item_id,))
        return rows[0] if rows else None

    def upsert_catalog_item(
        self,
        match_type: str,
        match_value: str,
        share_body: str,
        item_id: int | None = None,
        *,
        product_url: str = "",
        description: str = "",
    ) -> int:
        """新增或更新一条商品映射。

        ``share_body`` 为发给客户的整段网盘话术（必填）。
        ``product_url`` / ``description`` 可选，用于 LLM 与 GUI 展示；未填时运行时仍可从 ``share_body`` 解析。
        返回行 id。
        """
        if match_type not in ("goods_id", "sku_id", "keyword"):
            raise ValueError(f"match_type 必须是 goods_id/sku_id/keyword, 收到 {match_type!r}")
        if not match_value:
            raise ValueError("match_value 不能为空")
        sb = (share_body or "").strip()
        if not sb:
            raise ValueError("share_body 不能为空")
        pu = (product_url or "").strip()
        desc = (description or "").strip()

        if item_id is not None:
            self._exec(
                """
                UPDATE catalog_item SET
                    match_type=?, match_value=?, share_body=?,
                    product_url=?, description=?, updated_at=?
                WHERE id=?
                """,
                (match_type, match_value, sb, pu, desc, self.now(), item_id),
            )
            return item_id

        cur = self._exec(
            """
            INSERT INTO catalog_item(
                match_type, match_value, share_body, product_url, description, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_type, match_value) DO UPDATE SET
                share_body=excluded.share_body,
                product_url=excluded.product_url,
                description=excluded.description,
                updated_at=excluded.updated_at
            """,
            (match_type, match_value, sb, pu, desc, self.now()),
        )
        return cur.lastrowid

    def delete_catalog_item(self, item_id: int) -> None:
        self._exec("DELETE FROM catalog_item WHERE id=?", (item_id,))

    def find_catalog(
        self,
        goods_id: str | int | None = None,
        sku_id: str | int | None = None,
        goods_name: str | None = None,
    ) -> sqlite3.Row | None:
        """按优先级查找映射:goods_id > sku_id > keyword(最长命中)"""
        if goods_id:
            rows = self._query(
                "SELECT * FROM catalog_item WHERE match_type='goods_id' AND match_value=?",
                (str(goods_id),),
            )
            if rows:
                return rows[0]
        if sku_id:
            rows = self._query(
                "SELECT * FROM catalog_item WHERE match_type='sku_id' AND match_value=?",
                (str(sku_id),),
            )
            if rows:
                return rows[0]
        if goods_name:
            kw_rows = self._query(
                "SELECT * FROM catalog_item WHERE match_type='keyword'"
            )
            best: tuple[int, sqlite3.Row] | None = None
            for r in kw_rows:
                kws = [k.strip() for k in (r["match_value"] or "").split(",") if k.strip()]
                for kw in kws:
                    if kw in goods_name:
                        score = len(kw)
                        if best is None or score > best[0]:
                            best = (score, r)
            if best:
                return best[1]
        return None

    # ---------- qa_item（店铺标准问答，注入 LLM） ----------
    def list_qa_items(self) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM qa_item ORDER BY enabled DESC, COALESCE(updated_at,0) DESC, id DESC",
        )

    def insert_qa_item(
        self,
        question: str,
        answer: str = "",
        *,
        enabled: int = 1,
    ) -> int:
        q = (question or "").strip()
        if not q:
            raise ValueError("question 不能为空")
        ts = self.now()
        cur = self._exec(
            """
            INSERT INTO qa_item(question, answer, enabled, created_at, updated_at)
            VALUES(?,?,?,?,?)
            """,
            (q, (answer or "").strip(), 1 if enabled else 0, ts, ts),
        )
        return int(cur.lastrowid)

    def update_qa_item(
        self,
        item_id: int,
        question: str,
        answer: str,
        *,
        enabled: int = 1,
    ) -> None:
        q = (question or "").strip()
        if not q:
            raise ValueError("question 不能为空")
        self._exec(
            """
            UPDATE qa_item SET question=?, answer=?, enabled=?, updated_at=?
            WHERE id=?
            """,
            (q, (answer or "").strip(), 1 if enabled else 0, self.now(), item_id),
        )

    def delete_qa_item(self, item_id: int) -> None:
        self._exec("DELETE FROM qa_item WHERE id=?", (item_id,))

    def qa_context_block(self, *, max_chars: int = 6000, max_pairs: int = 80) -> str:
        """拼成注入 LLM 用户消息的 QA 段；仅含启用且答复非空的条目。"""
        rows = self._query(
            """
            SELECT question, answer FROM qa_item
            WHERE enabled=1 AND TRIM(COALESCE(answer,''))!=''
            ORDER BY COALESCE(updated_at,0) DESC, id DESC
            LIMIT ?
            """,
            (max_pairs,),
        )
        if not rows:
            return ""
        lines: list[str] = [
            "（以下为店铺人工维护的标准答复口径，请结合客户当前话灵活运用，勿机械照搬。）",
        ]
        total = len(lines[0]) + 2
        for r in rows:
            q = (r["question"] or "").strip()
            a = (r["answer"] or "").strip()
            if not q or not a:
                continue
            if len(q) > 800:
                q = q[:800] + "…"
            if len(a) > 2000:
                a = a[:2000] + "…"
            chunk = f"问：{q}\n答：{a}"
            if total + len(chunk) + 2 > max_chars:
                lines.append("…（已达长度上限，其余条目略）")
                break
            lines.append(chunk)
            total += len(chunk) + 2
        return "\n\n".join(lines)


# 模块级单例
_default: Store | None = None


def get() -> Store:
    global _default
    if _default is None:
        _default = Store()
    return _default
