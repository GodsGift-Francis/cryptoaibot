"""Composition root: the only place services are wired together from configuration."""
from __future__ import annotations

from dataclasses import dataclass

from engine.application.order_service import OrderService
from engine.application.outbox import OutboxDispatcher
from engine.application.position_service import PositionService
from engine.application.reconciliation_service import ReconciliationService
from engine.application.stream_ingestion import StreamIngestionService
from engine.application.trading_cycle import TradingCycle
from engine.control_plane.client import ControlPlaneClient
from engine.execution.factory import build_executor
from engine.infrastructure import logging as slog
from engine.infrastructure.clock import SystemClock
from engine.infrastructure.store import Store
from engine.market_data.fetcher import MarketDataService
from engine.notifications.telegram import TelegramNotifier
from engine.risk.manager import RiskManager
from engine.strategy.ai_strategy import build_strategy


@dataclass
class Services:
    cfg: object
    clock: object
    log: object
    store: Store
    executor: object
    positions: PositionService
    orders: OrderService
    reconciliation: ReconciliationService
    ingestion: StreamIngestionService
    control_plane: object
    outbox: OutboxDispatcher
    cycle: TradingCycle


def build(cfg, worker: str, clock=None, store=None, executor=None, control_plane=None, market=None, notifier=None) -> Services:
    for secret in (cfg.API_KEY, cfg.API_SECRET, cfg.CONTROL_PLANE_TOKEN, cfg.TELEGRAM_BOT_TOKEN):
        slog.register_secret(secret)
    clock = clock or SystemClock()
    log = slog.get_logger("crypto_bot", worker=worker, bot_instance_id=cfg.BOT_INSTANCE_ID, mode=cfg.TRADING_MODE)
    store = store or Store(cfg.ENGINE_DB_PATH, clock=clock.now)
    positions = PositionService(store, cfg, log)
    executor = executor or build_executor(cfg, store, positions, clock)
    orders = OrderService(store, executor, positions, cfg, clock, log)
    recon = ReconciliationService(store, executor, orders, positions, cfg, clock, log)
    ingestion = StreamIngestionService(store, orders, cfg, log)
    cp = control_plane or ControlPlaneClient(cfg.CONTROL_PLANE_URL, cfg.CONTROL_PLANE_TOKEN, cfg.BOT_INSTANCE_ID, cfg.REQUEST_TIMEOUT_SECONDS)
    notifier = notifier or TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    outbox = OutboxDispatcher(store, cp, notifier, cfg.OUTBOX_MAX_ATTEMPTS, log)
    market = market or MarketDataService(cfg, clock)
    cycle = TradingCycle(cfg, store, clock, cp, market, build_strategy(cfg), RiskManager(store, cfg),
                         orders, positions, recon, executor, log)
    return Services(cfg, clock, log, store, executor, positions, orders, recon, ingestion, cp, outbox, cycle)


def health_snapshot(s: Services, worker: str) -> dict:
    """Health metrics required by spec section 16 (sent with every heartbeat)."""
    st = s.store.get_recon_state(s.cfg.BOT_INSTANCE_ID)
    bot = s.cfg.BOT_INSTANCE_ID
    p = s.positions.get(s.cfg.SYMBOL)
    return {
        "worker": worker, "status": "RUNNING", "mode": s.cfg.TRADING_MODE, "symbol": s.cfg.SYMBOL,
        "timeframe": s.cfg.TIMEFRAME, "time": s.clock.now(),
        "last_market_data_at": s.store.kv_get(f"health:{bot}:last_market_data_at"),
        "last_cycle_at": s.store.kv_get(f"health:{bot}:last_cycle_at"),
        "ws_connected": st.ws_connected, "last_ws_event_at": st.last_ws_event_at,
        "reconciliation_state": st.status.value, "reconciliation_reason": st.reason,
        "last_reconciled_at": st.last_success_at,
        "non_terminal_orders": len(s.orders.non_terminal()),
        "open_mismatches": s.store.open_mismatch_count(bot),
        "outbox_backlog": s.store.outbox_backlog(),
        "position_quantity": str(p.quantity), "realized_pnl": str(p.realized_pnl),
    }
