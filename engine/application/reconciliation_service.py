"""ReconciliationService - rebuilds trustworthy account state from the exchange.

Runs on startup, after every WebSocket (re)connect, periodically, and after an
operator acknowledgement. It is idempotent: running it twice in a row with no
exchange changes produces no new fills, orders or position changes.

Algorithm (spec section 10):
  1  mark RECONCILING
  2  load local non-terminal orders
  3  query exchange open orders
  4  query exchange status for every locally tracked non-terminal order
     (+ verify recently terminal orders still agree with the exchange)
  5  query recent exchange fills
  6  query balances
  7  rebuild position from authoritative fills (+ balance sanity check)
  8  compare exchange vs local
  9  persist corrections + reconciliation run/mismatches
 10  unresolved CRITICAL mismatch -> HALTED + operator alert
 11  otherwise HEALTHY (or RECONCILING if an ambiguous order is still inside its grace window)
 12  entries are permitted by the trading worker only while HEALTHY
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from engine.application.order_service import OrderConflict
from engine.application.outbox import enqueue_event, position_payload
from engine.domain.enums import MismatchSeverity, OrderPurpose, ReconciliationStatus

CRIT, WARN, INFO = MismatchSeverity.CRITICAL.value, MismatchSeverity.WARNING.value, MismatchSeverity.INFO.value


@dataclass
class ReconResult:
    status: ReconciliationStatus
    reason: str
    mismatches: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    run_key: str = ""

    @property
    def critical(self) -> list:
        return [m for m in self.mismatches if m["severity"] == CRIT and not m.get("resolved")]


class ReconciliationService:
    TERMINAL_VERIFY_LIMIT = 20

    def __init__(self, store, executor, orders, positions, cfg, clock, logger):
        self.store = store
        self.executor = executor
        self.orders = orders
        self.positions = positions
        self.cfg = cfg
        self.clock = clock
        self.log = logger
        self.bot = cfg.BOT_INSTANCE_ID

    # ------------------------------------------------------------------ state helpers
    def set_state(self, status: ReconciliationStatus, reason: str, success: bool = False, **flags):
        st = self.store.get_recon_state(self.bot)
        st.status, st.reason = status, reason
        if success:
            st.last_success_at = self.clock.now()
        for k, v in flags.items():
            setattr(st, k, v)
        self.store.save_recon_state(st)
        return st

    def touch(self) -> None:
        """Freshness heartbeat: the trading worker ignores reconciliation state older than RECON_STATE_MAX_AGE_SECONDS."""
        st = self.store.get_recon_state(self.bot)
        self.store.save_recon_state(st)

    # ------------------------------------------------------------------ main algorithm
    def run(self, reason: str, operator_ack: bool = False) -> ReconResult:
        started = self.clock.now()
        previous = self.store.get_recon_state(self.bot)
        run_key = f"recon:{self.bot}:{started.isoformat()}:{uuid.uuid4().hex[:8]}"
        self.set_state(ReconciliationStatus.RECONCILING, f"reconciling ({reason})")
        mismatches: list[dict] = []
        summary: dict = {"reason": reason}
        symbol = self.cfg.SYMBOL
        pending_ambiguous = 0

        try:
            rules = self.executor.symbol_rules(symbol)
            tolerance = max(rules.step_size, Decimal("1e-8"))
            prefix = f"{''.join(ch for ch in self.cfg.CLIENT_ORDER_ID_PREFIX if ch.isalnum())[:8]}-"

            # 2-4: local non-terminal orders -> authoritative exchange status
            local_open = self.orders.non_terminal(symbol)
            summary["local_non_terminal_before"] = len(local_open)
            exchange_open = self.executor.open_orders(symbol)
            summary["exchange_open_orders"] = len(exchange_open)
            for order in local_open:
                try:
                    if order.submission_ambiguous or order.status.value == "SUBMITTING":
                        if self.orders.resolve_ambiguous(order) == "pending":
                            pending_ambiguous += 1
                            mismatches.append(self._m("AMBIGUOUS_ORDER_PENDING", WARN, order.client_order_id,
                                                      {"status": order.status.value}))
                    else:
                        self.orders.refresh(order)
                except OrderConflict as exc:
                    mismatches.append(self._m("ORDER_STATE_CONFLICT", CRIT, order.client_order_id, {"error": str(exc)}))

            # verify recently terminal orders (local FILLED but exchange disagrees)
            horizon = self.clock.now() - timedelta(hours=self.cfg.RECENT_FILLS_LOOKBACK_HOURS)
            recent_terminal = [o for o in self.store.orders(self.bot, symbol, limit=100)
                               if o.is_terminal and o.exchange_order_id and o.terminal_at and o.terminal_at >= horizon
                               and not (o.reject_reason or "").startswith("NOT_FOUND")][: self.TERMINAL_VERIFY_LIMIT]
            for order in recent_terminal:
                try:
                    report = self.executor.query_order(order)
                    if report is None:
                        mismatches.append(self._m("TERMINAL_ORDER_MISSING_ON_EXCHANGE", CRIT, order.client_order_id, {}))
                        continue
                    if report.status != order.status or report.executed_qty != order.executed_quantity:
                        self.orders.apply_report(report, source="reconciliation")   # raises on terminal conflict
                        fresh = self.store.get_order(order.client_order_id)
                        if report.executed_qty < fresh.executed_quantity:
                            mismatches.append(self._m("EXECUTED_QTY_EXCEEDS_EXCHANGE", CRIT, order.client_order_id,
                                                      {"local": str(fresh.executed_quantity), "exchange": str(report.executed_qty)}))
                except OrderConflict as exc:
                    mismatches.append(self._m("ORDER_STATE_CONFLICT", CRIT, order.client_order_id, {"error": str(exc)}))

            # exchange open orders that local state does not know about
            for rep in exchange_open:
                local = self.store.get_order(rep.client_order_id)
                if local is None:
                    ours = rep.client_order_id.startswith(prefix)
                    mismatches.append(self._m("UNKNOWN_BOT_ORDER" if ours else "FOREIGN_OPEN_ORDER", CRIT if ours else WARN,
                                              rep.client_order_id, {"exchange_order_id": rep.exchange_order_id,
                                                                    "status": rep.status.value}))

            # 5: recent fills - ingest anything missed (idempotent by event_key)
            since = horizon
            if previous.last_success_at:
                since = max(horizon, previous.last_success_at - timedelta(minutes=10))
            new_fills = unknown = 0
            for xf in self.executor.recent_fills(symbol, int(since.timestamp() * 1000)):
                local = self.store.get_order_by_exchange_id(self.executor.exchange_name, xf.exchange_order_id) if xf.exchange_order_id else None
                if local is None:
                    rep = self.executor.query_order_by_exchange_id(symbol, xf.exchange_order_id) if xf.exchange_order_id else None
                    coid = rep.client_order_id if rep else None
                    local = self.store.get_order(coid) if coid else None
                    if local is None:
                        unknown += 1
                        ours = bool(coid and coid.startswith(prefix))
                        mismatches.append(self._m("UNKNOWN_BOT_FILL" if ours else "FOREIGN_FILL", CRIT if ours else WARN,
                                                  coid, {"event_key": xf.event_key, "exchange_order_id": xf.exchange_order_id}))
                        continue
                    self.orders.apply_report(rep, source="reconciliation")
                if self.orders.ingest_fill(xf, source="reconciliation"):
                    new_fills += 1
            summary.update(recovered_fills=new_fills, unknown_fills=unknown)

            # executed quantity must be fully explained by recorded fills
            for order in self.store.orders(self.bot, symbol, limit=100):
                if order.executed_quantity <= 0 or order.status.value == "SUBMITTING":
                    continue
                have = self.orders.fill_quantity(order)
                if abs(have - order.executed_quantity) > tolerance:
                    self.orders._ingest_order_fills(order)
                    have = self.orders.fill_quantity(order)
                    if abs(have - order.executed_quantity) > tolerance:
                        mismatches.append(self._m("FILLS_INCOMPLETE", CRIT, order.client_order_id,
                                                  {"executed": str(order.executed_quantity), "fills": str(have)}))

            # 6-7: balances and position rebuild
            balances = self.executor.balances()
            before = self.positions.get(symbol)
            rebuilt, anomalies = self.positions.rebuild(symbol, persist=True)
            if abs(before.quantity - rebuilt.quantity) > tolerance:
                mismatches.append(self._m("POSITION_CORRECTED", WARN, None,
                                          {"before": str(before.quantity), "after": str(rebuilt.quantity)}, resolved=True))
            for a in anomalies:
                mismatches.append(self._m("POSITION_ANOMALY", CRIT, None, {"anomaly": a}))
            base_total = balances.total(rules.base_asset)
            if rebuilt.quantity - base_total > tolerance:
                mismatches.append(self._m("BALANCE_BELOW_POSITION", CRIT, None,
                                          {"position": str(rebuilt.quantity), "base_balance": str(base_total)}))
            summary.update(position_qty=str(rebuilt.quantity), base_balance=str(base_total),
                           quote_balance=str(balances.total(rules.quote_asset)))
            self.store.kv_set(f"balances:{self.bot}", {k: {kk: str(vv) for kk, vv in v.items()} for k, v in balances.balances.items()})
            self.store.enqueue("positions", f"position:{symbol}:recon:{run_key}", position_payload(rebuilt))

            # protective stop presence (placed by the trading worker; reported here)
            if self.cfg.PROTECTIVE_STOP_MODE == "exchange" and rebuilt.is_open(rules):
                live_stops = [o for o in self.orders.non_terminal(symbol) if o.purpose == OrderPurpose.PROTECTIVE_STOP]
                if not live_stops:
                    mismatches.append(self._m("PROTECTIVE_STOP_MISSING", WARN, None, {"position": str(rebuilt.quantity)}))

            summary["local_non_terminal_after"] = len(self.orders.non_terminal(symbol))
        except Exception as exc:  # noqa: BLE001 - exchange unreachable etc.: state stays untrusted
            self.log.exception("reconciliation could not complete")
            result = ReconResult(ReconciliationStatus.DEGRADED, f"reconciliation failed: {type(exc).__name__}: {str(exc)[:200]}",
                                 mismatches, summary, run_key)
            self._persist(result, reason, started)
            self.set_state(result.status, result.reason)
            return result

        result = ReconResult(ReconciliationStatus.HEALTHY, "reconciled", mismatches, summary, run_key)
        if result.critical:
            result.status, result.reason = ReconciliationStatus.HALTED, "; ".join(sorted({m["kind"] for m in result.critical}))
        elif pending_ambiguous:
            result.status, result.reason = ReconciliationStatus.RECONCILING, f"{pending_ambiguous} ambiguous order(s) inside grace window"
        elif previous.status == ReconciliationStatus.HALTED and not operator_ack:
            result.status, result.reason = ReconciliationStatus.HALTED, "clean, awaiting operator acknowledgement (was HALTED: " + previous.reason + ")"

        self._persist(result, reason, started)
        self.set_state(result.status, result.reason, success=result.status == ReconciliationStatus.HEALTHY)
        if result.status == ReconciliationStatus.HALTED and result.critical:
            enqueue_event(self.store, self.bot, "RECONCILIATION_HALTED", result.reason, "CRITICAL",
                          {"run_key": run_key, "mismatches": result.critical}, key=f"halt:{run_key}", alert=True)
        self.log.info("reconciliation finished", extra={"status": result.status.value, "reason": result.reason,
                                                        "mismatches": len(mismatches), "run_key": run_key})
        return result

    def _persist(self, result: ReconResult, reason: str, started) -> None:
        self.store.record_recon_run(result.run_key, self.bot, reason, result.status.value, started, result.summary, result.mismatches)
        self.store.enqueue("reconciliation", f"recon:{result.run_key}", {
            "run_key": result.run_key, "reason": reason, "status": result.status.value, "detail": result.reason,
            "started_at": started, "finished_at": self.clock.now(), "summary": result.summary,
            "mismatches": result.mismatches,
        })

    @staticmethod
    def _m(kind, severity, client_order_id, detail, resolved=False) -> dict:
        return {"kind": kind, "severity": severity, "client_order_id": client_order_id, "detail": detail, "resolved": resolved}
