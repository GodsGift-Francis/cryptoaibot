"""Reconciliation worker (TESTNET / LIVE only).

Owns the Binance user-data WebSocket and the reconciliation state machine:

    start ............................ CONNECTING
    subscribed ....................... RECONCILING -> full REST reconciliation -> HEALTHY | HALTED
    disconnect / stale / shutdown .... DEGRADED (entries blocked) -> reconnect with backoff+jitter
    reconnected ...................... RECONCILING again before anything is trusted
    unknown bot order / conflict ..... immediate reconciliation
    periodic ......................... reconciliation every RECONCILE_INTERVAL_SECONDS
    operator ack (Laravel) ........... reconciliation allowed to clear HALTED

It subscribes BEFORE reconciling, so no event is lost in between; events are
applied idempotently, so overlap with the REST snapshot is harmless.

    python -m workers.reconciliation_worker
"""
from __future__ import annotations

import asyncio
import signal
import sys

import config
from engine import bootstrap
from engine.domain.enums import ReconciliationStatus
from engine.exchanges.binance import normalizer as nz
from engine.exchanges.binance.websocket_client import BinanceUserDataStream
from engine.infrastructure import logging as slog


class ReconciliationWorker:
    TOUCH_SECONDS = 15
    CONTROL_POLL_SECONDS = 30

    def __init__(self, services, stream_factory=None):
        self.s = services
        self.cfg = services.cfg
        self.bot = self.cfg.BOT_INSTANCE_ID
        self.symbols = nz.SymbolMap([self.cfg.SYMBOL])
        self.connected = False
        self._lock = asyncio.Lock()
        self._stream_factory = stream_factory or self._default_stream
        self.stream = None

    def _default_stream(self, on_event, on_state):
        return BinanceUserDataStream(
            self.cfg.BINANCE_WS_API_URL, self.cfg.API_KEY, self.cfg.API_SECRET, self.s.clock, on_event, on_state,
            ping_interval=self.cfg.WS_PING_INTERVAL_SECONDS, response_timeout=self.cfg.WS_RESPONSE_TIMEOUT_SECONDS,
            max_backoff=self.cfg.WS_MAX_BACKOFF_SECONDS, recv_window_ms=self.cfg.WS_RECV_WINDOW_MS, logger=self.s.log)

    # ------------------------------------------------------------------ state callbacks
    async def on_state(self, state: str, detail: str) -> None:
        recon = self.s.reconciliation
        if state == "CONNECTING":
            self.connected = False
            current = self.s.store.get_recon_state(self.bot)
            if current.status != ReconciliationStatus.HALTED:
                recon.set_state(ReconciliationStatus.CONNECTING, detail, ws_connected=False)
        elif state == "DISCONNECTED":
            self.connected = False
            current = self.s.store.get_recon_state(self.bot)
            if current.status != ReconciliationStatus.HALTED:
                recon.set_state(ReconciliationStatus.DEGRADED, f"websocket down: {detail}", ws_connected=False)
            else:
                recon.set_state(ReconciliationStatus.HALTED, current.reason, ws_connected=False)
            self.s.log.warning("user data stream disconnected", extra={"detail": detail})
        elif state == "SUBSCRIBED":
            self.connected = True
            st = self.s.store.get_recon_state(self.bot)
            st.ws_connected = True
            self.s.store.save_recon_state(st)
            self.s.log.info("user data stream subscribed", extra={"detail": detail})
            await self.reconcile("websocket (re)subscribed")

    async def on_event(self, message: dict) -> None:
        _, event = nz.unwrap(message)
        if event is None:
            return
        ev = nz.stream_event(event, self.cfg.BINANCE_ACCOUNT_LABEL, self.symbols)
        result = await asyncio.to_thread(self.s.ingestion.handle, ev, "ws")
        st = self.s.store.get_recon_state(self.bot)
        st.last_ws_event_at = self.s.clock.now()
        self.s.store.save_recon_state(st)
        if result.needs_reconciliation:
            self.s.log.warning("event requires reconciliation", extra={"outcome": result.outcome, "event_key": ev.event_key})
            asyncio.create_task(self.reconcile(f"stream event: {result.outcome}"))

    # ------------------------------------------------------------------ reconciliation
    async def reconcile(self, reason: str, operator_ack: bool = False):
        async with self._lock:
            result = await asyncio.to_thread(self.s.reconciliation.run, reason, operator_ack)
            if not self.connected and result.status == ReconciliationStatus.HEALTHY:
                # The stream dropped while we were reconciling: REST state is fine but live updates are not.
                self.s.reconciliation.set_state(ReconciliationStatus.DEGRADED, "reconciled but websocket not connected",
                                                ws_connected=False)
            return result

    async def _periodic(self) -> None:
        elapsed = 0
        last_ack = self.s.store.get_recon_state(self.bot).last_ack_token
        while True:
            await asyncio.sleep(self.TOUCH_SECONDS)
            elapsed += self.TOUCH_SECONDS
            await asyncio.to_thread(self.s.reconciliation.touch)
            if elapsed % self.CONTROL_POLL_SECONDS == 0:
                await asyncio.to_thread(self._heartbeat)
                try:
                    control = await asyncio.to_thread(self.s.control_plane.get_control)
                    ack = control.get("reconciliation_ack_token")
                    if ack and ack != last_ack:
                        last_ack = ack
                        self.s.log.warning("operator acknowledged reconciliation halt", extra={"ack": ack})
                        result = await self.reconcile("operator acknowledgement", operator_ack=True)
                        st = self.s.store.get_recon_state(self.bot)
                        st.last_ack_token = ack
                        self.s.store.save_recon_state(st)
                        self.s.log.info("post-ack reconciliation", extra={"status": result.status.value})
                except Exception as exc:  # noqa: BLE001 - reconciliation keeps running during a Laravel outage
                    self.s.log.warning("control plane poll failed", extra={"error": str(exc)[:200]})
            if self.connected and elapsed % self.cfg.RECONCILE_INTERVAL_SECONDS < self.TOUCH_SECONDS:
                await self.reconcile("periodic")
            await asyncio.to_thread(self._flush)

    def _heartbeat(self) -> None:
        try:
            self.s.control_plane.heartbeat(bootstrap.health_snapshot(self.s, "reconciliation"))
        except Exception as exc:  # noqa: BLE001
            self.s.log.warning("heartbeat failed", extra={"error": str(exc)[:200]})

    def _flush(self) -> None:
        try:
            self.s.outbox.flush()
        except Exception:  # noqa: BLE001
            self.s.log.exception("outbox flush failed")

    async def run(self) -> None:
        self.s.reconciliation.set_state(ReconciliationStatus.CONNECTING, "worker starting", ws_connected=False) \
            if self.s.store.get_recon_state(self.bot).status != ReconciliationStatus.HALTED else None
        # Startup reconciliation happens on first SUBSCRIBED (restart recovery path).
        self.stream = self._stream_factory(self.on_event, self.on_state)
        periodic = asyncio.create_task(self._periodic())
        try:
            await self.stream.run_forever()
        finally:
            periodic.cancel()


def main() -> int:
    slog.configure(config.LOG_LEVEL)
    config.validate_runtime(config)
    if config.TRADING_MODE == "PAPER":
        print("PAPER mode: the trading worker reconciles its simulated ledger inline; reconciliation worker not needed.")
        return 0
    s = bootstrap.build(config, "reconciliation")
    worker = ReconciliationWorker(s)
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: worker.stream and worker.stream.stop())
    try:
        loop.run_until_complete(worker.run())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
