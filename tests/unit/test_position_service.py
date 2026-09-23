from datetime import datetime, timezone
from decimal import Decimal as D

from engine.application.position_service import apply_fill_to_position
from engine.domain.models import Fill, Position

T = datetime(2026, 9, 22, tzinfo=timezone.utc)


def fill(side, qty, price, fee="0", asset=None, key="k"):
    q, p = D(qty), D(price)
    return Fill(1, "c", "BTC/USDT", side, q, p, q * p, D(fee), asset, T, key)


def test_position_averaging_and_realized_pnl_without_fees():
    p = Position("b", "BTC/USDT")
    apply_fill_to_position(p, fill("BUY", "1", "100"))
    apply_fill_to_position(p, fill("BUY", "1", "110"))
    assert p.quantity == D("2") and p.average_entry_price == D("105")
    r = apply_fill_to_position(p, fill("SELL", "2", "120"))
    assert r.realized_delta == D("30") and p.quantity == 0 and p.average_entry_price == 0


def test_base_asset_commission_reduces_quantity_and_raises_cost_basis():
    p = Position("b", "BTC/USDT")
    apply_fill_to_position(p, fill("BUY", "1", "100", "0.001", "BTC"))
    assert p.quantity == D("0.999")
    assert p.average_entry_price == D("100") / D("0.999")


def test_quote_commission_on_sell_reduces_realized_pnl():
    p = Position("b", "BTC/USDT")
    apply_fill_to_position(p, fill("BUY", "1", "100"))
    r = apply_fill_to_position(p, fill("SELL", "1", "110", "0.11", "USDT"))
    assert r.realized_delta == D("9.89")


def test_foreign_fee_asset_tracked_separately():
    p = Position("b", "BTC/USDT")
    apply_fill_to_position(p, fill("BUY", "1", "100", "0.05", "BNB"))
    assert p.fees_other == {"BNB": D("0.05")} and p.quantity == 1


def test_oversell_is_flagged_not_hidden():
    p = Position("b", "BTC/USDT")
    apply_fill_to_position(p, fill("BUY", "1", "100"))
    r = apply_fill_to_position(p, fill("SELL", "2", "100"))
    assert "SELL_EXCEEDS_TRACKED_POSITION" in r.anomalies and p.quantity == 0


def test_rebuild_matches_incremental(paper):
    from engine.domain.enums import OrderPurpose
    from engine.domain.models import Order, OrderIntent
    store, ps = paper.store, paper.s.positions
    intent = OrderIntent("i1", "test-bot", "BTC/USDT", "BUY", "MARKET", D("1"), OrderPurpose.ENTRY, "s", "1")
    o = Order("test-bot", "paper", "BTC/USDT", "BUY", "MARKET", OrderPurpose.ENTRY, "cb1-a", "i1", D("1"))
    store.insert_intent_and_order(intent, o)
    for i, (q, pr, fee, asset) in enumerate([("0.5", "100", "0.0005", "BTC"), ("0.5", "104", "0.1", "USDT")]):
        f = fill("BUY", q, pr, fee, asset, key=f"k{i}")
        f.order_id = o.id
        with store.transaction():
            assert store.insert_fill(f)
            ps.apply_fill(f)
    incremental = ps.get("BTC/USDT")
    rebuilt, anomalies = ps.rebuild("BTC/USDT", persist=False)
    assert not anomalies
    assert rebuilt.quantity == incremental.quantity
    assert rebuilt.average_entry_price == incremental.average_entry_price
