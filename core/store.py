"""本地状态持久化（SQLite）

四张核心表（详见 md/architecture.md §5）：
    conv_state    会话级：每个 uid 当前进展、静默截止
    order_state   订单级：每张订单的 S2/S3/S4 进度
    card_code     核销码：去重 + 防重复核销
    action_log    操作审计：每次自动回复都留痕

所有方法都是同步阻塞，单进程使用够了。多线程访问时 sqlite 自带锁。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import config

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


# 模块级单例
_default: Store | None = None


def get() -> Store:
    global _default
    if _default is None:
        _default = Store()
    return _default
