"""OrderService - the ONLY path through which orders are submitted, and the only
place exchange order reports / fills are applied to local state.

Lifecycle:  intent -> SUBMITTING (persisted BEFORE the network call)
            -> ACK (NEW / PARTIALLY_FILLED / FILLED / EXPIRED ...)  or REJECTED
            -> AMBIGUOUS: stays SUBMITTING, flagged, and resolved by querying the
               exchange with the deterministic clientOrderId - never by blind retry.

Idempotency:
  * one order per client_intent_id (DB unique) - re-submitting an intent returns
    the existing order and makes NO exchange call;
  * one fill per exchange execution (DB unique event_key), inserted in the same
    transaction as the position update, so duplicate events cannot double P&L.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from engine.application.outbox import enqueue_event, fill_payload, order_payload, position_payload
from engine.domain.enums import OrderStatus, SubmitOutcomeKind
from engine.domain.events import ExchangeFill, ExchangeOrderReport
from engine.domain.models import NOT_FOUND_REASON, Fill, IllegalTransition, Order, OrderIntent, SymbolRules, make_client_order_id


class OrderConflict(Exception):
    """Local and exchange state disagree about a terminal order. Reconciliation must halt."""


class OrderService:
    def __init__(self, store, executor, positions, cfg, clock, logger):
        self.store = store
        self.executor = executor
        self.positions = positions
        self.cfg = cfg
        self.clock = clock
        self.log = logger
        self.bot = cfg.BOT_INSTANCE_ID

    # ------------------------------------------------------------------ submission
    def submit_intent(self, intent: OrderIntent, rules: SymbolRules) -> Order:
        existing = self.store.get_order_by_intent(intent.client_intent_id)
        if existing is not None:
            self.log.info("intent already has an order; not resubmitting",
                          extra={"client_intent_id": intent.client_intent_id, "client_order_id": existing.client_order_id,
                                 "status": existing.status.value})
            return existing

        order = Order(
            bot_instance_id=self.bot, exchange=self.executor.exchange_name, symbol=intent.symbol,
            side=intent.side, order_type=intent.order_type, purpose=intent.purpose,
            client_order_id=make_client_order_id(self.cfg.CLIENT_ORDER_ID_PREFIX, intent.client_intent_id),
            client_intent_id=intent.client_intent_id, requested_quantity=rules.round_qty(intent.quantity),
            limit_price=intent.limit_price, stop_price=intent.stop_price, reference_price=intent.reference_price,
        )
        now = self.clock.now()
        order.mark_submitting(now)
        with self.store.transaction():
            if not self.store.insert_intent_and_order(intent, order):
                return self.store.get_order_by_intent(intent.client_intent_id)
            self.store.save_order(order)          # persists submitted_at
            self._enqueue_order(order)
        log = self.log.bind(client_order_id=order.client_order_id, symbol=order.symbol, purpose=order.purpose.value)
        log.info("order submitting", extra={"side": order.side, "qty": str(order.requested_quantity)})

        try:
            outcome = self.executor.submit(order, rules)
        except Exception as exc:  # noqa: BLE001 - an executor bug must be treated as "unknown", never "not sent"
            log.exception("executor raised during submit; treating as AMBIGUOUS")
            from engine.domain.events import SubmitOutcome
            outcome = SubmitOutcome(SubmitOutcomeKind.AMBIGUOUS, error=str(exc)[:300])

        if outcome.kind == SubmitOutcomeKind.ACKNOWLEDGED:
            self.apply_report(outcome.report, source="rest_submit")
            for xf in outcome.fills:
                self.ingest_fill(xf, source="rest_submit")
        elif outcome.kind == SubmitOutcomeKind.REJECTED:
            with self.store.transaction():
                fresh = self.store.get_order(order.client_order_id)
                if fresh.mark_rejected(f"{outcome.error_code or ''} {outcome.error or 'rejected'}".strip(), self.clock.now()):
                    self.store.save_order(fresh)
                    self._enqueue_order(fresh)
            log.warning("order rejected", extra={"error": outcome.error, "error_code": outcome.error_code})
        else:
            with self.store.transaction():
                fresh = self.store.get_order(order.client_order_id)
                fresh.submission_ambiguous = True
                self.store.save_order(fresh)
                self._enqueue_order(fresh)
                enqueue_event(self.store, self.bot, "ORDER_SUBMISSION_AMBIGUOUS",
                              f"{order.client_order_id}: {outcome.error}", "WARNING",
                              {"client_order_id": order.client_order_id}, key=f"ambiguous:{order.client_order_id}")
            log.warning("order submission ambiguous; querying exchange before any retry", extra={"error": outcome.error})
            self.resolve_ambiguous(self.store.get_order(order.client_order_id))
        return self.store.get_order(order.client_order_id)

    def resolve_ambiguous(self, order: Order) -> str:
        """Query by clientOrderId. Returns 'resolved' or 'pending'. A timeout is not a rejection."""
        if order.is_terminal:
            return "resolved"
        try:
            report = self.executor.query_order(order)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("query for ambiguous order failed", extra={"client_order_id": order.client_order_id, "error": str(exc)[:200]})
            return "pending"
        if report is not None:
            self.apply_report(report, source="rest_query")
            self._ingest_order_fills(self.store.get_order(order.client_order_id))
            return "resolved"
        age = (self.clock.now() - (order.submitted_at or self.clock.now())).total_seconds()
        if age < self.cfg.AMBIGUOUS_ORDER_GRACE_SECONDS:
            return "pending"
        with self.store.transaction():
            fresh = self.store.get_order(order.client_order_id)
            if fresh.mark_rejected(f"{NOT_FOUND_REASON} after {int(age)}s", self.clock.now()):
                self.store.save_order(fresh)
                self._enqueue_order(fresh)
        self.log.warning("ambiguous order not found on exchange after grace; marked REJECTED",
                         extra={"client_order_id": order.client_order_id})
        return "resolved"

    # ------------------------------------------------------------------ applying exchange data
    def apply_report(self, report: ExchangeOrderReport, source: str) -> Order | None:
        conflict: IllegalTransition | None = None
        with self.store.transaction():
            order = self.store.get_order(report.client_order_id)
            if order is None and report.exchange_order_id:
                order = self.store.get_order_by_exchange_id(self.executor.exchange_name, report.exchange_order_id)
            if order is None:
                return None
            try:
                changed = order.apply_report(report, self.clock.now())
            except IllegalTransition as exc:
                conflict, changed = exc, False
            if changed:
                self.store.save_order(order)
                self._enqueue_order(order)
        if conflict is not None:
            # Recorded outside any enclosing transaction so a caller's rollback cannot erase the alert.
            self.record_conflict(order.client_order_id, report.status.value, str(conflict), source)
            raise OrderConflict(str(conflict)) from conflict
        return order

    def record_conflict(self, client_order_id: str, exchange_status: str, message: str, source: str) -> None:
        if self.store.in_transaction():
            return   # inside a caller's transaction: the caller records it after rolling back
        enqueue_event(self.store, self.bot, "ORDER_STATE_CONFLICT", message, "CRITICAL",
                      {"client_order_id": client_order_id, "source": source},
                      key=f"conflict:{client_order_id}:{exchange_status}", alert=True)
        self.log.error("order state conflict", extra={"client_order_id": client_order_id, "detail": message})

    def ingest_fill(self, xf: ExchangeFill, source: str) -> bool:
        """Records an execution exactly once and updates the position atomically."""
        with self.store.transaction():
            order = self.store.get_order(xf.client_order_id) if xf.client_order_id else None
            if order is None and xf.exchange_order_id:
                order = self.store.get_order_by_exchange_id(self.executor.exchange_name, xf.exchange_order_id)
            if order is None:
                return False
            fill = Fill(
                order_id=order.id, client_order_id=order.client_order_id, exchange_trade_id=xf.exchange_trade_id,
                symbol=order.symbol, side=order.side, quantity=xf.quantity, price=xf.price,
                quote_quantity=xf.quote_quantity, commission=xf.commission, commission_asset=xf.commission_asset,
                execution_time=datetime.fromtimestamp(xf.execution_time_ms / 1000, tz=timezone.utc) if xf.execution_time_ms else self.clock.now(),
                event_key=xf.event_key,
            )
            if not self.store.insert_fill(fill):
                return False                     # duplicate execution: no second fill, no second P&L
            result = self.positions.apply_fill(fill)
            self.store.enqueue("fills", f"fill:{fill.event_key}", fill_payload(fill))
            self.store.enqueue("positions", f"position:{order.symbol}:fill:{fill.event_key}", position_payload(result.position))
            if result.anomalies:
                enqueue_event(self.store, self.bot, "POSITION_ANOMALY", ",".join(result.anomalies), "CRITICAL",
                              {"event_key": fill.event_key}, key=f"anomaly:{fill.event_key}", alert=True)
        self.log.info("fill recorded", extra={"client_order_id": order.client_order_id, "exchange_order_id": order.exchange_order_id,
                                              "event_key": xf.event_key, "qty": str(xf.quantity), "price": str(xf.price), "source": source})
        return True

    def refresh(self, order: Order) -> Order:
        """Authoritative REST refresh of one order and its fills."""
        report = self.executor.query_order(order)
        if report is not None:
            self.apply_report(report, source="rest_query")
        self._ingest_order_fills(self.store.get_order(order.client_order_id))
        return self.store.get_order(order.client_order_id)

    def _ingest_order_fills(self, order: Order) -> None:
        if order is None or not order.exchange_order_id:
            return
        for xf in self.executor.query_fills(order):
            self.ingest_fill(xf, source="rest_trades")

    def cancel(self, order: Order) -> Order:
        outcome = self.executor.cancel(order)
        if outcome.kind == SubmitOutcomeKind.ACKNOWLEDGED and outcome.report is not None:
            self.apply_report(outcome.report, source="rest_cancel")
        # Whatever the cancel said, re-read the authoritative state (it may have filled meanwhile).
        try:
            return self.refresh(order)
        except OrderConflict:
            raise
        except Exception as exc:  # noqa: BLE001
            self.log.warning("refresh after cancel failed", extra={"client_order_id": order.client_order_id, "error": str(exc)[:200]})
            return self.store.get_order(order.client_order_id)

    # ------------------------------------------------------------------ helpers
    def fill_quantity(self, order: Order) -> Decimal:
        return sum((f.quantity for f in self.store.fills_for_order(order.id)), Decimal("0"))

    def _enqueue_order(self, order: Order) -> None:
        payload = order_payload(order)
        payload["signal_id"] = self.store.intent_signal_id(order.client_intent_id)
        self.store.enqueue("orders", f"order:{order.client_order_id}:v{order.version}", payload)

    def non_terminal(self, symbol: str | None = None) -> list[Order]:
        return self.store.non_terminal_orders(self.bot, symbol)

    @staticmethod
    def is_final(order: Order) -> bool:
        return order.status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED)
