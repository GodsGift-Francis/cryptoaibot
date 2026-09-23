"""One trading decision cycle.

Decision order (spec section 15) - any failed mandatory step means no new order:
    control state -> config validation -> reconciliation state -> risk limits
    -> market data validity -> strategy signal -> order intent -> execution

Gating policy:
    control plane unreachable ............ NO orders at all (fail closed)
    config invalid ....................... NO orders at all
    reconciliation not HEALTHY ........... no entries; exits only if DEGRADED (REST still trusted)
    paused (enabled=false) ............... no entries; protective + strategy exits allowed
    emergency stop ....................... no entries, no strategy exits; stop-loss exits allowed
    GLOBAL_TRADING_ENABLED=false ......... no entries
    daily loss limit ..................... no entries (V1.1 fix: stop-loss still runs)
    order already in flight .............. nothing new until it is terminal
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from engine.application.outbox import enqueue_event
from engine.domain.enums import OrderPurpose, OrderSide, OrderStatus, OrderType
from engine.domain.models import OrderIntent
from engine.strategy.strategy_service import MarketContext

RETRYABLE_TERMINAL = (OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.EXPIRED)
MAX_ATTEMPTS = {OrderPurpose.ENTRY: 1, OrderPurpose.EXIT: 3, OrderPurpose.STOP_EXIT: 3, OrderPurpose.PROTECTIVE_STOP: 3}


@dataclass
class CycleResult:
    timestamp: str
    action_taken: str
    signal: object = None
    price: float = 0.0
    equity: float | None = None
    reconciliation: str = ""
    control: dict | None = None
    blocked_reasons: list = field(default_factory=list)
    correlation_id: str = ""
    df: object = None


class TradingCycle:
    def __init__(self, cfg, store, clock, control_plane, market, strategy, risk, orders, positions,
                 reconciliation, executor, logger):
        self.cfg, self.store, self.clock = cfg, store, clock
        self.cp, self.market, self.strategy, self.risk = control_plane, market, strategy, risk
        self.orders, self.positions, self.recon, self.executor = orders, positions, reconciliation, executor
        self.bot = cfg.BOT_INSTANCE_ID
        self.base_log = logger

    # ------------------------------------------------------------------ entry point
    def run_once(self) -> CycleResult:
        now = self.clock.now()
        corr = uuid.uuid4().hex[:16]
        log = self.base_log.bind(correlation_id=corr, symbol=self.cfg.SYMBOL, mode=self.cfg.TRADING_MODE)
        res = CycleResult(timestamp=now.isoformat(), action_taken="NONE", correlation_id=corr)
        self.store.kv_set(f"health:{self.bot}:last_cycle_at", now.isoformat())

        # 1. control state (fail closed)
        try:
            control = self.cp.get_control()
        except Exception as exc:  # noqa: BLE001
            log.error("control plane unavailable - failing closed", extra={"error": str(exc)[:200]})
            enqueue_event(self.store, self.bot, "CONTROL_PLANE_UNAVAILABLE", str(exc)[:200], "WARNING",
                          key=f"cp_down:{self.bot}:{now.strftime('%Y%m%d%H')}")
            res.action_taken = "BLOCKED: control plane unavailable"
            return res
        res.control = control

        # 2. configuration
        try:
            self.cfg.validate_runtime(self.cfg) if callable(getattr(self.cfg, "validate_runtime", None)) else None
        except Exception as exc:  # noqa: BLE001
            res.action_taken = f"BLOCKED: invalid configuration ({exc})"
            return res

        # 3. reconciliation state
        recon_state = self._reconciliation_gate(control, log)
        res.reconciliation = recon_state.status.value
        max_age = self.cfg.RECON_STATE_MAX_AGE_SECONDS
        exits_ok = recon_state.allows_exits(self.clock.now(), max_age)
        entries_ok = recon_state.allows_entries(self.clock.now(), max_age)
        if not exits_ok:
            res.action_taken = f"BLOCKED: reconciliation {recon_state.status.value} ({recon_state.reason})"
            return res

        # 4-5. market data validity
        snap = self.market.snapshot(self.cfg.SYMBOL)
        if not snap.valid:
            log.warning("market data invalid", extra={"reason": snap.reason})
            res.action_taken = f"BLOCKED: market data invalid ({snap.reason})"
            return res
        self.store.kv_set(f"health:{self.bot}:last_market_data_at", self.clock.now().isoformat())
        res.price, res.df = float(snap.mark_price), snap.candles
        rules = self.executor.symbol_rules(self.cfg.SYMBOL)
        position = self.positions.mark(self.cfg.SYMBOL, snap.mark_price)

        # risk: equity + daily loss
        balances_ok, quote_free, equity = True, None, None
        try:
            bal = self.executor.balances()
            quote_total = bal.total(rules.quote_asset)
            quote_free = Decimal(bal.balances.get(rules.quote_asset, {}).get("free", 0))
            equity = self.positions.equity(self.cfg.SYMBOL, quote_total, snap.mark_price)
            daily_halted = self.risk.update_daily(equity, self.clock.now().date().isoformat())
            res.equity = float(equity)
        except Exception as exc:  # noqa: BLE001
            balances_ok, daily_halted = False, self.risk.is_daily_halted()
            log.warning("balance query failed; entries blocked", extra={"error": str(exc)[:200]})

        # 6. strategy signal
        signal = self.strategy.evaluate(MarketContext(self.cfg.SYMBOL, snap.candles, snap.market_data, snap.sentiment))
        res.signal = signal
        signal_id = f"signal:{self.bot}:{self.cfg.SYMBOL}:{snap.candle_ts}:{signal.action}:{signal.score}"
        self.store.enqueue("signals", signal_id, {
            "client_signal_id": signal_id, "symbol": self.cfg.SYMBOL, "action": signal.action, "score": signal.score,
            "price": str(snap.signal_price), "reasons": signal.reasons, "candle_open_time": snap.candle_ts,
            "strategy_name": self.strategy.name, "strategy_version": self.strategy.version,
            "occurred_at": self.clock.now(), "mode": self.cfg.TRADING_MODE,
        })

        # 7-8. in-flight orders block new decisions
        in_flight = [o for o in self.orders.non_terminal(self.cfg.SYMBOL) if o.purpose != OrderPurpose.PROTECTIVE_STOP]
        for o in in_flight:
            try:
                self.orders.refresh(o) if not o.submission_ambiguous else self.orders.resolve_ambiguous(o)
            except Exception as exc:  # noqa: BLE001
                log.warning("in-flight refresh failed", extra={"client_order_id": o.client_order_id, "error": str(exc)[:200]})
        in_flight = [o for o in self.orders.non_terminal(self.cfg.SYMBOL) if o.purpose != OrderPurpose.PROTECTIVE_STOP]
        if in_flight:
            res.action_taken = f"WAITING: {len(in_flight)} order(s) in flight"
            return res

        position = self.positions.get(self.cfg.SYMBOL)
        is_open = position.is_open(rules)
        if is_open and position.stop_price is None:
            position = self.positions.set_stop(self.cfg.SYMBOL, rules.round_price(self.risk.stop_price(position.average_entry_price)))

        # exits -------------------------------------------------------------
        if is_open:
            stop_low = snap.stop_check_low(position.opened_at)
            if position.stop_price is not None and stop_low is not None and stop_low <= position.stop_price:
                res.action_taken = self._exit(OrderPurpose.STOP_EXIT, position, rules, position.stop_price, snap, log)
                return res
            if signal.action == "SELL":
                if control.get("emergency_stop"):
                    res.blocked_reasons.append("emergency stop: strategy exits suspended")
                else:
                    res.action_taken = self._exit(OrderPurpose.EXIT, position, rules, snap.signal_price, snap, log)
                    return res
            if self.cfg.PROTECTIVE_STOP_MODE == "exchange":
                res.action_taken = self._ensure_protective_stop(position, rules, snap, log) or "HOLD (position open)"
            else:
                res.action_taken = "HOLD (position open)"
            return res

        # entries -----------------------------------------------------------
        if signal.action != "BUY":
            res.action_taken = "HALTED" if daily_halted else "NONE"
            return res
        reasons = []
        if not control.get("enabled", False):
            reasons.append("bot paused")
        if control.get("emergency_stop"):
            reasons.append("emergency stop engaged")
        if not self.cfg.GLOBAL_TRADING_ENABLED:
            reasons.append("GLOBAL_TRADING_ENABLED=false")
        if not entries_ok:
            reasons.append(f"reconciliation {recon_state.status.value}")
        if not balances_ok:
            reasons.append("balances unavailable")
        reasons += self.risk.entry_checks(self.positions.open_position_count({self.cfg.SYMBOL: rules}))
        if reasons:
            res.blocked_reasons = reasons
            res.action_taken = "HALTED" if daily_halted and len(reasons) == 1 else "BLOCKED: " + "; ".join(reasons)
            log.info("entry blocked", extra={"reasons": reasons})
            return res
        qty, why = self.risk.size_entry(equity, snap.signal_price, rules, quote_free)
        if qty <= 0:
            res.action_taken = f"BLOCKED: {why}"
            return res
        order = self._submit(OrderPurpose.ENTRY, OrderSide.BUY, qty, rules, snap.signal_price,
                             f"{self.bot}:{self.cfg.SYMBOL}:{snap.candle_ts}:ENTRY", signal_id, log)
        if order is None:
            res.action_taken = "BLOCKED: entry already attempted for this candle"
            return res
        res.action_taken = self._describe(order, "BUY")
        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            p = self.positions.get(self.cfg.SYMBOL)
            if p.is_open(rules):
                p = self.positions.set_stop(self.cfg.SYMBOL, rules.round_price(self.risk.stop_price(p.average_entry_price)))
                if self.cfg.PROTECTIVE_STOP_MODE == "exchange" and order.status == OrderStatus.FILLED:
                    self._ensure_protective_stop(p, rules, snap, log)
        return res

    # ------------------------------------------------------------------ helpers
    def _reconciliation_gate(self, control: dict, log):
        ack = control.get("reconciliation_ack_token")
        state = self.store.get_recon_state(self.bot)
        if self.cfg.TRADING_MODE == "PAPER":
            # PAPER has no exchange stream; the trading worker reconciles its simulated ledger inline.
            operator_ack = bool(ack) and ack != state.last_ack_token
            self.recon.run("paper-cycle", operator_ack=operator_ack)
            if operator_ack:
                st = self.store.get_recon_state(self.bot)
                st.last_ack_token = ack
                self.store.save_recon_state(st)
            state = self.store.get_recon_state(self.bot)
        return state

    def _exit(self, purpose, position, rules, ref_price, snap, log) -> str:
        # Cancel any resting exchange stop first so we never double-sell.
        for o in self.orders.non_terminal(self.cfg.SYMBOL):
            if o.purpose == OrderPurpose.PROTECTIVE_STOP:
                o = self.orders.cancel(o)
                if not o.is_terminal:
                    return "WAITING: protective stop cancel not confirmed"
        position = self.positions.get(self.cfg.SYMBOL)
        qty = rules.round_qty(position.quantity)
        if not rules.is_tradeable(qty, ref_price):
            return f"DUST: position {position.quantity} below exchange minimums"
        base = f"{self.bot}:{self.cfg.SYMBOL}:{position.opened_at.isoformat() if position.opened_at else 'na'}:{purpose.value}"
        realized_before = position.realized_pnl
        order = self._submit(purpose, OrderSide.SELL, qty, rules, Decimal(ref_price), base, None, log)
        if order is None:
            return f"BLOCKED: {purpose.value} attempts exhausted"
        pnl = self.positions.get(self.cfg.SYMBOL).realized_pnl - realized_before
        label = "STOP LOSS HIT" if purpose == OrderPurpose.STOP_EXIT else "SELL"
        if order.status == OrderStatus.FILLED:
            return f"{label} (pnl {pnl:+.2f})"
        return self._describe(order, label)

    def _ensure_protective_stop(self, position, rules, snap, log) -> str | None:
        live = [o for o in self.orders.non_terminal(self.cfg.SYMBOL) if o.purpose == OrderPurpose.PROTECTIVE_STOP]
        if live or position.stop_price is None:
            return None
        qty = rules.round_qty(position.quantity)
        limit = rules.round_price(position.stop_price * (Decimal(1) - Decimal(str(self.cfg.PROTECTIVE_STOP_LIMIT_OFFSET_PCT)) / 100))
        if not rules.is_tradeable(qty, limit):
            return "HOLD (position below minimums for protective stop)"
        base = f"{self.bot}:{self.cfg.SYMBOL}:{position.opened_at.isoformat() if position.opened_at else 'na'}:PROTECTIVE_STOP"
        order = self._submit(OrderPurpose.PROTECTIVE_STOP, OrderSide.SELL, qty, rules, position.stop_price, base, None, log,
                             order_type=OrderType.STOP_LOSS_LIMIT, limit_price=limit, stop_price=position.stop_price)
        return None if order is None else f"PROTECTIVE STOP {order.status.value}"

    def _submit(self, purpose, side, qty, rules, ref_price, base_intent_id, signal_id, log,
                order_type=OrderType.MARKET, limit_price=None, stop_price=None):
        intent_id = self._next_intent_id(base_intent_id, purpose)
        if intent_id is None:
            enqueue_event(self.store, self.bot, "ORDER_ATTEMPTS_EXHAUSTED", base_intent_id, "CRITICAL",
                          key=f"exhausted:{base_intent_id}", alert=True)
            return None
        intent = OrderIntent(
            client_intent_id=intent_id, bot_instance_id=self.bot, symbol=self.cfg.SYMBOL, side=side.value,
            order_type=order_type.value, quantity=Decimal(qty), purpose=purpose,
            strategy_name=self.strategy.name, strategy_version=self.strategy.version, signal_id=signal_id,
            limit_price=limit_price, stop_price=stop_price, reference_price=Decimal(ref_price),
            created_at=self.clock.now(),
        )
        return self.orders.submit_intent(intent, rules)

    def _next_intent_id(self, base: str, purpose: OrderPurpose) -> str | None:
        """Same logical decision -> same intent id (no duplicate orders). A new attempt
        is only allowed after the previous attempt ended without any fill."""
        for attempt in range(MAX_ATTEMPTS[purpose]):
            iid = f"{base}:{attempt}"
            existing = self.store.get_order_by_intent(iid)
            if existing is None:
                return iid
            if not (existing.status in RETRYABLE_TERMINAL and existing.executed_quantity == 0):
                return iid           # returns the existing order; submit_intent will not resubmit
        return None

    @staticmethod
    def _describe(order, label: str) -> str:
        if order.status == OrderStatus.FILLED:
            return label
        if order.status == OrderStatus.REJECTED:
            return f"{label} REJECTED: {order.reject_reason}"
        return f"{label} {order.status.value}" + (" (ambiguous - reconciling)" if order.submission_ambiguous else "")
