from decimal import Decimal as D

import pytest

from engine.domain.enums import OrderPurpose, OrderStatus as S
from engine.domain.events import ExchangeOrderReport
from engine.domain.models import IllegalTransition, NOT_FOUND_REASON, Order, can_transition, make_client_order_id, utcnow


def order(status=S.SUBMITTING):
    o = Order("b", "binance", "BTC/USDT", "BUY", "MARKET", OrderPurpose.ENTRY, "cb1-x", "i1", D("1"))
    o.status = status
    return o


def rep(status, executed="0", quote="0", t=1):
    return ExchangeOrderReport("cb1-x", "BTC/USDT", "BUY", "MARKET", status, D("1"), D(executed), D(quote),
                               exchange_order_id="42", update_time_ms=t)


def test_allowed_and_forbidden_transitions():
    assert can_transition(S.SUBMITTING, S.NEW)
    assert can_transition(S.NEW, S.PARTIALLY_FILLED)
    assert can_transition(S.PARTIALLY_FILLED, S.CANCELED)
    assert not can_transition(S.FILLED, S.NEW)
    assert not can_transition(S.CANCELED, S.PARTIALLY_FILLED)
    assert not can_transition(S.NEW, S.SUBMITTING)


def test_partial_fills_accumulate_monotonically():
    o = order()
    o.apply_report(rep(S.NEW), utcnow())
    o.apply_report(rep(S.PARTIALLY_FILLED, "0.4", "40", 2), utcnow())
    o.apply_report(rep(S.PARTIALLY_FILLED, "0.7", "71", 3), utcnow())
    assert o.status == S.PARTIALLY_FILLED and o.executed_quantity == D("0.7")
    assert o.average_fill_price == D("71") / D("0.7")
    o.apply_report(rep(S.FILLED, "1", "101", 4), utcnow())
    assert o.status == S.FILLED and o.terminal_at is not None


def test_terminal_state_never_moves_backwards_and_quantity_never_decreases():
    o = order()
    o.apply_report(rep(S.FILLED, "1", "100", 5), utcnow())
    changed = o.apply_report(rep(S.PARTIALLY_FILLED, "0.4", "40", 3), utcnow())   # stale, out-of-order
    assert o.status == S.FILLED and o.executed_quantity == D("1")
    assert changed is False


def test_conflicting_terminal_report_raises():
    o = order()
    o.apply_report(rep(S.CANCELED, "0", "0"), utcnow())
    with pytest.raises(IllegalTransition):
        o.apply_report(rep(S.FILLED, "1", "100"), utcnow())


def test_not_found_rejection_contradicted_by_exchange_raises():
    o = order()
    o.mark_rejected(NOT_FOUND_REASON + " after 90s", utcnow())
    with pytest.raises(IllegalTransition):
        o.apply_report(rep(S.NEW), utcnow())


def test_client_order_id_is_deterministic_and_binance_compatible():
    a = make_client_order_id("cb1", "bot:BTC/USDT:2026-09-22T11:00:00+00:00:ENTRY:0")
    b = make_client_order_id("cb1", "bot:BTC/USDT:2026-09-22T11:00:00+00:00:ENTRY:0")
    assert a == b and len(a) <= 36 and a.startswith("cb1-")
    assert all(ch.isalnum() or ch in "-_" for ch in a)
    assert a != make_client_order_id("cb1", "other")
