"""Spec section 18 integration scenarios, against a simulated Binance (raw REST + WS payloads)."""
from decimal import Decimal as D


from engine.domain.enums import OrderStatus as S, ReconciliationStatus as R
from engine.exchanges.binance import normalizer as nz
from tests.conftest import Harness

SYM = nz.SymbolMap(["BTC/USDT"])


def ws(h, message):
    _, ev = nz.unwrap(message)
    return h.s.ingestion.handle(nz.stream_event(ev, "test", SYM))


def only_order(h):
    [o] = h.store.orders("test-bot")
    return o


def entry(h, mode="fill"):
    assert h.healthy().status == R.HEALTHY
    h.rest.next_submit = mode
    return h.cycle("BUY")


# 1
def test_buy_fully_fills(testnet):
    r = entry(testnet)
    o = only_order(testnet)
    assert r.action_taken == "BUY" and o.status == S.FILLED
    pos = testnet.s.positions.get("BTC/USDT")
    # 10000 USDT * 1% / 3% = 3333.33 USDT -> 33.33333 BTC; 0.1% commission charged in BTC
    assert o.executed_quantity == D("33.33333")
    assert pos.quantity == D("33.33333") - D("33.33333") * D("0.001")


# 2
def test_buy_partial_then_completes_via_stream(testnet):
    entry(testnet, "partial")
    o = only_order(testnet)
    assert o.status == S.PARTIALLY_FILLED
    remaining = D(testnet.rest.orders[o.client_order_id]["origQty"]) - D(testnet.rest.orders[o.client_order_id]["executedQty"])
    assert ws(testnet, testnet.rest.execute(o.client_order_id, remaining)).outcome == "applied"
    o = only_order(testnet)
    assert o.status == S.FILLED and testnet.s.orders.fill_quantity(o) == o.executed_quantity
    assert testnet.healthy().status == R.HEALTHY


# 3
def test_buy_partial_then_canceled(testnet):
    entry(testnet, "partial")
    o = only_order(testnet)
    ws(testnet, testnet.rest.cancel_on_exchange(o.client_order_id))
    o = only_order(testnet)
    assert o.status == S.CANCELED and o.executed_quantity > 0
    pos = testnet.s.positions.get("BTC/USDT")
    assert pos.quantity == o.executed_quantity * D("0.999")          # position = partial fill net of BTC fee
    assert testnet.healthy().status == R.HEALTHY


# 4
def test_order_rejected(testnet):
    r = entry(testnet, "reject")
    o = only_order(testnet)
    assert o.status == S.REJECTED and "-2010" in o.reject_reason and r.action_taken.startswith("BUY REJECTED")
    assert testnet.s.positions.get("BTC/USDT").quantity == 0
    testnet.cycle("BUY")   # next candle: a fresh intent may be tried; the rejected one is never resubmitted
    assert testnet.rest.calls.count("new_order") == 2


# 5
def test_websocket_disconnect_right_after_submission_recovered_by_reconciliation(testnet):
    entry(testnet, "new")                                   # rests NEW; WS "drops" so no events are seen
    o = only_order(testnet)
    testnet.rest.execute(o.client_order_id, D("33.33333"))  # fills while we are blind
    testnet.s.reconciliation.set_state(R.DEGRADED, "websocket down")
    # DEGRADED: REST refresh of the in-flight order is allowed, new entries are not.
    assert testnet.cycle("BUY").action_taken == "HOLD (position open)"
    assert only_order(testnet).status == S.FILLED and testnet.rest.calls.count("new_order") == 1
    assert testnet.healthy().status == R.HEALTHY
    o = only_order(testnet)
    assert o.status == S.FILLED and testnet.s.positions.get("BTC/USDT").quantity > 0


# 6
def test_worker_restart_with_open_exchange_order(tmp_path):
    h1 = Harness(tmp_path, "TESTNET")
    entry(h1, "new")
    coid = only_order(h1).client_order_id
    h1.rest.execute(coid, D("10"))                          # partial fill happens while "down"
    h2 = Harness(tmp_path, "TESTNET", clock=h1.clock, rest=h1.rest)   # restart: same ledger, same exchange
    assert h2.store.get_recon_state("test-bot").status == R.HEALTHY    # persisted, but...
    h1.clock.advance(h2.cfg.RECON_STATE_MAX_AGE_SECONDS + 1)
    assert h2.cycle("BUY").action_taken.startswith("BLOCKED: reconciliation")   # ...stale until reconciled
    assert h2.healthy().status == R.HEALTHY
    o = h2.store.get_order(coid)
    assert o.status == S.PARTIALLY_FILLED and o.executed_quantity == D("10")
    assert h2.cycle("BUY").action_taken.startswith("WAITING")        # in-flight order blocks new orders


# 7
def test_duplicate_fill_event_does_not_double_count(testnet):
    entry(testnet, "new")
    o = only_order(testnet)
    msg = testnet.rest.execute(o.client_order_id, D("5"))
    assert ws(testnet, msg).outcome == "applied"
    assert ws(testnet, msg).outcome == "duplicate"
    testnet.healthy()                                       # REST myTrades returns the same trade id again
    assert len(testnet.store.fills_for_order(o.id)) == 1
    assert testnet.s.positions.get("BTC/USDT").quantity == D("5") * D("0.999")


# 8
def test_out_of_order_events(testnet):
    entry(testnet, "new")
    o = only_order(testnet)
    first = testnet.rest.execute(o.client_order_id, D("10"))
    second = testnet.rest.execute(o.client_order_id, D("23.33333"))   # completes the order
    ws(testnet, second)
    ws(testnet, first)                                      # older partial arrives late
    o = only_order(testnet)
    assert o.status == S.FILLED and o.executed_quantity == D("33.33333")
    assert testnet.s.orders.fill_quantity(o) == D("33.33333")


# 9
def test_local_open_but_exchange_filled(testnet):
    entry(testnet, "new")
    o = only_order(testnet)
    testnet.rest.execute(o.client_order_id, D("33.33333"))
    assert only_order(testnet).status == S.NEW
    assert testnet.healthy().status == R.HEALTHY
    assert only_order(testnet).status == S.FILLED


# 10
def test_local_filled_but_exchange_disagrees_halts(testnet):
    entry(testnet)
    o = only_order(testnet)
    testnet.rest.orders[o.client_order_id]["status"] = "CANCELED"      # exchange now reports a different terminal state
    res = testnet.healthy()
    assert res.status == R.HALTED and any(m["kind"] == "ORDER_STATE_CONFLICT" for m in res.critical)
    assert testnet.cycle("BUY").action_taken.startswith("BLOCKED: reconciliation HALTED")
    testnet.s.outbox.flush()
    assert any("ORDER_STATE_CONFLICT" in t or "RECONCILIATION_HALTED" in t for t in testnet.notifier.sent)


# 11
def test_pause_while_order_open(testnet):
    entry(testnet, "new")
    testnet.cp.control["enabled"] = False
    o = only_order(testnet)
    testnet.rest.execute(o.client_order_id, D("33.33333"))
    testnet.cycle("BUY")                                    # paused: refreshes in-flight order, no new entry
    assert only_order(testnet).status == S.FILLED
    assert testnet.rest.calls.count("new_order") == 1


# 12
def test_emergency_stop(testnet):
    testnet.healthy()
    testnet.cp.control["emergency_stop"] = True
    r = testnet.cycle("BUY")
    assert "emergency stop engaged" in r.blocked_reasons and testnet.rest.calls.count("new_order") == 0


# 13
def test_timeout_after_submission_is_resolved_by_query_not_retry(testnet):
    r = entry(testnet, "timeout_accepted")
    o = only_order(testnet)
    assert o.submission_ambiguous and o.status == S.FILLED     # query by clientOrderId found it
    assert testnet.rest.calls.count("new_order") == 1 and "get_order" in testnet.rest.calls
    assert testnet.s.positions.get("BTC/USDT").quantity > 0
    assert r.action_taken == "BUY"


def test_timeout_where_order_never_reached_exchange(testnet):
    entry(testnet, "timeout_lost")
    o = only_order(testnet)
    assert o.status == S.SUBMITTING and o.submission_ambiguous
    res = testnet.healthy()                                 # inside grace window: not trusted yet
    assert res.status == R.RECONCILING
    assert testnet.cycle("BUY").action_taken.startswith("BLOCKED: reconciliation")
    testnet.clock.advance(testnet.cfg.AMBIGUOUS_ORDER_GRACE_SECONDS + 1)
    assert testnet.healthy().status == R.HEALTHY
    o = only_order(testnet)
    assert o.status == S.REJECTED and o.reject_reason.startswith("NOT_FOUND_ON_EXCHANGE")
    assert testnet.rest.calls.count("new_order") == 1       # never blindly retried


# 14
def test_reconciliation_success_resumes_trading(testnet):
    testnet.s.reconciliation.set_state(R.DEGRADED, "ws down")
    assert testnet.cycle("BUY").action_taken.startswith("BLOCKED")
    assert testnet.healthy().status == R.HEALTHY
    testnet.rest.next_submit = "fill"
    assert testnet.cycle("BUY").action_taken == "BUY"


# 15
def test_reconciliation_failure_keeps_trading_halted_until_operator_ack(testnet):
    testnet.healthy()
    testnet.rest.balances["BTC"][0] = D("0")
    testnet.s.positions.store.save_position(testnet.s.positions.get("BTC/USDT"))
    entry(testnet)                                         # buy fills
    testnet.rest.balances["BTC"][0] = D("1")               # someone moved BTC out of the account
    res = testnet.healthy()
    assert res.status == R.HALTED and res.critical[0]["kind"] == "BALANCE_BELOW_POSITION"
    testnet.rest.balances["BTC"][0] = D("100")             # operator fixes the account
    res = testnet.healthy()
    assert res.status == R.HALTED and "awaiting operator" in res.reason
    res = testnet.s.reconciliation.run("operator ack", operator_ack=True)
    assert res.status == R.HEALTHY


def test_reconciliation_is_idempotent(testnet):
    entry(testnet)
    fills = len(testnet.store.fills("test-bot", "BTC/USDT"))
    qty = testnet.s.positions.get("BTC/USDT").quantity
    for _ in range(3):
        assert testnet.healthy().status == R.HEALTHY
    assert len(testnet.store.fills("test-bot", "BTC/USDT")) == fills
    assert testnet.s.positions.get("BTC/USDT").quantity == qty


def test_unknown_bot_order_on_exchange_halts(testnet):
    testnet.healthy()
    testnet.rest.next_submit = "new"
    testnet.rest.new_order({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "1",
                            "newClientOrderId": "cb1-orphan-order-not-in-ledger"})
    res = testnet.healthy()
    assert res.status == R.HALTED and res.critical[0]["kind"] == "UNKNOWN_BOT_ORDER"


def test_foreign_manual_order_is_a_warning_not_a_halt(testnet):
    testnet.rest.next_submit = "new"
    testnet.rest.new_order({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "1",
                            "newClientOrderId": "web_manual_123"})
    res = testnet.healthy()
    assert res.status == R.HEALTHY and any(m["kind"] == "FOREIGN_OPEN_ORDER" for m in res.mismatches)


def test_exchange_protective_stop_lifecycle(tmp_path):
    h = Harness(tmp_path, "TESTNET", PROTECTIVE_STOP_MODE="exchange")
    entry(h)
    stops = [o for o in h.store.orders("test-bot") if o.purpose.value == "PROTECTIVE_STOP"]
    assert len(stops) == 1 and stops[0].status == S.NEW and stops[0].order_type == "STOP_LOSS_LIMIT"
    assert stops[0].requested_quantity == h.s.positions.get("BTC/USDT").quantity.quantize(D("0.00001"), rounding="ROUND_DOWN")
    h.market.price = D("120")
    r = h.cycle("SELL")                                   # strategy exit cancels the resting stop first
    assert r.action_taken.startswith("SELL"), r.action_taken
    assert h.store.get_order(stops[0].client_order_id).status == S.CANCELED
    assert h.healthy().status == R.HEALTHY
