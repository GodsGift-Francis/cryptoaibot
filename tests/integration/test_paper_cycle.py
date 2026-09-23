"""PAPER mode end-to-end through the production lifecycle (intent -> order -> fill -> position)."""
from decimal import Decimal as D

from engine.domain.enums import OrderStatus, ReconciliationStatus


def test_paper_buy_then_stop_loss_matches_v1_math(paper):
    r = paper.cycle("BUY")
    assert r.action_taken == "BUY", r.action_taken
    pos = paper.s.positions.get("BTC/USDT")
    # V1 sizing: 1000 * 1% / 3% = 333.33 -> 3.33333 BTC @ 100 (paper step 1e-8)
    assert pos.quantity == D("3.33333333") and pos.average_entry_price == D("100")
    assert pos.stop_price == D("97.00000000")
    paper.market.price, paper.market.low = D("98"), D("96.5")      # candle low breaches the stop
    r = paper.cycle("HOLD")
    assert r.action_taken.startswith("STOP LOSS HIT"), r.action_taken
    pos = paper.s.positions.get("BTC/USDT")
    assert pos.quantity == 0
    assert pos.realized_pnl == D("3.33333333") * D("-3")              # V1 paper exits at the stop price
    assert paper.s.positions.paper_cash("BTC/USDT") == D("1000") - D("3.33333333") * D("3")


def test_strategy_sell_exit_records_realized_pnl(paper):
    paper.cycle("BUY")
    paper.market.price = D("110")
    r = paper.cycle("SELL")
    assert r.action_taken.startswith("SELL (pnl +33.33"), r.action_taken


def test_control_plane_unavailable_fails_closed(paper):
    paper.cp.down = True
    r = paper.cycle("BUY")
    assert r.action_taken == "BLOCKED: control plane unavailable"
    assert paper.store.orders("test-bot") == []


def test_control_plane_unavailable_blocks_even_stop_exits(paper):
    paper.cycle("BUY")
    paper.cp.down = True
    paper.market.low = D("90")
    assert paper.cycle("HOLD").action_taken == "BLOCKED: control plane unavailable"
    assert paper.s.positions.get("BTC/USDT").quantity > 0


def test_paused_blocks_entries_but_allows_stop_exit(paper):
    paper.cp.control["enabled"] = False
    r = paper.cycle("BUY")
    assert r.action_taken.startswith("BLOCKED") and "bot paused" in r.blocked_reasons
    paper.cp.control["enabled"] = True
    paper.cycle("BUY")
    paper.cp.control["enabled"] = False
    paper.market.low = D("90")
    assert paper.cycle("HOLD").action_taken.startswith("STOP LOSS HIT")


def test_emergency_stop_blocks_entries_and_strategy_exits_not_stop_loss(paper):
    paper.cycle("BUY")
    paper.cp.control["emergency_stop"] = True
    r = paper.cycle("SELL")
    assert r.action_taken == "HOLD (position open)" and "emergency stop" in r.blocked_reasons[0]
    paper.market.low = D("90")
    assert paper.cycle("HOLD").action_taken.startswith("STOP LOSS HIT")
    assert paper.cycle("BUY").action_taken.startswith("BLOCKED")


def test_global_trading_disabled_blocks_entries(paper):
    paper.cfg.GLOBAL_TRADING_ENABLED = False
    assert "GLOBAL_TRADING_ENABLED=false" in paper.cycle("BUY").blocked_reasons


def test_daily_loss_halt_blocks_entries_but_not_stop(paper):
    paper.cycle("BUY")
    paper.market.price, paper.market.low = D("80"), D("96")   # equity drawdown > 5%, stop hit
    assert paper.cycle("HOLD").action_taken.startswith("STOP LOSS HIT")  # V1.1 fix: stop runs while halted
    assert paper.s.cycle.risk.is_daily_halted()
    assert paper.cycle("BUY").action_taken == "HALTED"


def test_same_candle_intent_never_submits_twice(paper):
    paper.strategy.next = "BUY"
    paper.s.cycle.run_once()
    paper.s.cycle.run_once()          # same candle index -> same intent id
    assert len(paper.store.orders("test-bot")) == 1


def test_invalid_market_data_blocks_trading(paper):
    paper.market.valid = False
    assert paper.cycle("BUY").action_taken.startswith("BLOCKED: market data invalid")


def test_stale_reconciliation_state_blocks_exchange_trading(testnet):
    testnet.healthy()
    testnet.clock.advance(testnet.cfg.RECON_STATE_MAX_AGE_SECONDS + 5)
    r = testnet.cycle("BUY")
    assert r.action_taken.startswith("BLOCKED: reconciliation")
    assert testnet.rest.calls.count("new_order") == 0


def test_paper_restart_recovers_position_from_ledger(tmp_path):
    from tests.conftest import Harness
    h1 = Harness(tmp_path, "PAPER")
    h1.cycle("BUY")
    qty = h1.s.positions.get("BTC/USDT").quantity
    h2 = Harness(tmp_path, "PAPER", clock=h1.clock)   # same DB file, new process
    assert h2.s.positions.get("BTC/USDT").quantity == qty
    assert h2.healthy().status == ReconciliationStatus.HEALTHY
    assert h2.cycle("BUY").action_taken == "HOLD (position open)"   # no duplicate entry after restart


def test_outbox_delivers_idempotently_to_control_plane(paper):
    paper.cycle("BUY")
    n = paper.s.outbox.flush()
    kinds = {k for k, _, _ in paper.cp.posts}
    assert {"signals", "orders", "fills", "positions", "reconciliation"} <= kinds and n > 0
    ids = [rid for _, _, rid in paper.cp.posts]
    assert len(ids) == len(set(ids))
    assert paper.s.outbox.flush() == 0          # nothing redelivered once acknowledged


def test_orders_reach_filled_status_through_lifecycle(paper):
    paper.cycle("BUY")
    [o] = paper.store.orders("test-bot")
    assert o.status == OrderStatus.FILLED and o.submitted_at and o.acknowledged_at and o.terminal_at
    assert len(paper.store.fills_for_order(o.id)) == 1
