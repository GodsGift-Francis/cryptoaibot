"""PositionService - the ONLY code allowed to mutate position / P&L state.

A position is a pure function of its fills, so `rebuild()` (used by
reconciliation) and incremental `apply_fill()` (used on every new fill) share
one accounting routine and must always agree.

Fee handling (Binance Spot):
  * commission in BASE asset (typical on BUY): reduces quantity received;
  * commission in QUOTE asset (typical on SELL): raises cost / lowers proceeds;
  * commission in another asset (e.g. BNB): tracked in `fees_other`, not
    converted into P&L (no price source is assumed). Documented limitation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from engine.domain.models import ZERO, Fill, Position


@dataclass
class ApplyResult:
    position: Position
    realized_delta: Decimal = ZERO
    anomalies: list = field(default_factory=list)


def _assets(symbol: str) -> tuple[str, str]:
    base, quote = symbol.split("/")
    return base, quote


def apply_fill_to_position(p: Position, fill: Fill) -> ApplyResult:
    base, quote = _assets(fill.symbol)
    qty, price, fee = fill.quantity, fill.price, fill.commission
    gross = fill.quote_quantity if fill.quote_quantity > 0 else qty * price
    res = ApplyResult(position=p)
    fee_asset = fill.commission_asset

    if fee > 0 and fee_asset not in (base, quote, None):
        p.fees_other[fee_asset] = p.fees_other.get(fee_asset, ZERO) + fee

    if fill.side == "BUY":
        received = qty - (fee if fee_asset == base else ZERO)
        cost = gross + (fee if fee_asset == quote else ZERO)
        if received <= 0:
            res.anomalies.append("BUY_FILL_NON_POSITIVE_QUANTITY")
            return res
        new_qty = p.quantity + received
        p.average_entry_price = ((p.quantity * p.average_entry_price) + cost) / new_qty
        if p.quantity <= 0:
            p.opened_at = fill.execution_time
        p.quantity = new_qty
    elif fill.side == "SELL":
        delivered = qty + (fee if fee_asset == base else ZERO)
        proceeds = gross - (fee if fee_asset == quote else ZERO)
        if delivered > p.quantity + Decimal("1e-12"):
            res.anomalies.append("SELL_EXCEEDS_TRACKED_POSITION")
            delivered_for_cost = p.quantity
        else:
            delivered_for_cost = delivered
        # A base-asset fee on a SELL is delivered from holdings, so its cost basis
        # is included in delivered_for_cost while proceeds only cover `qty`.
        realized = proceeds - p.average_entry_price * delivered_for_cost
        p.realized_pnl += realized
        res.realized_delta = realized
        p.quantity = max(ZERO, p.quantity - delivered)
        if p.quantity == 0:
            p.average_entry_price = ZERO
            p.stop_price = None
            p.opened_at = None
    else:
        res.anomalies.append(f"UNKNOWN_SIDE:{fill.side}")
    if p.last_mark_price is not None:
        p.mark(p.last_mark_price)
    return res


class PositionService:
    def __init__(self, store, cfg, logger=None):
        self.store = store
        self.cfg = cfg
        self.bot = cfg.BOT_INSTANCE_ID
        self.log = logger

    # ------------------------------------------------------------------ mutations
    def apply_fill(self, fill: Fill) -> ApplyResult:
        """Must be called inside the same store transaction that inserted the fill."""
        p = self.store.get_position(self.bot, fill.symbol)
        res = apply_fill_to_position(p, fill)
        self.store.save_position(p)
        return res

    def rebuild(self, symbol: str, persist: bool = True) -> tuple[Position, list]:
        """Deterministically recompute the position from every recorded fill."""
        existing = self.store.get_position(self.bot, symbol)
        p = Position(bot_instance_id=self.bot, symbol=symbol, last_mark_price=existing.last_mark_price)
        anomalies: list = []
        for f in self.store.fills(self.bot, symbol):
            anomalies += apply_fill_to_position(p, f).anomalies
        if p.quantity > 0:
            p.stop_price = existing.stop_price
        if persist:
            self.store.save_position(p)
        return p, anomalies

    def set_stop(self, symbol: str, stop_price: Decimal) -> Position:
        with self.store.transaction():
            p = self.store.get_position(self.bot, symbol)
            p.stop_price = Decimal(stop_price)
            self.store.save_position(p)
            return p

    def mark(self, symbol: str, price: Decimal) -> Position:
        with self.store.transaction():
            p = self.store.get_position(self.bot, symbol)
            p.mark(Decimal(str(price)))
            self.store.save_position(p)
            return p

    # ------------------------------------------------------------------ queries
    def get(self, symbol: str) -> Position:
        return self.store.get_position(self.bot, symbol)

    def open_position_count(self, rules_by_symbol: dict | None = None) -> int:
        n = 0
        for p in self.store.positions(self.bot):
            rules = (rules_by_symbol or {}).get(p.symbol)
            if p.is_open(rules):
                n += 1
        return n

    def paper_cash(self, symbol: str) -> Decimal:
        """PAPER cash is derived from fills: start + sell proceeds - buy costs (quote-asset fees included)."""
        base, quote = _assets(symbol)
        cash = Decimal(str(self.cfg.STARTING_PAPER_BALANCE))
        for f in self.store.fills(self.bot, symbol):
            gross = f.quote_quantity if f.quote_quantity > 0 else f.quantity * f.price
            fee = f.commission if f.commission_asset == quote else ZERO
            cash += (gross - fee) if f.side == "SELL" else -(gross + fee)
        return cash

    def paper_balances(self, symbol: str):
        base, quote = _assets(symbol)
        return quote, self.paper_cash(symbol), base, self.get(symbol).quantity

    def equity(self, symbol: str, quote_balance_total: Decimal, mark_price: Decimal) -> Decimal:
        """V1 definition kept: quote balance + tracked position * mark."""
        return Decimal(quote_balance_total) + self.get(symbol).quantity * Decimal(str(mark_price))
