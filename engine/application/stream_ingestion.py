"""Applies normalized user-data-stream events to local state.

Each event is applied at most once: the processed_events row, the order
update and the fill/position update are written in ONE transaction. A crash
between receipt and commit simply means the event is applied on redelivery or
recovered by the next REST reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.application.order_service import OrderConflict
from engine.exchanges.binance.normalizer import NormalizedStreamEvent


@dataclass
class IngestResult:
    outcome: str                  # applied | duplicate | foreign | unknown_bot_order | conflict | balance | terminated | ignored
    needs_reconciliation: bool = False


class StreamIngestionService:
    def __init__(self, store, orders, cfg, logger):
        self.store = store
        self.orders = orders
        self.cfg = cfg
        self.log = logger
        prefix = "".join(ch for ch in cfg.CLIENT_ORDER_ID_PREFIX if ch.isalnum())[:8]
        self.prefix = f"{prefix}-"

    def handle(self, ev: NormalizedStreamEvent, source: str = "ws") -> IngestResult:
        if ev.kind == "TERMINATED":
            return IngestResult("terminated", needs_reconciliation=True)
        if ev.kind == "BALANCE":
            current = self.store.kv_get(f"balances:{self.cfg.BOT_INSTANCE_ID}", {}) or {}
            for asset, b in ev.balances.balances.items():
                current[asset] = {"free": str(b["free"]), "locked": str(b["locked"])}
            self.store.kv_set(f"balances:{self.cfg.BOT_INSTANCE_ID}", current)
            return IngestResult("balance")
        if ev.kind != "ORDER":
            return IngestResult("ignored")

        report = ev.report
        local = self.store.get_order(report.client_order_id)
        if local is None and report.exchange_order_id:
            local = self.store.get_order_by_exchange_id(self.orders.executor.exchange_name, report.exchange_order_id)
        if local is None:
            if report.client_order_id.startswith(self.prefix):
                # Our ID but no local record: local state is incomplete -> force reconciliation.
                self.log.error("user-stream event for unknown bot order", extra={"client_order_id": report.client_order_id})
                return IngestResult("unknown_bot_order", needs_reconciliation=True)
            return IngestResult("foreign")

        try:
            with self.store.transaction():
                if not self.store.mark_event_processed(ev.event_key, source, ev.event_type):
                    return IngestResult("duplicate")
                self.orders.apply_report(report, source=source)
                if ev.fill is not None:
                    self.orders.ingest_fill(ev.fill, source=source)
        except OrderConflict as exc:
            self.orders.record_conflict(report.client_order_id, report.status.value, str(exc), source)
            return IngestResult("conflict", needs_reconciliation=True)
        return IngestResult("applied")
