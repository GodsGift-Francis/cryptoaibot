"""Durable engine ledger (SQLite, WAL mode).

Why a local store and not only Laravel: the engine must keep an authoritative,
crash-safe record of intents, orders, fills and positions even when the
control plane is unreachable (the reconciliation worker keeps ingesting
exchange events during a Laravel outage). Laravel receives a mirror of this
data through the idempotent outbox.

Idempotency is enforced by UNIQUE constraints, never by in-memory sets:
  * order_intents.client_intent_id      -> one order per intent
  * orders.client_order_id              -> one internal order per exchange order
  * fills.event_key                     -> one fill per exchange execution
  * processed_events.event_key          -> one application per exchange event
  * outbox.idempotency_key              -> one delivery per logical message
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from engine.domain.enums import MismatchSeverity, OrderPurpose, OrderStatus, ReconciliationStatus, TERMINAL_STATUSES
from engine.domain.models import Fill, Order, OrderIntent, Position, ReconciliationState

SCHEMA = """
CREATE TABLE IF NOT EXISTS order_intents (
    client_intent_id TEXT PRIMARY KEY,
    bot_instance_id  TEXT NOT NULL,
    symbol TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
    quantity TEXT NOT NULL, purpose TEXT NOT NULL,
    limit_price TEXT, stop_price TEXT, reference_price TEXT,
    strategy_name TEXT NOT NULL, strategy_version TEXT NOT NULL, signal_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_instance_id TEXT NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, order_type TEXT NOT NULL, purpose TEXT NOT NULL,
    client_order_id TEXT NOT NULL UNIQUE,
    client_intent_id TEXT NOT NULL UNIQUE REFERENCES order_intents(client_intent_id),
    exchange_order_id TEXT,
    requested_quantity TEXT NOT NULL, executed_quantity TEXT NOT NULL DEFAULT '0',
    cumulative_quote_quantity TEXT NOT NULL DEFAULT '0', average_fill_price TEXT,
    limit_price TEXT, stop_price TEXT, reference_price TEXT,
    status TEXT NOT NULL, submission_ambiguous INTEGER NOT NULL DEFAULT 0, reject_reason TEXT,
    submitted_at TEXT, acknowledged_at TEXT, last_exchange_update_at TEXT,
    last_exchange_update_ms INTEGER, terminal_at TEXT,
    raw_json TEXT, version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(bot_instance_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_exchange_id ON orders(exchange, exchange_order_id) WHERE exchange_order_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    client_order_id TEXT NOT NULL, exchange_trade_id TEXT,
    symbol TEXT NOT NULL, side TEXT NOT NULL,
    quantity TEXT NOT NULL, price TEXT NOT NULL, quote_quantity TEXT NOT NULL,
    commission TEXT NOT NULL, commission_asset TEXT,
    execution_time TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_trade ON fills(order_id, exchange_trade_id) WHERE exchange_trade_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS positions (
    bot_instance_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
    quantity TEXT NOT NULL, average_entry_price TEXT NOT NULL,
    realized_pnl TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
    last_mark_price TEXT, stop_price TEXT, fees_other_json TEXT,
    opened_at TEXT, updated_at TEXT,
    PRIMARY KEY (bot_instance_id, symbol)
);
CREATE TABLE IF NOT EXISTS processed_events (
    event_key TEXT PRIMARY KEY, source TEXT NOT NULL, event_type TEXT, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliation_state (
    bot_instance_id TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT,
    updated_at TEXT, last_success_at TEXT, ws_connected INTEGER NOT NULL DEFAULT 0,
    last_ws_event_at TEXT, last_ack_token TEXT
);
CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_key TEXT NOT NULL UNIQUE,
    bot_instance_id TEXT NOT NULL, reason TEXT NOT NULL, result_status TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS reconciliation_mismatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES reconciliation_runs(id),
    kind TEXT NOT NULL, severity TEXT NOT NULL, client_order_id TEXT, detail_json TEXT,
    resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL, last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Leases give one process exclusive ownership of a job (e.g. outbox delivery) with expiry-based takeover.
CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL);
-- Point-in-time news archive. News CANNOT be backtested without knowing exactly when each headline was
-- visible, and no free archive provides that. Recording it from now on is the only way to ever get a
-- dataset that is honest about timing. `seen_at` is when WE saw it, which is what a live bot would know.
CREATE TABLE IF NOT EXISTS news_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seen_at TEXT NOT NULL,
    published_at TEXT,
    source TEXT,
    title TEXT NOT NULL,
    sentiment REAL,
    item_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_news_seen ON news_snapshots(seen_at);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _s(value) -> str | None:
    return None if value is None else str(value)


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, datetime):
            return _iso(o)
        if hasattr(o, "value"):
            return o.value
        return super().default(o)


def dumps(obj) -> str:
    return json.dumps(obj, cls=DecimalEncoder, sort_keys=True)


class Store:
    def __init__(self, path: str, clock=None):
        self.path = path
        self._local = threading.local()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # A single shared connection is required for in-memory databases.
        self._shared = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None) if path == ":memory:" else None
        self._lock = threading.RLock()
        conn = self._conn()
        conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ plumbing
    def _conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def now(self) -> datetime:
        return self._clock()

    def in_transaction(self) -> bool:
        return getattr(self._local, "depth", 0) > 0

    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE so concurrent workers serialize writers, re-entrant within a thread."""
        with self._lock:
            conn = self._conn()
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                conn.execute("BEGIN IMMEDIATE")
            self._local.depth = depth + 1
            try:
                yield conn
            except BaseException:
                self._local.depth = depth
                if depth == 0:
                    conn.execute("ROLLBACK")
                raise
            else:
                self._local.depth = depth
                if depth == 0:
                    conn.execute("COMMIT")

    def _query(self, sql, params=()):
        with self._lock:
            cur = self._conn().execute(sql, params)
            cols = [c[0] for c in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ------------------------------------------------------------------ intents / orders
    def insert_intent_and_order(self, intent: OrderIntent, order: Order) -> bool:
        """Atomically records an intent and its order. False if the intent already exists."""
        now = _iso(self.now())
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO order_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (intent.client_intent_id, intent.bot_instance_id, intent.symbol, intent.side, intent.order_type,
                 str(intent.quantity), intent.purpose.value, _s(intent.limit_price), _s(intent.stop_price),
                 _s(intent.reference_price), intent.strategy_name, intent.strategy_version, intent.signal_id,
                 _iso(intent.created_at)),
            )
            if cur.rowcount == 0:
                return False
            cur = conn.execute(
                """INSERT INTO orders (bot_instance_id, exchange, symbol, side, order_type, purpose, client_order_id,
                   client_intent_id, requested_quantity, limit_price, stop_price, reference_price, status,
                   created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order.bot_instance_id, order.exchange, order.symbol, order.side, order.order_type,
                 order.purpose.value, order.client_order_id, order.client_intent_id, str(order.requested_quantity),
                 _s(order.limit_price), _s(order.stop_price), _s(order.reference_price), order.status.value, now, now),
            )
            order.id = cur.lastrowid
            return True

    def save_order(self, order: Order) -> None:
        """Optimistic-concurrency update: fails loudly if another writer changed the row."""
        with self.transaction() as conn:
            cur = conn.execute(
                """UPDATE orders SET exchange_order_id=?, executed_quantity=?, cumulative_quote_quantity=?,
                   average_fill_price=?, status=?, submission_ambiguous=?, reject_reason=?, submitted_at=?,
                   acknowledged_at=?, last_exchange_update_at=?, last_exchange_update_ms=?, terminal_at=?,
                   raw_json=?, version=version+1, updated_at=? WHERE id=? AND version=?""",
                (order.exchange_order_id, str(order.executed_quantity), str(order.cumulative_quote_quantity),
                 _s(order.average_fill_price), order.status.value, int(order.submission_ambiguous),
                 order.reject_reason, _iso(order.submitted_at), _iso(order.acknowledged_at),
                 _iso(order.last_exchange_update_at), order.last_exchange_update_ms, _iso(order.terminal_at),
                 dumps(order.raw or {}), _iso(self.now()), order.id, order.version),
            )
            if cur.rowcount != 1:
                raise ConcurrentModification(f"order {order.client_order_id} changed concurrently")
            order.version += 1

    def _row_to_order(self, r: dict) -> Order:
        return Order(
            id=r["id"], bot_instance_id=r["bot_instance_id"], exchange=r["exchange"], symbol=r["symbol"],
            side=r["side"], order_type=r["order_type"], purpose=OrderPurpose(r["purpose"]),
            client_order_id=r["client_order_id"], client_intent_id=r["client_intent_id"],
            exchange_order_id=r["exchange_order_id"], requested_quantity=Decimal(r["requested_quantity"]),
            executed_quantity=Decimal(r["executed_quantity"]),
            cumulative_quote_quantity=Decimal(r["cumulative_quote_quantity"]),
            average_fill_price=_dec(r["average_fill_price"]), limit_price=_dec(r["limit_price"]),
            stop_price=_dec(r["stop_price"]), reference_price=_dec(r["reference_price"]),
            status=OrderStatus(r["status"]), submission_ambiguous=bool(r["submission_ambiguous"]),
            reject_reason=r["reject_reason"], submitted_at=_dt(r["submitted_at"]),
            acknowledged_at=_dt(r["acknowledged_at"]), last_exchange_update_at=_dt(r["last_exchange_update_at"]),
            last_exchange_update_ms=r["last_exchange_update_ms"], terminal_at=_dt(r["terminal_at"]),
            raw=json.loads(r["raw_json"] or "{}"), version=r["version"],
        )

    def get_order(self, client_order_id: str) -> Order | None:
        rows = self._query("SELECT * FROM orders WHERE client_order_id=?", (client_order_id,))
        return self._row_to_order(rows[0]) if rows else None

    def get_order_by_intent(self, client_intent_id: str) -> Order | None:
        rows = self._query("SELECT * FROM orders WHERE client_intent_id=?", (client_intent_id,))
        return self._row_to_order(rows[0]) if rows else None

    def intent_signal_id(self, client_intent_id: str) -> str | None:
        rows = self._query("SELECT signal_id FROM order_intents WHERE client_intent_id=?", (client_intent_id,))
        return rows[0]["signal_id"] if rows else None

    def get_order_by_exchange_id(self, exchange: str, exchange_order_id: str) -> Order | None:
        rows = self._query("SELECT * FROM orders WHERE exchange=? AND exchange_order_id=?", (exchange, str(exchange_order_id)))
        return self._row_to_order(rows[0]) if rows else None

    def non_terminal_orders(self, bot_instance_id: str, symbol: str | None = None) -> list[Order]:
        terminal = tuple(s.value for s in TERMINAL_STATUSES)
        sql = f"SELECT * FROM orders WHERE bot_instance_id=? AND status NOT IN ({','.join('?' * len(terminal))})"
        params = [bot_instance_id, *terminal]
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        return [self._row_to_order(r) for r in self._query(sql + " ORDER BY id", params)]

    def orders(self, bot_instance_id: str, symbol: str | None = None, limit: int = 500) -> list[Order]:
        sql, params = "SELECT * FROM orders WHERE bot_instance_id=?", [bot_instance_id]
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        return [self._row_to_order(r) for r in self._query(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])]

    # ------------------------------------------------------------------ fills
    def insert_fill(self, fill: Fill) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO fills (order_id, client_order_id, exchange_trade_id, symbol, side, quantity,
                   price, quote_quantity, commission, commission_asset, execution_time, event_key, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fill.order_id, fill.client_order_id, fill.exchange_trade_id, fill.symbol, fill.side,
                 str(fill.quantity), str(fill.price), str(fill.quote_quantity), str(fill.commission),
                 fill.commission_asset, _iso(fill.execution_time), fill.event_key, _iso(self.now())),
            )
            if cur.rowcount == 0:
                return False
            fill.id = cur.lastrowid
            return True

    def fill_exists(self, event_key: str) -> bool:
        return bool(self._query("SELECT 1 FROM fills WHERE event_key=?", (event_key,)))

    def fills(self, bot_instance_id: str, symbol: str) -> list[Fill]:
        rows = self._query(
            """SELECT f.* FROM fills f JOIN orders o ON o.id=f.order_id
               WHERE o.bot_instance_id=? AND f.symbol=? ORDER BY f.execution_time, f.id""",
            (bot_instance_id, symbol))
        return [self._row_to_fill(r) for r in rows]

    def fills_for_order(self, order_id: int) -> list[Fill]:
        return [self._row_to_fill(r) for r in self._query("SELECT * FROM fills WHERE order_id=? ORDER BY id", (order_id,))]

    @staticmethod
    def _row_to_fill(r: dict) -> Fill:
        return Fill(
            id=r["id"], order_id=r["order_id"], client_order_id=r["client_order_id"],
            exchange_trade_id=r["exchange_trade_id"], symbol=r["symbol"], side=r["side"],
            quantity=Decimal(r["quantity"]), price=Decimal(r["price"]), quote_quantity=Decimal(r["quote_quantity"]),
            commission=Decimal(r["commission"]), commission_asset=r["commission_asset"],
            execution_time=_dt(r["execution_time"]), event_key=r["event_key"],
        )

    # ------------------------------------------------------------------ positions
    def get_position(self, bot_instance_id: str, symbol: str) -> Position:
        rows = self._query("SELECT * FROM positions WHERE bot_instance_id=? AND symbol=?", (bot_instance_id, symbol))
        if not rows:
            return Position(bot_instance_id=bot_instance_id, symbol=symbol)
        r = rows[0]
        return Position(
            bot_instance_id=r["bot_instance_id"], symbol=r["symbol"], side=r["side"],
            quantity=Decimal(r["quantity"]), average_entry_price=Decimal(r["average_entry_price"]),
            realized_pnl=Decimal(r["realized_pnl"]), unrealized_pnl=Decimal(r["unrealized_pnl"]),
            last_mark_price=_dec(r["last_mark_price"]), stop_price=_dec(r["stop_price"]),
            fees_other={k: Decimal(v) for k, v in json.loads(r["fees_other_json"] or "{}").items()},
            opened_at=_dt(r["opened_at"]), updated_at=_dt(r["updated_at"]),
        )

    def positions(self, bot_instance_id: str) -> list[Position]:
        return [self.get_position(bot_instance_id, r["symbol"])
                for r in self._query("SELECT symbol FROM positions WHERE bot_instance_id=?", (bot_instance_id,))]

    def save_position(self, p: Position) -> None:
        p.updated_at = self.now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(bot_instance_id, symbol) DO UPDATE SET side=excluded.side, quantity=excluded.quantity,
                   average_entry_price=excluded.average_entry_price, realized_pnl=excluded.realized_pnl,
                   unrealized_pnl=excluded.unrealized_pnl, last_mark_price=excluded.last_mark_price,
                   stop_price=excluded.stop_price, fees_other_json=excluded.fees_other_json,
                   opened_at=excluded.opened_at, updated_at=excluded.updated_at""",
                (p.bot_instance_id, p.symbol, p.side, str(p.quantity), str(p.average_entry_price),
                 str(p.realized_pnl), str(p.unrealized_pnl), _s(p.last_mark_price), _s(p.stop_price),
                 dumps(p.fees_other), _iso(p.opened_at), _iso(p.updated_at)),
            )

    # ------------------------------------------------------------------ events
    def mark_event_processed(self, event_key: str, source: str, event_type: str | None = None) -> bool:
        with self.transaction() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO processed_events VALUES (?,?,?,?)",
                               (event_key, source, event_type, _iso(self.now())))
            return cur.rowcount == 1

    # ------------------------------------------------------------------ reconciliation
    def get_recon_state(self, bot_instance_id: str) -> ReconciliationState:
        rows = self._query("SELECT * FROM reconciliation_state WHERE bot_instance_id=?", (bot_instance_id,))
        if not rows:
            return ReconciliationState(bot_instance_id, ReconciliationStatus.CONNECTING, "never reconciled")
        r = rows[0]
        return ReconciliationState(
            bot_instance_id=bot_instance_id, status=ReconciliationStatus(r["status"]), reason=r["reason"] or "",
            updated_at=_dt(r["updated_at"]), last_success_at=_dt(r["last_success_at"]),
            ws_connected=bool(r["ws_connected"]), last_ws_event_at=_dt(r["last_ws_event_at"]),
            last_ack_token=r["last_ack_token"],
        )

    def save_recon_state(self, st: ReconciliationState) -> None:
        st.updated_at = self.now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO reconciliation_state VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(bot_instance_id) DO UPDATE SET status=excluded.status, reason=excluded.reason,
                   updated_at=excluded.updated_at, last_success_at=excluded.last_success_at,
                   ws_connected=excluded.ws_connected, last_ws_event_at=excluded.last_ws_event_at,
                   last_ack_token=excluded.last_ack_token""",
                (st.bot_instance_id, st.status.value, st.reason, _iso(st.updated_at), _iso(st.last_success_at),
                 int(st.ws_connected), _iso(st.last_ws_event_at), st.last_ack_token),
            )

    def record_recon_run(self, run_key: str, bot_instance_id: str, reason: str, status: str,
                         started_at: datetime, summary: dict, mismatches: list[dict]) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO reconciliation_runs (run_key, bot_instance_id, reason, result_status, started_at, finished_at, summary_json) VALUES (?,?,?,?,?,?,?)",
                (run_key, bot_instance_id, reason, status, _iso(started_at), _iso(self.now()), dumps(summary)))
            run_id = cur.lastrowid
            for m in mismatches:
                conn.execute(
                    "INSERT INTO reconciliation_mismatches (run_id, kind, severity, client_order_id, detail_json, resolved, created_at) VALUES (?,?,?,?,?,?,?)",
                    (run_id, m["kind"], m["severity"], m.get("client_order_id"), dumps(m.get("detail", {})),
                     int(m.get("resolved", False)), _iso(self.now())))
            return run_id

    def open_mismatch_count(self, bot_instance_id: str) -> int:
        rows = self._query(
            """SELECT COUNT(*) AS n FROM reconciliation_mismatches m JOIN reconciliation_runs r ON r.id=m.run_id
               WHERE r.bot_instance_id=? AND m.resolved=0 AND m.severity=? AND r.id=(SELECT MAX(id) FROM reconciliation_runs WHERE bot_instance_id=?)""",
            (bot_instance_id, MismatchSeverity.CRITICAL.value, bot_instance_id))
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------ outbox
    def enqueue(self, kind: str, idempotency_key: str, payload: dict) -> bool:
        now = _iso(self.now())
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO outbox (kind, idempotency_key, payload_json, next_attempt_at, created_at) VALUES (?,?,?,?,?)",
                (kind, idempotency_key, dumps(payload), now, now))
            return cur.rowcount == 1

    def pending_outbox(self, limit: int = 100) -> list[dict]:
        return self._query(
            "SELECT * FROM outbox WHERE status='PENDING' AND next_attempt_at<=? ORDER BY id LIMIT ?",
            (_iso(self.now()), limit))

    def mark_outbox_sent(self, outbox_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE outbox SET status='SENT', sent_at=? WHERE id=?", (_iso(self.now()), outbox_id))

    def mark_outbox_failed(self, outbox_id: int, error: str, next_attempt_at: datetime, dead: bool = False) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE outbox SET attempts=attempts+1, last_error=?, next_attempt_at=?, status=? WHERE id=?",
                (error[:500], _iso(next_attempt_at), "DEAD" if dead else "PENDING", outbox_id))

    def outbox_backlog(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM outbox WHERE status='PENDING'")[0]["n"])

    # ------------------------------------------------------------------ leases
    def acquire_lease(self, name: str, owner: str, ttl_seconds: float) -> bool:
        """Atomically take or renew a lease. True if `owner` holds it until now+ttl."""
        from datetime import timedelta
        now = self.now()
        with self.transaction() as conn:
            row = conn.execute("SELECT owner, expires_at FROM leases WHERE name=?", (name,)).fetchone()
            if row and row[0] != owner and datetime.fromisoformat(row[1]) > now:
                return False
            conn.execute("INSERT INTO leases(name, owner, expires_at) VALUES(?,?,?) "
                         "ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, expires_at=excluded.expires_at",
                         (name, owner, _iso(now + timedelta(seconds=ttl_seconds))))
            return True

    def release_lease(self, name: str, owner: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))

    # ------------------------------------------------------------------ kv
    # ------------------------------------------------------------------ news archive
    def record_news(self, items: list[dict], seen_at) -> int:
        """Store headlines we can see right now. Deduplicated by source+title+published, so repeated
        cycles do not inflate the archive. Returns how many were new."""
        import sentiment as sentiment_mod
        new = 0
        with self.transaction() as conn:
            for item in items:
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                key = hashlib.sha256(f"{item.get('source','')}|{item.get('published','')}|{title}".encode()).hexdigest()
                cur = conn.execute(
                    "INSERT OR IGNORE INTO news_snapshots(seen_at, published_at, source, title, sentiment, item_key) "
                    "VALUES(?,?,?,?,?,?)",
                    (_iso(seen_at), item.get("published"), item.get("source"), title[:500],
                     sentiment_mod.score_text(title), key))
                new += cur.rowcount
        return new

    def news_count(self) -> int:
        return self._query("SELECT COUNT(*) AS n FROM news_snapshots")[0]["n"]

    def kv_get(self, key: str, default=None):
        rows = self._query("SELECT value_json FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value_json"]) if rows else default

    def kv_set(self, key: str, value) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT INTO kv VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                         (key, dumps(value), _iso(self.now())))


class ConcurrentModification(Exception):
    pass
